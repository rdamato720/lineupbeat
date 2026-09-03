#!/usr/bin/env python3
"""Adapter for the immutable, validated college Week 1 projection release."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from decision_engine import (DecisionContext, closest_calls,
                             strongest_projection_edges)
from college_team_logos import load_registry

ROOT = Path(__file__).resolve().parent.parent
COLLEGE = ROOT / "data" / "college"
MARKET_RELEASE = COLLEGE / "2026" / "week-1" / "market-context"
MARKET_SOURCE = MARKET_RELEASE / "therundown-2026-09-03.json"


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
    schedule = release / "provenance" / "college_week1_schedule_2026.json"
    schedule_expected = manifest["files"][schedule.name]
    if (schedule.stat().st_size != schedule_expected["bytes"] or
            _digest(schedule) != schedule_expected["sha256"]):
        raise ValueError("college weekly schedule artifact does not match its manifest")
    schedule_payload = json.loads(schedule.read_text())
    market_manifest = json.loads((MARKET_RELEASE / "manifest.json").read_text())
    market_expected = market_manifest["files"][MARKET_SOURCE.name]
    if (MARKET_SOURCE.stat().st_size != market_expected["bytes"]
            or _digest(MARKET_SOURCE) != market_expected["sha256"]):
        raise ValueError("college market-context artifact does not match its manifest")
    market_payload = json.loads(MARKET_SOURCE.read_text())
    if (market_payload.get("schemaVersion")
            != "lineupbeat-college-week1-market-context-v1"
            or market_payload.get("season") != 2026
            or market_payload.get("week") != 1
            or market_payload.get("coverage", {}).get("modeled_teams") != 64
            or market_payload.get("coverage", {}).get(
                "teams_with_spread_and_total") != 64):
        raise ValueError("college market-context scope is incomplete")
    market_teams = market_payload["teams"]
    raw = json.loads(source.read_text())
    if raw.get("season") != 2026 or raw.get("week") != 1:
        raise ValueError("unexpected college weekly horizon")
    logos = load_registry()
    players = []
    for row in raw["players"]:
        market = market_teams.get(row["teamId"])
        if (not market or market.get("state") != "available"
                or market.get("team") != row["team"]
                or market.get("opponent") != row.get("opponent")
                or market.get("home") != row.get("home")):
            raise ValueError(f"college market context does not match {row['team']}")
        players.append({
            "id": row["id"], "name": row["name"], "team": row["team"],
            "team_id": row["teamId"], "position": row["pos"],
            "conference": None, "photo": None,
            "team_logo": logos[row["teamId"]]["local_asset_path"],
            "team_color": "#" + (logos[row["teamId"]].get("primary_color") or "C6F53C").lstrip("#"),
            "adp": None,
            "opponent": row.get("opponent"),
            "home": row.get("home"),
            "game_date": row.get("gameDate"),
            "implied_total": row.get("impliedTotal"),
            "expected_opportunity": {
                "pass_attempts": row.get("passAtt"),
                "carries": row.get("rushAtt"),
                "receptions": row.get("rec"),
            },
            "projection_confidence": row.get("confidence"),
            "player_market": {
                "state": ("available" if row.get("marketEvidence")
                          else "role_only" if row.get("marketRoleEvidence")
                          else "unavailable"),
                "components": row.get("marketEvidence") or [],
                "role_evidence": bool(row.get("marketRoleEvidence")),
                "is_projection_input": bool(row.get("marketEvidence")),
            },
            "history": {}, "history_season": None,
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
        "market": {
            "state": "available_delayed_market_context",
            "label": ("TheRundown Pro main-line consensus from Pinnacle, DraftKings, "
                      "and FanDuel; 30-second plan delay"),
            "captured_on": market_payload["captured_on"],
            "latest_market_update_at": market_payload["source"][
                "latest_market_update_at"],
            "data_delay_seconds": market_payload["source"]["data_delay_seconds"],
            "coverage": market_payload["coverage"],
            "player_coverage": raw.get("marketInput", {}),
        },
        "conference_available": False, "counts": raw["counts"],
        "market_context_by_team": market_teams,
        "identity_coverage": {"resolved": len(ids), "total": len(players)},
        "players": players,
        "closest_calls": [_summary(r) for r in closest_calls(
            players, "yahoo", context=context)],
        "strongest_edges": [_summary(r) for r in strongest_projection_edges(
            players, "yahoo", context=context)],
        "available_formats": ["yahoo"],
        "editorial_opinions": [], "schedule_sos_available": False,
        "opponent_context_available": True,
        "schedule_sos_required_artifact": (
            "data/college/2026/week-1/comparison_scenarios.json: validated "
            "same-player Yahoo projections against an alternate opponent or "
            "neutral baseline, keyed by stable college player id"),
        "sources": {
            "projections": {"label": "Validated College Week 1 Yahoo projections",
                            "updated_at": raw["generatedAt"]},
            "ranks": {"label": "College Week 1 overall and position ranks",
                      "updated_at": raw["generatedAt"]},
            "adp": {"label": "Validated college ADP", "updated_at": None},
            "history": {"label": "Validated college weekly history", "updated_at": None},
            "editorial": {"label": "Lineup Beat college editorial opinion", "updated_at": None},
            "schedule_sos": {"label": schedule_payload["source"],
                             "updated_at": schedule_payload["generated_at"]},
            "market": {
                "label": ("TheRundown delayed game consensus plus exact, "
                          "team-bounded player component markets"),
                "updated_at": market_payload["source"]["latest_market_update_at"],
                "captured_on": market_payload["captured_on"],
                "data_delay_seconds": market_payload["source"]["data_delay_seconds"],
                "note": ("Game markets describe the scoring environment. Exact "
                         "player markets anchor only their named component; coverage "
                         "varies and market inputs are not outcomes or guarantees."),
            },
        },
    }


def _summary(result: dict) -> dict:
    a = result["winner"] or result["player_a"]
    b = result["runner_up"] or result["player_b"]
    return {"a": a["id"], "b": b["id"], "gap": result["gap"],
            "confidence": result["confidence"],
            "winner": result["winner"]["id"] if result["winner"] else None}
