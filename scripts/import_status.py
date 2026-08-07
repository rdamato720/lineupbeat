#!/usr/bin/env python3
"""Import weekly roster status, so a missed game has a reason.

    python3 scripts/import_status.py --seasons 2019,2020,2021,2022,2023,2024,2025
    python3 scripts/import_status.py --show "George Kittle"

WHY

A missing box score row says a player did not play. It does not say why, and
the difference is the whole claim.

George Kittle has missed roughly twenty games and carries four "Out" reports
across seventeen seasons, because a player on injured reserve drops off the
weekly injury report entirely. Meanwhile a receiver who spent 2019 on a
practice squad reads as having missed all seventeen -- he was not hurt, he
was not on the team.

Weekly rosters fix both. Every player, every week, with a status:

    ACT  active
    RES  reserve, which is injured reserve and its relatives
    INA  on the roster, inactive for that game
    DEV  practice squad
    CUT  not on the team
    RET  retired
    EXE  exempt

So an absence resolves to injured, scratched, or not on the roster at all --
and only the first two belong in a durability record.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ("https://github.com/nflverse/nflverse-data/releases/download/"
        "weekly_rosters/roster_weekly_{season}.csv")

# What each status means for a durability record.
#
# RES and INA are absences that count: he was on the team and did not play.
# DEV, CUT and RET are not absences at all -- he was somewhere else, and
# counting those against him is how a practice squad season becomes
# seventeen missed games.
COUNTS_AS_ABSENCE = {"RES", "INA", "EXE"}
NOT_ON_TEAM = {"DEV", "CUT", "RET", "TRC"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS weekly_status (
    season INTEGER, week INTEGER, gsis_id TEXT, player TEXT,
    name_key TEXT, team TEXT, position TEXT,
    status TEXT,              -- ACT, RES, INA, DEV, CUT, RET, EXE
    status_abbr TEXT,         -- R01, P01, I01 and friends: the reserve type
    fetched_at TEXT,
    PRIMARY KEY (season, week, gsis_id)
);
CREATE INDEX IF NOT EXISTS idx_ws_key ON weekly_status(name_key, season);
CREATE INDEX IF NOT EXISTS idx_ws_gsis ON weekly_status(gsis_id, season);
"""


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def fetch(season):
    url = BASE.format(season=season)
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace")))), len(raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--seasons",
                    default="2018,2019,2020,2021,2022,2023,2024,2025")
    ap.add_argument("--show", help="one player's week-by-week record")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if args.show:
        k = key(args.show)
        rows = conn.execute("""SELECT season, week, team, status, status_abbr
                               FROM weekly_status WHERE name_key=?
                               ORDER BY season, week""", (k,)).fetchall()
        if not rows:
            sys.exit(f"  nothing stored for {args.show}")
        print(f"\n  {args.show}\n")
        by_season = {}
        for r in rows:
            by_season.setdefault(r["season"], []).append(r)
        for s, rs in by_season.items():
            line = "".join({"ACT": ".", "RES": "R", "INA": "x",
                            "DEV": "d", "CUT": "-", "RET": "!",
                            "EXE": "e"}.get(r["status"], "?") for r in rs)
            act = sum(1 for r in rs if r["status"] == "ACT")
            res = sum(1 for r in rs if r["status"] == "RES")
            ina = sum(1 for r in rs if r["status"] == "INA")
            off = sum(1 for r in rs if r["status"] in NOT_ON_TEAM)
            print(f"    {s}  {line:<20}  active {act:>2}  "
                  f"reserve {res:>2}  inactive {ina:>2}"
                  + (f"  off roster {off}" if off else ""))
        print(f"\n    . active   R reserve/IR   x inactive   d practice squad")
        print(f"    - not on a roster   ! retired")
        return

    now = datetime.now(timezone.utc).isoformat()
    total = 0
    for s in [int(x) for x in args.seasons.split(",")]:
        try:
            rows, size = fetch(s)
        except urllib.error.HTTPError as e:
            print(f"  {s}: HTTP {e.code}")
            continue
        except Exception as exc:
            print(f"  {s}: {str(exc)[:70]}")
            continue
        n = 0
        for r in rows:
            gid = (r.get("gsis_id") or "").strip()
            if not gid:
                continue
            try:
                wk = int(r.get("week") or 0)
            except ValueError:
                continue
            name = r.get("full_name") or r.get("player_name") or ""
            conn.execute("INSERT OR REPLACE INTO weekly_status VALUES "
                         "(?,?,?,?,?,?,?,?,?,?)",
                         (s, wk, gid, name, key(name), r.get("team"),
                          r.get("position"), (r.get("status") or "").strip(),
                          (r.get("status_description_abbr") or "").strip(),
                          now))
            n += 1
        conn.commit()
        print(f"  {s}: {n:,} rows  ({size/1e6:.1f} MB)")
        total += n

    print(f"\n  {total:,} player-weeks stored")
    if total:
        r = conn.execute("""SELECT status, COUNT(*) n FROM weekly_status
                            GROUP BY status ORDER BY n DESC""").fetchall()
        print("\n  status mix:")
        for x in r:
            print(f"    {x['status'] or '(blank)':<8}{x['n']:>9,}")
        print(f"\n  next: python3 scripts/durability.py")


if __name__ == "__main__":
    main()
