#!/usr/bin/env python3
"""Adapter for the immutable, validated college Week 1 projection release."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from decision_engine import (DecisionContext, closest_calls,
                             strongest_projection_edges)

ROOT = Path(__file__).resolve().parent.parent
COLLEGE = ROOT / "data" / "college"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_weekly() -> dict:
    config = json.loads((COLLEGE / "config.json").read_text())
    release = COLLEGE / config["activeCollegeWeeklyProjectionVersion"]
    manifest = json.loads((release / "manifest.json").read_text())
    source = release / "college_week1_site_projections_2026.json"
    expected = manifest["files"][source.name]
    if source.stat().st_size != expected["bytes"] or _digest(source) != expected["sha256"]:
        raise ValueError("college weekly projection artifact does not match its manifest")
    if manifest.get("qa_status") != "PASS":
        raise ValueError("college weekly projection release has not passed QA")
    raw = json.loads(source.read_text())
    if raw.get("season") != 2026 or raw.get("week") != 1:
        raise ValueError("unexpected college weekly horizon")
    players = []
    for row in raw["players"]:
        players.append({
            "id": row["id"], "name": row["name"], "team": row["team"],
            "team_id": row["teamId"], "position": row["pos"],
            "conference": None, "photo": None, "team_logo": None, "adp": None,
            "opponent": row.get("opponent"),
            "formats": {"yahoo": {
                "projected_points": row["pts"], "overall_rank": row["overallRank"],
                "position_rank": row["rank"],
            }},
        })
    if len(players) != raw["counts"]["players"]:
        raise ValueError("college player count mismatch")
    ids = {p["id"] for p in players}
    if len(ids) != len(players) or any(not p["id"] for p in players):
        raise ValueError("college stable player identities are incomplete")
    context = DecisionContext("weekly", 2026, "yahoo", week=1)
    return {
        "sport": "college", "mode": "weekly", "season": 2026, "week": 1,
        "title": "College Week 1 Decision Room",
        "projection_horizon": "Week 1 projections",
        "scoring_format": "yahoo", "scoring_label": raw["scoring"],
        "updated_at": raw["generatedAt"], "adp_available": False,
        "conference_available": False, "counts": raw["counts"],
        "identity_coverage": {"resolved": len(ids), "total": len(players)},
        "players": players,
        "closest_calls": [_summary(r) for r in closest_calls(
            players, "yahoo", context=context)],
        "strongest_edges": [_summary(r) for r in strongest_projection_edges(
            players, "yahoo", context=context)],
        "available_formats": ["yahoo"],
    }


def _summary(result: dict) -> dict:
    a = result["winner"] or result["player_a"]
    b = result["runner_up"] or result["player_b"]
    return {"a": a["id"], "b": b["id"], "gap": result["gap"],
            "confidence": result["confidence"],
            "winner": result["winner"]["id"] if result["winner"] else None}
