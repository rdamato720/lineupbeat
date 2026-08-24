#!/usr/bin/env python3
"""Apply one authorized GitHub mobile comment through wire_publish.py."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wire_onsi_batch_preview as batch_preview
import wire_mobile_inbox as mobile_inbox
import wire_publication_preview as publication_preview
from wire import mobile_approval as mobile
from wire import players
from wire import public_summary


ROOT = Path(__file__).resolve().parent.parent
PUBLICATIONS = ROOT / "data" / "wire_publications.json"
PREVIEW_JSON = ROOT / "data" / "wire_publication_preview.json"
PREVIEW_HTML = ROOT / "data" / "wire_publication_preview.html"
LEDGER = ROOT / "data" / "wire_mobile_approvals.json"
RESULT = ROOT / "data" / "wire_mobile_approval_result.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_ledger() -> dict:
    if not LEDGER.exists():
        return {"schema_version": mobile.APPROVAL_SCHEMA,
                "count": 0, "receipts": []}
    payload = json.loads(LEDGER.read_text())
    if payload.get("schema_version") != mobile.APPROVAL_SCHEMA:
        raise ValueError("mobile approval ledger schema is unsupported")
    receipts = payload.get("receipts")
    if not isinstance(receipts, list) or payload.get("count") != len(receipts):
        raise ValueError("mobile approval ledger count is invalid")
    return payload


def decided_ids(ledger: dict, batch_id: str) -> set[str]:
    return {
        outcome["candidate_id"]
        for receipt in ledger["receipts"]
        if receipt.get("batch_id") == batch_id
        for outcome in receipt.get("outcomes") or []
        if outcome.get("decision") in {"APPROVED", "REJECTED"}
    }


def validate_identity(card: dict, registry) -> list[str]:
    errors = []
    player = registry.by_id.get(str(card.get("player_id") or ""))
    if player is None:
        return ["player id is absent from the Wire identity registry"]
    shown = (players.norm(str(card.get("player") or "")),
             str(card.get("team") or ""), str(card.get("position") or ""))
    expected = (players.norm(player.full_name), player.team, player.position)
    if shown != expected:
        errors.append("card identity does not match the Wire registry")
    return errors


def validate_finished(card: dict) -> list[str]:
    errors = publication_preview.readiness_failures(card)
    errors.extend(public_summary.validate(
        card["public_summary"], card["player"], card["evidence"],
        card.get("content_type", "REPORTING"),
        bool(card.get("summary_subject_context"))))
    return sorted(set(errors))


def run(command: list[str]) -> None:
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()

    event = json.loads(args.event.read_text())
    issue, comment = mobile.validate_event(event)
    issue_body = str(issue.get("body") or "")
    manifest = mobile.decode_manifest(issue_body)
    visible = issue_body.replace("\r\n", "\n").rstrip()
    expected_visible = mobile_inbox.render(manifest).replace(
        "\r\n", "\n").rstrip()
    if visible != expected_visible:
        raise ValueError(
            "visible issue wording does not match its embedded manifest")
    decisions = mobile.parse_commands(comment, len(manifest["cards"]))
    ledger = load_ledger()
    publication_sha_before = hashlib.sha256(PUBLICATIONS.read_bytes()).hexdigest()
    comment_id = str((event.get("comment") or {}).get("id") or "")
    receipt_id = f"mobile:{manifest['batch_id']}:{comment_id}"
    if any(x.get("receipt_id") == receipt_id for x in ledger["receipts"]):
        print(f"  comment {comment_id} was already applied")
        return 0

    already = decided_ids(ledger, manifest["batch_id"])
    selected_numbers = decisions["approved"] + decisions["rejected"]
    selected_ids = {
        manifest["cards"][number - 1]["evidence_candidate_id"]
        for number in selected_numbers
    }
    repeated = sorted(selected_ids & already)
    if repeated:
        raise ValueError("comment repeats decided cards: " + ", ".join(repeated))

    publication_payload = json.loads(PUBLICATIONS.read_text())
    publications_before = int(publication_payload.get("count") or 0)
    if publications_before < manifest["publication_count_at_draft"]:
        raise ValueError(
            "publication count moved backward since this inbox was drafted; "
            "a fresh inbox is required")
    existing_ids = {
        str(row.get("evidence_candidate_id") or "")
        for row in publication_payload.get("publications") or []
    }
    duplicate_approvals = sorted(
        manifest["cards"][number - 1]["evidence_candidate_id"]
        for number in decisions["approved"]
        if manifest["cards"][number - 1]["evidence_candidate_id"] in existing_ids
    )
    if duplicate_approvals:
        raise ValueError("selected cards are already published: " +
                         ", ".join(duplicate_approvals))

    approved_at = now_utc()
    approved_cards = []
    outcomes = []
    registry = players.load()
    for number in selected_numbers:
        source_card = manifest["cards"][number - 1]
        card = dict(source_card)
        edited = decisions["edits"].get(number)
        decision = "APPROVED" if number in decisions["approved"] else "REJECTED"
        if edited:
            card.update(edited)
        if decision == "APPROVED":
            card.update({
                "reviewer_action": "APPROVE_WITH_EDIT",
                "public_summary_approved_by": mobile.APPROVER_NAME,
                "commentary_approved_by": mobile.APPROVER_NAME,
                "approved_at": approved_at,
                "evidence_sha256": sha256_text(card["evidence"]),
            })
            failures = validate_identity(card, registry) + validate_finished(card)
            if failures:
                raise ValueError(
                    f"card {number} {card['player']} failed approval: " +
                    "; ".join(sorted(set(failures))))
            card["readiness_failures"] = []
            approved_cards.append(card)
        outcomes.append({
            "card_number": number,
            "candidate_id": card["evidence_candidate_id"],
            "player_id": card["player_id"],
            "player_name": card["player"],
            "decision": decision,
            "edited": bool(edited),
            "public_summary": card["public_summary"],
            "lineupbeat_impact": card["commentary"],
            "evidence_sha256": sha256_text(card["evidence"]),
            "public_summary_sha256": sha256_text(card["public_summary"]),
            "lineupbeat_impact_sha256": sha256_text(card["commentary"]),
        })

    preview = {
        "schema_version": "wire-mobile-publication-preview-v1",
        "batch_id": manifest["batch_id"],
        "generated_at": manifest["generated_at"],
        "reviewer_action": "APPROVED",
        "reviewer": mobile.APPROVED_GITHUB_ACTOR,
        "reviewer_name": mobile.APPROVER_NAME,
        "approved_at": approved_at,
        "approval_statement": comment,
        "catalog_counts": {"source_items": len(manifest["cards"]),
                           "player_candidates": len(manifest["cards"]),
                           "discovered_not_captured": 0},
        "model_calls": manifest.get("model_calls", 0),
        "cost_usd": manifest.get("cost_usd", 0),
        "publications_applied": 0,
        "cards": approved_cards,
        "held_back": [
            {"player": manifest["cards"][number - 1]["player"],
             "why": "rejected from GitHub mobile review"}
            for number in decisions["rejected"]
        ],
    }
    PREVIEW_JSON.write_text(json.dumps(
        preview, indent=1, ensure_ascii=False) + "\n")
    PREVIEW_HTML.write_text(batch_preview.render(preview))

    if approved_cards and args.publish:
        run([sys.executable, "scripts/wire_health.py", "--snapshot"])
        run([sys.executable, "scripts/wire_publish.py", "--dry-run"])
        run([sys.executable, "scripts/wire_publish.py", "--publish",
             "--actor", mobile.APPROVER_NAME])
    elif approved_cards:
        print("  dry run; approved cards prepared but not published")

    after_payload = json.loads(PUBLICATIONS.read_text())
    publication_sha_after = hashlib.sha256(PUBLICATIONS.read_bytes()).hexdigest()
    publications_after = int(after_payload.get("count") or 0)
    applied = publications_after - publications_before
    expected = len(approved_cards) if args.publish else 0
    if applied != expected:
        raise ValueError(
            f"publication count changed by {applied}; expected {expected}")

    receipt = {
        "receipt_id": receipt_id,
        "batch_id": manifest["batch_id"],
        "issue_number": int(issue.get("number") or 0),
        "comment_id": comment_id,
        "approved_by": mobile.APPROVER_NAME,
        "approved_by_handle": mobile.APPROVED_GITHUB_ACTOR,
        "approved_at": approved_at,
        "approval_statement": comment,
        "manifest_sha256": manifest["batch_id"],
        "publication_sha256_at_draft": manifest[
            "publication_sha256_at_draft"],
        "publication_sha256_before": publication_sha_before,
        "publication_sha256_after": publication_sha_after,
        "publication_store_changed_since_draft": (
            publication_sha_before != manifest["publication_sha256_at_draft"]),
        "publication_count_before": publications_before,
        "publication_count_after": publications_after,
        "publications_applied": applied,
        "outcomes": outcomes,
    }
    ledger["receipts"].append(receipt)
    ledger["count"] = len(ledger["receipts"])
    LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n")

    all_decided = decided_ids(ledger, manifest["batch_id"])
    remaining = [
        card["evidence_candidate_id"] for card in manifest["cards"]
        if card["evidence_candidate_id"] not in all_decided
    ]
    result = {
        "batch_id": manifest["batch_id"],
        "issue_number": int(issue.get("number") or 0),
        "approved": len(approved_cards),
        "rejected": len(decisions["rejected"]),
        "published": applied,
        "remaining": len(remaining),
        "close_issue": not remaining,
    }
    RESULT.write_text(json.dumps(result, indent=1) + "\n")
    print(f"  mobile approval: {len(approved_cards)} approved, "
          f"{len(decisions['rejected'])} rejected, {applied} published")
    print(f"  {len(remaining)} card(s) remain in issue #{result['issue_number']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
