#!/usr/bin/env python3
"""Capture a minimal, private ESPN injury-status snapshot for Week 1.

Only normalized status facts needed by the weekly product are retained. ESPN
comments and long-form analysis never enter the model or public artifact.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / ".cache" / "week1-intelligence" / "espn_injuries.json"
SOURCE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
PUBLIC_SOURCE_URL = "https://www.espn.com/nfl/injuries"
POSITIONS = {"QB", "RB", "WR", "TE"}
TEAM_ALIASES = {"WSH": "WAS", "JAC": "JAX", "LA": "LAR"}
ALLOWED_STATUSES = {
    "Active", "Questionable", "Doubtful", "Out", "Injured Reserve",
    "Suspension",
}
UNAVAILABLE_STATUSES = {"Out", "Injured Reserve", "Suspension"}


def _player_id(athlete: dict) -> str | None:
    for link in athlete.get("links") or []:
        match = re.search(r"/_/id/(\d+)(?:/|$)", str(link.get("href") or ""))
        if match:
            return match.group(1)
    return None


def normalize_payload(raw: dict, expected_season: int = 2026) -> dict:
    if raw.get("status") != "success":
        raise ValueError("ESPN injury response was not successful")
    if int((raw.get("season") or {}).get("year") or 0) != expected_season:
        raise ValueError("ESPN injury response has the wrong season")
    groups = raw.get("injuries") or []
    if len(groups) != 32:
        raise ValueError(f"ESPN injury response covers {len(groups)} teams, expected 32")
    captured_at = str(raw.get("timestamp") or "")
    try:
        datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("ESPN injury response has an invalid timestamp") from exc

    records = []
    seen = set()
    for group in groups:
        for injury in group.get("injuries") or []:
            athlete = injury.get("athlete") or {}
            position = str((athlete.get("position") or {}).get("abbreviation") or "")
            status = str(injury.get("status") or "")
            if position not in POSITIONS or status == "Active":
                continue
            if status not in ALLOWED_STATUSES:
                raise ValueError(f"unsupported ESPN injury status: {status}")
            team = str((athlete.get("team") or {}).get("abbreviation") or "").upper()
            team = TEAM_ALIASES.get(team, team)
            name = str(athlete.get("displayName") or "").strip()
            if not name or not team:
                raise ValueError("ESPN injury row is missing exact player identity")
            key = (name, team, position)
            if key in seen:
                raise ValueError(f"duplicate ESPN injury identity: {key}")
            seen.add(key)
            details = injury.get("details") or {}
            injury_type = str(details.get("type") or "").strip() or None
            status_type = injury.get("type") or {}
            abbreviation = str(status_type.get("abbreviation") or "").strip()
            if not abbreviation:
                abbreviation = {
                    "Questionable": "Q", "Doubtful": "D", "Out": "O",
                    "Injured Reserve": "IR", "Suspension": "SUS",
                }[status]
            records.append({
                "name": name,
                "team": team,
                "position": position,
                "espn_id": _player_id(athlete),
                "status": status,
                "abbreviation": abbreviation,
                "injury_type": injury_type,
                "updated_at": injury.get("date") or captured_at,
                "return_date": details.get("returnDate"),
                "confirmed_unavailable": status in UNAVAILABLE_STATUSES,
            })
    if len(records) < 20:
        raise ValueError(f"ESPN injury response has only {len(records)} fantasy-status rows")
    records.sort(key=lambda row: (row["team"], row["position"], row["name"]))
    return {
        "schema": "lineupbeat-private-espn-injury-status-v1",
        "season": expected_season,
        "fetched_at": captured_at,
        "team_count": len(groups),
        "source_url": PUBLIC_SOURCE_URL,
        "records": records,
    }


def fetch() -> dict:
    request = urllib.request.Request(
        SOURCE_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "LineupBeat-Injury-Status/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--input", type=Path,
                        help="parse a saved response instead of making a request")
    args = parser.parse_args()
    raw = json.loads(args.input.read_text()) if args.input else fetch()
    payload = normalize_payload(raw)
    write_atomic(args.output, payload)
    unavailable = sum(row["confirmed_unavailable"] for row in payload["records"])
    print(f"ESPN injury status: {len(payload['records'])} fantasy rows; "
          f"{unavailable} confirmed unavailable; 32 teams")


if __name__ == "__main__":
    main()
