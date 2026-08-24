#!/usr/bin/env python3
"""Render an exact, phone-friendly GitHub issue from a draft Wire batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wire_publication_preview as publication_preview
from wire import mobile_approval as mobile
from wire import public_summary


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "wire_mobile_batch.json"
PUBLICATIONS = ROOT / "data" / "wire_publications.json"
OUT_JSON = ROOT / "data" / "wire_mobile_inbox.json"
OUT_MD = ROOT / "data" / "wire_mobile_inbox.md"
MAX_ISSUE_BYTES = 62_000


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_card(card: dict) -> list[str]:
    failures = publication_preview.readiness_failures({
        **card, "reviewer_action": "APPROVE_WITH_EDIT"
    })
    failures.extend(public_summary.validate(
        str(card.get("public_summary") or ""),
        str(card.get("player") or ""),
        str(card.get("evidence") or ""),
        str(card.get("content_type") or "REPORTING"),
        bool(card.get("summary_subject_context")),
    ))
    if card.get("reviewer_action") not in (None, "", "PENDING"):
        failures.append("draft card already carries a reviewer decision")
    if card.get("public_summary_approved_by"):
        failures.append("draft summary already carries an approval")
    if card.get("commentary_approved_by"):
        failures.append("draft impact already carries an approval")
    return sorted(set(failures))


def render(manifest: dict) -> str:
    lines = [
        "# Lineup Beat Wire approval",
        "",
        (f"**{len(manifest['cards'])} cards** · batch "
         f"`{manifest['batch_id'][:12]}` · "
         f"{manifest['model_calls']} draft calls · "
         f"${manifest['cost_usd']:.4f}"),
        "",
        "Review the exact wording below. Nothing is live yet.",
        "",
        "## Approve from your phone",
        "",
        "Comment with one or more commands:",
        "",
        "```text",
        "approve all",
        "approve 1,3",
        "reject 2",
        "edit 3 | replacement What changed sentence. | Replacement impact.",
        "```",
        "",
        "Only comments from `rdamato720` can publish.",
    ]
    for number, card in enumerate(manifest["cards"], 1):
        label = "Fantasy analysis" if card["content_type"] == \
            "FANTASY_ANALYSIS" else "What changed"
        lines.extend([
            "",
            "---",
            "",
            (f"## {number}. {card['player']} — {card['team']} "
             f"{card['position']}"),
            "",
            (f"**{card['reader_label']}** · {card['mechanism']} · "
             f"{card['strength']} evidence"),
            "",
            f"### {label}",
            "",
            card["public_summary"],
            "",
            "### Lineup Beat impact",
            "",
            card["commentary"],
            "",
            (f"[{card['author']} · {card['source']}]({card['url']}) · "
             f"{str(card['date'])[:10]}"),
            "",
            "<details><summary>Full evidence reviewed</summary>",
            "",
            card["evidence"],
            "",
            "</details>",
        ])
    lines.extend(["", mobile.encode_manifest(manifest), ""])
    body = "\n".join(lines)
    if len(body.encode()) > MAX_ISSUE_BYTES:
        raise ValueError(
            f"mobile inbox issue is {len(body.encode())} bytes; "
            f"limit is {MAX_ISSUE_BYTES}")
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    source = json.loads(args.source.read_text())
    cards = list(source.get("cards") or [])
    if not cards:
        print("  no mobile draft cards; no inbox created")
        return 2
    if len(cards) > mobile.MAX_CARDS:
        cards = cards[:mobile.MAX_CARDS]
    blocked = []
    for index, card in enumerate(cards, 1):
        failures = validate_card(card)
        if failures:
            blocked.append(f"card {index} {card.get('player')}: " +
                           "; ".join(failures))
    if blocked:
        raise ValueError("mobile inbox refused:\n  " + "\n  ".join(blocked))

    generated_at = str(source.get("generated_at") or
                       datetime.now(timezone.utc).isoformat())
    manifest = mobile.make_manifest(
        cards, generated_at=generated_at,
        publication_sha256=sha256(PUBLICATIONS),
        publication_count=int(json.loads(PUBLICATIONS.read_text()).get("count") or 0),
        source_batch_sha256=sha256(args.source),
        model_calls=int(source.get("model_calls") or 0),
        cost_usd=float(source.get("cost_usd") or 0),
    )
    args.out_json.write_text(json.dumps(
        manifest, indent=1, ensure_ascii=False) + "\n")
    args.out_md.write_text(render(manifest))
    print(f"  mobile inbox {manifest['batch_id'][:12]}: {len(cards)} card(s)")
    print(f"  issue body {args.out_md.stat().st_size} bytes")
    print("  0 publications applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
