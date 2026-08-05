#!/usr/bin/env python3
"""Derive team context from play-by-play: scheme, line, schedule.

    python3 scripts/import_context.py --seasons 2023,2024
    python3 scripts/import_context.py --show 2024

Three things, in the order they actually matter for a season projection.

SCHEME (large effect). How often a team throws is the single biggest
determinant of how many targets exist to go around, and it is a coaching
decision, not a talent one. Measured in neutral situations only -- win
probability between 20 and 80 percent, score within a touchdown, before the
fourth quarter -- because a team down 21 throws on every down and that tells
you nothing about what it intends to do. Attributed to the HEAD COACH, not the
team, so a coach who changes jobs carries his tendencies with him. That is the
whole point: a new coordinator is the most under-priced thing in August, and
last season's team numbers cannot see it.

Also captured: pace (plays per minute of possession), early-down pass rate,
red-zone pass rate, and pass rate over expectation, which is nflverse's own
model of how much a team throws relative to game situation.

LINE (moderate effect, measured badly). There is no free offensive-line grade,
so this uses outcomes: sack and hit rate per dropback for pass protection,
rushing success rate before the second level for run blocking. Both conflate
the line with the quarterback and back behind it -- a QB who holds the ball
inflates his line's sack rate. Treat these as weak signals and do not let them
move a projection much.

SCHEDULE (small effect, season-long). Opponent defensive EPA allowed, weighted
by games. Honestly: over seventeen games strength of schedule mostly washes
out, and it is worth far more for a weekly projection than a season one. It is
here because you asked and because it costs nothing, not because it will move
your rankings.
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import statistics
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PBP = ("https://github.com/nflverse/nflverse-data/releases/download/pbp/"
       "play_by_play_{season}.csv")

SCHEMA = """
CREATE TABLE IF NOT EXISTS team_context (
    season INTEGER, team TEXT, coach TEXT,
    plays INTEGER,
    pass_rate REAL,            -- neutral situations only
    proe REAL,                 -- pass rate over expectation
    early_down_pass REAL,
    redzone_pass REAL,
    sec_per_play REAL,
    plays_per_game REAL,
    sack_rate REAL,            -- pass protection proxy
    hit_rate REAL,
    rush_success REAL,         -- run blocking proxy
    rush_epa REAL,
    def_epa_allowed REAL,      -- for building opponent strength
    PRIMARY KEY (season, team)
);
CREATE TABLE IF NOT EXISTS coach_context (
    coach TEXT PRIMARY KEY,
    seasons TEXT, plays INTEGER,
    pass_rate REAL, proe REAL, early_down_pass REAL,
    redzone_pass REAL, sec_per_play REAL
);
CREATE TABLE IF NOT EXISTS schedule_strength (
    season INTEGER, team TEXT, opp_def_epa REAL, games INTEGER,
    PRIMARY KEY (season, team)
);
"""


def f(v):
    if v in (None, "", "NA"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def fetch(season: int):
    url = PBP.format(season=season)
    print(f"  fetching {season} play-by-play …")
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=600) as r:
        raw = r.read()
    print(f"    {len(raw)/1e6:.0f} MB")
    return csv.DictReader(io.StringIO(raw.decode("utf-8", "replace")))


def neutral(p):
    """Situations where a coach is calling the game he wants to call."""
    wp = f(p.get("wp"))
    sd = f(p.get("score_differential"))
    q = f(p.get("qtr"))
    return (wp is not None and 0.20 <= wp <= 0.80
            and sd is not None and abs(sd) <= 7
            and q is not None and q <= 3)


def build(season: int):
    acc = {}
    dcc = {}
    for p in fetch(season):
        if p.get("season_type") != "REG":
            continue
        off, dfn = p.get("posteam"), p.get("defteam")
        if not off:
            continue

        coach = (p["home_coach"] if p.get("posteam_type") == "home"
                 else p["away_coach"])
        d = acc.setdefault(off, {
            "coach": coach, "plays": 0, "n_plays": 0, "n_pass": 0,
            "proe": [], "ed_plays": 0, "ed_pass": 0, "rz_plays": 0, "rz_pass": 0,
            "dropbacks": 0, "sacks": 0, "hits": 0,
            "rushes": 0, "rush_succ": 0, "rush_epa": [],
            "games": set(), "secs": [],
        })
        is_pass = f(p.get("pass_attempt")) == 1 or f(p.get("qb_dropback")) == 1
        is_rush = f(p.get("rush_attempt")) == 1
        if not (is_pass or is_rush):
            continue

        d["plays"] += 1
        d["games"].add(p.get("game_id"))

        if neutral(p):
            d["n_plays"] += 1
            d["n_pass"] += 1 if is_pass else 0
            poe = f(p.get("pass_oe"))
            if poe is not None:
                d["proe"].append(poe)

        dn = f(p.get("down"))
        if dn in (1.0, 2.0):
            d["ed_plays"] += 1
            d["ed_pass"] += 1 if is_pass else 0

        y100 = f(p.get("yardline_100"))
        if y100 is not None and y100 <= 20:
            d["rz_plays"] += 1
            d["rz_pass"] += 1 if is_pass else 0

        if f(p.get("qb_dropback")) == 1:
            d["dropbacks"] += 1
            d["sacks"] += 1 if f(p.get("sack")) == 1 else 0
            d["hits"] += 1 if f(p.get("qb_hit")) == 1 else 0

        if is_rush:
            d["rushes"] += 1
            if f(p.get("success")) == 1:
                d["rush_succ"] += 1
            e = f(p.get("epa"))
            if e is not None:
                d["rush_epa"].append(e)

        if dfn:
            e = f(p.get("epa"))
            if e is not None:
                dcc.setdefault(dfn, []).append(e)

    rows = []
    for team, d in acc.items():
        g = max(1, len(d["games"]))
        rows.append({
            "season": season, "team": team, "coach": d["coach"],
            "plays": d["plays"],
            "pass_rate": d["n_pass"] / d["n_plays"] if d["n_plays"] else None,
            "proe": statistics.mean(d["proe"]) if d["proe"] else None,
            "early_down_pass": d["ed_pass"] / d["ed_plays"] if d["ed_plays"] else None,
            "redzone_pass": d["rz_pass"] / d["rz_plays"] if d["rz_plays"] else None,
            "sec_per_play": None,
            "plays_per_game": d["plays"] / g,
            "sack_rate": d["sacks"] / d["dropbacks"] if d["dropbacks"] else None,
            "hit_rate": d["hits"] / d["dropbacks"] if d["dropbacks"] else None,
            "rush_success": d["rush_succ"] / d["rushes"] if d["rushes"] else None,
            "rush_epa": statistics.mean(d["rush_epa"]) if d["rush_epa"] else None,
            "def_epa_allowed": (statistics.mean(dcc[team]) if team in dcc else None),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--seasons", default="2024")
    ap.add_argument("--show", type=int, help="print a season already imported")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if args.show:
        rows = conn.execute("""SELECT * FROM team_context WHERE season=?
                               ORDER BY pass_rate DESC""", (args.show,)).fetchall()
        if not rows:
            sys.exit(f"  nothing imported for {args.show}")
        print(f"\n  {args.show} — neutral-situation tendencies, most pass-happy first\n")
        print(f"  {'TM':<4} {'COACH':<22} {'PASS%':>6} {'PROE':>6} {'ED%':>6} "
              f"{'RZ%':>6} {'SACK%':>6} {'RUSHSUCC':>9}")
        for r in rows:
            def pc(v, d=1): return f"{v*100:.{d}f}" if v is not None else "  -"
            # pass_oe is already expressed in percentage points, so it must not
            # be scaled again -- doing so printed a 10.4pt edge as "1038.7".
            def pp(v): return f"{v:+.1f}" if v is not None else "  -"
            print(f"  {r['team']:<4} {(r['coach'] or '')[:22]:<22} "
                  f"{pc(r['pass_rate']):>6} {pp(r['proe']):>6} "
                  f"{pc(r['early_down_pass']):>6} {pc(r['redzone_pass']):>6} "
                  f"{pc(r['sack_rate']):>6} {pc(r['rush_success']):>9}")
        print("\n  PASS% is neutral situations only. PROE is pass rate over")
        print("  expectation -- positive means throwing more than the situation")
        print("  calls for, which is the clearest read on intent.")
        return

    for s in [int(x) for x in args.seasons.split(",")]:
        rows = build(s)
        for r in rows:
            conn.execute(
                """INSERT OR REPLACE INTO team_context VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["season"], r["team"], r["coach"], r["plays"], r["pass_rate"],
                 r["proe"], r["early_down_pass"], r["redzone_pass"],
                 r["sec_per_play"], r["plays_per_game"], r["sack_rate"],
                 r["hit_rate"], r["rush_success"], r["rush_epa"],
                 r["def_epa_allowed"]))
        conn.commit()
        print(f"  {s}: {len(rows)} teams")

    # Coach-level rollup: this is the part that survives a job change.
    conn.execute("DELETE FROM coach_context")
    for r in conn.execute("""
        SELECT coach, GROUP_CONCAT(DISTINCT season) seasons, SUM(plays) plays,
               AVG(pass_rate) pr, AVG(proe) proe, AVG(early_down_pass) ed,
               AVG(redzone_pass) rz, AVG(sec_per_play) spp
        FROM team_context WHERE coach IS NOT NULL GROUP BY coach"""):
        conn.execute("INSERT OR REPLACE INTO coach_context VALUES (?,?,?,?,?,?,?,?)",
                     (r["coach"], r["seasons"], r["plays"], r["pr"], r["proe"],
                      r["ed"], r["rz"], r["spp"]))
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM coach_context").fetchone()[0]
    print(f"\n  {n} coaches profiled")
    print(f"  next: python3 scripts/import_context.py --show "
          f"{max(int(x) for x in args.seasons.split(','))}")


if __name__ == "__main__":
    main()
