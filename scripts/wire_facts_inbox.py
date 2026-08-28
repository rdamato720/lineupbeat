#!/usr/bin/env python3
"""Render a compact facts-only review issue."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def render(payload: dict) -> str:
    facts = payload.get("proposals") or []
    lines = ["# Fantasy Football News Updates You Need to Know", "",
             f"**{len(facts)} factual updates** · {payload['duplicate_count']} duplicates merged · "
             "0 model calls · $0.0000", "",
             "Nothing here can publish. Each bullet is mechanically cleaned from one exact source sentence.", ""]
    for number, fact in enumerate(facts, 1):
        lines.append(f"{number}. {fact['bullet']}")
    if facts:
        lines += ["", "<details><summary>Sources and exact evidence</summary>", ""]
        for number, fact in enumerate(facts, 1):
            lines += [f"### {number}. {fact['player']} — {fact['event_type']}", "",
                      f"[{fact['author']} · {fact['source_name']}]({fact['source_url']})", "",
                      f"> {fact['exact_evidence']}", ""]
        lines += ["</details>"]
    lines += ["", "<details><summary>Suppression diagnostics</summary>", "",
              f"- Discovery-only independent articles: "
              f"{payload.get('rejection_counts', {}).get('independent_article_is_discovery_only', 0)}",
              f"- No current named fact sentence: "
              f"{payload.get('rejection_counts', {}).get('no_current_named_fact_sentence', 0)}",
              "", "</details>"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "data/wire_facts_dark_batch.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/wire_facts_dark_inbox.md")
    args = parser.parse_args()
    body = render(json.loads(args.source.read_text()))
    if len(body.encode()) > 62_000:
        raise SystemExit("facts-only review exceeds GitHub's issue limit")
    args.output.write_text(body)
    print("  Facts-only review rendered; 0 publications")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
