#!/usr/bin/env python3
"""Import the bounded, pre-authorized ESPN College team-logo cohort.

This script never discovers teams. It consumes one previously captured ESPN
directory response plus the six explicitly authorized team responses, proves
that they cover the repository's exact College-team union, and downloads one
default HTTPS PNG per team into the static deployment.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEEK = ROOT / "data/college/2026/week-1/v1.0/college_week1_site_projections_2026.json"
SEASON = ROOT / "data/college/2026/v1.1/college_site_projections_2026.json"
OUT = ROOT / "site/assets/college-teams"
REGISTRY = ROOT / "data/college/team_logo_provenance.json"
DIRECTORY = Path("/tmp/lineupbeat-espn-college-teams.json")
TARGETED = {
    "CFF_PUR": ("Purdue", "2509"),
    "CFF_SC": ("South Carolina", "2579"),
    "CFF_SMU": ("SMU", "2567"),
    "CFF_TCU": ("TCU", "2628"),
    "CFF_TENN": ("Tennessee", "2633"),
    "CFF_TTU": ("Texas Tech", "2641"),
}


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def required_teams():
    week = json.loads(WEEK.read_text())
    season = json.loads(SEASON.read_text())
    required = {p["teamId"]: p["team"] for p in week["players"]}
    required.update({t["id"]: t["name"] for t in season["teams"]})
    return required, {p["teamId"] for p in week["players"]}, {t["id"] for t in season["teams"]}


def directory_teams(payload):
    return [entry.get("team", entry)
            for sport in payload.get("sports", [])
            for league in sport.get("leagues", [])
            for entry in league.get("teams", [])]


def primary_logo(team):
    logos = [logo for logo in team.get("logos", [])
             if str(logo.get("href", "")).startswith("https://")]
    defaults = [logo for logo in logos if "default" in logo.get("rel", [])]
    chosen = (defaults or sorted(logos, key=lambda logo: logo["href"]))
    return chosen[0]["href"] if chosen else None


def canonical_page(team):
    links = [link.get("href") for link in team.get("links", [])
             if "clubhouse" in link.get("rel", []) and str(link.get("href", "")).startswith("https://")]
    return links[0] if links else None


def png_dimensions(blob: bytes):
    if len(blob) < 24 or blob[:8] != b"\x89PNG\r\n\x1a\n" or blob[12:16] != b"IHDR":
        raise ValueError("response is not a valid PNG")
    width, height = struct.unpack(">II", blob[16:24])
    if not width or not height:
        raise ValueError("PNG has invalid dimensions")
    return width, height


def main():
    required, week_ids, season_ids = required_teams()
    if len(required) != 68 or len(week_ids) != 64 or len(season_ids) != 68:
        raise SystemExit("repository College-team counts changed; refusing import")

    teams = directory_teams(json.loads(DIRECTORY.read_text()))
    by_name = {}
    for team in teams:
        for field in ("displayName", "shortDisplayName", "name", "location", "nickname"):
            if team.get(field):
                by_name.setdefault(normalized(team[field]), {})[str(team.get("id"))] = team

    resolved = {}
    for internal_id, name in required.items():
        if internal_id in TARGETED:
            team = json.loads(Path(f"/tmp/lineupbeat-espn-{internal_id}.json").read_text()).get("team")
            expected_name, expected_id = TARGETED[internal_id]
            if str(team.get("id")) != expected_id or normalized(team.get("location", "")) != normalized(expected_name):
                raise SystemExit(f"targeted identity failed: {internal_id}")
            resolved[internal_id] = team
            continue
        hits = list(by_name.get(normalized(name), {}).values())
        if len(hits) != 1:
            raise SystemExit(f"directory identity is not unique: {internal_id} {name} ({len(hits)})")
        resolved[internal_id] = hits[0]

    if len(resolved) != 68 or len({str(t["id"]) for t in resolved.values()}) != 68:
        raise SystemExit("resolved ESPN identities are not exactly 68 unique teams")
    if any(not primary_logo(team) or not canonical_page(team) for team in resolved.values()):
        raise SystemExit("a resolved team lacks an HTTPS logo or canonical page")

    OUT.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    records = []
    for internal_id, name in sorted(required.items()):
        team = resolved[internal_id]
        logo_url = primary_logo(team)
        request = urllib.request.Request(logo_url, headers={"Accept": "image/png"})
        with urllib.request.urlopen(request, timeout=30) as response:
            mime = response.headers.get_content_type()
            blob = response.read()
        if mime != "image/png":
            raise SystemExit(f"unexpected MIME for {internal_id}: {mime}")
        width, height = png_dimensions(blob)
        local_path = OUT / f"{internal_id}.png"
        local_path.write_bytes(blob)
        records.append({
            "internal_team_id": internal_id,
            "internal_team_name": name,
            "espn_team_id": str(team["id"]),
            "espn_team_name": team.get("displayName"),
            "espn_abbreviation": team.get("abbreviation"),
            "primary_color": team.get("color"),
            "alternate_color": team.get("alternateColor"),
            "source_page": canonical_page(team),
            "original_asset_url": logo_url,
            "retrieved_at": fetched_at,
            "local_asset_path": f"/assets/college-teams/{internal_id}.png",
            "mime_type": mime,
            "width": width,
            "height": height,
            "file_size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
        })

    REGISTRY.write_text(json.dumps({
        "schema_version": 1,
        "source": "ESPN college football team directory and six authorized team validations",
        "retrieved_at": fetched_at,
        "required_team_count": 68,
        "week_1_coverage": len(week_ids & set(resolved)),
        "season_coverage": len(season_ids & set(resolved)),
        "teams": records,
    }, indent=2, sort_keys=True) + "\n")
    print(f"Imported {len(records)} validated College team logos")


if __name__ == "__main__":
    main()
