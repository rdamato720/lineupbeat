#!/usr/bin/env python3
"""Every in-window evidence candidate, in exactly one terminal category.

    python3 scripts/wire_backfill_reconcile.py --from 2026-08-19T16:31:51+00:00 \
                                               --to   2026-08-21T16:31:51+00:00

The backfill's own counters answer "how many did each filter stop", which is
not the same question and does not have to sum to anything. This assigns each
candidate one terminal bucket in pipeline order, so the categories add up to
the corpus exactly. Where the two disagree, this file is the arithmetic and
the other is the narrative.

Nothing here changes data. It reads and counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import evidence as ev
from wire import registry as artreg
from wire import relevance as rv
from wire.store import WireStore

FANTASY = {"QB", "RB", "WR", "TE"}
OL = {"OL", "T", "G", "C", "OT", "OG"}
DEF = {"DB", "CB", "S", "FS", "SS", "DL", "DE", "DT", "LB", "ILB", "OLB", "NT"}

# The reviewer's taxonomy. Order is the order a candidate is tested in, so a
# row lands in the first that applies and appears exactly once.
CATEGORIES = [
    "stale or outside the backfill window",
    "defensive player",
    "offensive line",
    "player outside the fantasy-relevance registry",
    "wrong team",
    "unapproved author",
    "duplicate of the same underlying report",
    "relayed reporting",
    "analysis or opinion",
    "insufficient evidence",
    "direct quotation",
    "official transaction or participation designation",
    "firsthand observation",
    "sent to Claude",
    "other",
]


def parse(ts):
    """A timezone-aware timestamp, or None. Naive values are read as UTC.

    A naive stamp compared against an aware one raises rather than sorting
    wrongly, which is the good failure -- but half this corpus is stored
    without an offset, so the comparison has to be made possible rather than
    merely safe.
    """
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", required=True)
    ap.add_argument("--to", dest="hi", required=True)
    ap.add_argument("--out", default="data/wire_backfill_reconcile.json")
    args = ap.parse_args()
    lo, hi = parse(args.lo), parse(args.hi)

    store = WireStore()
    rel = rv.load() if hasattr(rv, "load") else None

    # In-window articles, by the publisher's own timestamp.
    urls = set()
    for a in store.conn.execute(
            "SELECT canonical_url, published_at FROM wire_source_items"):
        ts = parse(a["published_at"])
        if ts and lo <= ts <= hi:
            urls.add(a["canonical_url"])

    rows = [dict(r) for r in store.evidence()
            if r["review_status"] == "PENDING" and r["source_url"] in urls]

    bucket = {}
    detail = defaultdict(Counter)
    seen_reports, seen_claims = {}, {}

    for r in rows:
        cid = r["candidate_id"]
        pos = (r["position"] or "").upper()
        excl = r["exclusion_reason"] or ""
        cls = r["evidence_class"]

        def put(cat, why=""):
            bucket[cid] = cat
            detail[cat][why or cat] += 1

        if excl:
            if pos in DEF or "is not a fantasy position" in excl and pos in DEF:
                put("defensive player", excl)
            elif pos in OL or "offensive line" in excl:
                put("offensive line", excl)
            elif pos in FANTASY:
                put("other", f"excluded at a fantasy position: {excl}")
            else:
                put("player outside the fantasy-relevance registry", excl)
            continue
        if not r["player_id"]:
            put("player outside the fantasy-relevance registry",
                "no exact player identity")
            continue
        if pos not in FANTASY:
            put("player outside the fantasy-relevance registry",
                f"{pos or 'unknown'} is not a fantasy position")
            continue
        if r["duplicate_of"]:
            put("duplicate of the same underlying report", "duplicate_of set")
            continue
        if cls == "RELAYED_REPORTING":
            put("relayed reporting", "classified RELAYED_REPORTING")
            continue
        if cls == "ANALYSIS_OR_OPINION":
            put("analysis or opinion", "classified ANALYSIS_OR_OPINION")
            continue
        if cls == "UNCERTAIN":
            reasons = r["classification_reasons"] or "[]"
            key = "UNCERTAIN: " + str(reasons)[:70]
            put("insufficient evidence", key)
            continue

        # FIRSTHAND_OBSERVATION, DIRECT_QUOTATION and OFFICIAL_DESIGNATION.
        verdict = rv.assess(r["player_id"], pos, r["evidence_text"], rel)
        if not verdict["eligible"]:
            put("player outside the fantasy-relevance registry",
                verdict["reason"])
            continue
        urid = r["underlying_report_id"]
        if urid and urid in seen_reports:
            put("duplicate of the same underlying report",
                "another article carries this underlying report")
            continue
        if urid:
            seen_reports[urid] = cid
        ckey = (r["player_id"], ev.norm_claim(r["evidence_text"])[:180])
        if ckey in seen_claims:
            put("duplicate of the same underlying report", "identical claim")
            continue
        seen_claims[ckey] = cid
        put("sent to Claude", f"{cls} sent to the model")

    total = len(rows)
    counts = Counter(bucket.values())
    print(f"  window {lo.isoformat()} .. {hi.isoformat()}")
    print(f"  in-window articles      {len(urls)}")
    print(f"  evidence candidates     {total}\n")
    print(f"  {'CATEGORY':<52}{'N':>7}   {'%':>6}")
    print("  " + "-" * 68)
    run = 0
    for c in CATEGORIES:
        n = counts.get(c, 0)
        run += n
        print(f"  {c:<52}{n:>7}   {100*n/total:>5.1f}%")
    print("  " + "-" * 68)
    print(f"  {'TOTAL':<52}{run:>7}   {100*run/total:>5.1f}%")
    print(f"  reconciles: {'YES' if run == total else 'NO — ' + str(total - run) + ' unassigned'}")

    print("\n  WHY, within the largest categories")
    for c in ("insufficient evidence", "analysis or opinion",
              "defensive player", "offensive line",
              "player outside the fantasy-relevance registry",
              "duplicate of the same underlying report"):
        if not counts.get(c):
            continue
        print(f"\n    {c} ({counts[c]})")
        for why, n in detail[c].most_common(8):
            print(f"      {n:>6}  {why[:96]}")

    Path(args.out).write_text(json.dumps({
        "window": {"from": lo.isoformat(), "to": hi.isoformat()},
        "articles_in_window": len(urls),
        "candidates": total,
        "categories": {c: counts.get(c, 0) for c in CATEGORIES},
        "reconciles": run == total,
        "detail": {c: dict(detail[c]) for c in detail},
        "assignment": bucket,
    }, indent=1) + "\n")
    print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
