#!/usr/bin/env python3
"""Refresh the Wire player registry from the public nflverse roster.

    python3 scripts/wire_players_refresh.py
    python3 scripts/wire_players_refresh.py --dry-run
    python3 scripts/wire_players_refresh.py --check      # resolve a few names

Identity only: a stable id, the names a reporter might use, team, position,
status and season. Nothing fantasy-derived, at any depth, ever.

It never reads `rosters/nfl.csv`. Not to seed, not to compare, not to fill a
gap -- that file carries ADP, and the Wire may not touch fantasy data even
while building a registry.

A failed download or a file that does not validate leaves the existing
registry untouched. Yesterday's roster is a good answer; half of today's is
not an answer at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import players
from wire.store import WireStore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=players.SOURCE_URL)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="resolve a handful of names against the registry")
    args = ap.parse_args()

    if args.check:
        reg = players.load()
        if not reg.players:
            sys.exit("  no registry yet; run without --check first")
        print(f"  registry {reg.version}  {len(reg.players)} players  "
              f"fetched {reg.source_fetched_at}")
        # The pairs that matter: a name that must resolve, a name that must
        # not, and the misheard-caption case the whole design exists for.
        for name, team, pos in [("Jahmyr Gibbs", "DET", "RB"),
                                ("Marvin Harrison", "ARI", "WR"),
                                ("Jayden Reed", "GB", "WR"),
                                ("Jarran Reed", "SEA", "DL"),
                                ("Josh Allen", "BUF", "QB"),
                                ("Josh Allen", "JAX", "LB"),
                                ("Nobody At All", "TB", "WR")]:
            hits, how = reg.resolve(name, team, pos)
            verdict = (f"{hits[0].player_id} {hits[0].position}" if len(hits) == 1
                       else f"{len(hits)} matches -> MANUAL_REVIEW_ONLY")
            print(f"    {name:<20}{team:<5}{pos:<4}{how:<24}{verdict}")
        loose, how = reg.resolve("Josh Allen")
        print(f"    {'Josh Allen':<20}{'-':<5}{'-':<4}{how:<24}"
              f"{len(loose)} matches -> MANUAL_REVIEW_ONLY")
        return 0

    print(f"  source {args.url}")
    try:
        text = players.fetch(args.url)
    except Exception as e:
        print(f"  download failed: {type(e).__name__}: {str(e)[:80]}")
        print("  keeping the existing registry")
        return 1

    payload = players.build_from_csv(text, args.url)
    problems = players.validate(payload)
    print(f"  {payload['player_count']} players, "
          f"registry {payload['registry_version']}")
    from collections import Counter
    pos = Counter(p["position"] for p in payload["players"])
    cand = sum(1 for p in payload["players"] if p["fantasy_candidate"])
    ctx = sum(1 for p in payload["players"] if p["context_only"])
    print(f"  {cand} fantasy candidates (QB/RB/WR/TE), "
          f"{ctx} linemen kept as context, "
          f"{payload['player_count'] - cand - ctx} others recognised only")
    print("  top positions: " + "  ".join(f"{k} {v}" for k, v in pos.most_common(6)))

    if problems:
        for p in problems[:5]:
            print(f"    ! {p}")
        print("  refusing to replace the registry")
        return 1
    if args.dry_run:
        print("  --dry-run, nothing written")
        return 0

    prior = players.load()
    players.write_atomic(payload)
    print(f"  wrote {players.REGISTRY.relative_to(players.ROOT)} "
          f"({players.REGISTRY.stat().st_size:,} bytes)")
    if prior.version and prior.version != payload["registry_version"]:
        print(f"  registry moved {prior.version} -> {payload['registry_version']}")

    # Mirror into the Wire database so resolution needs no file read.
    store = WireStore()
    store.replace_players(payload)
    print(f"  {payload['player_count']} rows in wire_players")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
