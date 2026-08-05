#!/usr/bin/env python3
"""Check the roster and depth chart are fit to publish projections from.

    python3 scripts/verify_roster.py
    python3 scripts/verify_roster.py --strict     # exit non-zero on any problem

Run this before every publish. A projection listing a player on a team he
left in March is the kind of error people screenshot, and it costs more trust
than the projection itself earns.

What it checks, roughly in order of how badly each one bites:

  FRESHNESS   When the roster file was last written. Sleeper is live, so a
              stale file is entirely our fault.
  MOVES       Players whose current team disagrees with where they played
              last season. Some of these are real transfers and some are our
              own join failing, and the only way to tell is to look.
  DEPTH       How much of the depth chart is actually populated. In August
              this is often thin or not yet updated for the new season, and
              a depth chart we half-believe is worse than none: it will
              quietly demote starters.
  RETIRED     Anyone still carrying a projection who is no longer on a roster.
  COVERAGE    Whether the players we project actually exist in the roster.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROSTER = ROOT / "rosters" / "nfl.csv"

TEAMS = {"ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB",
         "HOU","IND","JAX","KC","LV","LAC","LAR","MIA","MIN","NE","NO","NYG",
         "NYJ","PHI","PIT","SF","SEA","TB","TEN","WAS"}
SKILL = {"QB", "RB", "FB", "WR", "TE"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    problems, warnings = [], []

    if not ROSTER.exists():
        sys.exit("  no roster file. Run scripts/import_rosters.py nfl")

    age_h = (datetime.now(timezone.utc)
             - datetime.fromtimestamp(ROSTER.stat().st_mtime, timezone.utc)
             ).total_seconds() / 3600
    rows = list(csv.DictReader(ROSTER.open()))
    skill = [r for r in rows if (r.get("position") or "").upper() in SKILL]

    print(f"\n  ROSTER")
    print(f"    file written      {age_h:.0f} hours ago")
    print(f"    players           {len(rows):,}  ({len(skill):,} skill)")
    if age_h > 48:
        problems.append(f"roster is {age_h/24:.0f} days old — "
                        f"run scripts/import_rosters.py nfl")
    elif age_h > 12:
        warnings.append(f"roster is {age_h:.0f} hours old")

    teams = Counter(r["team"] for r in rows if r.get("team"))
    missing = TEAMS - set(teams)
    print(f"    teams represented {len(set(teams) & TEAMS)}/32")
    if missing:
        problems.append(f"no players for: {', '.join(sorted(missing))}")

    # --- depth chart -----------------------------------------------------
    with_depth = [r for r in skill if (r.get("depth_order") or "").strip()]
    pct = 100 * len(with_depth) / max(1, len(skill))
    print(f"\n  DEPTH CHART")
    print(f"    skill players with a slot   {len(with_depth):,}/{len(skill):,}"
          f"  ({pct:.0f}%)")
    starters = Counter()
    for r in with_depth:
        try:
            if int(r["depth_order"]) == 1:
                starters[(r["team"], (r.get("depth_pos") or "").upper())] += 1
        except ValueError:
            pass
    qb1 = sum(1 for (t, p), n in starters.items() if p.startswith("QB"))
    print(f"    teams with a listed QB1     {qb1}/32")
    if pct < 40:
        problems.append(f"only {pct:.0f}% of skill players have a depth slot — "
                        f"the depth factor will demote real starters. Consider "
                        f"--enable off for depth until Sleeper populates it.")
    elif pct < 70:
        warnings.append(f"depth chart is {pct:.0f}% populated, thin for August")
    if qb1 < 28:
        warnings.append(f"only {qb1} teams have a listed QB1 — depth chart "
                        f"may not be updated for the new season yet")

    dupes = [(k, n) for k, n in starters.items() if n > 1]
    if dupes:
        warnings.append(f"{len(dupes)} team/position slots list more than one "
                        f"starter, e.g. {dupes[0][0]}")

    # --- team moves ------------------------------------------------------
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    moved, unknown = [], []
    try:
        last = {}
        for r in conn.execute("""
            SELECT player_name, team, MAX(season) s FROM weekly_stats
            WHERE season_type='REG' GROUP BY player_name"""):
            last[r["player_name"].lower()] = (r["team"], r["s"])
        for r in skill:
            prev = last.get(r["name"].lower())
            if not prev:
                continue
            if prev[0] and r.get("team") and prev[0] != r["team"]:
                moved.append((r["name"], prev[0], r["team"], prev[1]))
        print(f"\n  TEAM CHANGES since their last recorded season")
        print(f"    players on a new team       {len(moved)}")
        for n, a, b, s in sorted(moved, key=lambda x: x[0])[:12]:
            print(f"      {n[:24]:<24} {a} -> {b}   (last played {s})")
        if len(moved) > 12:
            print(f"      … and {len(moved) - 12} more")
        print("\n    Spot-check a few of these by hand. Real transfers and a")
        print("    broken name join look identical from here.")
    except sqlite3.OperationalError:
        warnings.append("no weekly_stats table — cannot cross-check teams")

    # --- what we are about to publish ------------------------------------
    try:
        pr = conn.execute("SELECT player, position, team FROM projections").fetchall()
        names = {r["name"].lower(): r for r in rows}
        orphan = [p for p in pr if p["player"].lower() not in names]
        print(f"\n  PROJECTIONS")
        print(f"    published                   {len(pr):,}")
        print(f"    not found in the roster     {len(orphan)}")
        for p in orphan[:8]:
            print(f"      {p['player'][:24]:<24} {p['position']} {p['team']}")
        if orphan:
            warnings.append(f"{len(orphan)} projected players are not on any "
                            f"roster — likely retired, cut, or a name mismatch")

        mismatch = [p for p in pr if p["player"].lower() in names
                    and names[p["player"].lower()].get("team")
                    and names[p["player"].lower()]["team"] != p["team"]]
        print(f"    team disagrees with roster  {len(mismatch)}")
        for p in mismatch[:8]:
            print(f"      {p['player'][:24]:<24} projected {p['team']}, "
                  f"roster says {names[p['player'].lower()]['team']}")
        if mismatch:
            problems.append(f"{len(mismatch)} projections use a different team "
                            f"than the roster — republish after re-importing")
    except sqlite3.OperationalError:
        print("\n  PROJECTIONS\n    none published yet")

    print()
    for w in warnings:
        print(f"  WARN   {w}")
    for p_ in problems:
        print(f"  PROBLEM  {p_}")
    if not problems and not warnings:
        print("  Clean. Safe to publish.")
    elif not problems:
        print("\n  No blockers, but read the warnings before publishing.")
    else:
        print("\n  Fix the problems before publishing. A projection with a "
              "player on\n  the wrong team costs more trust than the "
              "projection earns.")

    if args.strict and problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
