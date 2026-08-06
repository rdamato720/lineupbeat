#!/usr/bin/env python3
"""Import official NFL injury reports from nflverse.

    python3 scripts/import_injuries.py --season 2026
    python3 scripts/import_injuries.py --show
    python3 scripts/import_injuries.py --season 2025 --week 18

WHY THIS RATHER THAN ESPN

Injury status is the one input a projection cannot derive. Nothing in three
years of Ricky Pearsall's statistics says he is out for the season, and a
model that cannot read a wire will project him as a starting receiver every
time.

We have been reading it from ESPN's fantasy endpoint, which is undocumented
and could close or change shape mid-season with no notice. This is the same
fact from a source that is published, versioned, joins on the same player id
as everything else here, and is not going anywhere.

WHAT IT IS AND IS NOT

These are game-week injury reports: the Wednesday, Thursday and Friday
practice designations teams are required to file, plus the final game status.
They begin when the season begins.

So in August this returns nothing, and it will not tell you about a
preseason placement on injured reserve -- that never appears on a practice
report. ESPN's status field covers that gap until Week 1, at which point
this becomes the better source and should take over.
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ("https://github.com/nflverse/nflverse-data/releases/download/injuries/"
        "injuries_{season}.csv")

SCHEMA = """
CREATE TABLE IF NOT EXISTS injuries (
    season INTEGER, week INTEGER, team TEXT,
    gsis_id TEXT, player TEXT, position TEXT,
    -- The game-status report: Out, Doubtful, Questionable, or blank when a
    -- player appeared on the practice report but carries no game designation.
    report_status TEXT,
    report_injury TEXT,
    -- The practice report: Did Not Participate, Limited, Full.
    practice_status TEXT,
    practice_injury TEXT,
    fetched_at TEXT,
    PRIMARY KEY (season, week, gsis_id)
);
CREATE INDEX IF NOT EXISTS idx_inj_player ON injuries(gsis_id, season, week);
CREATE INDEX IF NOT EXISTS idx_inj_week ON injuries(season, week);
"""

# What a designation means for the games ahead. Out is this week only unless
# the same designation repeats -- a season is not written off on one report.
STATUS_WEIGHT = {
    "Out": 0.0,
    "Doubtful": 0.25,
    "Questionable": 0.75,
}


def fetch(season: int) -> list[dict]:
    url = BASE.format(season=season)
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    print(f"    {len(raw)/1e6:.1f} MB")
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=datetime.now().year)
    ap.add_argument("--week", type=int, help="filter the summary to one week")
    ap.add_argument("--show", action="store_true",
                    help="what is already stored, no fetch")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if not args.show:
        print(f"  fetching {args.season} injury reports")
        try:
            rows = fetch(args.season)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  no file for {args.season} yet.")
                print(f"  These are game-week reports and they begin when the")
                print(f"  season does. Nothing to import in the offseason.")
                return
            sys.exit(f"  HTTP {e.code}")
        except Exception as exc:
            sys.exit(f"  {str(exc)[:90]}")

        now = datetime.now(timezone.utc).isoformat()
        n = 0
        for r in rows:
            gid = (r.get("gsis_id") or "").strip()
            if not gid:
                continue
            try:
                wk = int(r.get("week") or 0)
            except ValueError:
                continue
            conn.execute("INSERT OR REPLACE INTO injuries VALUES "
                         "(?,?,?,?,?,?,?,?,?,?,?)",
                         (args.season, wk, r.get("team"), gid,
                          r.get("full_name"), r.get("position"),
                          (r.get("report_status") or "").strip(),
                          (r.get("report_primary_injury") or "").strip(),
                          (r.get("practice_status") or "").strip(),
                          (r.get("practice_primary_injury") or "").strip(),
                          now))
            n += 1
        conn.commit()
        print(f"  stored {n:,} report rows")

    latest = conn.execute("""SELECT MAX(week) w FROM injuries WHERE season=?""",
                          (args.season,)).fetchone()
    if not latest or latest["w"] is None:
        print(f"\n  nothing stored for {args.season}")
        return
    week = args.week or latest["w"]

    print(f"\n  WEEK {week}, {args.season}")
    rows = conn.execute("""SELECT * FROM injuries WHERE season=? AND week=?
                           AND report_status != '' ORDER BY
                           CASE report_status WHEN 'Out' THEN 0
                                WHEN 'Doubtful' THEN 1 ELSE 2 END,
                           position, player""",
                        (args.season, week)).fetchall()
    if not rows:
        print("    no game-status designations that week")
    skill = [r for r in rows if r["position"] in ("QB", "RB", "WR", "TE")]
    print(f"    {len(rows)} designations, {len(skill)} at a skill position\n")
    for r in skill[:20]:
        inj = r["report_injury"] or r["practice_injury"] or ""
        print(f"    {r['report_status']:<13}{r['player'][:24]:<24}"
              f"{r['position']:<4}{r['team']:<4} {inj}")
    if len(skill) > 20:
        print(f"    … and {len(skill) - 20} more")

    # A player who has been Out for weeks is a different case from one ruled
    # out on Friday, and only the first should touch a season projection.
    print(f"\n  OUT FOR MULTIPLE WEEKS  (the ones a season projection cares about)")
    streak = conn.execute("""SELECT player, position, team, COUNT(*) n,
                             MIN(week) f, MAX(week) l FROM injuries
                             WHERE season=? AND report_status='Out'
                             AND position IN ('QB','RB','WR','TE')
                             GROUP BY gsis_id HAVING n >= 2
                             ORDER BY n DESC LIMIT 12""",
                          (args.season,)).fetchall()
    if not streak:
        print("    none")
    for r in streak:
        print(f"    {r['player'][:24]:<24}{r['position']:<4}{r['team']:<4}"
              f"out weeks {r['f']}-{r['l']} ({r['n']})")


if __name__ == "__main__":
    main()
