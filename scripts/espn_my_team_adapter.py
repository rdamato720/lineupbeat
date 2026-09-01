#!/usr/bin/env python3
"""ESPN browser-extension payload adapter.

The input is visible roster data captured locally by the extension. This
adapter explicitly selects allowed fields and therefore cannot carry manager
identity, credentials, cookies, or session tokens into the normalized layer.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from my_team_adapter import SCHEMA_VERSION, SUPPORTED_POSITIONS

RESERVE_SLOTS = {"IR", "RES", "RESERVE", "TAXI"}
BENCH_SLOTS = {"BE", "BENCH"}
FLEX_ALLOWED = {
    "FLEX": ["RB", "WR", "TE"],
    "RB/WR/TE": ["RB", "WR", "TE"],
    "WR/RB/TE": ["RB", "WR", "TE"],
    "RB/WR": ["RB", "WR"],
    "WR/RB": ["RB", "WR"],
    "WR/TE": ["WR", "TE"],
    "RB/TE": ["RB", "TE"],
    "OP": ["QB", "RB", "WR", "TE"],
    "SUPERFLEX": ["QB", "RB", "WR", "TE"],
}


def scoring_format(settings: dict[str, Any]) -> tuple[str, float]:
    value = float(settings.get("receptionPoints") or 0)
    if value >= 0.75:
        return "ppr", value
    if value >= 0.25:
        return "half_ppr", value
    return "non_ppr", value


def adapt_espn_payload(raw: dict[str, Any]) -> dict[str, Any]:
    league = raw.get("league") or {}
    team = raw.get("team") or {}
    settings = league.get("scoringSettings") or {}
    fmt, reception_points = scoring_format(settings)
    grouped = {"starters": [], "bench": [], "reserve": []}
    slot_counts: Counter[str] = Counter()
    for source in raw.get("roster") or []:
        position = str(source.get("position") or "").upper()
        slot = str(source.get("lineupSlot") or "").upper()
        if slot in RESERVE_SLOTS:
            group = "reserve"
        elif slot in BENCH_SLOTS:
            group = "bench"
        else:
            group = "starters"
            slot_counts[slot or position] += 1
        supported = position in SUPPORTED_POSITIONS
        grouped[group].append({
            "providerPlayerId": str(source.get("providerPlayerId") or ""),
            "name": str(source.get("name") or "").strip(),
            "providerTeam": str(source.get("team") or "").upper() or None,
            "position": position,
            "lineupSlot": slot or position,
            "lineupGroup": group.rstrip("s"),
            "identity": None,
            "matchStatus": "pending" if supported else "unsupported_position",
            "unresolvedReason": None if supported else f"{position or 'Unknown position'} is not supported by the Week 1 model",
        })
    slots = []
    for slot, count in sorted(slot_counts.items()):
        slots.append({
            "slotId": slot,
            "label": slot,
            "allowedPositions": FLEX_ALLOWED.get(slot, [slot] if slot in SUPPORTED_POSITIONS else []),
            "count": count,
        })
    return {
        "schemaVersion": SCHEMA_VERSION,
        "provider": "espn",
        "connectionType": "browser_extension",
        "league": {
            "id": str(league.get("id") or "unknown"),
            "name": str(league.get("name") or "ESPN league"),
            "season": int(league.get("season") or 2026),
            "scoring": {"format": fmt, "receptionPoints": reception_points},
        },
        "team": {
            "id": str(team.get("id") or "unknown"),
            "name": str(team.get("name") or "My ESPN team"),
        },
        "startingLineupSlots": slots,
        "roster": grouped,
    }
