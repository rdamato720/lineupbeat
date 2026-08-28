#!/usr/bin/env python3
"""Render the complete digest as one human-review issue."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def render(payload: dict) -> str:
    updates = payload.get("proposals") or []
    lines = ["# Fantasy Football News Updates You Need to Know", "",
             f"**{len(updates)} updates** selected from {payload['reviewed_report_count']} prioritized reports · "
             f"{payload.get('high_signal_report_count', 0)} high-signal reports in the full window · "
             f"1 batch call · ${payload['cost_usd']:.4f}", "",
             "Nothing here can publish. Approve or edit the complete digest as one editorial package.", ""]
    for number, update in enumerate(updates, 1):
        lines.append(f"{number}. {update['bullet']} [Source]({update['source_url']})")
    if updates:
        lines += ["", "<details><summary>Exact evidence audit</summary>", ""]
        for number, update in enumerate(updates, 1):
            lines += [f"### {number}. {update['player']} — {update['event_type']}", "",
                      f"[{update['author']} · {update['source_name']}]({update['source_url']})", "",
                      f"> {update['evidence_quote']}", ""]
        lines += ["</details>"]
    rejected = payload.get("validation_rejections") or []
    lines += ["", "<details><summary>Validation diagnostics</summary>", "",
              f"- Model selections rejected deterministically: {len(rejected)}"]
    for row in rejected:
        lines.append(f"- {', '.join(row['failures'])}")
    lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "data/wire_digest_dark_batch.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/wire_digest_dark_inbox.md")
    args = parser.parse_args()
    body = render(json.loads(args.source.read_text()))
    if len(body.encode()) > 62_000:
        raise SystemExit("digest issue exceeds GitHub limit")
    args.output.write_text(body)
    print("  Digest review issue rendered; 0 publications")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
