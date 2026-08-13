#!/usr/bin/env python3
"""Import the data behind the offensive line and RB performance page.

    python3 scripts/import_rushing.py --seasons 2025
    python3 scripts/import_rushing.py --seasons 2020 2021 2022 2023 2024 2025
    python3 scripts/import_rushing.py --rbwr data/rbwr_2025.csv

Three sources, each with a different job, and deliberately not substituted
for one another.

NEXT GEN STATS, via nflverse
    What the runner produced. Rush yards over expected is the important
    one: it already accounts for the position, speed and direction of every
    blocker and defender at the handoff, which is why it can say a back
    beat his blocking rather than merely that he gained yards.

PLAY BY PLAY, via nflverse
    Rates a reader already understands -- stuffed, explosive, successful --
    computed from designed runs only. QB kneels and scrambles are excluded,
    because a kneel is a stuffed run by any arithmetic and means nothing.

RUN BLOCK WIN RATE, by hand
    ESPN's blocking metric, pasted in from a CSV. There is no official API
    and the community scraper reads an undocumented page, so a production
    page that depends on it breaks the week ESPN redesigns something. A
    manual file is thirty-two rows a week and it never breaks.

WHY THE RB TABLE IS BY PLAYER AND TEAM, NOT BY PLAYER
    A back traded in October played behind two different lines. Summing his
    season and attaching it to his current team would credit the wrong
    blocking for half of it, so every row is a player-team stint.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NGS_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
           "nextgen_stats/ngs_rushing.csv.gz")
PBP_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
           "pbp/play_by_play_{season}.csv.gz")

# RB and FB only. A quarterback's designed runs are a different thing from a
# rushing attack, and letting them in distorts every rate on the page.
RUSH_POSITIONS = {"RB", "FB", "HB"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ol_team_season (
    season        INTEGER NOT NULL,
    season_type   TEXT NOT NULL DEFAULT 'REG',
    team          TEXT NOT NULL,
    rbwr_pct      REAL,
    rbwr_rank     INTEGER,
    rbwr_tier     TEXT,
    source_name   TEXT NOT NULL DEFAULT 'ESPN Analytics',
    -- Where the numbers came from and when. Typed in by hand, so the
    -- provenance has to travel with them or nobody can check the figure
    -- against its source a month later.
    source_url    TEXT,
    source_date   TEXT,
    source_updated_at TEXT,
    retrieved_at  TEXT NOT NULL,
    PRIMARY KEY (season, season_type, team)
);

CREATE TABLE IF NOT EXISTS rb_ngs_weekly (
    season       INTEGER NOT NULL,
    season_type  TEXT NOT NULL,
    week         INTEGER NOT NULL,
    player_gsis_id TEXT NOT NULL,
    player_name  TEXT,
    team         TEXT,
    position     TEXT,
    rush_attempts INTEGER,
    rush_yards   REAL,
    avg_rush_yards REAL,
    rush_touchdowns INTEGER,
    expected_rush_yards REAL,
    rush_yards_over_expected REAL,
    rush_yards_over_expected_per_att REAL,
    rush_pct_over_expected REAL,
    avg_time_to_los REAL,
    efficiency   REAL,
    percent_attempts_gte_eight_defenders REAL,
    PRIMARY KEY (season, season_type, week, player_gsis_id, team)
);

CREATE TABLE IF NOT EXISTS rb_pbp_season (
    season       INTEGER NOT NULL,
    player_gsis_id TEXT NOT NULL,
    player_name  TEXT,
    team         TEXT NOT NULL,
    qualifying_carries INTEGER NOT NULL DEFAULT 0,
    rush_success_rate REAL,
    stuff_rate   REAL,
    explosive_run_rate REAL,
    short_yardage_attempts INTEGER DEFAULT 0,
    short_yardage_conversion_rate REAL,
    PRIMARY KEY (season, player_gsis_id, team)
);

CREATE INDEX IF NOT EXISTS idx_ngs_season ON rb_ngs_weekly(season, team);
CREATE INDEX IF NOT EXISTS idx_pbp_season ON rb_pbp_season(season, team);
"""

ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LAR", "LA": "LAR"}

