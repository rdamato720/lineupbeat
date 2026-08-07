#!/usr/bin/env python3
"""Injury risk against draft price, from the record alone.

    python3 scripts/durability.py
    python3 scripts/durability.py --pos RB --top 25

WHY NO PROJECTIONS

An earlier version compared a projected full season against a projected
adjusted one, which put a number we are unsure about on both sides of the
comparison. If the projection is wrong the whole table is wrong, and a
reader has no way to tell.

This uses only what happened. Games played per season, from the box scores.
Weeks a team officially ruled a player out, from the injury reports. How
many separate spells he has had, because four one-week absences and one
four-week absence are different facts about a body.

Set against ADP, which is where people are actually drafting him. Both
numbers are records of things that occurred, so a disagreement between them
is real rather than a modelling artefact.
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
SEASON_GAMES = 17

# Not every week on reserve is an injury.
#
# The league codes the reason, and lumping them together libels people.
# Calvin Ridley's 2022 was a gambling suspension and his 2021 was time away
# for mental health -- neither says anything about whether his body holds up,
# and both were reading as injured reserve alongside a hamstring.
#
# R01 is injured reserve proper and covers the overwhelming majority.
# Everything else on reserve gets counted separately and named honestly.
# Reserve codes, mapped from the data rather than guessed.
#
# Determined by looking at who each code hits and when. R62 appears only in
# 2020 and contains Whitworth, Olsen, Edelman and Sherman: the COVID opt-out
# list. R59 runs 2020-21 across 759 players for about a week each, which is
# the COVID reserve list -- a positive test, not an injury. R30 is Calvin
# Ridley's gambling suspension. R27 is time away from the team.
#
# The blank code appears only in 2018-2020 and holds Alex Smith and Delanie
# Walker, both of whom broke a leg. That is injured reserve before the
# coding was applied consistently.
#
# Counting a pandemic opt-out or a suspension as an injury is not a rounding
# error. It says something false about a man's body, on a page with his name
# on it.
IR_CODES = {"R01", "R04", "R05", "R48", "R23", "R02", "R03", "R06", ""}

# Weeks that should not count as missed at all.
#
# A positive test in 2021 is not a fact about a player's body, and the 2020
# opt-out was a choice made during a pandemic. Both leave a hole in the box
# score that looks exactly like an injury, and both would follow a player
# through his durability record for the rest of his career.
COVID_CODES = {"R59", "R62"}

NON_INJURY_RESERVE = {
    "R62": "opted out, 2020",
    "R59": "covid list",
    "R30": "suspended",
    # Rashee Rice, 2025 weeks 1-6, and Ja'Marr Chase's single game.
    "R40": "suspended",
    "R27": "away from the team",
    
    "R33": "",
    "R47": "",
    "R49": "",
    "R34": "",
    "R42": "",
    "R36": "",
}


def key(n):
    n = re.sub(r"[.\'`]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--seasons", default="2019,2020,2021,2022,2023,2024,2025",
                    help="how far back to read; nflverse has injuries to 2009")
    # No --confirmed-only. It was here and it was wrong: weekly injury
    # reports carry game-status designations, and a player on injured
    # reserve never appears on one. George Kittle has four Out reports
    # across seventeen seasons and has missed roughly twenty games -- so
    # counting only confirmed absences hides precisely the players worth
    # flagging. The count comes from games played; the reports supply the
    # reason where there is one.
    ap.add_argument("--pos")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--max-adp", type=float, default=150)
    ap.add_argument("--json")
    args = ap.parse_args()

    seasons = [int(s) for s in args.seasons.split(",")]
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    adp, pos_of, team_of = {}, {}, {}
    rp = ROOT / "rosters" / "nfl.csv"
    if not rp.exists():
        sys.exit("  no roster file")
    for r in csv.DictReader(rp.open()):
        k = key(r["name"])
        pos_of[k] = (r.get("position") or "").upper()
        team_of[k] = r.get("team") or ""
        v = (r.get("adp") or "").strip()
        if v:
            try:
                adp[k] = float(v)
            except ValueError:
                pass
    if not adp:
        sys.exit("  no ADP. Run import_adp.py after import_rosters.py.")

    # Why each week was missed, from the weekly roster.
    #
    # This is what makes the record defensible. A missing box score row says
    # a player did not play; the roster says whether he was on injured
    # reserve, inactive, or not on the team at all. Only the first two are
    # his durability. A season on a practice squad is not seventeen missed
    # games.
    status = {}
    have_status = False
    try:
        for r in conn.execute("""SELECT name_key, season, status, status_abbr,
                                 COUNT(*) n FROM weekly_status
                                 WHERE season >= ?
                                 GROUP BY name_key, season, status, status_abbr""",
                              (min(seasons),)):
            d = status.setdefault(r["name_key"], {}).setdefault(r["season"], {})
            st, abbr = r["status"], (r["status_abbr"] or "")
            # Split reserve by its reason rather than counting it all as IR.
            if st == "RES":
                if abbr in COVID_CODES:
                    d["COVID"] = d.get("COVID", 0) + r["n"]
                elif abbr in NON_INJURY_RESERVE and abbr:
                    d["NONINJ"] = d.get("NONINJ", 0) + r["n"]
                    d.setdefault("_why", set()).add(NON_INJURY_RESERVE[abbr])
                else:
                    d["RES"] = d.get("RES", 0) + r["n"]
            else:
                d[st] = d.get(st, 0) + r["n"]
            have_status = True
    except sqlite3.OperationalError:
        pass
    if not have_status:
        print("  (no weekly roster data; run import_status.py to tell an")
        print("   injury from a practice squad season)\n")

    # Games played, per season, from the box scores.
    played = {}
    for s in seasons:
        for r in conn.execute("""SELECT player_name, COUNT(*) g FROM weekly_stats
                                 WHERE season=? AND season_type='REG'
                                 GROUP BY player_id""", (s,)):
            played.setdefault(key(r["player_name"]), {})[s] = r["g"]

    # Which absences a team actually reported as an injury.
    #
    # A missing box score row is not evidence of an injury. It can be a
    # healthy scratch, a suspension, a backup who did not take a snap, or a
    # week the data is simply thin. Claiming a player is fragile on that
    # basis is the kind of thing a reader can disprove in one search.
    #
    # An official report is different: the team filed it, the league
    # published it, and it names the body part. Seventeen seasons of them
    # sit in the injuries table.
    out_weeks, spells, kinds = {}, {}, {}
    have_reports = False
    try:
        rows = conn.execute("""SELECT player, season, week, report_injury
                               FROM injuries WHERE report_status='Out'
                               AND season >= ?
                               ORDER BY player, season, week""",
                            (min(seasons),)).fetchall()
        have_reports = bool(rows)
        cur, last = None, None
        for r in rows:
            k = key(r["player"])
            out_weeks.setdefault(k, {}).setdefault(r["season"], 0)
            out_weeks[k][r["season"]] += 1
            if k != cur or last is None or r["week"] != last + 1:
                spells[k] = spells.get(k, 0) + 1
            inj = (r["report_injury"] or "").strip()
            # The report's own placeholder for an absence with no injury.
            if inj and "not injury" not in inj.lower():
                # "Right Shoulder" and "Shoulder" are the same complaint.
                inj = re.sub(r"^(right|left)\s+", "", inj, flags=re.I)
                kinds.setdefault(k, []).append(inj.title())
            cur, last = k, r["week"]
    except sqlite3.OperationalError:
        pass
    if not have_reports:
        print("  (no injury reports for these seasons; run import_injuries.py)\n")

    out = []
    for k, a in adp.items():
        if a > args.max_adp:
            continue
        pos = pos_of.get(k, "")
        if pos not in ("QB", "RB", "WR", "TE"):
            continue
        if args.pos and pos != args.pos.upper():
            continue
        by_season = played.get(k) or {}
        st = status.get(k, {})

        # A season only counts if he was on an active roster for most of it.
        # Otherwise a practice squad year reads as seventeen missed games.
        usable = []
        for s in seasons:
            g = by_season.get(s)
            if not g:
                continue
            ss = st.get(s, {})
            if have_status and ss:
                on_team = (ss.get("ACT", 0) + ss.get("RES", 0)
                           + ss.get("INA", 0) + ss.get("NONINJ", 0)
                           + ss.get("COVID", 0))
                if on_team < 8:
                    continue          # not really his season
            usable.append((s, g))
        # No minimum. A rookie with no record is more useful shown than
        # hidden: somebody drafting him wants to know THAT is why there is no
        # number, not wonder where he went. The row says so instead.
        seen = [g for _, g in usable]

        # Absences, split by reason.
        ir = sum(st.get(s, {}).get("RES", 0) for s, _ in usable)
        inactive = sum(st.get(s, {}).get("INA", 0) for s, _ in usable)
        noninj = sum(st.get(s, {}).get("NONINJ", 0) for s, _ in usable)
        why = set()
        for s, _ in usable:
            why |= st.get(s, {}).get("_why", set())
        # Give back the weeks lost to covid. A man who played sixteen and
        # spent one on the covid list played sixteen games, and his record
        # should say so for the rest of his career.
        covid_by_season = {s: st.get(s, {}).get("COVID", 0) for s, _ in usable}
        missed = [max(0, SEASON_GAMES - g - covid_by_season.get(s, 0))
                  for s, g in usable]
        # Confirmed: weeks a team filed him Out, per season we have stats for.
        conf = out_weeks.get(k, {})
        confirmed = [conf.get(s, 0) for s in seasons if by_season.get(s)]

        out.append({
            "name": k, "pos": pos, "team": team_of.get(k, ""),
            "adp": a,
            "seasons": seen,
            "seasons_of_record": len(usable),
            "missed_total": sum(missed),
            "missed_avg": statistics.mean(missed) if missed else None,
            "worst": max(missed) if missed else 0,
            "clean": sum(1 for x in missed if x <= 1),
            "of": len(seen),
            "ir": ir, "inactive": inactive,
            "noninj": noninj, "why": sorted(why),
            "covid": sum(covid_by_season.values()),
            "confirmed": sum(confirmed),
            "confirmed_avg": statistics.mean(confirmed) if confirmed else 0,
            "spells": spells.get(k, 0),
            "kinds": sorted(set(kinds.get(k, [])))[:3],
        })

    if not out:
        sys.exit("  nothing matched.")

    def display(k):
        # roster names are keyed; recover something readable
        return " ".join(w.capitalize() for w in k.split())

    risky = sorted([r for r in out if r["missed_avg"] is not None],
                   key=lambda x: (-x["missed_avg"], x["adp"]))[:args.top]
    print(f"\n  DRAFTED EARLY, MISSES TIME\n")
    print(f"  {'PLAYER':<22}{'POS':<4}{'ADP':>6}{'MISSED/YR':>10}"
          f"{'ON IR':>7}{'SCRATCH':>9}  WHAT")
    for r in risky:
        what = ", ".join(r["kinds"]) if r["kinds"] else ""
        if r["why"] and r["noninj"] >= 2:
            what = (what + "  [" + ", ".join(r["why"]) + "]").strip()
        print(f"  {display(r['name'])[:22]:<22}{r['pos']:<4}{r['adp']:>6.1f}"
              f"{r['missed_avg']:>10.1f}{r['ir']:>7}{r['inactive']:>9}"
              f"  {what[:30]}")

    print(f"\n  Weeks on the covid list, and the 2020 opt-out, are given back:")
    print(f"  a positive test is not a fact about a body, and it should not")
    print(f"  follow a player through the record for the rest of a career.")
    print(f"\n  Weeks on reserve for a reason that is not an injury -- a")
    print(f"  suspension, time away from the team -- are named in brackets")
    print(f"  and kept out of the IR count. Calvin Ridley's 2022 was a")
    print(f"  gambling suspension and it was reading as eleven weeks hurt.")
    print(f"\n  ON IR is weeks on injured reserve. SCRATCH is weeks he was on")
    print(f"  the roster and inactive. Both come from the weekly roster, so a")
    print(f"  season spent on a practice squad is excluded rather than counted")
    print(f"  as seventeen missed games. WHAT names the injury where the")
    print(f"  weekly report carried one.")
    print(f"  Nothing is projected: these are reports that were filed.")

    # Drafted LATE and durable. The ADP floor matters: Bijan Robinson at 2.0
    # is iron and is also the second pick in the draft, which is not a
    # finding. The interesting names are the ones nobody is paying for.
    #
    # Four seasons minimum, because three clean years from a player who
    # entered the league in 2023 says less than seven from one who did not.
    iron = sorted([r for r in out
                   if r["missed_avg"] is not None and r["missed_avg"] <= 0.7
                   and r["of"] >= 4 and r["adp"] >= 40],
                  key=lambda x: -x["adp"])[:12]
    if iron:
        print(f"\n\n  AVAILABLE EVERY WEEK, DRAFTED LATE\n")
        print(f"  {'PLAYER':<24}{'POS':<5}{'ADP':>7}{'MISSED/YR':>11}"
              f"  GAMES BY SEASON")
        for r in iron:
            g = "-".join(str(x) for x in r["seasons"])
            print(f"  {display(r['name'])[:24]:<24}{r['pos']:<5}{r['adp']:>7.1f}"
                  f"{r['missed_avg']:>11.1f}  {g}")
        print(f"\n  Durability is the cheapest thing on a draft board,")
        print(f"  because no board shows it.")

    early = [r for r in out if r["adp"] <= 36 and r["missed_avg"] is not None]
    if len(early) >= 8:
        print(f"\n\n  FIRST THREE ROUNDS ({len(early)} players)")
        print(f"    games missed a year, median  "
              f"{statistics.median(r['missed_avg'] for r in early):.1f}")
        print(f"    played 16+ in every season   "
              f"{sum(1 for r in early if r['clean'] == r['of'])}"
              f" of {len(early)}")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        board = sorted(out, key=lambda x: x["adp"])
        p.write_text(json.dumps(
            {"board": board, "risky": risky, "iron": iron}, indent=1))
        print(f"\n  wrote {p}")


if __name__ == "__main__":
    main()
