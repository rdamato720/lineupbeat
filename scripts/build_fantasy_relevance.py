#!/usr/bin/env python3
"""Generate data/wire_fantasy_relevance.json. Identity fields only.

    python3 scripts/build_fantasy_relevance.py --build

Position eligibility and fantasy relevance are different questions, and
treating them as one is what published a backup quarterback taking routine
second-team reps. Anthony Richardson resolved cleanly to a QB in the
registry, so every check passed; nothing asked whether a report about him
could matter to a 2026 redraft roster.

This file answers that, once, upstream. It is built here from the projection
board so the Wire never opens that board during interpretation -- the Wire
reads tiers and reasons, never numbers. No ADP, no projected points, no
rank, no projected statistic appears in the output, and a test walks the
file to prove it.

The boundaries are configurable because they are editorial, not derived.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import players as pl

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "data" / "nfl_rankings_2026.json"
OUT = ROOT / "data" / "wire_fantasy_relevance.json"

ROSTERABLE, WATCHLIST, CONTINGENT, NOT_RELEVANT = (
    "ROSTERABLE", "WATCHLIST", "CONTINGENT", "NOT_RELEVANT")
TIERS = {ROSTERABLE, WATCHLIST, CONTINGENT, NOT_RELEVANT}

# Broad redraft boundaries. Editorial, so they live here and are recorded in
# the output rather than being inferred later from the data.
DEFAULT_BOUNDS = {"QB": 24, "RB": 48, "WR": 60, "TE": 24}
# Beyond the boundary but close enough to matter if the role moves.
WATCH_MARGIN = 1.4

# Nothing derived from a number may leave this script.
FORBIDDEN = ("adp", "rank", "ranking", "points", "projected", "projection",
             "vorp", "score", "tier_points", "ppr", "value", "ownership")


def load_board() -> list[dict]:
    if not BOARD.exists():
        return []
    return json.loads(BOARD.read_text()).get("players", [])


def build(bounds: dict) -> dict:
    reg = pl.load()
    board = load_board()
    by_pos: dict = {}
    for p in board:
        by_pos.setdefault(p.get("position", ""), []).append(p)
    # The board is already ordered within a position by the rankings build;
    # position order is all that is read, and the number never leaves here.
    rows = []
    for pos, limit in bounds.items():
        pool = by_pos.get(pos, [])
        for i, p in enumerate(pool):
            name = p.get("player_name") or p.get("name") or ""
            team = p.get("team", "")
            hits, how = reg.resolve(name, team, pos)
            if len(hits) != 1:
                continue                  # no exact identity, no entry
            player = hits[0]
            if i < limit:
                tier, why = ROSTERABLE, (
                    f"inside the {pos} redraft boundary of {limit} on the "
                    f"current board")
            elif i < int(limit * WATCH_MARGIN):
                tier, why = WATCHLIST, (
                    f"just outside the {pos} boundary of {limit}; a role "
                    f"change would make him relevant")
            else:
                continue                  # deep players qualify by evidence
            rows.append({
                "player_id": player.player_id,
                "team": player.team,
                "position": player.position,
                "relevance_tier": tier,
                "relevance_reason": why,
                "effective_at": datetime.now(timezone.utc)
                .replace(microsecond=0).isoformat(),
                "registry_version": reg.version,
            })
    return {
        "schema_version": "relevance-v1",
        "note": ("Identity and tier only. No ADP, rank, projected points or "
                 "projected statistic appears here or reaches the model. A "
                 "player absent from this file may still enter review when "
                 "the evidence itself establishes a material opportunity."),
        "boundaries": bounds,
        "watch_margin": WATCH_MARGIN,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(rows),
        "players": rows,
    }


def validate(payload: dict) -> list[str]:
    bad = []
    allowed = {"player_id", "team", "position", "relevance_tier",
               "relevance_reason", "effective_at", "registry_version"}
    for r in payload.get("players", []):
        extra = set(r) - allowed
        if extra:
            bad.append(f"{r.get('player_id')}: extra field(s) {sorted(extra)}")
        if r.get("relevance_tier") not in TIERS:
            bad.append(f"{r.get('player_id')}: bad tier {r.get('relevance_tier')}")
        if not r.get("player_id"):
            bad.append("a row with no player_id")

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if any(f == str(k).lower() for f in FORBIDDEN):
                    bad.append(f"forbidden field {path}{k!r}")
                walk(v, f"{path}{k}.")
        elif isinstance(node, list):
            for x in node[:80]:
                walk(x, path)
    walk({"players": payload.get("players", [])})
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    for pos, n in DEFAULT_BOUNDS.items():
        ap.add_argument(f"--{pos.lower()}", type=int, default=n)
    args = ap.parse_args()
    bounds = {p: getattr(args, p.lower()) for p in DEFAULT_BOUNDS}

    payload = build(bounds)
    problems = validate(payload)
    from collections import Counter
    print(f"  boundaries {bounds}")
    print(f"  {payload['count']} players")
    print(f"  {dict(Counter(r['relevance_tier'] for r in payload['players']))}")
    print(f"  {dict(Counter(r['position'] for r in payload['players']))}")
    if problems:
        for p in problems[:5]:
            print(f"    ! {p}")
        print("  refusing to write")
        return 1
    if args.build:
        OUT.write_text(json.dumps(payload, indent=1) + "\n")
        print(f"  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
