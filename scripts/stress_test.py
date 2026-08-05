#!/usr/bin/env python3
"""Try to find projections that would embarrass us in public.

    python3 scripts/stress_test.py
    python3 scripts/stress_test.py --strict

This is not an accuracy test -- the backtest does that. This looks for output
that is obviously, visibly wrong to anyone who follows football: a backup
quarterback ranked ahead of a starter, a team whose receivers are collectively
projected more targets than the offence will throw, a retired player with a
number next to his name.

Being a few points off is forgivable and expected. Ranking Kirk Cousins ahead
of Josh Allen is not, and nobody will look at the backtest afterwards.

Each check prints the worst offenders rather than a pass/fail, because the
judgement is usually "is this defensible" rather than "is this within
tolerance", and that judgement needs a human and a name.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROSTER = ROOT / "rosters" / "nfl.csv"


TEAM_ALIAS = {"LA": "LAR", "SD": "LAC", "OAK": "LV", "STL": "LAR",
              "WSH": "WAS", "JAC": "JAX", "ARZ": "ARI"}


def norm_team(code):
    c = (code or "").strip().upper()
    return TEAM_ALIAS.get(c, c)


def _key(name):
    """Same normalisation the projection uses. Suffixes broke the join."""
    import re
    n = re.sub(r"[.\'`]", "", (name or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())

# Roughly what a real NFL offence produces in a season. Used to catch a team
# whose players collectively add up to something impossible.
TEAM_LIMITS = {"targets": (480, 700), "carries": (350, 560)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    fails, warns = [], []

    try:
        rows = conn.execute("SELECT * FROM projections").fetchall()
    except sqlite3.OperationalError:
        sys.exit("  no projections. Publish first.")
    if not rows:
        sys.exit("  no projections. Publish first.")

    roster = {}
    if ROSTER.exists():
        for r in csv.DictReader(ROSTER.open()):
            roster[(r.get("name") or "").lower()] = r
            roster[_key(r.get("name"))] = r

    print(f"\n  {len(rows)} projections under test\n")

    # 1 -------------------------------------------------------------------
    bad = [r for r in rows if r["ppr"] is None or r["ppr"] < 0
           or r["ppr"] > 500 or (r["floor"] or 0) > (r["ceiling"] or 0)]
    print(f"  1. impossible numbers                      {len(bad)}")
    for r in bad[:5]:
        print(f"       {r['player'][:24]:<24} ppr={r['ppr']} "
              f"floor={r['floor']} ceil={r['ceiling']}")
    if bad:
        fails.append(f"{len(bad)} projections are impossible on their face")

    # 2 -------------------------------------------------------------------
    print(f"\n  2. quarterbacks ranked above their own starter")
    qbs = [r for r in rows if r["position"] == "QB"]
    by_team = defaultdict(list)
    for q in qbs:
        by_team[q["team"]].append(q)
    upsets = []
    for team, group in by_team.items():
        ranked = []
        for q in group:
            ro = roster.get(_key(q["player"]))
            try:
                slot = int(ro["depth_order"]) if ro and ro.get("depth_order") else None
            except (TypeError, ValueError):
                slot = None
            if slot:
                ranked.append((slot, q))
        if len(ranked) < 2:
            continue
        # Sort on the slot only. Two players sharing a depth slot made this
        # fall through to comparing sqlite3.Row objects, which is not a thing.
        ranked.sort(key=lambda x: x[0])
        starter = ranked[0]
        for slot, q in ranked[1:]:
            if q["ppr"] > starter[1]["ppr"] + 1:
                upsets.append((team, starter[1]["player"], starter[1]["ppr"],
                               q["player"], q["ppr"], slot))
    print(f"       {len(upsets)} found")
    for t, s, sp, b, bp, slot in upsets[:8]:
        print(f"       {t}: QB{slot} {b} ({bp:.0f}) > QB1 {s} ({sp:.0f})")
    if upsets:
        fails.append(f"{len(upsets)} backup QBs outrank their starter — "
                     f"this is the most visible error we can make")

    # 3 -------------------------------------------------------------------
    print(f"\n  3. team volume that could not happen")
    tv = defaultdict(lambda: {"tgt": 0.0, "car": 0.0})
    for r in rows:
        if not r["team"]:
            continue
        tv[r["team"]]["tgt"] += (r["rec"] or 0) / 0.65      # rough targets
        tv[r["team"]]["car"] += (r["ruyd"] or 0) / 4.3      # rough carries
    over = []
    for team, v in tv.items():
        if v["tgt"] > TEAM_LIMITS["targets"][1] * 1.15:
            over.append((team, "targets", v["tgt"]))
        if v["car"] > TEAM_LIMITS["carries"][1] * 1.15:
            over.append((team, "carries", v["car"]))
    print(f"       {len(over)} teams over a plausible ceiling")
    for t, what, val in sorted(over, key=lambda x: -x[2])[:8]:
        print(f"       {t}: ~{val:.0f} {what}")
    if over:
        warns.append(f"{len(over)} teams exceed plausible volume — "
                     f"reconciliation is leaking. Note this only covers "
                     f"players we project, so some overshoot is expected.")

    # 4 -------------------------------------------------------------------
    print(f"\n  4. projected players who are not on a roster")
    orphan = [r for r in rows if _key(r["player"]) not in roster]
    print(f"       {len(orphan)}")
    for r in sorted(orphan, key=lambda x: -(x["ppr"] or 0))[:8]:
        print(f"       {r['player'][:24]:<24} {r['position']} {r['team']} "
              f"{r['ppr']:.0f}")
    if any((r["ppr"] or 0) > 100 for r in orphan):
        fails.append("a player with a meaningful projection is not on any "
                     "roster — likely retired or cut")

    # 5 -------------------------------------------------------------------
    print(f"\n  5. team on the projection disagrees with the roster")
    wrong = []
    for r in rows:
        ro = roster.get(_key(r["player"]))
        if (ro and ro.get("team") and r["team"]
                and norm_team(ro["team"]) != norm_team(r["team"])):
            wrong.append((r["player"], r["team"], ro["team"], r["ppr"]))
    print(f"       {len(wrong)}")
    for n, a, b, p in sorted(wrong, key=lambda x: -(x[3] or 0))[:8]:
        print(f"       {n[:24]:<24} projected {a}, roster {b}  ({p:.0f})")
    if wrong:
        fails.append(f"{len(wrong)} projections use the wrong team")

    # 6 -------------------------------------------------------------------
    print(f"\n  6. deep backups with starter-level numbers")
    silly = []
    for r in rows:
        ro = roster.get(_key(r["player"]))
        try:
            slot = int(ro["depth_order"]) if ro and ro.get("depth_order") else None
        except (TypeError, ValueError):
            slot = None
        if slot and slot >= 4 and (r["ppr"] or 0) > 120:
            silly.append((r["player"], r["position"], r["team"], slot, r["ppr"]))
    print(f"       {len(silly)}")
    for n, pos, t, s, p in sorted(silly, key=lambda x: -x[4])[:8]:
        print(f"       {n[:24]:<24} {pos} {t} depth {s}  {p:.0f}")
    if silly:
        warns.append(f"{len(silly)} players listed 4th or deeper project above "
                     f"120 points")

    # 7 -------------------------------------------------------------------
    print(f"\n  7. sanity of the very top of each position")
    for pos in ("QB", "RB", "WR", "TE"):
        top = sorted([r for r in rows if r["position"] == pos],
                     key=lambda x: -(x["ppr"] or 0))[:5]
        names = ", ".join(f"{r['player'].split()[-1]} {r['ppr']:.0f}" for r in top)
        print(f"       {pos}: {names}")
    print("\n       Read that line. If a name there makes you wince, the")
    print("       model is wrong in a way no automated check will catch.")

    # 8 -------------------------------------------------------------------
    print(f"\n  8. ADP disagreements worth defending")
    big = []
    for r in rows:
        ro = roster.get(_key(r["player"]))
        adp = None
        if ro and (ro.get("adp") or "").strip():
            try:
                adp = float(ro["adp"])
            except ValueError:
                pass
        if adp and adp <= 36 and (r["ppr"] or 0) < 120:
            big.append((r["player"], r["position"], adp, r["ppr"]))
    print(f"       {len(big)} players drafted in the first three rounds "
          f"that we project under 120")
    for n, pos, a, p in sorted(big, key=lambda x: x[2])[:8]:
        print(f"       {n[:24]:<24} {pos} ADP {a:.1f}  ours {p:.0f}")
    if big:
        warns.append(f"{len(big)} early-round players project very low — "
                     f"each one needs an explanation you would say out loud")

    # 9 -------------------------------------------------------------------
    print(f"\n  9. the same player twice")
    seen, dupes = {}, []
    for r in rows:
        k = _key(r["player"])
        if k in seen:
            dupes.append((r["player"], seen[k]["team"], r["team"],
                          seen[k]["ppr"], r["ppr"]))
        else:
            seen[k] = r
    print(f"       {len(dupes)}")
    for n, t1, t2, p1, p2 in dupes[:6]:
        print(f"       {n[:24]:<24} {t1} {p1:.0f}  and  {t2} {p2:.0f}")
    if dupes:
        fails.append(f"{len(dupes)} players appear twice — a reader seeing one "
                     f"name on two teams stops believing anything else")

    # 10 ------------------------------------------------------------------
    print(f"\n  10. huge swings from last season without a role change")
    swings = []
    for r in rows:
        prev = conn.execute("""SELECT SUM(fantasy_points_ppr) p, COUNT(*) g
                               FROM weekly_stats WHERE player_id=? AND season=2025
                               AND season_type='REG'""", (r["player_id"],)).fetchone()
        if not prev or not prev["g"] or prev["g"] < 12 or not prev["p"]:
            continue          # a partial season explains itself
        ro = roster.get(_key(r["player"]))
        try:
            slot = int(ro["depth_order"]) if ro and ro.get("depth_order") else None
        except (TypeError, ValueError):
            slot = None
        change = (r["healthy"] or r["ppr"]) - prev["p"]
        if abs(change) > 110 and (slot or 9) <= 2:
            swings.append((r["player"], r["position"], r["team"], prev["p"],
                           r["healthy"] or r["ppr"], slot))
    print(f"       {len(swings)}")
    for n, pos, tm, was, now, slot in sorted(swings, key=lambda x: -abs(x[4]-x[3]))[:8]:
        print(f"       {n[:22]:<22} {pos} {tm}  {was:.0f} -> {now:.0f} "
              f"({now-was:+.0f})  depth {slot}")
    if swings:
        warns.append(f"{len(swings)} starters swing more than 110 points from "
                     f"last season — each needs a reason you would say aloud")

    # 11 ------------------------------------------------------------------
    print(f"\n  11. positional shape")
    for pos, expect in (("QB", (240, 400)), ("RB", (200, 400)),
                        ("WR", (200, 380)), ("TE", (140, 300))):
        grp = sorted([r["ppr"] for r in rows if r["position"] == pos
                      and r["ppr"]], reverse=True)
        if len(grp) < 12:
            continue
        top, twelfth = grp[0], grp[11]
        flag = "" if expect[0] <= top <= expect[1] else "   <- outside normal"
        print(f"       {pos}: best {top:.0f}, 12th {twelfth:.0f}, "
              f"drop {100*(1-twelfth/top):.0f}%{flag}")
        if not (expect[0] <= top <= expect[1]):
            warns.append(f"{pos}1 at {top:.0f} is outside the range a real "
                         f"season produces")

    # ---------------------------------------------------------------------
    print()
    for w in warns:
        print(f"  WARN     {w}")
    for f_ in fails:
        print(f"  FAIL     {f_}")
    if not fails and not warns:
        print("  Nothing embarrassing found. Check 7 still needs your eyes.")
    elif not fails:
        print("\n  No blockers. Read the warnings, then decide.")
    else:
        print("\n  Do not publish. These are the errors people screenshot.")

    if args.strict and fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