# Descriptive labels on the league rank, not a second calculation. A team
# is Elite because it finished top five in run block win rate, and the
# label carries no information the rank does not.
TIERS = [(5, "Elite"), (10, "Strong"), (22, "Average"),
         (27, "Weak"), (32, "Poor")]


def tier_for(rank):
    for limit, name in TIERS:
        if rank <= limit:
            return name
    return "Poor"


def team(code):
    c = (code or "").strip().upper()
    return ALIASES.get(c, c)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def integer(v):
    f = num(v)
    return int(f) if f is not None else None


def migrate(conn):
    """Add columns a previous version of this table did not have.

    CREATE TABLE IF NOT EXISTS does nothing when the table already exists,
    so a database built by an earlier run keeps the old shape and every
    insert fails on a column that is only in the schema string. Adding
    them one at a time is dull and survives being run twice.
    """
    have = {r[1] for r in conn.execute("PRAGMA table_info(ol_team_season)")}
    for col, decl in (("rbwr_tier", "TEXT"),
                      ("source_url", "TEXT"),
                      ("source_date", "TEXT")):
        if col not in have:
            conn.execute(
                f"ALTER TABLE ol_team_season ADD COLUMN {col} {decl}")
            print(f"    added column {col}")
    conn.commit()


def fetch_csv(url: str, label: str) -> list[dict]:
    print(f"    {label}")
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read()
    if url.endswith(".gz"):
        raw = gzip.decompress(raw)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))


def import_ngs(conn, seasons: set[int]) -> int:
    """Next Gen Stats rushing, weekly.

    Weekly rows rather than the week==0 season summary, because a traded
    player needs to keep each stint attached to the line he ran behind.
    """
    rows = fetch_csv(NGS_URL, "next gen stats, rushing")
    n = 0
    for r in rows:
        season = integer(r.get("season"))
        if season is None or (seasons and season not in seasons):
            continue
        week = integer(r.get("week"))
        gsis = (r.get("player_gsis_id") or "").strip()
        if not gsis or week is None:
            continue
        pos = (r.get("player_position") or "").upper()
        if pos and pos not in RUSH_POSITIONS:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO rb_ngs_weekly
               (season, season_type, week, player_gsis_id, player_name, team,
                position, rush_attempts, rush_yards, avg_rush_yards,
                rush_touchdowns, expected_rush_yards, rush_yards_over_expected,
                rush_yards_over_expected_per_att, rush_pct_over_expected,
                avg_time_to_los, efficiency,
                percent_attempts_gte_eight_defenders)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (season, (r.get("season_type") or "REG").upper(), week, gsis,
             r.get("player_display_name"), team(r.get("team_abbr")), pos,
             integer(r.get("rush_attempts")), num(r.get("rush_yards")),
             num(r.get("avg_rush_yards")), integer(r.get("rush_touchdowns")),
             num(r.get("expected_rush_yards")),
             num(r.get("rush_yards_over_expected")),
             num(r.get("rush_yards_over_expected_per_att")),
             num(r.get("rush_pct_over_expected")),
             num(r.get("avg_time_to_los")), num(r.get("efficiency")),
             num(r.get("percent_attempts_gte_eight_defenders"))))
        n += 1
    conn.commit()
    return n


