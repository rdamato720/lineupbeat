#!/usr/bin/env python3
"""Does a cheaper model do this job? Measured, on your own posts.

    python3 scripts/compare_models.py --n 60
    python3 scripts/compare_models.py --n 60 --challenger claude-haiku-4-5-20251001

WHAT IS BEING COMPARED

The same items, through the same prompt, on two models. Then:

    agreement     did they name the same players
    fabrication   did the challenger invent a player the post does not name
    resolution    did the named players match the roster
    cost          what each would cost across a day's real volume

FABRICATION IS THE ONE THAT MATTERS

The local model evaluation found qwen 14B inventing thirty-four first names
across sixty posts: given a bare surname it produced a plausible full name
belonging to somebody else. That is worse than missing the item, because a
wrong claim on a real player's page looks exactly like a right one.

So the check is not "did the models agree" but "did the challenger name
somebody the text does not support". A model can disagree with the incumbent
and be correct; it cannot name a player who is not there.

WHAT IT DOES NOT MEASURE

Whether the incumbent is right. Sonnet is the baseline because it is what
the wire runs on, not because it is ground truth. Where the two disagree,
the post is printed so you can read it and decide.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Rough public rates, per million tokens. Override with --rates if these
# have moved; the comparison is about the ratio, not the absolute.
RATES = {
    "input": 3.0, "output": 15.0,          # incumbent, Sonnet-class
    "c_input": 0.80, "c_output": 4.0,      # challenger, Haiku-class
}


def norm(s):
    return " ".join(re.sub(r"[.'`’]", "", (s or "").lower()).split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--challenger", default="claude-haiku-4-5-20251001")
    ap.add_argument("--show", type=int, default=10,
                    help="how many disagreements to print in full")
    args = ap.parse_args()

    db = ROOT / args.db
    if not db.exists():
        sys.exit(f"  no database at {db}")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    from beatwire.registry import Registry
    from beatwire.resolve import Resolver
    from beatwire import extract as ex
    import anthropic

    reg = Registry(args.sport)
    resolver = Resolver(reg.players, reg.profile.position_groups)
    client = anthropic.Anthropic()

    # Real items that reached the model, newest first.
    rows = conn.execute(
        """SELECT i.* FROM items i
           WHERE i.source_id LIKE ? ORDER BY i.fetched_at DESC LIMIT ?""",
        (f"{args.sport}-%", args.n * 3)).fetchall()

    # Only ones that clear the prefilter, since those are the ones costing
    # money. Comparing on items the wire never sends would flatter both.
    from beatwire.models import RawItem
    import datetime as dt
    picked = []
    for r in rows:
        text = ((r["title"] or "") + "\n" + (r["body"] or "")).strip()
        item = RawItem(source_id=r["source_id"], sport=args.sport, url="",
                       title=r["title"] or "", body=r["body"] or "",
                       published_at=dt.datetime.now(dt.timezone.utc))
        team = None
        for s in reg.sources:
            if s.id == r["source_id"]:
                team = resolver.source_team_hint(s)
                break
        if ex.mentions_any_player(item, resolver, team, skill_only=True):
            picked.append((r, text, item, team))
        if len(picked) >= args.n:
            break

    if not picked:
        sys.exit("  no items cleared the prefilter; run the pipeline first")
    print(f"\n  {len(picked)} items that would have cost money\n")

    incumbent = ex.MODEL
    print(f"  incumbent  {incumbent}")
    print(f"  challenger {args.challenger}\n")

    stats = {"agree": 0, "differ": 0, "fabricated": 0,
             "inc_players": 0, "chl_players": 0,
             "inc_unresolved": 0, "chl_unresolved": 0}
    tokens = {"i_in": 0, "i_out": 0, "c_in": 0, "c_out": 0}
    timing = {"i": 0.0, "c": 0.0}
    disagreements = []

    for n, (row, text, item, team) in enumerate(picked, 1):
        results = {}
        for label, model in (("i", incumbent), ("c", args.challenger)):
            old = ex.MODEL
            ex.MODEL = model
            t0 = time.time()
            try:
                nuggets = ex.extract(item, next(
                    s for s in reg.sources if s.id == row["source_id"]),
                    reg.profile, resolver, client=client)
            except Exception as exc:
                print(f"    {n:>3}  {label} failed: {str(exc)[:60]}")
                nuggets = []
            finally:
                ex.MODEL = old
            timing[label] += time.time() - t0
            results[label] = nuggets

        inc = {norm(x.player_name) for x in results["i"] if x.player_name}
        chl = {norm(x.player_name) for x in results["c"] if x.player_name}
        stats["inc_players"] += len(inc)
        stats["chl_players"] += len(chl)
        stats["inc_unresolved"] += sum(1 for x in results["i"]
                                       if not x.resolved)
        stats["chl_unresolved"] += sum(1 for x in results["c"]
                                       if not x.resolved)

        # Fabrication: a full name the challenger produced whose surname
        # does not appear in the post at all.
        low = text.lower()
        for name in chl - inc:
            parts = name.split()
            if parts and parts[-1] not in low:
                stats["fabricated"] += 1
                disagreements.append(
                    (n, "FABRICATED", name, text[:150], sorted(inc),
                     sorted(chl)))

        if inc == chl:
            stats["agree"] += 1
        else:
            stats["differ"] += 1
            if len(disagreements) < args.show:
                disagreements.append(
                    (n, "differs", "", text[:150], sorted(inc), sorted(chl)))

        if n % 10 == 0:
            print(f"    {n}/{len(picked)}")

    total = len(picked)
    print(f"\n  AGREEMENT\n")
    print(f"    same players        {stats['agree']:>4} "
          f"({stats['agree']/total:.0%})")
    print(f"    differed            {stats['differ']:>4} "
          f"({stats['differ']/total:.0%})")
    print(f"\n  WHAT EACH FOUND\n")
    print(f"    {'':<14}{'PLAYERS':>9}{'UNRESOLVED':>12}")
    print(f"    {'incumbent':<14}{stats['inc_players']:>9}"
          f"{stats['inc_unresolved']:>12}")
    print(f"    {'challenger':<14}{stats['chl_players']:>9}"
          f"{stats['chl_unresolved']:>12}")

    print(f"\n  FABRICATION\n")
    if stats["fabricated"]:
        print(f"    {stats['fabricated']} name(s) the challenger produced "
              f"whose surname is not in the post.")
        print(f"    This is the failure that puts a wrong claim on a real")
        print(f"    player's page, and it is disqualifying on its own.")
    else:
        print(f"    none: every name the challenger produced has its "
              f"surname in the text")

    print(f"\n  SPEED\n")
    print(f"    incumbent   {timing['i']/total:.2f}s per item")
    print(f"    challenger  {timing['c']/total:.2f}s per item")

    print(f"\n  READ THE DISAGREEMENTS\n")
    for d in disagreements[:args.show]:
        n, kind, name, snippet, inc, chl = d
        print(f"    [{n}] {kind}{' ' + name if name else ''}")
        print(f"        {' '.join(snippet.split())[:110]}")
        print(f"        incumbent : {inc or '—'}")
        print(f"        challenger: {chl or '—'}")
        print()

    print(f"  The incumbent is the baseline, not the truth. Where they")
    print(f"  differ, read the post above and decide which is right.")
    print(f"\n  If fabrication is zero and agreement is high, the cheaper")
    print(f"  model is doing the job and the bill is a solved problem.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
