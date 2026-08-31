#!/usr/bin/env python3
"""Validated data adapter for the preseason Decision Room."""

from __future__ import annotations

import json
import re
from pathlib import Path

import build_ranking_formats as rankings

ROOT = Path(__file__).resolve().parent.parent
PROJECTIONS = ROOT / "data" / "projections.xlsx"
IDENTITIES = ROOT / "sources" / "wire_players.json"
DISPLAY = ROOT / "data" / "wire_display_fantasy.json"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_season(season: int = 2026) -> dict:
    if season != 2026:
        raise ValueError("only the validated 2026 projection set is available")
    identities = json.loads(IDENTITIES.read_text())
    if identities.get("season") != season:
        raise ValueError("identity season does not match projection season")
    by_key = {(p["full_name"], p["team"], p["position"]): p
              for p in identities["players"]}
    display = json.loads(DISPLAY.read_text())["players"]
    raw = rankings.read_projection_formats(PROJECTIONS)
    ranked = {fmt: rankings.rank(raw[fmt], fmt)[0]
              for fmt in ("ppr", "non_ppr")}
    # Half-PPR has its own validated ranking artifact, generated from the same
    # workbook and carrying its source hash and timestamp.
    half_payload = json.loads((ROOT / "data" / "nfl_rankings_2026.json").read_text())
    ranked["half_ppr"] = half_payload["players"]
    players: dict[str, dict] = {}
    for fmt, rows in ranked.items():
        for row in rows:
            if row.get("overall_rank") is None or row.get("position_rank") is None:
                continue
            key = (row["player_name"], row["team"], row["position"])
            identity = by_key.get(key)
            if identity is None:
                continue
            pid = identity["player_id"]
            show = display.get(pid, {})
            player = players.setdefault(pid, {
                "id": pid, "slug": slug(row["player_name"]), "name": row["player_name"],
                "team": row["team"], "position": row["position"], "formats": {},
                "adp": show.get("adp"),
                "photo": _photo(show),
                "team_logo": f"https://a.espncdn.com/i/teamlogos/nfl/500/{row['team'].lower()}.png",
            })
            player["formats"][fmt] = {
                "projected_points": round(float(row["projected_points"]), 1),
                "overall_rank": int(row["overall_rank"]),
                "position_rank": int(row["position_rank"]),
            }
    complete = [p for p in players.values()
                if all(fmt in p["formats"] for fmt in ("ppr", "half_ppr", "non_ppr"))]
    complete.sort(key=lambda p: (p["position"], p["formats"]["half_ppr"]["position_rank"], p["name"]))
    timestamp = rankings.source_updated(PROJECTIONS)
    return {"mode": "season", "season": season, "week": None,
            "updated_at": timestamp.isoformat(), "players": complete}


def _photo(display: dict) -> str | None:
    if display.get("espn"):
        return f"https://a.espncdn.com/i/headshots/nfl/players/full/{display['espn']}.png"
    ref = str(display.get("player_ref") or "")
    if ref.startswith("nfl-"):
        return f"https://sleepercdn.com/content/nfl/players/thumb/{ref[4:]}.jpg"
    return None
