#!/usr/bin/env python3
"""Compare our projections against someone else's ranking file.

    python3 scripts/compare.py ~/Downloads/rankings-RB-ppr.csv --position RB
    python3 scripts/compare.py file.csv --position RB --worst 15

Takes a CSV with a player-name column and a points column, matches on
suffix-stripped names, and prints the disagreements largest first.

The number to watch is rank correlation, not the point gap. Nobody compares
absolute totals across sites; they compare order. A systematic offset is
invisible and a scrambled order is not.

Prints the worst disagreements by name so each one can be looked at
individually with trace.py. A list of gaps is a to-do list, not a verdict.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import statistics
import sys
from pathlib import Path


def key(name: str) -> str:
    n = re.sub(r"[.'`]", "", (name or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def sniff(path: Path):
    """Find the name and points columns without being told."""
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit("  empty file")
    cols = list(rows[0])
    name_col = next((c for c in cols
                     if c.strip().lower() in ("player", "name", "player name")), None)
    if not name_col:
        name_col = next((c for c in cols if "player" in c.lower()), None)
    pts_col = None
    for cand in ("3d proj", "fpts", "ppr", "points", "proj", "total"):
        pts_col = next((c for c in cols if c.strip().lower() == cand), None)
        if pts_col:
            break
    if not pts_col:
        # first column after the name that parses as a float on most rows
        for c in cols:
            if c == name_col:
                continue
            ok = 0
            for r in rows[:20]:
                try:
                    float(str(r[c]).replace(",", ""))
                    ok += 1
                except (TypeError, ValueError):
                    pass
            if ok > 15:
                pts_col = c
                break
    if not name_col or not pts_col:
        sys.exit(f"  could not find name/points columns in {cols[:12]}")
    return rows, name_col, pts_col


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--position", default="RB")
    ap.add_argument("--top", type=int, default=40, help="how deep to compare")
    ap.add_argument("--worst", type=int, default=12)
    args = ap.parse_args()

    rows, name_col, pts_col = sniff(Path(args.file).expanduser())
    print(f"  reading '{name_col}' and '{pts_col}' from {Path(args.file).name}")

    theirs = {}
    for i, r in enumerate(rows, 1):
        try:
            pts = float(str(r[pts_col]).replace(",", ""))
        except (TypeError, ValueError):
            continue
        theirs[key(r[name_col])] = {"rank": len(theirs) + 1, "pts": pts,
                                    "name": r[name_col]}

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    ours = [dict(r) for r in conn.execute(
        "SELECT player, team, ppr, adjusted FROM projections WHERE position=? "
        "ORDER BY ppr DESC", (args.position.upper(),))]
    if not ours:
        sys.exit(f"  no {args.position} projections. Publish first.")

    print(f"\n  {'#':<4}{'PLAYER':<24}{'OURS':>6}{'THEIRS':>8}{'THEIR#':>8}{'GAP':>8}")
    gaps, pairs, misses = [], [], []
    for i, o in enumerate(ours[:args.top], 1):
        t = theirs.get(key(o["player"]))
        if not t:
            misses.append(o["player"])
            print(f"  {i:<4}{o['player'][:24]:<24}{o['ppr']:>6.0f}"
                  f"{'not ranked':>16}")
            continue
        gaps.append(o["ppr"] - t["pts"])
        pairs.append((i - 1, t["rank"] - 1, o["player"], o["ppr"], t["pts"], t["rank"]))
        print(f"  {i:<4}{o['player'][:24]:<24}{o['ppr']:>6.0f}{t['pts']:>8.0f}"
              f"{t['rank']:>8}{o['ppr'] - t['pts']:>+8.0f}")

    if not pairs:
        sys.exit("\n  nothing matched — check the name column")

    n = len(pairs)
    rho = 1 - 6 * sum((a - b) ** 2 for a, b, *_ in pairs) / (n * (n * n - 1))
    print(f"\n  matched {n}   mean gap {statistics.mean(gaps):+.0f}   "
          f"median {statistics.median(gaps):+.0f}")
    print(f"  within 25 points: {sum(1 for g in gaps if abs(g) <= 25)}/{n}")
    print(f"  rank correlation: {rho:+.2f}", end="")
    if rho >= 0.75:
        print("   strong agreement")
    elif rho >= 0.5:
        print("   reasonable agreement")
    elif rho >= 0.3:
        print("   weak — worth understanding the outliers")
    else:
        print("   the boards disagree structurally")

    print(f"\n  BIGGEST DISAGREEMENTS  (trace each one)")
    for a, b, name, mine, thm, thrank in sorted(
            pairs, key=lambda x: -abs(x[0] - x[1]))[:args.worst]:
        print(f"    {name[:24]:<24} ours #{a+1:<3} theirs #{thrank:<3}  "
              f"{mine:.0f} vs {thm:.0f}")
        print(f"        python3 scripts/trace.py \"{name}\" "
              f"--season 2025 --compare {thm:.0f}")

    if misses:
        print(f"\n  in our top {args.top} but not on their list: "
              f"{', '.join(misses[:8])}")
        print("  Either they know something we do not, or a name did not match.")


if __name__ == "__main__":
    main()
