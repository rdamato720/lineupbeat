#!/usr/bin/env python3
"""Collapse evidence rows that were only ever one candidate.

    python3 scripts/wire_dedup_migrate.py --plan
    python3 scripts/wire_dedup_migrate.py --apply

candidate_id used to derive from the span's GROUP, which carries the span's
location. Overlapping windows produce the same passage at seg24s0 and
seg24s1, so one claim about one player in one article acquired two ids and
two rows. 598 rows in a single 48-hour window were exact duplicates of
another row -- same url, same player, same text.

The id derives from the article, the player and the normalised passage now,
so re-extraction updates rather than duplicates. This migrates what the old
key already produced.

WHAT IS PRESERVED

A reviewer's decision outranks everything. Where a duplicate group contains a
row that is not PENDING -- reviewed, published, superseded, excluded -- that
row is the survivor and the others are marked SUPERSEDED. Nothing is deleted,
ever: a decision that cannot be traced back to the row it was made on cannot
be audited.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import evidence as ev
from wire.store import WireStore

# Most decided first: the survivor of a group is the row furthest through
# review, so a migration can never discard a decision in favour of a blank.
RANK = {"PUBLISHED": 0, "APPROVED": 1, "EDITORIAL_REVIEW": 2, "REJECTED": 3,
        "NO_FANTASY_IMPACT": 4, "EXCLUDED": 5, "PENDING": 6, "SUPERSEDED": 7}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan", action="store_true")
    args = ap.parse_args()

    store = WireStore()
    rows = [dict(r) for r in store.evidence()]
    print(f"  {len(rows)} evidence row(s)")

    groups = defaultdict(list)
    for r in rows:
        key = ev.candidate_id(r["source_id"] or r["source_url"],
                              r["player_id"] or "", r["player_name"] or "",
                              r["evidence_text"] or "")
        groups[key].append(r)

    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    redundant = sum(len(v) - 1 for v in dupes.values())
    print(f"  {len(groups)} distinct claims; {len(dupes)} of them hold more "
          f"than one row")
    print(f"  {redundant} redundant row(s)")

    states = Counter()
    at_risk = []
    for k, v in dupes.items():
        v.sort(key=lambda r: (RANK.get(r["review_status"], 9),
                              r["candidate_id"]))
        keep, drop = v[0], v[1:]
        states[keep["review_status"]] += 1
        decided = [d for d in drop if d["review_status"] not in
                   ("PENDING", "SUPERSEDED")]
        if decided:
            at_risk.append((keep, decided))

    print("  survivor status:", dict(states))
    print(f"  groups where a NON-survivor carries a decision: {len(at_risk)}")
    for keep, decided in at_risk[:5]:
        print(f"    {keep['player_name']}: keeping {keep['review_status']}, "
              f"others {[d['review_status'] for d in decided]}")
    if at_risk:
        print("    refusing to collapse those groups; they are left alone")

    if not args.apply:
        print("\n  --plan only, nothing written")
        return 0

    merged = 0
    for k, v in dupes.items():
        v.sort(key=lambda r: (RANK.get(r["review_status"], 9),
                              r["candidate_id"]))
        keep, drop = v[0], v[1:]
        if any(d["review_status"] not in ("PENDING", "SUPERSEDED")
               for d in drop):
            continue          # a decision lives here; leave the group intact
        for d in drop:
            if d["review_status"] == "SUPERSEDED":
                continue
            store.conn.execute(
                "UPDATE wire_evidence SET review_status = 'SUPERSEDED', "
                "duplicate_of = ? WHERE candidate_id = ?",
                (keep["candidate_id"], d["candidate_id"]))
            merged += 1
    store.conn.commit()
    print(f"\n  {merged} row(s) marked SUPERSEDED against their survivor")
    print("  nothing deleted; every row is still readable and auditable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
