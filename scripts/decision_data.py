#!/usr/bin/env python3
"""Validated data adapter for the preseason Decision Room."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

import build_ranking_formats as rankings

ROOT = Path(__file__).resolve().parent.parent
PROJECTIONS = ROOT / "data" / "projections.xlsx"
IDENTITIES = ROOT / "sources" / "wire_players.json"
DISPLAY = ROOT / "data" / "wire_display_fantasy.json"
HISTORY = ROOT / "data" / "nfl_player_consistency_2025.json"
EDITORIAL = ROOT / "data" / "comparison_editorial_opinions.json"
ADP_META = ROOT / "rosters" / "adp_meta.json"
WEEK1 = ROOT / "data" / "week1" / "2026" / "v1.0" / "nfl_week1_projections.json"
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
WEEKLY_RECOMMENDATION_STATE = {
    "enabled": False,
    "label": "No reliable call",
    "reason": (
        "Week 1 recommendations are disabled for this release. Forecasts are "
        "available for inspection, but the trusted season release does not "
        "authorize lineup recommendations."
    ),
}


def normalize_player_name(value: str) -> str:
    """Normalize only mechanical name variants; never perform fuzzy matching."""
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    tokens = re.sub(r"[^a-z0-9\s]", "", ascii_name.lower()).split()
    while tokens and tokens[-1] in NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def identity_index(players: list[dict]) -> dict[tuple[str, str, str], dict]:
    """Index stable identities by normalized name plus exact team and position."""
    index: dict[tuple[str, str, str], dict] = {}
    for player in players:
        key = (normalize_player_name(player["full_name"]), player["team"], player["position"])
        if key in index:
            other = index[key]
            raise ValueError(
                "ambiguous normalized identity: "
                f"{other['player_id']} {other['full_name']} and "
                f"{player['player_id']} {player['full_name']} for {key}"
            )
        index[key] = player
    return index


def load_weekly(season: int = 2026, week: int = 1) -> dict:
    """Load the immutable Lineup Beat-owned weekly projection artifact."""
    if (season, week) != (2026, 1):
        raise ValueError("only the validated 2026 Week 1 artifact is available")
    payload = json.loads(WEEK1.read_text())
    if (payload.get("mode"), payload.get("season"), payload.get("week")) != ("weekly", season, week):
        raise ValueError("unexpected NFL weekly projection identity")
    population = payload.get("population", {})
    if population.get("projection_source") != (
        population.get("identity_resolved", 0) + population.get("identity_unresolved", 0)
    ):
        raise ValueError("NFL projection-source identity coverage does not reconcile")
    if population.get("ranked_production") != (
        len(payload.get("players", [])) + len(payload.get("excluded_players", []))
    ):
        raise ValueError("NFL ranked production coverage does not reconcile")
    if population.get("identity_resolved") != (
        population.get("ranked_production", 0)
        + population.get("identity_resolved_not_ranked", 0)
    ):
        raise ValueError("NFL resolved projection-source population does not reconcile")
    return {**payload, "recommendation_state": WEEKLY_RECOMMENDATION_STATE}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_season(season: int = 2026) -> dict:
    if season != 2026:
        raise ValueError("only the validated 2026 projection set is available")
    identities = json.loads(IDENTITIES.read_text())
    if identities.get("season") != season:
        raise ValueError("identity season does not match projection season")
    by_key = identity_index(identities["players"])
    display = json.loads(DISPLAY.read_text())["players"]
    history_payload = json.loads(HISTORY.read_text())
    history = {row["player_id"]: row["formats"]
               for row in history_payload["players"]}
    editorial_payload = _editorial(by_key)
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
            key = (normalize_player_name(row["player_name"]), row["team"], row["position"])
            identity = by_key.get(key)
            if identity is None:
                continue
            pid = identity["player_id"]
            show = display.get(pid, {})
            player = players.setdefault(pid, {
                "id": pid, "slug": slug(row["player_name"]), "name": row["player_name"],
                "team": row["team"], "position": row["position"], "formats": {},
                "adp": show.get("adp"),
                "history": history.get(pid, {}),
                "history_season": history_payload["season"] if pid in history else None,
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
    adp_meta = json.loads(ADP_META.read_text())
    source_rows = raw["ppr"]
    source_keys = [{"name": row["player_name"], "team": row["team"],
                    "position": row["position"]} for row in source_rows]
    unresolved = [row for row in source_keys
                  if (normalize_player_name(row["name"]), row["team"], row["position"]) not in by_key]
    return {"mode": "season", "season": season, "week": None,
            "updated_at": timestamp.isoformat(), "players": complete,
            "population": {
                "projection_source": len(source_keys),
                "identity_resolved": len(source_keys) - len(unresolved),
                "identity_unresolved": len(unresolved),
                "ranked_production": len(complete),
                "identity_resolved_not_ranked": (
                    len(source_keys) - len(unresolved) - len(complete)
                ),
            },
            "unresolved_players": unresolved,
            "identity_method": (
                "suffix- and punctuation-normalized name plus exact team and position; "
                "no fuzzy matching; ambiguity is fatal"
            ),
            "available_formats": ["ppr", "half_ppr", "non_ppr"],
            "editorial_opinions": editorial_payload,
            "schedule_sos_available": False,
            "schedule_sos_required_artifact": (
                "data/nfl_position_sos_2026.json: validated 2026 team/week/opponent "
                "schedule joined to position-specific adjusted fantasy points allowed, "
                "with source timestamps and QA metadata"),
            "sources": {
                "projections": {"label": "Lineup Beat 2026 projections",
                                "updated_at": timestamp.isoformat()},
                "ranks": {"label": "Lineup Beat 2026 ranks",
                          "updated_at": half_payload["metadata"]["generated_at"]},
                "adp": {"label": f"{adp_meta['drafts']:,} twelve-team PPR drafts",
                        "updated_at": adp_meta["end"], "start_at": adp_meta["start"]},
                "history": {"label": history_payload["source"],
                            "updated_at": "2025 regular season"},
                "editorial": {"label": "Lineup Beat historical ranking opinion",
                              "updated_at": editorial_payload[0]["evidence_date"]
                              if editorial_payload else None},
                "schedule_sos": {"label": "Position-specific strength of schedule",
                                 "updated_at": None},
            }}


def _editorial(by_key: dict[tuple[str, str, str], dict]) -> list[dict]:
    payload = json.loads(EDITORIAL.read_text())
    if payload.get("schema_version") != "comparison-editorial-opinions-v1":
        raise ValueError("unexpected comparison editorial schema")
    if not payload.get("historical_only") or len(payload.get("opinions", [])) != 6:
        raise ValueError("comparison editorial layer must contain six historical opinions")
    for source in payload.get("source_artifacts", []):
        path = ROOT / source["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError(f"editorial provenance hash mismatch: {path.name}")
    resolved = []
    for row in payload["opinions"]:
        subject = row["subject"]
        other = row["preferred_over"]
        subject_identity = by_key.get((normalize_player_name(subject["name"]),
                                       subject["team"], subject["position"]))
        other_identity = by_key.get((normalize_player_name(other["name"]),
                                     other["team"], other["position"]))
        if not subject_identity or not other_identity:
            raise ValueError(f"unresolved comparison editorial identity: {row['opinion_id']}")
        resolved.append({
            **row,
            "subject_id": subject_identity["player_id"],
            "preferred_over_id": other_identity["player_id"],
            "evidence_date": payload["evidence_date"],
            "historical_only": True,
            "source_file": payload["source_artifacts"][1]["file"],
            "source_sheet": payload["source_artifacts"][1]["sheet"],
        })
    return resolved


def _photo(display: dict) -> str | None:
    if display.get("espn"):
        return f"https://a.espncdn.com/i/headshots/nfl/players/full/{display['espn']}.png"
    ref = str(display.get("player_ref") or "")
    if ref.startswith("nfl-"):
        return f"https://sleepercdn.com/content/nfl/players/thumb/{ref[4:]}.jpg"
    return None
