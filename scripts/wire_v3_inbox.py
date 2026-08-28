#!/usr/bin/env python3
"""Render the review-only Wire V3 issue."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def render(payload: dict) -> str:
    cards = payload.get("proposals") or []
    lines = ["# Fantasy Football News Updates You Need to Know", "",
             f"**{len(cards)} proposals** from {payload['reviewed_story_count']} reviewed stories · "
             f"{payload['reports_merged']} reports merged · ${payload['cost_usd']:.4f}", "",
             "Nothing here can publish. Judge whether each update belongs on the Beat.", ""]
    for card in cards:
        lines.append(f"- {card['what_changed']}")
    if cards:
        lines += ["", "---", "", "## Editor detail"]
    for number, card in enumerate(cards, 1):
        source = card["primary_source"]
        lines += ["", "---", "", f"## {number}. {card['player']} — {card['team']} {card['position']}",
                  "", f"**{card['reader_label']}** · {card['event_type']}",
                  "", "### What changed", "", card["what_changed"],
                  "", "### Lineup Beat impact", "", card["lineupbeat_impact"],
                  "", f"[{source['author']} · {source['source_name']}]({source['url']})",
                  "", "<details><summary>Exact evidence</summary>", "",
                  card["evidence_basis"], "", "</details>"]
    rejected = [row for row in payload.get("outcomes") or [] if row["decision"] != "PROPOSE"]
    if rejected:
        lines += ["", "---", "", "<details><summary>Stories not proposed and diagnostics</summary>", ""]
        for row in rejected:
            detail = "; ".join(row.get("validation_failures") or []) or row.get("reason") or "No reason"
            lines.append(f"- **{', '.join(row['players'])}** — {row['decision']}: {detail}")
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "data/wire_v3_dark_batch.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/wire_v3_dark_inbox.md")
    args = parser.parse_args()
    body = render(json.loads(args.source.read_text()))
    if len(body.encode()) > 62_000:
        raise SystemExit("Wire V3 issue exceeds GitHub limit")
    args.output.write_text(body)
    print("  Wire V3 review issue rendered; 0 publications")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
