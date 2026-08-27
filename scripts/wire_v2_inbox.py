#!/usr/bin/env python3
"""Render a review-only GitHub issue for a Wire V2 dark-launch batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "wire_v2_dark_batch.json"
OUT = ROOT / "data" / "wire_v2_dark_inbox.md"
MAX_BYTES = 62_000


def render(payload: dict) -> str:
    proposals = payload.get("proposals") or []
    lines = [
        "# Wire V2 dark-launch review",
        "",
        (f"**{len(proposals)} proposals** from {payload['reviewed_event_count']} "
         f"reviewed events · {payload['reports_merged']} duplicate reports merged · "
         f"${payload['cost_usd']:.4f}"),
        "",
        "This is a side-by-side editorial test. Nothing here can publish.",
        "Reply with `keep 1`, `reject 2`, or plain-language notes so the V2 rules can be evaluated.",
    ]
    for number, card in enumerate(proposals, 1):
        source = card["primary_source"]
        lines.extend([
            "", "---", "",
            f"## {number}. {card['player']} — {card['team']} {card['position']}",
            "",
            f"**{card['reader_label']}** · {card['event_type']} · "
            f"{len(card['sources'])} source(s)",
            "", "### What changed", "", card["what_changed"],
            "", "### Lineup Beat impact", "", card["lineupbeat_impact"],
            "", f"[{source['author']} · {source['source_name']}]({source['url']})",
        ])
        if len(card["sources"]) > 1:
            lines.extend(["", "**Corroborating reports:** " + ", ".join(
                f"[{row['author']} · {row['source_name']}]({row['url']})"
                for row in card["sources"][1:]
            )])
        lines.extend([
            "", "<details><summary>Evidence basis</summary>", "",
            card["evidence_basis"], "", "</details>",
        ])
        if card.get("limitations"):
            lines.extend(["", "**Limits:** " + " ".join(card["limitations"])])
    ignored = [row for row in payload.get("outcomes") or []
               if row["decision"] != "PROPOSE"]
    if ignored:
        lines.extend(["", "---", "", "## Events not proposed", ""])
        for row in ignored:
            failures = "; ".join(row.get("validation_failures") or [])
            detail = failures or row.get("reason") or "No reason supplied"
            lines.append(
                f"- **{row['player']} ({row['team']})** — "
                f"{row['decision']}: {detail}"
            )
    body = "\n".join(lines) + "\n"
    if len(body.encode()) > MAX_BYTES:
        raise ValueError(f"Wire V2 issue is {len(body.encode())} bytes; limit {MAX_BYTES}")
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    payload = json.loads(args.source.read_text())
    body = render(payload)
    args.output.write_text(body)
    print(f"  Wire V2 review issue: {len(payload.get('proposals') or [])} proposals, "
          f"{len(body.encode())} bytes, 0 publications")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
