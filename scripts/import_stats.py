#!/usr/bin/env python3
"""Pull nflverse weekly player stats into a local table.

    python3 scripts/import_stats.py --seasons 2022,2023,2024,2025
    python3 scripts/import_stats.py --list

nflverse is community-maintained, openly licensed, and built from the NFL's
own play-by-play. It is the honest foundation for projections: everything is
derived rather than taken from someone else's rankings, so the model can be
argued with rather than trusted.

Stored in its own table, not in `nuggets`. Stats and news are different shapes
with different lifecycles -- news decays in hours, a 2024 season does not
change -- and mixing them would make both harder to reason about.
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
# nflverse moved. The old `player_stats/player_stats.csv` asset is frozen at
# 2024 and is still served, so a script pointed at it keeps working while
# quietly building on year-old data -- exactly the kind of silent staleness
# worth guarding against. Per-season files under `stats_player` are current.
URL_PER_SEASON = ("https://github.com/nflverse/nflverse-data/releases/download/"
                  "stats_player/stats_player_week_{season}.csv")
URL_LEGACY = ("https://github.com/nflverse/nflverse-data/releases/download/"
              "player_stats/player_stats.csv")

# Column names changed with the move. Old on the left, new on the right.
RENAMES = {"recent_team": "team", "interceptions": "passing_interceptions"}

# Only what a projection needs. The file carries 53 columns; hauling all of
# them into SQLite makes the table slower to scan for no benefit.
KEEP = [
    "player_id", "player_display_name", "position", "recent_team",
    "season", "week", "season_type",
    "completions", "attempts", "passing_yards", "passing_tds", "interceptions",
    "carries", "rushing_yards", "rushing_tds",
    "receptions", "targets", "receiving_yards", "receiving_tds",
    # Fumbles lost are worth -2 in standard scoring and were simply missing.
    "rushing_fumbles_lost", "receiving_fumbles_lost", "sack_fumbles_lost",
    "target_share", "air_yards_share", "wopr",
    "fantasy_points", "fantasy_points_ppr",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS weekly_stats (
    player_id     TEXT NOT NULL,
    player_name   TEXT NOT NULL,
    position      TEXT,
    team          TEXT,
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    season_type   TEXT,
    completions   REAL, attempts REAL, passing_yards REAL,
    passing_tds   REAL, interceptions REAL,
    carries       REAL, rushing_yards REAL, rushing_tds REAL,
    receptions    REAL, targets REAL, receiving_yards REAL, receiving_tds REAL,
    rushing_fumbles_lost REAL, receiving_fumbles_lost REAL, sack_fumbles_lost REAL,
    target_share  REAL, air_yards_share REAL, wopr REAL,
    fantasy_points REAL, fantasy_points_ppr REAL,
    PRIMARY KEY (player_id, season, week, season_type)
);
CREATE INDEX IF NOT EXISTS idx_stats_season ON weekly_stats(season, position);
CREATE INDEX IF NOT EXISTS idx_stats_player ON weekly_stats(player_id, season);
"""


def num(v):
    if v is None or v == "" or v == "NA":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def get(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read()
    print(f"    {len(raw)/1e6:.1f} MB")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
    # Normalise the new names back to the ones the rest of the code expects.
    for r_ in rows:
        for old, new in RENAMES.items():
            if old not in r_ and new in r_:
                r_[old] = r_[new]
    return rows


def download(seasons=None) -> list[dict]:
    """Per-season files first; the frozen legacy file only as a fallback."""
    out, got = [], []
    for s in sorted(seasons or []):
        try:
            print(f"  fetching {s}")
            out += get(URL_PER_SEASON.format(season=s))
            got.append(s)
        except Exception as exc:
            print(f"    unavailable: {str(exc)[:50]}")
    if out:
        print(f"  loaded {sorted(got)} from the current feed")
        return out
    print(f"  falling back to the legacy file (frozen at 2024)")
    return get(URL_LEGACY)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--seasons", default="",
                    help="comma separated, e.g. 2023,2024,2025. Default: last 4.")
    ap.add_argument("--list", action="store_true",
                    help="show which seasons the feed currently carries")
    args = ap.parse_args()

    if args.list:
        # Probe forward from a few years back so staleness is visible.
        from datetime import datetime
        now = datetime.now().year
        print("  checking which seasons the current feed carries\n")
        for s in range(now - 5, now + 1):
            try:
                n = len(get(URL_PER_SEASON.format(season=s)))
                print(f"    {s}: {n:,} player-weeks")
            except Exception:
                print(f"    {s}: not published")
        return

    want = ({int(s) for s in args.seasons.split(",")} if args.seasons
            else set(range(__import__("datetime").datetime.now().year - 3,
                           __import__("datetime").datetime.now().year + 1)))
    rows = download(want)
    if not rows:
        sys.exit("  empty feed")

    seasons_available = sorted({int(r["season"]) for r in rows if r.get("season")})
    print(f"  seasons in hand: {seasons_available[0]}..{seasons_available[-1]}")
    print(f"  importing: {sorted(want)}")

    conn = sqlite3.connect(args.db)
    # Rebuild if the stored table predates a schema change. These rows are a
    # copy of a public dataset, so dropping and refetching costs a download
    # rather than data -- and a missing column otherwise surfaces much later
    # as an opaque "no such column" from deep inside the projection.
    have = {r[1] for r in conn.execute("PRAGMA table_info(weekly_stats)")}
    if have and not {"rushing_fumbles_lost", "sack_fumbles_lost"} <= have:
        print("  schema changed, rebuilding weekly_stats")
        conn.execute("DROP TABLE weekly_stats")
    conn.executescript(SCHEMA)

    kept = 0
    for r in rows:
        try:
            season = int(r["season"])
        except (TypeError, ValueError):
            continue
        if season not in want:
            continue
        vals = (
            r["player_id"], r["player_display_name"], r.get("position"),
            r.get("recent_team"), season, int(float(r["week"])),
            r.get("season_type"),
            *(num(r.get(c)) for c in KEEP[7:]),
        )
        conn.execute(
            f"INSERT OR REPLACE INTO weekly_stats VALUES "
            f"({','.join('?' * len(vals))})", vals)
        kept += 1

    conn.commit()
    print(f"\n  stored {kept:,} player-weeks")

    for s in sorted(want):
        n = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT player_id) FROM weekly_stats "
            "WHERE season = ? AND season_type = 'REG'", (s,)).fetchone()
        print(f"    {s}: {n[0]:,} rows, {n[1]:,} players")

    print("\n  next: python3 scripts/project.py --season", max(want))


if __name__ == "__main__":
    main()
