#!/usr/bin/env python3
"""Import depth charts from nflverse into the roster.

    python3 scripts/import_depth.py
    python3 scripts/import_depth.py --show JAX
    python3 scripts/import_depth.py --changes 14      # what moved recently

WHY NOT SLEEPER

Sleeper carries a depth chart and it is often thin or stale in the offseason,
which is the worst possible state for this particular field: a half-populated
depth chart does not fail loudly, it quietly demotes real starters. nflverse
publishes ESPN's chart, refreshed several times a day, with a full snapshot
history going back to March.

That history is the part worth having. A depth chart is a claim about right
now, and being able to see that a player moved from RB2 to RB1 three weeks ago
is a different and better signal than a single current value.

Joined on gsis_id through the crosswalk built by import_snaps.py, so no name
matching. Falls back to name only where an id is missing, and reports how many
did so, because a silent name match is how a Josh Allen ends up at the wrong
position.
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROSTER = ROOT / "rosters" / "nfl.csv"
URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
       "depth_charts/depth_charts_{season}.csv")
SKILL = {"QB", "RB", "FB", "WR", "TE"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS depth_charts (
    snapshot TEXT, season INTEGER, team TEXT,
    gsis_id TEXT, espn_id TEXT, player TEXT,
    pos TEXT, pos_rank INTEGER,
    PRIMARY KEY (snapshot, team, gsis_id, pos)
);
CREATE INDEX IF NOT EXISTS idx_depth_player ON depth_charts(gsis_id, season);
"""


def fetch(season):
    url = URL.format(season=season)
    print(f"  fetching {season} depth charts")
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=240) as r:
        raw = r.read()
    print(f"    {len(raw)/1e6:.1f} MB")
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--show", help="print one team's chart")
    ap.add_argument("--changes", type=int, metavar="DAYS",
                    help="players whose rank moved in the last N days")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if args.show or args.changes:
        if args.show:
            rows = conn.execute("""
                SELECT * FROM depth_charts WHERE team=? AND season=?
                  AND snapshot = (SELECT MAX(snapshot) FROM depth_charts WHERE season=?)
                  AND pos IN ('QB','RB','WR','TE','FB')
                ORDER BY pos, pos_rank""", (args.show.upper(), args.season, args.season))
            rows = list(rows)
            if not rows:
                sys.exit(f"  nothing for {args.show}. Import first.")
            print(f"\n  {args.show.upper()} — latest chart\n")
            cur = None
            for r in rows:
                if r["pos"] != cur:
                    cur = r["pos"]; print(f"  {cur}")
                print(f"    {r['pos_rank']}. {r['player']}")
            return
        rows = conn.execute(f"""
            WITH latest AS (SELECT MAX(snapshot) s FROM depth_charts WHERE season=?),
                 before AS (SELECT MAX(snapshot) s FROM depth_charts
                            WHERE season=? AND snapshot < datetime(
                              (SELECT s FROM latest), '-{int(args.changes)} days'))
            SELECT n.player, n.team, n.pos, o.pos_rank old_rank, n.pos_rank new_rank
            FROM depth_charts n
            JOIN depth_charts o ON o.gsis_id=n.gsis_id AND o.pos=n.pos
                               AND o.snapshot=(SELECT s FROM before)
            WHERE n.snapshot=(SELECT s FROM latest) AND n.season=?
              AND n.pos IN ('QB','RB','WR','TE') AND o.pos_rank != n.pos_rank
            ORDER BY (o.pos_rank - n.pos_rank) DESC""",
            (args.season, args.season, args.season)).fetchall()
        print(f"\n  DEPTH CHART MOVES, last {args.changes} days\n")
        for r in rows[:30]:
            arrow = "up" if r["new_rank"] < r["old_rank"] else "down"
            print(f"    {r['player'][:24]:<24} {r['team']:<4} {r['pos']:<3} "
                  f"{r['old_rank']} -> {r['new_rank']}  {arrow}")
        if not rows:
            print("    nothing moved")
        print("\n  These are the players whose projection should have changed.")
        return

    rows = fetch(args.season)
    if not rows:
        sys.exit("  empty feed")

    snaps = sorted({r["dt"] for r in rows if r.get("dt")})
    latest = snaps[-1]
    print(f"  {len(snaps)} snapshots, newest {latest}")

    n = 0
    for r in rows:
        try:
            rank = int(r["pos_rank"])
        except (TypeError, ValueError):
            continue
        conn.execute("INSERT OR REPLACE INTO depth_charts VALUES (?,?,?,?,?,?,?,?)",
                     (r["dt"], args.season, r["team"], r.get("gsis_id"),
                      r.get("espn_id"), r.get("player_name"),
                      r.get("pos_abb"), rank))
        n += 1
    conn.commit()
    print(f"  stored {n:,} rows")

    cur = conn.execute("""SELECT * FROM depth_charts
                          WHERE snapshot=? AND pos IN ('QB','RB','WR','TE','FB')""",
                       (latest,)).fetchall()
    print(f"  latest snapshot: {len(cur)} skill entries, "
          f"{len({c['team'] for c in cur})} teams")

    # --- write onto the roster -------------------------------------------
    if not ROSTER.exists():
        sys.exit("  no roster file to update")

    xwalk = {}
    try:
        for r in conn.execute("""SELECT gsis_id, sleeper_id FROM id_map
                                 WHERE sleeper_id IS NOT NULL AND sleeper_id != ''"""):
            xwalk[r["gsis_id"]] = f"nfl-{r['sleeper_id']}"
    except sqlite3.OperationalError:
        print("  no id_map — run scripts/import_snaps.py for id-based joins")

    by_id, by_name = {}, {}
    for c in cur:
        rec = {"pos": c["pos"], "rank": c["pos_rank"]}
        pid = xwalk.get(c["gsis_id"])
        if pid:
            by_id[pid] = rec
        if c["player"]:
            by_name[c["player"].lower()] = rec

    roster = list(csv.DictReader(ROSTER.open()))
    fields = list(roster[0])
    for col in ("depth_pos", "depth_order"):
        if col not in fields:
            fields.append(col)

    hit_id = hit_name = 0
    for r in roster:
        rec = by_id.get(r["id"])
        if rec:
            hit_id += 1
        else:
            rec = by_name.get((r.get("name") or "").lower())
            if rec:
                hit_name += 1
        if rec:
            r["depth_pos"] = rec["pos"]
            r["depth_order"] = rec["rank"]

    skill = [r for r in roster if (r.get("position") or "").upper() in SKILL]
    filled = [r for r in skill if str(r.get("depth_order") or "").strip()]
    pct = 100 * len(filled) / max(1, len(skill))

    print(f"\n  matched by id    {hit_id:,}")
    print(f"  matched by name  {hit_name:,}"
          + ("   <- these could be wrong, check a few" if hit_name > 40 else ""))
    print(f"  skill players with a depth slot: {len(filled)}/{len(skill)} ({pct:.0f}%)")

    qb1 = len({r["team"] for r in roster
               if (r.get("depth_pos") or "").upper().startswith("QB")
               and str(r.get("depth_order")) == "1"})
    print(f"  teams with a listed QB1: {qb1}/32")

    if args.dry_run:
        print("\n  --dry-run, roster not written")
        return

    with ROSTER.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(roster)
    print(f"\n  wrote {ROSTER}")
    if pct < 60:
        print("  Coverage is still thin. Role priors will only partly fire.")
    else:
        print("  Good coverage. Role priors will work.")
    print("  next: python3 scripts/project3.py --season 2025 --publish")


if __name__ == "__main__":
    main()
