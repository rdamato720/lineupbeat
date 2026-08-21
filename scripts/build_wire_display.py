#!/usr/bin/env python3
"""Generate data/wire_display_fantasy.json: display-only fields by player_id.

    python3 scripts/build_wire_display.py --build

The Wire may not read rosters/nfl.csv or the projection board -- that file
carries ADP, and the whole point of the Wire's separate registry is that no
fantasy number is anywhere near evidence interpretation. So the join is
built here, upstream, and the Wire package reads only the flat result.

Same shape as the relevance registry and for the same reason: a value that
cannot be reached cannot leak into a decision.

The output is keyed by stable player_id. There is no name in it that anything
downstream could match on.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import players as pl

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "data" / "nfl_rankings_2026.json"
ROSTER = ROOT / "rosters" / "nfl.csv"
OUT = ROOT / "data" / "wire_display_fantasy.json"


def build() -> dict:
    reg = pl.load()
    rows: dict = {}

    if BOARD.exists():
        for p in json.loads(BOARD.read_text()).get("players", []):
            hits, _ = reg.resolve(p.get("player_name", ""), p.get("team", ""),
                                  p.get("position", ""))
            if len(hits) != 1:
                continue
            e = rows.setdefault(hits[0].player_id, {})
            if p.get("position_rank"):
                e["position_rank"] = f"{p['position']}{p['position_rank']}"
            if p.get("projected_points") is not None:
                e["projected_points"] = round(float(p["projected_points"]), 1)

    if ROSTER.exists():
        with ROSTER.open() as f:
            for r in csv.DictReader(f):
                hits, _ = reg.resolve(r.get("name", ""), r.get("team", ""),
                                      r.get("position", ""))
                if len(hits) != 1:
                    continue
                # Identity first, and unconditionally. The site keys its
                # headshots and its PLAYERS map on this id, not on the gsis
                # id the Wire uses, so without the bridge every Wire card
                # falls back to initials -- which is what shipped. It is
                # written before the ADP guards below on purpose: a player
                # with no draft slot still has a face.
                site_id = (r.get("id") or "").strip()
                if site_id:
                    e = rows.setdefault(hits[0].player_id, {})
                    e["player_ref"] = site_id
                    if (r.get("espn_id") or "").strip():
                        e["espn"] = r["espn_id"].strip()

                # Only the ADP column. An empty ADP used to fall through to
                # the roster's "rank", which is a different number entirely:
                # Chris Blair was shown an ADP of 635 that was his rank.
                raw = (r.get("adp") or "").strip()
                if not raw:
                    continue
                try:
                    adp = float(raw)
                except ValueError:
                    continue
                if adp <= 0 or adp > 400:
                    continue          # sentinel values are not a draft slot
                rows.setdefault(hits[0].player_id, {})["adp"] = adp

    return {
        "schema_version": "display-v2",
        "note": ("Display only, keyed by stable player_id. Never read during "
                 "evidence interpretation, relevance assessment or review. "
                 "Carries no player name, so nothing downstream can match on "
                 "one. player_ref/espn are image identifiers for the "
                 "homepage renderer and carry no fantasy value."),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(rows),
        "players": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()
    payload = build()
    with_adp = sum(1 for v in payload["players"].values() if "adp" in v)
    with_rank = sum(1 for v in payload["players"].values() if "position_rank" in v)
    with_pts = sum(1 for v in payload["players"].values() if "projected_points" in v)
    print(f"  {payload['count']} players with display data")
    print(f"    adp {with_adp}   positional rank {with_rank}   "
          f"projected points {with_pts}")
    if any("player_name" in v or "name" in v for v in payload["players"].values()):
        print("  ! a name leaked into the display file; refusing to write")
        return 1
    if args.build:
        OUT.write_text(json.dumps(payload, indent=1) + "\n")
        print(f"  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
