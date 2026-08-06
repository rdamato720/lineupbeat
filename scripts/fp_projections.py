#!/usr/bin/env python3
"""Import FantasyPros projections, with the stat line, and apply our own
availability work on top.

    export FANTASYPROS_API_KEY=...
    python3 scripts/fp_projections.py --season 2026
    python3 scripts/fp_projections.py --show RB
    python3 scripts/fp_projections.py --show RB --scoring half

WHAT THIS IS

Their consensus is the base: a hundred-odd analysts, aggregated, carrying
everything a model cannot derive -- that a team drafted a back, that a
thirty-year-old with four hundred touches is due a step back, that an
offence got worse.

Ours is the second column: how many games we expect a player to actually
play, from four seasons of availability, official injury reports and depth
slot. Nobody else publishes that. Every projection you can buy assumes a
full healthy season and quietly hopes.

So the product is their volume, scored to your league, discounted by our
durability. Two numbers side by side: what he does if he plays, and what he
does given he might not.

LICENSING

The free key is for prototyping. Displaying any of this commercially needs a
paid licence, and the terms explicitly do not transfer rights to their
calculated data -- so ask before shipping, not after. Nothing here publishes
anything; it writes to a table and prints to a terminal.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.fantasypros.com/public/v2/json"
POS = ("QB", "RB", "WR", "TE")

# Your league, from the ESPN settings we verified against actual scoring.
# Their stat line means we are not stuck with whatever scoring they applied.
SCORING = {
    "pass_yds": 0.04, "pass_tds": 4.0, "pass_ints": -2.0,
    "rush_yds": 0.10, "rush_tds": 6.0,
    "rec_yds": 0.10, "rec_tds": 6.0, "rec_rec": 1.0,
    "fumbles": -2.0,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS fp_projections (
    season INTEGER, fpid INTEGER, player TEXT, name_key TEXT,
    position TEXT, team TEXT,
    points REAL, points_ppr REAL, points_half REAL,
    -- The stat line is the point. Points alone cannot be rescored for a
    -- league, cannot be shown to a reader, and cannot have a games
    -- adjustment applied to its components.
    rush_att REAL, rush_yds REAL, rush_tds REAL,
    rec REAL, rec_yds REAL, rec_tds REAL,
    pass_att REAL, pass_yds REAL, pass_tds REAL, pass_ints REAL,
    fumbles REAL, fetched_at TEXT,
    PRIMARY KEY (season, fpid)
);
CREATE INDEX IF NOT EXISTS idx_fp_key ON fp_projections(name_key, season);
"""


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def fetch(season, pos, api_key, week=0, scoring="PPR"):
    url = (f"{BASE}/nfl/{season}/projections"
           f"?position={pos}&scoring={scoring}&week={week}")
    req = urllib.request.Request(url, headers={
        "x-api-key": api_key, "User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def num(d, *names):
    for n in names:
        v = d.get(n)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def score(row, rules=SCORING):
    """Their volume, our league's rules.

    A points total is somebody else's scoring assumption. A stat line can be
    scored for whoever is reading, which is the whole reason to take the
    stat line.
    """
    return (row["pass_yds"] * rules["pass_yds"]
            + row["pass_tds"] * rules["pass_tds"]
            + row["pass_ints"] * rules["pass_ints"]
            + row["rush_yds"] * rules["rush_yds"]
            + row["rush_tds"] * rules["rush_tds"]
            + row["rec_yds"] * rules["rec_yds"]
            + row["rec_tds"] * rules["rec_tds"]
            + row["rec"] * rules["rec_rec"]
            + row["fumbles"] * rules["fumbles"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--our-season", type=int, default=2025,
                    help="the season our availability work reads from")
    ap.add_argument("--show", help="print one position")
    ap.add_argument("--scoring", default="ppr",
                    choices=["ppr", "half", "standard", "league"],
                    help="'league' rescores their stat line with our rules")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if not args.show:
        api_key = os.environ.get("FANTASYPROS_API_KEY")
        if not api_key:
            sys.exit("  set FANTASYPROS_API_KEY first")
        now = datetime.now(timezone.utc).isoformat()
        total = 0
        for pos in POS:
            try:
                data = fetch(args.season, pos, api_key)
            except urllib.error.HTTPError as e:
                print(f"  {pos}: HTTP {e.code}"
                      + ("  (not on this tier)" if e.code == 403 else ""))
                continue
            rows = data.get("players") or data.get("projections") or []
            for p in rows:
                s = p.get("stats") or {}
                conn.execute(
                    "INSERT OR REPLACE INTO fp_projections VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (args.season, p.get("fpid"), p.get("name"),
                     key(p.get("name")), p.get("position_id"), p.get("team_id"),
                     num(s, "points"), num(s, "points_ppr"), num(s, "points_half"),
                     num(s, "rush_att"), num(s, "rush_yds"), num(s, "rush_tds"),
                     num(s, "rec_rec"), num(s, "rec_yds"), num(s, "rec_tds"),
                     num(s, "pass_att"), num(s, "pass_yds"), num(s, "pass_tds"),
                     num(s, "pass_ints"), num(s, "fumbles"), now))
            print(f"  {pos}: {len(rows)} players")
            total += len(rows)
        conn.commit()
        print(f"\n  {total} projections stored for {args.season}")
        if not args.show:
            print(f"  next: python3 scripts/fp_projections.py --show RB")
        return

    # ---- the product view ------------------------------------------------
    pos = args.show.upper()
    rows = conn.execute("""SELECT * FROM fp_projections
                           WHERE season=? AND position=?""",
                        (args.season, pos)).fetchall()
    if not rows:
        sys.exit(f"  nothing stored for {pos}. Import first.")

    # our half: expected games, from availability, injuries and depth slot
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p5", str(ROOT / "scripts" / "project5.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    ours = {key(r["name"]): r for r in
            m.build(conn, args.our_season, m.roster(), m.crosswalk(conn))}

    out = []
    for r in rows:
        d = dict(r)
        if args.scoring == "league":
            pts = score(d)
        elif args.scoring == "half":
            pts = d["points_half"]
        elif args.scoring == "standard":
            pts = d["points"]
        else:
            pts = d["points_ppr"]
        o = ours.get(r["name_key"])
        games = o["games"] if o else None
        out.append({
            "name": r["player"], "team": r["team"], "pts": pts,
            "games": games,
            "adj": pts * (games / m.GAMES) if games else None,
            "note": (o or {}).get("note", ""),
            "rush": d["rush_att"], "rec": d["rec"],
            "ryd": d["rush_yds"], "recyd": d["rec_yds"],
            "td": d["rush_tds"] + d["rec_tds"] + d["pass_tds"],
        })
    out.sort(key=lambda x: -(x["adj"] if x["adj"] is not None else x["pts"]))

    label = {"ppr": "PPR", "half": "half PPR", "standard": "standard",
             "league": "your league"}[args.scoring]
    print(f"\n  {pos}, {args.season}, scored {label}")
    print(f"  Consensus volume from FantasyPros. Expected games are ours.\n")
    print(f"  {'#':<4}{'PLAYER':<22}{'TM':<4}{'PROJ':>7}{'G':>6}{'ADJ':>7}"
          f"{'ATT':>6}{'REC':>6}{'YDS':>7}{'TD':>6}  NOTE")
    shown = 0
    for i, r in enumerate(out[:args.top], 1):
        g = f"{r['games']:.1f}" if r["games"] is not None else "—"
        adj = f"{r['adj']:.0f}" if r["adj"] is not None else "—"
        yds = r["ryd"] + r["recyd"]
        print(f"  {i:<4}{r['name'][:22]:<22}{(r['team'] or ''):<4}"
              f"{r['pts']:>7.0f}{g:>6}{adj:>7}"
              f"{r['rush']:>6.0f}{r['rec']:>6.0f}{yds:>7.0f}{r['td']:>6.1f}"
              f"  {r['note']}")
        shown += 1

    have = sum(1 for r in out if r["games"] is not None)
    print(f"\n  {len(out)} players, {have} with an availability estimate.")
    print(f"\n  PROJ is a healthy season, which is what every published board")
    print(f"  gives you. ADJ is that discounted by the games we expect a")
    print(f"  player to play -- four seasons of availability, official injury")
    print(f"  reports, and where he sits on a depth chart. That column is the")
    print(f"  part nobody else has.")


if __name__ == "__main__":
    main()
