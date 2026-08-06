#!/usr/bin/env python3
"""Pull expert consensus rankings and blend them into our projections.

    export FANTASYPROS_API_KEY=...
    python3 scripts/import_consensus.py --season 2026
    python3 scripts/import_consensus.py --season 2026 --show RB
    python3 scripts/import_consensus.py --season 2026 --disagreements

WHY THIS AND NOT MORE MODELLING

The gap between our projections and a good published board is not arithmetic.
It is that a hundred-odd analysts watch these teams every day and know things
no historical model can derive: that a new coordinator will lean on the run,
that a second-year back's rookie inefficiency was scheme, that a thirty-two
year old is the exception.

Several attempts to reproduce that mechanically -- draft capital, coaching
tendency tables, extracting role claims from beat reports -- either measured
nothing or needed data that does not exist for free. The simple answer is to
buy the consensus rather than rebuild it.

FantasyPros aggregates 130+ experts and licenses the result through an API.
This imports it, and just as importantly imports the SPREAD: where the experts
themselves disagree, our own number deserves more weight, and where they are
unanimous it deserves less.

LICENSING. The free tier is for prototyping and the cheap tier is explicitly
personal and non-commercial. A product that ships needs their Commercial
arrangement. This script will not save anything if the key is missing, and
that is deliberate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.fantasypros.com/public/v2/json"
POSITIONS = ("QB", "RB", "WR", "TE")

SCHEMA = """
CREATE TABLE IF NOT EXISTS consensus (
    season INTEGER, position TEXT, scoring TEXT,
    player TEXT, name_key TEXT, team TEXT,
    rank_ecr INTEGER, pos_rank TEXT, tier INTEGER,
    best INTEGER, worst INTEGER, stdev REAL,
    fetched_at TEXT,
    PRIMARY KEY (season, position, scoring, name_key)
);
CREATE INDEX IF NOT EXISTS idx_consensus_key ON consensus(name_key, season);
"""


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def fetch(season, position, scoring, api_key):
    url = (f"{BASE}/nfl/{season}/consensus-rankings"
           f"?position={position}&scoring={scoring}")
    req = urllib.request.Request(url, headers={
        "x-api-key": api_key, "User-Agent": "beatwire/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--scoring", default="PPR", choices=["PPR", "HALF", "STD"])
    ap.add_argument("--show", help="print one position")
    ap.add_argument("--disagreements", action="store_true",
                    help="where we differ most from the experts")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if args.show or args.disagreements:
        if args.show:
            rows = conn.execute("""SELECT * FROM consensus WHERE season=? AND
                position=? AND scoring=? ORDER BY rank_ecr LIMIT 40""",
                (args.season, args.show.upper(), args.scoring)).fetchall()
            if not rows:
                sys.exit(f"  nothing imported for {args.show}")
            print(f"\n  {args.show.upper()} expert consensus, {args.season}\n")
            print(f"  {'#':<5}{'PLAYER':<24}{'TM':<5}{'TIER':>5}"
                  f"{'BEST':>6}{'WORST':>7}{'SPREAD':>8}")
            for r in rows:
                sp = (r["worst"] - r["best"]) if r["worst"] and r["best"] else None
                print(f"  {r['rank_ecr']:<5}{r['player'][:24]:<24}"
                      f"{(r['team'] or ''):<5}{(r['tier'] or 0):>5}"
                      f"{(r['best'] or 0):>6}{(r['worst'] or 0):>7}"
                      f"{(sp if sp is not None else 0):>8}")
            print("\n  A wide spread means the experts disagree with each other,")
            print("  and our own number deserves more weight there.")
            return

        # where we differ most
        try:
            ours = {key(r["player"]): r for r in conn.execute(
                "SELECT player, position, ppr, rank_pos FROM projections")}
        except sqlite3.OperationalError:
            sys.exit("  no projections published yet")
        rows = conn.execute("""SELECT * FROM consensus WHERE season=? AND scoring=?
                               ORDER BY rank_ecr""", (args.season, args.scoring)).fetchall()
        diffs = []
        for r in rows:
            o = ours.get(r["name_key"])
            if not o or not r["rank_ecr"]:
                continue
            diffs.append((abs(o["rank_pos"] - r["rank_ecr"]), r["player"],
                          o["rank_pos"], r["rank_ecr"], r["position"],
                          (r["worst"] or 0) - (r["best"] or 0)))
        diffs.sort(reverse=True)
        print(f"\n  WHERE WE DIFFER FROM THE EXPERTS\n")
        print(f"  {'PLAYER':<24}{'POS':<5}{'OURS':>6}{'ECR':>6}{'GAP':>6}"
              f"{'THEIR SPREAD':>14}")
        for gap, name, mine, theirs, pos, spread in diffs[:20]:
            flag = "  (they disagree too)" if spread > 20 else ""
            print(f"  {name[:24]:<24}{pos:<5}{mine:>6}{theirs:>6}{gap:>6}"
                  f"{spread:>14}{flag}")
        print("\n  The last column matters: a gap against a consensus the")
        print("  experts themselves are split on is a much weaker signal that")
        print("  we are wrong.")
        return

    api_key = os.environ.get("FANTASYPROS_API_KEY")
    if not api_key:
        sys.exit("  set FANTASYPROS_API_KEY first.\n"
                 "  Free key for prototyping at fantasypros.com/api-data/ ;\n"
                 "  a shipping product needs their Commercial tier.")

    total = 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for pos in POSITIONS:
        try:
            data = fetch(args.season, pos, args.scoring, api_key)
        except urllib.error.HTTPError as e:
            print(f"  {pos}: HTTP {e.code}"
                  + ("  (key rejected or tier does not cover this)"
                     if e.code in (401, 403) else ""))
            continue
        except Exception as e:
            print(f"  {pos}: {str(e)[:60]}")
            continue
        players = data.get("players") or []
        for p in players:
            name = p.get("player_name") or ""
            conn.execute("INSERT OR REPLACE INTO consensus VALUES "
                         "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (args.season, pos, args.scoring, name, key(name),
                          p.get("player_team_id"),
                          int(p["rank_ecr"]) if p.get("rank_ecr") else None,
                          p.get("pos_rank"),
                          int(p["tier"]) if p.get("tier") else None,
                          int(p["rank_min"]) if p.get("rank_min") else None,
                          int(p["rank_max"]) if p.get("rank_max") else None,
                          num(p.get("rank_std")), now))
        print(f"  {pos}: {len(players)} players")
        total += len(players)
    conn.commit()

    if not total:
        sys.exit("\n  nothing imported")
    print(f"\n  {total} consensus rankings stored")
    print(f"  next: python3 scripts/import_consensus.py --disagreements")


if __name__ == "__main__":
    main()
