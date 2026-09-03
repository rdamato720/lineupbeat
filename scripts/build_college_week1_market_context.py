#!/usr/bin/env python3
"""Build a minimal, derived College Week 1 market-context artifact.

The input is a user-captured TheRundown ZIP. This script makes no network
requests and never reads an API key. It emits only consensus game context for
the 64 teams in the published College Week 1 model; raw prices are not copied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WEEKLY = (ROOT / "data/college/2026/week-1/v1.0/"
          "college_week1_site_projections_2026.json")
DEFAULT_OUTPUT = (ROOT / "data/college/2026/week-1/market-context/"
                  "therundown-2026-09-03.json")
BOOKS = {"3": "Pinnacle", "19": "DraftKings", "23": "FanDuel"}
TEAM_ALIASES = {
    "Long Island University": "Long Island",
    "Miami": "Miami (FL)",
    "Nicholls": "Nicholls State",
    "Pitt": "Pittsburgh",
}
MARKET_IDS = {"moneyline": 1, "spread": 2, "total": 3}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def numeric(value) -> float:
    if value is None:
        raise ValueError("market line is missing a value")
    return float(value)


def median(values: dict[str, float]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values.values())), 2)


def range_of(values: dict[str, float]) -> float | None:
    if not values:
        return None
    return round(max(values.values()) - min(values.values()), 2)


def price_map(participant: dict, *, line_value: bool) -> tuple[dict[str, float], list[str]]:
    values: dict[str, float] = {}
    updates: list[str] = []
    for line in participant.get("lines") or []:
        for book_id, price in (line.get("prices") or {}).items():
            if book_id not in BOOKS or not price.get("is_main_line"):
                continue
            value = numeric(line.get("value") if line_value else price.get("price"))
            if book_id in values and values[book_id] != value:
                raise ValueError(f"conflicting main lines for sportsbook {book_id}")
            values[book_id] = value
            if price.get("updated_at"):
                updates.append(price["updated_at"])
    return values, updates


def market_for(event: dict, market_id: int) -> dict | None:
    rows = [market for market in event.get("markets") or []
            if market.get("market_id") == market_id
            and market.get("period_id") == 0]
    if len(rows) > 1:
        raise ValueError(f"duplicate full-game market {market_id}")
    return rows[0] if rows else None


def team_participant(market: dict | None, provider_team_id: int) -> dict | None:
    if not market:
        return None
    rows = [row for row in market.get("participants") or []
            if row.get("type") == "TYPE_TEAM"
            and row.get("id") == provider_team_id]
    if len(rows) > 1:
        raise ValueError("ambiguous team participant")
    return rows[0] if rows else None


def total_participant(market: dict | None) -> dict | None:
    if not market:
        return None
    rows = [row for row in market.get("participants") or []
            if row.get("type") == "TYPE_RESULT"
            and row.get("name") == "Over"]
    if len(rows) > 1:
        raise ValueError("ambiguous total participant")
    return rows[0] if rows else None


def load_capture(path: Path) -> tuple[dict, list[dict], dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "capture-summary.json" not in names:
            raise ValueError("capture summary is missing")
        summary = json.loads(archive.read("capture-summary.json"))
        if (summary.get("sport_id") != 1
                or summary.get("market_ids") != [1, 2, 3]):
            raise ValueError("unexpected capture sport or market scope")
        events: list[dict] = []
        source_hashes: dict[str, str] = {}
        for row in summary.get("dates") or []:
            if row.get("http_status") != 200:
                raise ValueError("capture contains a failed response")
            if row.get("tier") != "pro" or row.get("data_delay_seconds") != "30":
                raise ValueError("capture plan or delay does not match the approved source")
            member = f"ncaaf-{row['date']}.json"
            if member not in names:
                raise ValueError(f"capture member is missing: {member}")
            raw = archive.read(member)
            if digest(raw) != row.get("sha256"):
                raise ValueError(f"capture checksum mismatch: {member}")
            source_hashes[member] = digest(raw)
            payload = json.loads(raw)
            events.extend(payload.get("events") or [])
    ids = [event.get("event_id") for event in events]
    if len(ids) != len(set(ids)):
        raise ValueError("capture contains duplicate events")
    if len(events) != summary.get("total_events"):
        raise ValueError("capture event count does not reconcile")
    return summary, events, source_hashes


def modeled_schedule() -> dict[str, dict]:
    raw = json.loads(WEEKLY.read_text())
    schedules: dict[str, dict] = {}
    for player in raw["players"]:
        row = {
            "team_id": player["teamId"],
            "team": player["team"],
            "opponent": player["opponent"],
            "home": bool(player["home"]),
            "game_date": player["gameDate"],
        }
        existing = schedules.setdefault(player["team"], row)
        if existing != row:
            raise ValueError(f"conflicting modeled schedule for {player['team']}")
    if len(schedules) != 64:
        raise ValueError("expected 64 modeled College teams")
    return schedules


def find_event(events: list[dict], schedule: dict) -> tuple[dict, dict]:
    provider_name = TEAM_ALIASES.get(schedule["team"], schedule["team"])
    expected_opponent = TEAM_ALIASES.get(schedule["opponent"], schedule["opponent"])
    rows = []
    for event in events:
        event_date = datetime.fromisoformat(event["event_date"].replace("Z", "+00:00"))
        modeled_date = datetime.fromisoformat(
            schedule["game_date"].replace("Z", "+00:00"))
        if event_date != modeled_date:
            continue
        teams = event.get("teams") or []
        selected = next((team for team in teams if team.get("name") == provider_name), None)
        opponents = [team for team in teams if team.get("name") == expected_opponent]
        if (selected and len(opponents) == 1
                and bool(selected.get("is_home")) == schedule["home"]
                and event.get("score", {}).get("event_status") == "STATUS_SCHEDULED"):
            rows.append((event, selected))
    if len(rows) != 1:
        raise ValueError(
            f"expected one scheduled market event for {schedule['team']} vs. "
            f"{schedule['opponent']}; found {len(rows)}")
    return rows[0]


def context_for(event: dict, provider_team: dict, schedule: dict) -> dict:
    moneyline = market_for(event, MARKET_IDS["moneyline"])
    spread = market_for(event, MARKET_IDS["spread"])
    total = market_for(event, MARKET_IDS["total"])
    ml_values, ml_updates = price_map(
        team_participant(moneyline, provider_team["team_id"]) or {}, line_value=False)
    spread_values, spread_updates = price_map(
        team_participant(spread, provider_team["team_id"]) or {}, line_value=True)
    total_values, total_updates = price_map(
        total_participant(total) or {}, line_value=True)
    spread_consensus = median(spread_values)
    total_consensus = median(total_values)
    if spread_consensus is None or total_consensus is None:
        raise ValueError(f"spread/total unavailable for modeled team {schedule['team']}")
    complete_books = sorted(set(spread_values) & set(total_values),
                            key=lambda value: int(value))
    if not complete_books:
        raise ValueError(f"no same-book spread/total pair for {schedule['team']}")
    updates = ml_updates + spread_updates + total_updates
    opponent_total = round((total_consensus + spread_consensus) / 2, 2)
    team_total = round((total_consensus - spread_consensus) / 2, 2)
    return {
        "state": "available",
        "opponent": schedule["opponent"],
        "home": schedule["home"],
        "event_date": event["event_date"],
        "team_spread": spread_consensus,
        "game_total": total_consensus,
        "team_implied_total": team_total,
        "opponent_implied_total": opponent_total,
        "moneyline_median": median(ml_values),
        "consensus_book_count": len(complete_books),
        "consensus_books": [BOOKS[book_id] for book_id in complete_books],
        "spread_book_count": len(spread_values),
        "total_book_count": len(total_values),
        "spread_range": range_of(spread_values),
        "total_range": range_of(total_values),
        "latest_market_update_at": max(updates) if updates else None,
        "blowout_risk": abs(spread_consensus) >= 21,
    }


def build(input_zip: Path) -> dict:
    summary, events, source_hashes = load_capture(input_zip)
    schedules = modeled_schedule()
    teams = {}
    for team_name in sorted(schedules):
        schedule = schedules[team_name]
        event, provider_team = find_event(events, schedule)
        teams[schedule["team_id"]] = {
            "team": team_name,
            **context_for(event, provider_team, schedule),
        }
    latest = max(row["latest_market_update_at"] for row in teams.values()
                 if row["latest_market_update_at"])
    return {
        "schemaVersion": "lineupbeat-college-week1-market-context-v1",
        "season": 2026,
        "week": 1,
        "captured_on": "2026-09-03",
        "source": {
            "provider": "TheRundown",
            "plan": "pro",
            "data_delay_seconds": 30,
            "market_ids": MARKET_IDS,
            "bookmakers": list(BOOKS.values()),
            "raw_capture_sha256": digest(input_zip.read_bytes()),
            "source_member_sha256": source_hashes,
            "latest_market_update_at": latest,
            "note": ("Derived main-line consensus context; not player props and not "
                     "a projection input."),
        },
        "coverage": {
            "modeled_teams": len(teams),
            "teams_with_spread_and_total": sum(
                row["state"] == "available" for row in teams.values()),
            "provider_events_in_capture": summary["total_events"],
            "provider_market_objects_in_capture": summary["total_markets"],
        },
        "teams": teams,
    }


def write(output: Path, payload: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True,
                          ensure_ascii=False) + "\n").encode()
    output.write_bytes(encoded)
    manifest = {
        "schemaVersion": "lineupbeat-college-week1-market-context-manifest-v1",
        "files": {output.name: {"bytes": len(encoded), "sha256": digest(encoded)}},
    }
    (output.parent / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_zip", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    write(args.output, build(args.input_zip))
    print(args.output)


if __name__ == "__main__":
    main()
