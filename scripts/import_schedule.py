#!/usr/bin/env python3
"""Import the NFL schedule, and results as they happen.

    python3 scripts/import_schedule.py
    python3 scripts/import_schedule.py --seasons 2024 2025 2026

One file carries every game since 1999, with scores blank until a game is
played. That is what makes the strength-of-schedule page update itself:
re-import weekly and the same table answers "who is left" and "how did the
teams they already played do".

WHY THE SCHEDULE IS A SEPARATE TABLE

weekly_stats knows which team a player was on and not who he played. Points
allowed by a defense is the join between them, and without the schedule
there is no way to compute it -- the number everyone actually wants from a
strength-of-schedule page is unavailable from the stats alone.
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id    TEXT PRIMARY KEY,
    season     INTEGER NOT NULL,
    game_type  TEXT,
    week       INTEGER NOT NULL,
    gameday    TEXT,
    away_team  TEXT NOT NULL,
    home_team  TEXT NOT NULL,
    -- Blank until played. That is the whole point: the same row describes a
    -- fixture before kickoff and a result after it.
    away_score REAL,
    home_score REAL,
    result     REAL,
    total      REAL
);
CREATE INDEX IF NOT EXISTS idx_games_season ON games(season, week);
CREATE INDEX IF NOT EXISTS idx_games_home ON games(season, home_team);
CREATE INDEX IF NOT EXISTS idx_games_away ON games(season, away_team);
"""

# nflverse uses a few historical codes. The roster uses the current ones.
ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LAR", "LA": "LAR"}


def team(code):
    c = (code or "").strip().upper()
    return ALIASES.get(c, c)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--seasons", nargs="*", type=int)
    args = ap.parse_args()

    db = ROOT / args.db
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)

    print(f"\n  fetching the schedule")
    req = urllib.request.Request(URL, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        text = r.read().decode("utf-8", "replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    print(f"    {len(rows):,} games in the file")

    want = set(args.seasons) if args.seasons else None
    kept = played = 0
    for g in rows:
        try:
            season = int(g["season"])
            week = int(g["week"])
        except (TypeError, ValueError):
            continue
        if want and season not in want:
            continue
        home_s, away_s = num(g.get("home_score")), num(g.get("away_score"))
        if home_s is not None:
            played += 1
        conn.execute(
            """INSERT INTO games
               (game_id, season, game_type, week, gameday, away_team,
                home_team, away_score, home_score, result, total)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(game_id) DO UPDATE SET
                 away_score=excluded.away_score,
                 home_score=excluded.home_score,
                 result=excluded.result, total=excluded.total,
                 gameday=excluded.gameday""",
            (g["game_id"], season, g.get("game_type"), week, g.get("gameday"),
             team(g["away_team"]), team(g["home_team"]),
             away_s, home_s, num(g.get("result")), num(g.get("total"))))
        kept += 1
    conn.commit()

    print(f"    stored {kept:,} games, {played:,} with a result\n")
    for r in conn.execute(
            """SELECT season, COUNT(*) n,
                      SUM(CASE WHEN home_score IS NOT NULL THEN 1 ELSE 0 END) p
               FROM games WHERE season >= 2024
               GROUP BY season ORDER BY season DESC"""):
        state = ("complete" if r[1] == r[2]
                 else f"{r[2]} of {r[1]} played" if r[2]
                 else "not started")
        print(f"    {r[0]}  {r[1]:>4} games, {state}")

    print(f"\n  next: python3 scripts/schedule_strength.py")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
