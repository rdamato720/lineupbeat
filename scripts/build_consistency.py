#!/usr/bin/env python3
"""Derive auditable player consistency metrics from nflverse weekly stats."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "nfl_player_consistency_2025.json"
POSITIONS = {"QB", "RB", "WR", "TE"}
BOOM = {"QB": 20.0, "RB": 15.0, "WR": 15.0, "TE": 12.0}
BUST = {"QB": 12.0, "RB": 8.0, "WR": 8.0, "TE": 6.0}


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    at = (len(values) - 1) * q
    lo, hi = math.floor(at), math.ceil(at)
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - at) + values[hi] * (at - lo)


def metrics(points: list[float], position: str) -> dict:
    avg = statistics.fmean(points)
    sd = statistics.pstdev(points) if len(points) > 1 else 0.0
    # Consistency rewards stable weekly output and a full sample. It is a
    # descriptive score, never a projection or copied outside rating.
    cv = sd / max(avg, 1.0)
    sample = min(len(points), 17) / 17
    score = max(0.0, min(100.0, (1.0 - min(cv, 1.0)) * 80 + sample * 20))
    return {
        "games": len(points), "average": round(avg, 1),
        "median": round(statistics.median(points), 1),
        "standard_deviation": round(sd, 1),
        "floor_p25": round(percentile(points, .25), 1),
        "ceiling_p75": round(percentile(points, .75), 1),
        "best_game": round(max(points), 1), "worst_game": round(min(points), 1),
        "boom_rate": round(sum(p >= BOOM[position] for p in points) / len(points) * 100),
        "bust_rate": round(sum(p < BUST[position] for p in points) / len(points) * 100),
        "consistency_score": round(score),
    }


def build(db: Path, season: int) -> dict:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT player_id, player_name, position, team, week,
                  COALESCE(fantasy_points, 0) standard,
                  COALESCE(fantasy_points_ppr, 0) ppr
           FROM weekly_stats
           WHERE season=? AND season_type='REG' AND position IN ('QB','RB','WR','TE')
           ORDER BY player_id, week""", (season,)).fetchall()
    grouped = {}
    for row in rows:
        item = grouped.setdefault(row["player_id"], {
            "player_id": row["player_id"], "player_name": row["player_name"],
            "position": row["position"], "teams": set(), "ppr": [],
            "half_ppr": [], "non_ppr": [],
        })
        if row["team"]:
            item["teams"].add(row["team"])
        standard, ppr = float(row["standard"]), float(row["ppr"])
        item["non_ppr"].append(standard)
        item["ppr"].append(ppr)
        item["half_ppr"].append((standard + ppr) / 2)
    players = []
    for item in grouped.values():
        if not item["ppr"]:
            continue
        players.append({
            "player_id": item["player_id"], "player_name": item["player_name"],
            "position": item["position"], "teams": sorted(item["teams"]),
            "formats": {key: metrics(item[key], item["position"])
                        for key in ("ppr", "half_ppr", "non_ppr")},
        })
    players.sort(key=lambda p: (p["position"], p["player_name"]))
    return {
        "season": season, "source": "nflverse weekly player stats",
        "method": "regular-season weekly scoring; population standard deviation; 25th/75th percentile floor and ceiling",
        "players": players,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    payload = build(Path(args.db), args.season)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # This is a generated browser payload. Keep it deterministic and compact so
    # the comparison tool does not add unnecessary weight to the repository.
    out.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"  wrote {out} ({len(payload['players'])} players)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
