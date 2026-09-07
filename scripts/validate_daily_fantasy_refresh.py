#!/usr/bin/env python3
"""Fail-closed checks for the automatic fantasy-data refresh.

The daily job builds a candidate first.  This validator is the publication
boundary: a bad, partial, stale, or unexpectedly volatile candidate is never
allowed to replace the last known-good weekly projection release.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


FORMATS = {"ppr": 1.0, "half_ppr": 0.5, "non_ppr": 0.0}
FORBIDDEN = (
    "bookmaker_key", "american_price", "apiKey",
    "DraftKings", "FanDuel", "Caesars",
)
VISIBLE_INJURY_STATUSES = {"Active", "Questionable", "Doubtful"}
UNAVAILABLE_INJURY_STATUSES = {"Out", "Injured Reserve", "Suspension"}


def points(stat: dict, reception_value: float) -> float:
    return (
        float(stat.get("passing_yards") or 0) * .04
        + float(stat.get("passing_tds") or 0) * 4
        - float(stat.get("passing_interceptions") or 0) * 2
        + float(stat.get("rushing_yards") or 0) * .1
        + float(stat.get("rushing_tds") or 0) * 6
        + float(stat.get("receiving_yards") or 0) * .1
        + float(stat.get("receiving_tds") or 0) * 6
        + float(stat.get("receptions") or 0) * reception_value
        - float(stat.get("fumbles_lost_total") or 0) * 2
        + sum(float(stat.get(key) or 0) for key in (
            "passing_2pt_conversions", "rushing_2pt_conversions",
            "receiving_2pt_conversions")) * 2
        + float(stat.get("special_teams_tds") or 0) * 6
    )


def validate(candidate: dict, previous: dict | None = None,
             now: datetime | None = None, require_injuries: bool = True) -> dict:
    problems = []
    now = now or datetime.now(timezone.utc)
    try:
        updated = datetime.fromisoformat(candidate["updated_at"].replace("Z", "+00:00"))
        age_hours = (now - updated.astimezone(timezone.utc)).total_seconds() / 3600
        if age_hours < -1 or age_hours > 30:
            problems.append(f"market snapshot age is {age_hours:.1f} hours")
    except (KeyError, TypeError, ValueError) as exc:
        problems.append(f"invalid updated_at: {exc}")
    injury_updated_at = ((candidate.get("sources") or {}).get("injuries") or {}).get(
        "updated_at"
    )
    if require_injuries or injury_updated_at:
        try:
            injury_updated = datetime.fromisoformat(
                injury_updated_at.replace("Z", "+00:00")
            )
            injury_age = (
                now - injury_updated.astimezone(timezone.utc)
            ).total_seconds() / 3600
            if injury_age < -1 or injury_age > 30:
                problems.append(f"injury status age is {injury_age:.1f} hours")
        except (AttributeError, TypeError, ValueError) as exc:
            problems.append(f"invalid injury updated_at: {exc}")

    players = candidate.get("players") or []
    ids = [row.get("id") for row in players]
    teams = {row.get("team") for row in players}
    games = {row.get("game_id") for row in players}
    if len(players) < 350:
        problems.append(f"only {len(players)} projected players")
    if len(ids) != len(set(ids)) or None in ids:
        problems.append("player ids are missing or duplicated")
    if len(teams) != 32:
        problems.append(f"coverage is {len(teams)} NFL teams, expected 32")
    if len(games) != 16:
        problems.append(f"coverage is {len(games)} NFL games, expected 16")

    position_ranks = {fmt: defaultdict(list) for fmt in FORMATS}
    overall_ranks = {fmt: [] for fmt in FORMATS}
    prop_players = 0
    tagged_players = 0
    unavailable_players = 0
    for player in players:
        market = player.get("market") or {}
        if market.get("quality") != "HIGH" or int(market.get("game_book_count") or 0) < 3:
            problems.append(f"{player.get('name')}: unqualified game consensus")
        if market.get("player_components"):
            prop_players += 1
        availability = player.get("availability") or {}
        injury_status = availability.get("status")
        if require_injuries or injury_updated_at:
            if injury_status not in VISIBLE_INJURY_STATUSES | UNAVAILABLE_INJURY_STATUSES:
                problems.append(f"{player.get('name')}: invalid injury status")
            if injury_status != "Active":
                tagged_players += 1
            if injury_status in VISIBLE_INJURY_STATUSES:
                if (availability.get("projection_adjusted")
                        or availability.get("projection_factor") != 1.0):
                    problems.append(
                        f"{player.get('name')}: {injury_status} changed the projection")
            elif injury_status in UNAVAILABLE_INJURY_STATUSES:
                unavailable_players += 1
                if (not availability.get("projection_adjusted")
                        or availability.get("projection_factor") != 0.0):
                    problems.append(
                        f"{player.get('name')}: unavailable status was not applied")
        for fmt, reception_value in FORMATS.items():
            record = (player.get("formats") or {}).get(fmt) or {}
            expected = round(points(player.get("stat_projection") or {}, reception_value), 1)
            if record.get("projected_points") != expected:
                problems.append(f"{player.get('name')} {fmt}: scoring does not reconcile")
            if injury_status in UNAVAILABLE_INJURY_STATUSES and record.get("projected_points") != 0.0:
                problems.append(f"{player.get('name')} {fmt}: unavailable player is not zero")
            overall_ranks[fmt].append(record.get("overall_rank"))
            position_ranks[fmt][player.get("position")].append(record.get("position_rank"))
    if prop_players < 1:
        problems.append(f"only {prop_players} players have qualified prop numbers")
    for fmt in FORMATS:
        if sorted(overall_ranks[fmt]) != list(range(1, len(players) + 1)):
            problems.append(f"{fmt}: overall ranks are not sequential")
        for position, ranks in position_ranks[fmt].items():
            if sorted(ranks) != list(range(1, len(ranks) + 1)):
                problems.append(f"{fmt} {position}: position ranks are not sequential")

    if previous:
        old = {row["id"]: row for row in previous.get("players") or []}
        common = [row for row in players if row["id"] in old]
        if len(common) < min(len(players), len(old)) * .90:
            problems.append("more than 10% of the previous player pool disappeared")
        for player in common:
            before = old[player["id"]]["formats"]["half_ppr"]["projected_points"]
            after = player["formats"]["half_ppr"]["projected_points"]
            current_unavailable = (player.get("availability") or {}).get("status") in UNAVAILABLE_INJURY_STATUSES
            previous_unavailable = (old[player["id"]].get("availability") or {}).get("status") in UNAVAILABLE_INJURY_STATUSES
            if abs(after - before) > 8 and not (current_unavailable or previous_unavailable):
                problems.append(
                    f"{player['name']}: Half-PPR moved {before:.1f} to {after:.1f}")

    serialized = json.dumps(candidate, sort_keys=True)
    for value in FORBIDDEN:
        if value in serialized:
            problems.append(f"private sportsbook detail is public: {value}")
    if problems:
        sample = "\n  - ".join(problems[:25])
        raise ValueError(f"daily fantasy refresh rejected:\n  - {sample}")
    return {
        "players": len(players), "teams": len(teams), "games": len(games),
        "players_with_props": prop_players, "players_with_injury_tags": tagged_players,
        "confirmed_unavailable_players": unavailable_players,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    candidate = json.loads(args.candidate.read_text())
    previous = json.loads(args.previous.read_text()) if args.previous else None
    print(json.dumps(validate(candidate, previous), sort_keys=True))


if __name__ == "__main__":
    main()
