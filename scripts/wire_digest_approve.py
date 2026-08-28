#!/usr/bin/env python3
"""Apply one authorized, hash-bound digest approval comment."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wire import digest_approval, players
import wire_digest_inbox

PUBLICATIONS = ROOT / "data/wire_digest_publications.json"
LEDGER = ROOT / "data/wire_digest_approvals.json"
RESULT = ROOT / "data/wire_digest_approval_result.json"


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def named(text: str, player: str) -> bool:
    value = " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "
    words = re.findall(r"[a-z0-9]+", player.lower())
    return bool(words and (f" {' '.join(words)} " in value or
                           (len(words[-1]) >= 4 and f" {words[-1]} " in value)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=Path, required=True)
    args = parser.parse_args()
    event = json.loads(args.event.read_text())
    issue, comment = digest_approval.validate_event(event)
    manifest = digest_approval.decode(str(issue.get("body") or ""))
    if str(issue.get("body") or "").replace("\r\n", "\n").rstrip() != \
            wire_digest_inbox.render(manifest).replace("\r\n", "\n").rstrip():
        raise ValueError("visible digest wording does not match its manifest")
    decisions = digest_approval.parse_commands(comment, len(manifest["updates"]))
    publications = json.loads(PUBLICATIONS.read_text())
    if int(publications.get("count") or 0) < manifest["publication_count_at_draft"]:
        raise ValueError("digest publication count moved backward; generate a fresh issue")
    ledger = json.loads(LEDGER.read_text())
    comment_id = str((event.get("comment") or {}).get("id") or "")
    receipt_id = f"digest:{manifest['batch_id']}:{comment_id}"
    if any(row.get("receipt_id") == receipt_id for row in ledger["receipts"]):
        print("  digest approval comment already applied")
        return 0
    existing = {row["digest_item_id"] for row in publications["publications"]}
    registry = players.load()
    approved_at = now()
    outcomes, added = [], []
    for number in decisions["approved"] + decisions["rejected"]:
        source = manifest["updates"][number - 1]
        bullet = decisions["edits"].get(number, source["bullet"]).strip()
        decision = "APPROVED" if number in decisions["approved"] else "REJECTED"
        item_id = "digest:" + hashlib.sha256(
            f"{source['player_id']}|{source['event_type']}|{source['evidence_quote']}".encode()).hexdigest()[:24]
        if decision == "APPROVED":
            player = registry.by_id.get(source["player_id"])
            if player is None or (player.full_name, player.team, player.position) != (
                    source["player"], source["team"], source["position"]):
                raise ValueError(f"update {number} identity does not match the registry")
            if not 12 <= len(bullet) <= 200 or not named(bullet, source["player"]):
                raise ValueError(f"update {number} edited bullet is invalid")
            if item_id in existing:
                raise ValueError(f"update {number} is already published")
            added.append({
                "digest_item_id": item_id, "batch_id": manifest["batch_id"],
                "player_id": source["player_id"], "player": source["player"],
                "team": source["team"], "position": source["position"],
                "event_type": source["event_type"], "bullet": bullet,
                "evidence_quote": source["evidence_quote"],
                "evidence_sha256": hashlib.sha256(source["evidence_quote"].encode()).hexdigest(),
                "source_url": source["source_url"], "author": source["author"],
                "source_name": source["source_name"],
                "source_published_at": source["published_at"],
                "approved_by": digest_approval.APPROVER,
                "approved_by_handle": digest_approval.ACTOR,
                "approved_at": approved_at,
            })
            existing.add(item_id)
        outcomes.append({"number": number, "digest_item_id": item_id,
                         "decision": decision, "edited": number in decisions["edits"],
                         "bullet": bullet})
    publications["publications"].extend(added)
    publications["publications"].sort(key=lambda row: row["source_published_at"], reverse=True)
    publications["count"] = len(publications["publications"])
    PUBLICATIONS.write_text(json.dumps(publications, indent=1, ensure_ascii=False) + "\n")
    ledger["receipts"].append({
        "receipt_id": receipt_id, "batch_id": manifest["batch_id"],
        "issue_number": int(issue.get("number") or 0), "comment_id": comment_id,
        "approved_by": digest_approval.APPROVER, "approved_at": approved_at,
        "approval_statement": comment, "outcomes": outcomes,
    })
    ledger["count"] = len(ledger["receipts"])
    LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n")
    decided = {row["number"] for receipt in ledger["receipts"]
               if receipt.get("batch_id") == manifest["batch_id"]
               for row in receipt.get("outcomes") or []}
    remaining = len(manifest["updates"]) - len(decided)
    result = {"approved": len(added), "rejected": len(decisions["rejected"]),
              "published": len(added), "remaining": remaining,
              "close_issue": remaining == 0}
    RESULT.write_text(json.dumps(result, indent=1) + "\n")
    print(f"  digest approval: {len(added)} published, {len(decisions['rejected'])} rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
