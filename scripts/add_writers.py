#!/usr/bin/env python3
"""Turn writers.nfl.txt into twitterapi source entries in sources/<sport>.yaml.

    python3 scripts/add_writers.py --priority          # the 36 flagged writers
    python3 scripts/add_writers.py --all               # all 111
    python3 scripts/add_writers.py --priority --dry-run

Idempotent: re-running skips handles already present, so you can start with
--priority, confirm cost and quality, then run --all to add the rest.

Cost note before you go wide: at ~$0.15 per 1000 tweets and ~20 posts per
writer per day, 111 writers is roughly $10/month. The cursor is what keeps it
there -- without it you re-fetch and re-pay the same posts on every poll.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def parse_writers(path: Path) -> list[tuple[str, str, bool]]:
    """Returns (handle, team, is_priority)."""
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        priority = "PRIORITY" in line
        line = line.split("#")[0].strip()
        if not line:
            continue
        handle, _, team = line.partition(",")
        handle = handle.strip().lstrip("@")
        team = team.strip().upper()
        if handle and team:
            out.append((handle, team, priority))
    return out


def slug(handle: str) -> str:
    return re.sub(r"[^a-z0-9]", "", handle.lower())[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--file", default="writers.nfl.txt")
    ap.add_argument("--priority", action="store_true",
                    help="only writers flagged PRIORITY")
    ap.add_argument("--all", action="store_true", help="every writer in the file")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not (args.priority or args.all):
        ap.error("pass --priority or --all")

    writers = parse_writers(ROOT / args.file)
    if args.priority:
        writers = [w for w in writers if w[2]]
    if not writers:
        raise SystemExit("  no writers matched. Check the file and flags.")

    path = ROOT / "sources" / f"{args.sport}.yaml"
    doc = yaml.safe_load(path.read_text())
    existing = {
        (s.get("handle") or "").lower()
        for s in doc.get("sources", [])
        if s.get("kind") == "twitterapi"
    }

    added, skipped = [], []
    for handle, team, _ in writers:
        if handle.lower() in existing:
            skipped.append(handle)
            continue
        doc["sources"].append({
            "id": f"{args.sport}-{team.lower()}-tapi-{slug(handle)}",
            "kind": "twitterapi",
            "handle": handle,
            "name": handle,
            "outlet": "X",
            "teams": [team],
        })
        existing.add(handle.lower())
        added.append((handle, team))

    per_team = Counter(t for _, t in added)
    print(f"  adding {len(added)} writers across {len(per_team)} teams"
          f" ({len(skipped)} already present)")
    if per_team:
        thin = [t for t, n in per_team.items() if n == 1]
        print(f"  per team: min {min(per_team.values())}, max {max(per_team.values())}")
        if thin:
            print(f"  only one writer for: {', '.join(sorted(thin))}")

    daily = len(added) * 20 * 0.00015
    print(f"  estimated cost for these: ${daily:.2f}/day, ${daily * 30:.2f}/month")

    if args.dry_run:
        print("\n  --dry-run, nothing written")
        return

    # Preserve the header comments; yaml.dump drops them.
    header = "\n".join(
        l for l in path.read_text().splitlines() if l.startswith("#")
    )
    path.write_text(
        header + "\n\n"
        + yaml.dump(doc, sort_keys=False, allow_unicode=True, width=100)
    )
    print(f"\n  wrote {path}")
    print(f"  next: python3 -m beatwire.cli doctor --sport {args.sport}")


if __name__ == "__main__":
    main()
