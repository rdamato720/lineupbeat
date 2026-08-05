#!/usr/bin/env python3
"""Import snap counts, plus the ID crosswalk that ties everything together.

    python3 scripts/import_snaps.py --seasons 2023,2024
    python3 scripts/import_snaps.py --show 2024 --position WR

WHY SNAPS

Targets and carries tell you what a player did. Snap share tells you what his
team intends for him, and the two come apart in ways that matter.

A receiver on 85 percent of snaps with modest targets is a different bet from
one on 40 percent with the same targets: the first has volume waiting to be
converted, the second is already maxed out. Snap share moves first when a role
changes -- before the targets follow -- which is exactly the lag a projection
should be exploiting.

It is also the number a beat report is usually describing. "Took first-team
reps" is a claim about snaps, so this is the historical series that our own
camp reports attach to.

WHY THE CROSSWALK

Nothing in this stack shares an id. Weekly stats use gsis, snap counts use
pfr, our rosters use Sleeper. The nflverse roster file carries all of them in
one row, so it is imported here as a lookup table -- which also means "import
my ESPN league" later is a join rather than a project.
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
import urllib.request

SNAPS = ("https://github.com/nflverse/nflverse-data/releases/download/"
         "snap_counts/snap_counts_{season}.csv")
ROSTER = ("https://github.com/nflverse/nflverse-data/releases/download/"
          "rosters/roster_{season}.csv")

SCHEMA = """
CREATE TABLE IF NOT EXISTS snap_counts (
    season INTEGER, week INTEGER, player_id TEXT, pfr_id TEXT,
    player TEXT, position TEXT, team TEXT,
    offense_snaps REAL, offense_pct REAL,
    PRIMARY KEY (season, week, pfr_id)
);
CREATE INDEX IF NOT EXISTS idx_snaps_player ON snap_counts(player_id, season);

CREATE TABLE IF NOT EXISTS id_map (
    gsis_id TEXT PRIMARY KEY,
    pfr_id TEXT, sleeper_id TEXT, espn_id TEXT, yahoo_id TEXT,
    name TEXT, position TEXT, team TEXT, season INTEGER
);
CREATE INDEX IF NOT EXISTS idx_idmap_pfr ON id_map(pfr_id);
CREATE INDEX IF NOT EXISTS idx_idmap_sleeper ON id_map(sleeper_id);
"""


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read()
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace")))), len(raw)


def num(v):
    if v in (None, "", "NA"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--seasons", default="2024")
    ap.add_argument("--show", type=int)
    ap.add_argument("--position", default="WR")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if args.show:
        rows = conn.execute("""
            SELECT s.player, s.position, s.team,
                   AVG(s.offense_pct) snap_pct, COUNT(*) g,
                   AVG(w.targets) tgt, AVG(w.fantasy_points_ppr) ppg
            FROM snap_counts s
            LEFT JOIN weekly_stats w
              ON w.player_id = s.player_id AND w.season = s.season AND w.week = s.week
            WHERE s.season = ? AND s.position = ? AND s.offense_pct > 0
            GROUP BY s.pfr_id HAVING g >= 8
            ORDER BY snap_pct DESC LIMIT 25
        """, (args.show, args.position.upper())).fetchall()
        if not rows:
            sys.exit(f"  nothing for {args.show} {args.position}")
        print(f"\n  {args.show} {args.position.upper()} — snap share vs what came of it\n")
        print(f"  {'PLAYER':<24} {'TM':<4} {'SNAP%':>6} {'TGT/G':>6} {'PPG':>6}  "
              f"{'TGT PER SNAP%':>14}")
        for r in rows:
            tgt = r["tgt"] or 0
            eff = tgt / r["snap_pct"] if r["snap_pct"] else 0
            print(f"  {r['player'][:24]:<24} {r['team']:<4} "
                  f"{r['snap_pct']*100:>5.0f}% {tgt:>6.1f} {(r['ppg'] or 0):>6.1f}"
                  f"  {eff:>14.1f}")
        print("\n  The last column is targets per point of snap share. High snap")
        print("  share with a low number is a role without volume -- often the")
        print("  most under-priced player on a roster.")
        return

    for s in [int(x) for x in args.seasons.split(",")]:
        # crosswalk first: snaps have no gsis id of their own
        try:
            roster, sz = get(ROSTER.format(season=s))
            print(f"  {s} roster: {sz/1e6:.1f} MB, {len(roster):,} rows")
            for r in roster:
                if not r.get("gsis_id"):
                    continue
                conn.execute("INSERT OR REPLACE INTO id_map VALUES (?,?,?,?,?,?,?,?,?)",
                             (r["gsis_id"], r.get("pfr_id"), r.get("sleeper_id"),
                              r.get("espn_id"), r.get("yahoo_id"),
                              r.get("full_name") or r.get("player_name"),
                              r.get("position"), r.get("team"), s))
            conn.commit()
        except Exception as exc:
            print(f"  {s} roster failed: {exc}")

        pfr_to_gsis = {r["pfr_id"]: r["gsis_id"] for r in
                       conn.execute("SELECT pfr_id, gsis_id FROM id_map WHERE pfr_id IS NOT NULL")}

        try:
            snaps, sz = get(SNAPS.format(season=s))
        except Exception as exc:
            print(f"  {s} snaps failed: {exc}")
            continue

        kept, unmatched = 0, 0
        for r in snaps:
            if r.get("game_type") != "REG":
                continue
            pfr = r.get("pfr_player_id")
            gsis = pfr_to_gsis.get(pfr)
            if not gsis:
                unmatched += 1
            conn.execute("INSERT OR REPLACE INTO snap_counts VALUES (?,?,?,?,?,?,?,?,?)",
                         (s, int(float(r["week"])), gsis, pfr, r.get("player"),
                          r.get("position"), r.get("team"),
                          num(r.get("offense_snaps")), num(r.get("offense_pct"))))
            kept += 1
        conn.commit()
        print(f"  {s} snaps: {kept:,} rows ({unmatched:,} without a stats id)")

    n = conn.execute("SELECT COUNT(*) FROM id_map").fetchone()[0]
    sl = conn.execute("SELECT COUNT(*) FROM id_map WHERE sleeper_id IS NOT NULL "
                      "AND sleeper_id != ''").fetchone()[0]
    print(f"\n  crosswalk: {n:,} players, {sl:,} carry a Sleeper id")
    print(f"  next: python3 scripts/import_snaps.py --show "
          f"{max(int(x) for x in args.seasons.split(','))} --position WR")


if __name__ == "__main__":
    main()