def import_pbp(conn, season: int) -> int:
    """Stuff, explosive, success and short yardage, from designed runs.

    Excluding kneels and scrambles is not fussiness. A kneel is a stuffed
    run by any arithmetic and a scramble is a passing play that went wrong;
    counting either tells you something true about the game and nothing
    true about the running attack.
    """
    try:
        rows = fetch_csv(PBP_URL.format(season=season),
                         f"play by play, {season}")
    except Exception as exc:
        print(f"      no play by play for {season}: {str(exc)[:60]}")
        return 0

    agg = {}
    for p in rows:
        if (p.get("season_type") or "").upper() != "REG":
            continue
        if p.get("rush_attempt") != "1" or p.get("play_type") != "run":
            continue
        if p.get("qb_kneel") == "1" or p.get("qb_scramble") == "1":
            continue
        gsis = (p.get("rusher_player_id") or "").strip()
        tm = team(p.get("posteam"))
        if not gsis or not tm:
            continue
        yards = num(p.get("rushing_yards"))
        if yards is None:
            continue

        k = (gsis, tm)
        a = agg.setdefault(k, {"name": p.get("rusher_player_name"),
                               "carries": 0, "success": 0, "stuffed": 0,
                               "explosive": 0, "short": 0, "short_ok": 0})
        a["carries"] += 1
        if p.get("success") == "1":
            a["success"] += 1
        if yards <= 0:
            a["stuffed"] += 1
        if yards >= 10:
            a["explosive"] += 1

        down = integer(p.get("down"))
        togo = num(p.get("ydstogo"))
        if down in (3, 4) and togo is not None and togo <= 2:
            a["short"] += 1
            if p.get("first_down_rush") == "1" or p.get("rush_touchdown") == "1":
                a["short_ok"] += 1

    n = 0
    for (gsis, tm), a in agg.items():
        c = a["carries"]
        if not c:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO rb_pbp_season
               (season, player_gsis_id, player_name, team, qualifying_carries,
                rush_success_rate, stuff_rate, explosive_run_rate,
                short_yardage_attempts, short_yardage_conversion_rate)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (season, gsis, a["name"], tm, c,
             a["success"] / c, a["stuffed"] / c, a["explosive"] / c,
             a["short"], (a["short_ok"] / a["short"]) if a["short"] else None))
        n += 1
    conn.commit()
    return n


