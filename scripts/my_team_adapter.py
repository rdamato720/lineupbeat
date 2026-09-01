#!/usr/bin/env python3
"""Provider-neutral normalized league contract for development-only My Team.

This module deliberately knows nothing about ESPN. Provider adapters must emit
this shape before identity matching or lineup analysis can run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "lineupbeat-league-v1"
SUPPORTED_POSITIONS = {"QB", "RB", "WR", "TE"}
ROSTER_GROUPS = ("starters", "bench", "reserve")
MATCH_STATUSES = {
    "pending", "matched_provider_id", "matched_identity",
    "unresolved_identity", "ambiguous_identity", "unsupported_position",
}


@dataclass(frozen=True)
class ContractError:
    path: str
    message: str


def validate_normalized_league(payload: dict[str, Any]) -> list[ContractError]:
    """Return all contract errors without applying provider-specific logic."""
    errors: list[ContractError] = []

    def required(obj: Any, key: str, path: str, expected: type) -> Any:
        value = obj.get(key) if isinstance(obj, dict) else None
        if not isinstance(value, expected) or (expected is str and not value.strip()):
            errors.append(ContractError(f"{path}.{key}", f"must be {expected.__name__}"))
        return value

    if not isinstance(payload, dict):
        return [ContractError("$", "must be object")]
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(ContractError("$.schemaVersion", f"must equal {SCHEMA_VERSION}"))
    required(payload, "provider", "$", str)
    required(payload, "connectionType", "$", str)
    league = required(payload, "league", "$", dict)
    team = required(payload, "team", "$", dict)
    slots = required(payload, "startingLineupSlots", "$", list)
    roster = required(payload, "roster", "$", dict)
    if isinstance(league, dict):
        for key in ("id", "name"):
            required(league, key, "$.league", str)
        if not isinstance(league.get("season"), int):
            errors.append(ContractError("$.league.season", "must be int"))
        scoring = required(league, "scoring", "$.league", dict)
        if isinstance(scoring, dict) and scoring.get("format") not in {
            "ppr", "half_ppr", "non_ppr"
        }:
            errors.append(ContractError("$.league.scoring.format", "unsupported format"))
    if isinstance(team, dict):
        required(team, "id", "$.team", str)
        required(team, "name", "$.team", str)
    if isinstance(slots, list):
        for index, slot in enumerate(slots):
            path = f"$.startingLineupSlots[{index}]"
            required(slot, "slotId", path, str)
            required(slot, "label", path, str)
            allowed = required(slot, "allowedPositions", path, list)
            if isinstance(allowed, list) and not all(isinstance(p, str) for p in allowed):
                errors.append(ContractError(f"{path}.allowedPositions", "must contain strings"))
            if not isinstance(slot.get("count"), int) or slot.get("count", 0) < 1:
                errors.append(ContractError(f"{path}.count", "must be positive int"))
    if isinstance(roster, dict):
        for group in ROSTER_GROUPS:
            players = required(roster, group, "$.roster", list)
            if not isinstance(players, list):
                continue
            for index, player in enumerate(players):
                path = f"$.roster.{group}[{index}]"
                for key in ("providerPlayerId", "name", "position", "lineupSlot",
                            "lineupGroup", "matchStatus"):
                    required(player, key, path, str)
                if player.get("lineupGroup") != group.rstrip("s"):
                    errors.append(ContractError(f"{path}.lineupGroup", f"must equal {group.rstrip('s')}"))
                if player.get("matchStatus") not in MATCH_STATUSES:
                    errors.append(ContractError(f"{path}.matchStatus", "unsupported status"))
                status = player.get("matchStatus")
                reason = player.get("unresolvedReason")
                if status in {"unresolved_identity", "ambiguous_identity", "unsupported_position"}:
                    if not isinstance(reason, str) or not reason.strip():
                        errors.append(ContractError(f"{path}.unresolvedReason", "required for unmatched player"))
                identity = player.get("identity")
                if status in {"matched_provider_id", "matched_identity"}:
                    if not isinstance(identity, dict):
                        errors.append(ContractError(f"{path}.identity", "required for matched player"))
                    else:
                        for key in ("playerId", "name", "team", "position"):
                            required(identity, key, f"{path}.identity", str)
    return errors


def classify_projection_gap(a_points: float, b_points: float) -> str:
    """Use the weekly deterministic decision thresholds, never probability."""
    gap = abs(float(a_points) - float(b_points))
    reference = max(abs(float(a_points)), abs(float(b_points)), 0.1)
    percentage = gap / reference * 100
    if gap <= 0.5 or percentage <= 3:
        return "Toss-Up"
    if gap < 2 or percentage < 10:
        return "Lean"
    if gap < 4 or percentage < 20:
        return "Edge"
    return "Strong Edge"
