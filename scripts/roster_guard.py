#!/usr/bin/env python3
"""Cross-check our roster against an independent source, on every run.

    python3 scripts/roster_guard.py                 # check and report
    python3 scripts/roster_guard.py --strict        # exit 1 on disagreement
    python3 scripts/roster_guard.py --fix           # adopt the second source
    python3 scripts/roster_guard.py --history       # what has changed over time

WHY TWO SOURCES

One source cannot tell you it is wrong. Sleeper is live and generally
excellent, but a stale cache, a delayed transaction, or a failed import all
look identical from inside our own file. A second source that agrees is
strong evidence; a second source that disagrees is exactly the thing we need
to see before publishing a projection with a player on the wrong team.

nflverse publishes a season roster that carries `sleeper_id`, so the two join
on an identifier rather than on names. That matters: name matching would
invent disagreements for every Jr., accent and hyphen, and we would learn to
ignore the warnings.

WHAT IT DOES NOT DO

It does not decide who is right. When the two disagree the script says so and
stops; adopting either automatically would replace a visible problem with an
invisible one. `--fix` exists, prints exactly what it will change, and should
be run by someone who has looked at the list.

Every run appends to a history table, so a team flapping between two values
across days is visible rather than being noticed once and forgotten.
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROSTER = ROOT / "rosters" / "nfl.csv"
NFLVERSE = ("https://github.com/nflverse/nflverse-data/releases/download/"
            "rosters/roster_{season}.csv")
SKILL = {"QB", "RB", "WR", "TE"}   # no fullbacks

# The two sources spell three teams differently. Without this the check
# invents a disagreement for every Ram, Charger and Raider and the warnings
# become noise -- which is worse than not checking, because people learn to
# ignore them.
TEAM_ALIAS = {"LA": "LAR", "SD": "LAC", "OAK": "LV", "STL": "LAR",
              "WSH": "WAS", "JAC": "JAX", "ARZ": "ARI", "BLT": "BAL",
              "CLV": "CLE", "HST": "HOU"}


def norm_team(code):
    c = (code or "").strip().upper()
    return TEAM_ALIAS.get(c, c)

SCHEMA = """
CREATE TABLE IF NOT EXISTS roster_checks (
    checked_at TEXT, season INTEGER,
    ours INTEGER, theirs INTEGER, matched INTEGER,
    team_disagreements INTEGER, missing_from_ours INTEGER,
    verdict TEXT
);
CREATE TABLE IF NOT EXISTS roster_disagreements (
    checked_at TEXT, sleeper_id TEXT, name TEXT, position TEXT,
    our_team TEXT, their_team TEXT
);
"""


def fetch(season: int):
    url = NFLVERSE.format(season=season)
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=datetime.now().year)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--history", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if args.history:
        rows = conn.execute("""SELECT * FROM roster_checks
                               ORDER BY checked_at DESC LIMIT 20""").fetchall()
        if not rows:
            sys.exit("  no checks recorded yet")
        print(f"\n  {'WHEN':<22} {'OURS':>6} {'THEIRS':>7} {'MATCH':>6} "
              f"{'DISAGREE':>9}  VERDICT")
        for r in rows:
            print(f"  {r['checked_at'][:19]:<22} {r['ours']:>6} {r['theirs']:>7} "
                  f"{r['matched']:>6} {r['team_disagreements']:>9}  {r['verdict']}")
        flappers = conn.execute("""
            SELECT name, COUNT(DISTINCT their_team) t, COUNT(*) n
            FROM roster_disagreements GROUP BY sleeper_id
            HAVING n > 1 ORDER BY n DESC LIMIT 10""").fetchall()
        if flappers:
            print("\n  players who keep disagreeing across checks:")
            for f in flappers:
                print(f"    {f['name'][:26]:<26} {f['n']} times")
            print("\n  Repeats are the interesting ones. A single disagreement")
            print("  is usually a transaction in flight; a recurring one is a")
            print("  join that does not work.")
        return

    if not ROSTER.exists():
        sys.exit("  no roster file. Run scripts/import_rosters.py nfl")

    ours = list(csv.DictReader(ROSTER.open()))
    by_sleeper = {}
    for r in ours:
        sid = (r.get("id") or "").replace("nfl-", "").strip()
        if sid:
            by_sleeper[sid] = r

    print(f"  ours     {len(ours):,} players")
    try:
        theirs_rows = fetch(args.season)
    except Exception as exc:
        print(f"  could not reach the second source: {str(exc)[:60]}")
        print("  Publishing on a single unverified source. Not fatal, but "
              "worth knowing.")
        if args.strict:
            sys.exit(1)
        return

    theirs = {}
    for r in theirs_rows:
        sid = (r.get("sleeper_id") or "").strip()
        if sid and (r.get("position") or "").upper() in SKILL:
            theirs[sid] = r
    print(f"  theirs   {len(theirs_rows):,} players "
          f"({len(theirs):,} skill with a Sleeper id)")

    disagree, missing, matched = [], [], 0
    for sid, t in theirs.items():
        o = by_sleeper.get(sid)
        if not o:
            missing.append(t)
            continue
        matched += 1
        ot = norm_team(o.get("team"))
        tt = norm_team(t.get("team"))
        if ot and tt and ot != tt:
            disagree.append((sid, t.get("full_name") or o.get("name"),
                             t.get("position"), ot, tt))

    print(f"  matched  {matched:,} on Sleeper id")
    print(f"\n  TEAM DISAGREEMENTS: {len(disagree)}")
    for sid, name, pos, ot, tt in sorted(disagree, key=lambda x: x[1] or "")[:20]:
        print(f"    {(name or '?')[:26]:<26} {pos:<3}  ours {ot:<4} "
              f"theirs {tt}")
    if len(disagree) > 20:
        print(f"    … and {len(disagree) - 20} more")

    notable = [m for m in missing
               if (m.get("position") or "") in ("QB", "RB", "WR", "TE")][:10]
    print(f"\n  ON THEIR ROSTER, NOT OURS: {len(missing)}")
    for m in notable:
        print(f"    {(m.get('full_name') or '?')[:26]:<26} "
              f"{m.get('position'):<3}  {m.get('team')}")

    rate = 100 * len(disagree) / max(1, matched)
    match_rate = 100 * matched / max(1, len(theirs))

    # A low match rate is the more dangerous failure. Zero disagreements out of
    # zero comparisons is not agreement, it is a broken join reported as
    # success -- and that is precisely the shape of error this whole script
    # exists to catch.
    if match_rate < 60:
        verdict = "do not publish"
        print(f"\n  only {match_rate:.0f}% of their skill players matched ours "
              f"by Sleeper id.\n  That is a broken join, not agreement.")
    elif rate == 0 and len(missing) < 120:
        verdict = "clean"
    elif rate < 0.5:
        verdict = "minor"
    elif rate < 2:
        verdict = "check"
    else:
        verdict = "do not publish"

    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO roster_checks VALUES (?,?,?,?,?,?,?,?)",
                 (now, args.season, len(ours), len(theirs), matched,
                  len(disagree), len(missing), verdict))
    for sid, name, pos, ot, tt in disagree:
        conn.execute("INSERT INTO roster_disagreements VALUES (?,?,?,?,?,?)",
                     (now, sid, name, pos, ot, tt))
    conn.commit()

    print(f"\n  matched {match_rate:.0f}% of their skill players")
    print(f"  disagreement rate {rate:.2f}%   VERDICT: {verdict}")
    if verdict == "clean":
        print("  Two independent sources agree. Safe to publish.")
    elif verdict in ("minor", "check"):
        print("  Read the list above. A handful of disagreements in the days")
        print("  after a transaction window is normal; the same player")
        print("  disagreeing every day is a broken join.")
    else:
        print("  Something is wrong with the roster import. Do not publish")
        print("  projections until this is understood.")

    if args.fix and disagree:
        print(f"\n  --fix: adopting the second source for {len(disagree)} players")
        for sid, name, pos, ot, tt in disagree:
            by_sleeper[sid]["team"] = tt
        with ROSTER.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(ours[0]), extrasaction="ignore")
            w.writeheader(); w.writerows(ours)
        print("  roster updated. Re-run without --fix to confirm it is clean.")

    if args.strict and verdict in ("check", "do not publish"):
        sys.exit(1)


if __name__ == "__main__":
    main()
