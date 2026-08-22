#!/usr/bin/env python3
"""How N eligible evidence rows become M unique model calls.

    python3 scripts/wire_funnel_bridge.py --from ... --to ...

Two true statements sat next to each other and looked contradictory: 705 rows
were in an eligible evidence class, and 60 reached the model. Both are right,
and the gap is not one filter -- it is four, applied in an order that matters.

Class eligibility is tested SIXTH, not first. A row can be in the best
evidence class the pipeline has and still be about a defensive lineman, still
be the second copy of a claim, still be a backup with no promotion. This walks
the same order the filter walks and prints what leaves at each step, so the
two numbers can be read as one sentence.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import evidence as ev
from wire import relevance as rv
from wire.store import WireStore

ELIGIBLE = ("FIRSTHAND_OBSERVATION", "DIRECT_QUOTATION",
            "OFFICIAL_DESIGNATION", "APPROVED_REPORTER_DECLARATION")
FANTASY = {"QB", "RB", "WR", "TE"}


def parse(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", required=True)
    ap.add_argument("--to", dest="hi", required=True)
    ap.add_argument("--out", default="data/wire_funnel_bridge.json")
    args = ap.parse_args()
    lo, hi = parse(args.lo), parse(args.hi)

    store = WireStore()
    urls = {a["canonical_url"] for a in store.conn.execute(
        "SELECT canonical_url, published_at FROM wire_source_items")
        if (lambda d: d and lo <= d <= hi)(parse(a["published_at"]))}
    rows = [dict(r) for r in store.evidence()
            if r["review_status"] == "PENDING" and r["source_url"] in urls]
    rel = rv.load() if hasattr(rv, "load") else None

    steps = []
    def step(label, kept, dropped, why):
        steps.append({"step": label, "left": len(kept), "dropped": dropped,
                      "why": why})
        return kept

    n0 = [r for r in rows if r["evidence_class"] in ELIGIBLE]
    steps.append({"step": "in an eligible evidence class", "left": len(n0),
                  "dropped": len(rows) - len(n0),
                  "why": "UNCERTAIN, ANALYSIS_OR_OPINION, RELAYED_REPORTING"})

    a = [r for r in n0 if not r["exclusion_reason"]]
    step("minus position exclusions", a, len(n0) - len(a),
         "defensive players and offensive linemen; tested BEFORE class, so "
         "an eligible class does not protect a non-fantasy position")

    b = [r for r in a if r["player_id"]]
    step("minus rows with no exact identity", b, len(a) - len(b),
         "zero or several registry matches; never guessed")

    c = [r for r in b if (r["position"] or "").upper() in FANTASY]
    step("minus non-fantasy positions surviving the above", c, len(b) - len(c),
         "kickers, punters, long snappers")

    d = [r for r in c if not r["duplicate_of"]]
    step("minus duplicates of an earlier copy", d, len(c) - len(d),
         "the same claim already queued from another row")

    e, drop_rel = [], 0
    for r in d:
        v = rv.assess(r["player_id"], (r["position"] or "").upper(),
                      r["evidence_text"], rel)
        if v["eligible"]:
            e.append(r)
        else:
            drop_rel += 1
    step("minus the fantasy-relevance gate", e, drop_rel,
         "backups with no promotion, routine reserve work")

    seen_u, seen_c, f = {}, {}, []
    du = dc = 0
    for r in e:
        u = r["underlying_report_id"]
        if u and u in seen_u:
            du += 1
            continue
        if u:
            seen_u[u] = 1
        k = (r["player_id"], ev.norm_claim(r["evidence_text"])[:180])
        if k in seen_c:
            dc += 1
            continue
        seen_c[k] = 1
        f.append(r)
    step("minus rewrites of one underlying report", f, du,
         "two outlets rewriting one original is one report")
    steps.append({"step": "minus identical claims already queued",
                  "left": len(f), "dropped": dc,
                  "why": "same player, same normalised passage"})

    print(f"  window {lo.isoformat()} .. {hi.isoformat()}")
    print(f"  PENDING evidence rows in the window: {len(rows)}\n")
    print(f"  {'STEP':<52}{'DROPPED':>9}{'LEFT':>8}")
    print("  " + "-" * 69)
    for s in steps:
        print(f"  {s['step']:<52}{s['dropped']:>9}{s['left']:>8}")
    print("  " + "-" * 69)
    print(f"  {'UNIQUE CLAIMS THAT REACH THE MODEL':<52}{'':>9}{len(f):>8}")
    print("\n  why each step drops what it does:")
    for s in steps:
        if s["dropped"]:
            print(f"    {s['step']}\n      {s['why']}")

    Path(args.out).write_text(json.dumps(
        {"window": {"from": lo.isoformat(), "to": hi.isoformat()},
         "pending_rows_in_window": len(rows),
         "steps": steps, "reaching_model": len(f)}, indent=1) + "\n")
    print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
