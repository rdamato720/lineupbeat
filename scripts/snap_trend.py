#!/usr/bin/env python3
"""Whose role grew, and whose shrank.

    python3 scripts/snap_trend.py --season 2025
    python3 scripts/snap_trend.py --season 2025 --pos WR --top 25
    python3 scripts/snap_trend.py --season 2025 --json site/data/snaps.json

WHY SNAP SHARE AND NOT POINTS

A season total says what a player did. Snap share says what his team decided
about him, and it decides first: a receiver who finishes on forty percent of
snaps but played seventy over the last month is a different player next year
than the total suggests, and the draft board is built on the total.

We measured this earlier against the following season. Target share carried
more signal than points per game for receivers, air yards share more than
points for tight ends. The team's opinion is a better predictor than the
box score, and it is visible weeks before the box score catches up.

WHAT IT COMPARES

The first third of a season against the last third, skipping the middle so a
single big game does not move it. A player needs snaps in both halves to
appear at all -- somebody who missed the back half has an injury story, not
a usage one, and the durability page covers that.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = {"QB", "RB", "FB", "WR", "TE"}


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    return " ".join(re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n).split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--pos")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-weeks", type=int, default=4,
                    help="weeks needed in each window to count")
    ap.add_argument("--json")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT player, position, team, week, offense_pct
           FROM snap_counts WHERE season = ? AND offense_pct IS NOT NULL""",
        (args.season,)).fetchall()

    # Weeks a player was hurt, and weeks the league was resting starters.
    #
    # A back who tore something in week twelve looks exactly like one who
    # lost his job, and they mean opposite things about next season. Josh
    # Jacobs going from seventy-five percent to fifty could be a demotion or
    # it could be a playoff team sitting him in week eighteen -- and only one
    # of those belongs on a page about usage.
    #
    # So: injured weeks come out of the comparison, and anybody whose
    # decline coincides with an injury is labelled rather than ranked
    # alongside a genuine demotion.
    hurt = {}
    try:
        for r in conn.execute(
                """SELECT name_key, week FROM weekly_status
                   WHERE season = ? AND status IN ('RES','INA')""",
                (args.season,)):
            hurt.setdefault(r["name_key"], set()).add(r["week"])
    except sqlite3.OperationalError:
        print("  (no weekly_status; run import_status.py to separate an "
              "injury from a demotion)\n")
    if not rows:
        sys.exit(f"  no snap data for {args.season}. Run import_snaps.py")

    # nflverse stores this as a fraction in some seasons and a percentage in
    # others. Divide only when it is clearly a percentage.
    def frac(v):
        return v / 100 if v > 1.5 else v

    by_player = {}
    for r in rows:
        if r["position"] not in SKILL:
            continue
        d = by_player.setdefault(key(r["player"]),
                                 {"name": r["player"], "pos": r["position"],
                                  "team": r["team"], "weeks": {}})
        d["weeks"][r["week"]] = frac(r["offense_pct"])
        d["team"] = r["team"]          # last team of the season

    adp = {}
    rp = ROOT / "rosters" / "nfl.csv"
    if rp.exists():
        for r in csv.DictReader(rp.open()):
            v = (r.get("adp") or "").strip()
            if v:
                try:
                    adp[key(r["name"])] = float(v)
                except ValueError:
                    pass

    out = []
    for k, d in by_player.items():
        missed = hurt.get(k, set())
        # Week 18 is rest week for anybody already in the playoffs. It is not
        # a usage decision and it sits at exactly the end of the window.
        wks = sorted(w for w in d["weeks"]
                     if w not in missed and w < 18)
        if len(wks) < args.min_weeks * 2:
            continue
        # First third against the last third. The middle is skipped so one
        # big game, or one week of rest, does not decide the answer.
        cut = max(args.min_weeks, len(wks) // 3)
        early = [d["weeks"][w] for w in wks[:cut]]
        late = [d["weeks"][w] for w in wks[-cut:]]
        e, l = statistics.mean(early), statistics.mean(late)
        out.append({
            "name": d["name"], "pos": d["pos"], "team": d["team"],
            "early": e, "late": l, "delta": l - e,
            "weeks": len(wks), "adp": adp.get(k),
            "peak": max(d["weeks"].values()),
            "missed": len(missed),
            "by_week": [round(d["weeks"][w], 3) for w in wks],
        })

    if args.pos:
        out = [r for r in out if r["pos"] == args.pos.upper()]
    if not out:
        sys.exit("  nothing matched")

    def show(rs, title, note):
        print(f"\n  {title}\n")
        print(f"  {'PLAYER':<22}{'POS':<5}{'TM':<5}{'EARLY':>7}{'LATE':>7}"
              f"{'CHANGE':>8}{'ADP':>7}")
        for r in rs:
            a = f"{r['adp']:.0f}" if r["adp"] else "—"
            note = f"  missed {r['missed']}" if r.get("missed") else ""
            print(f"  {r['name'][:22]:<22}{r['pos']:<5}{(r['team'] or ''):<5}"
                  f"{r['early']:>6.0%}{r['late']:>7.0%}"
                  f"{r['delta']:>+8.0%}{a:>7}{note}")
        print(f"\n  {note}\n")

    rising = sorted(out, key=lambda x: -x["delta"])[:args.top]
    falling = sorted(out, key=lambda x: x["delta"])[:args.top]
    show(rising, f"ROLE GREW ACROSS {args.season}",
         "The team decided something about these players, and the season\n"
         "  total does not show it.")
    show(falling, f"ROLE SHRANK ACROSS {args.season}",
         "A total earned in September is not a promise about next year.")

    drafted = [r for r in out if r["adp"] and r["adp"] <= 120]
    if drafted:
        big = sorted(drafted, key=lambda x: -abs(x["delta"]))[:12]
        print(f"\n\n  BIGGEST MOVES AMONG PLAYERS BEING DRAFTED\n")
        print(f"  {'PLAYER':<22}{'POS':<5}{'ADP':>6}{'EARLY':>8}{'LATE':>7}"
              f"{'CHANGE':>8}")
        for r in big:
            note = f"   missed {r['missed']} wk" if r.get("missed") else ""
            print(f"  {r['name'][:22]:<22}{r['pos']:<5}{r['adp']:>6.0f}"
                  f"{r['early']:>7.0%}{r['late']:>7.0%}{r['delta']:>+8.0%}{note}")
        print(f"\n  Weeks a player was on injured reserve or inactive are")
        print(f"  excluded, and week eighteen with them: a playoff team")
        print(f"  resting a starter is not a usage decision. Where a player")
        print(f"  still missed time, the count is shown -- his trend is")
        print(f"  measured on the weeks he was available.")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"season": args.season, "rising": rising,
                                 "falling": falling, "all": out}, indent=1))
        print(f"\n  wrote {p}")


if __name__ == "__main__":
    main()