def import_rbwr(conn, path: Path) -> int:
    """ESPN Run Block Win Rate, typed in by hand.

    No scraper, at any point: not at page load, not in the nightly job, not
    on deploy. ESPN publishes no API for this and the community helper
    reads an undocumented page, so a page that depends on it breaks the
    week ESPN changes a class name -- silently, and for everyone.

    Thirty-two rows a week is a small price for a number that cannot break.

    Expected columns: season, team, rbwr. Optional: rank, source_url,
    source_date. Rank is computed when absent; the tier always is, because
    a tier that disagrees with the rank beside it would be worse than none.
    """
    from datetime import datetime, timezone
    rows = list(csv.DictReader(path.open()))
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    by_season = {}
    for r in rows:
        low = {k.lower().strip(): v for k, v in r.items() if k}
        season = integer(low.get("season"))
        tm = team(low.get("team"))
        pct = num(low.get("rbwr") or low.get("rbwr_pct")
                  or low.get("run block win rate"))
        if season is None or not tm:
            continue
        # A blank is refused rather than defaulted. An invented figure is
        # indistinguishable from a measured one once it is in the table.
        if pct is None:
            print(f"      {tm}: no rbwr value, skipped")
            continue
        # Accept 0.752 or 75.2 and store one of them.
        if pct <= 1:
            pct *= 100
        by_season.setdefault(season, []).append({
            "team": tm, "pct": pct,
            "rank": integer(low.get("rank") or low.get("run_block_rank")
                            or low.get("rbwr_rank")),
            "url": low.get("source_url") or low.get("source"),
            "date": low.get("source_date") or low.get("updated"),
        })

    n = 0
    for season, teams in by_season.items():
        # Ties broken by team code, so two runs on the same data produce
        # the same ranks. A rank that moves when nothing changed is a bug
        # somebody will spend an afternoon chasing.
        ranked = sorted(teams, key=lambda x: (-x["pct"], x["team"]))
        computed = {r["team"]: i + 1 for i, r in enumerate(ranked)}
        for r in teams:
            rank = r["rank"] or computed[r["team"]]
            conn.execute(
                """INSERT OR REPLACE INTO ol_team_season
                   (season, season_type, team, rbwr_pct, rbwr_rank,
                    rbwr_tier, source_name, source_url, source_date,
                    source_updated_at, retrieved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (season, "REG", r["team"], round(r["pct"], 1), rank,
                 tier_for(rank), "ESPN Analytics", r["url"], r["date"],
                 r["date"], now))
            n += 1
        problems = validate_rbwr(teams, computed, season)
        if problems:
            print(f"      {season}: {len(problems)} problem(s)")
            for x in problems[:6]:
                print(f"        {x}")
    conn.commit()
    return n


def validate_rbwr(teams, computed, season):
    """The gates from the spec, before anything is published.

    A page that quietly shows nineteen teams' blocking is worse than one
    that shows none: the reader cannot tell which is missing, and neither
    can we a week later.
    """
    bad = []
    codes = [r["team"] for r in teams]
    if len(codes) != 32:
        bad.append(f"{len(codes)} teams, not 32")
    dupes = {c for c in codes if codes.count(c) > 1}
    if dupes:
        bad.append(f"duplicate teams: {sorted(dupes)}")
    for r in teams:
        if not 0 <= r["pct"] <= 100:
            bad.append(f"{r['team']} rbwr {r['pct']} outside 0-100")
        if r["rank"] and not 1 <= r["rank"] <= 32:
            bad.append(f"{r['team']} rank {r['rank']} outside 1-32")
    given = [r["rank"] for r in teams if r["rank"]]
    if given and len(set(given)) != len(given):
        # Only a problem when the values behind them are not tied: ESPN
        # can legitimately tie, and a shared rank is then correct.
        by_rank = {}
        for r in teams:
            if r["rank"]:
                by_rank.setdefault(r["rank"], []).append(r["pct"])
        for rank, pcts in by_rank.items():
            if len(pcts) > 1 and len(set(pcts)) > 1:
                bad.append(f"rank {rank} shared by different values {pcts}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--seasons", nargs="*", type=int, default=[2025])
    ap.add_argument("--rbwr", help="CSV of ESPN run block win rate")
    ap.add_argument("--skip-pbp", action="store_true",
                    help="NGS only, which is much faster")
    args = ap.parse_args()

    db = ROOT / args.db
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    migrate(conn)
    seasons = set(args.seasons or [])

    print(f"\n  fetching")
    n_ngs = import_ngs(conn, seasons)
    print(f"    {n_ngs:,} NGS player-weeks")

    n_pbp = 0
    if not args.skip_pbp:
        for s in sorted(seasons):
            n_pbp += import_pbp(conn, s)
    print(f"    {n_pbp:,} player-team season rows from play by play")

    if args.rbwr:
        p = Path(args.rbwr)
        if not p.is_absolute():
            p = ROOT / p
        if p.exists():
            n = import_rbwr(conn, p)
            print(f"    {n} run block win rate rows from {p.name}")
        else:
            print(f"    no {p}, skipping run block win rate")

    print(f"\n  stored")
    for s in sorted(seasons):
        ngs = conn.execute(
            "SELECT COUNT(DISTINCT player_gsis_id) FROM rb_ngs_weekly "
            "WHERE season=?", (s,)).fetchone()[0]
        pbp = conn.execute(
            "SELECT COUNT(*), SUM(qualifying_carries) FROM rb_pbp_season "
            "WHERE season=?", (s,)).fetchone()
        ol = conn.execute(
            "SELECT COUNT(*) FROM ol_team_season WHERE season=?",
            (s,)).fetchone()[0]
        print(f"    {s}  {ngs:>3} backs with NGS, "
              f"{pbp[0] or 0:>3} player-team stints "
              f"({pbp[1] or 0:,} carries), {ol}/32 teams with RBWR")

    if not conn.execute("SELECT COUNT(*) FROM ol_team_season").fetchone()[0]:
        print(f"\n  No run block win rate yet. The page builds without it,")
        print(f"  showing the runner half only. To add it, paste ESPN's")
        print(f"  numbers into a CSV with columns season,team,rbwr and run:")
        print(f"    python3 scripts/import_rushing.py --rbwr data/rbwr.csv")

    print(f"\n  next: python3 scripts/build_ol_rb.py")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
