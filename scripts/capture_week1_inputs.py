#!/usr/bin/env python3
"""Capture the bounded, private Week 1 nflverse input set.

Raw responses are stored only in the ignored local cache.  Every response is
verified against the release asset digest and described in a local manifest.
The public build never invokes this script or makes network requests.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / ".cache" / "week1-intelligence"
LICENSE = {
    "name": "Creative Commons Attribution 4.0 International",
    "spdx": "CC-BY-4.0",
    "url": "https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md",
    "attribution_required": True,
    "share_alike": False,
    "derived_use_permitted": True,
}
BASE = "https://github.com/nflverse/nflverse-data/releases/download"

# The schedule asset contains both requested seasons. Historical injury data
# is not needed here; the current ESPN status layer is captured separately.
CATALOG = (
    ("schedules", "games.csv.gz", "91beff306b6b5dfa8143074a4287202314d6901d304f4f86fff7584c796d8e61"),
    ("stats_player", "stats_player_week_2024.csv.gz", "61fc9a44706522218a4448001706e3fec65ebbb97c1d9c5746ceef364989125d"),
    ("stats_player", "stats_player_week_2025.csv.gz", "c7401e321bdb369443f3280b9acd74ba6e16d82e710383bf1c01acabfebeac0b"),
    ("stats_team", "stats_team_week_2024.csv.gz", "6f27957fd1d3a759a7160586bf0558cea010a83f9cb3ee68a9bda04e35674c14"),
    ("stats_team", "stats_team_week_2025.csv.gz", "5348409f099b285877641446e869dcda3179464584dd71f73becc087219b0383"),
    ("snap_counts", "snap_counts_2024.csv.gz", "fe1d819db55d6e333057a8efcec52e8506ef111c7961b0e0411310393bb912bb"),
    ("snap_counts", "snap_counts_2025.csv.gz", "700bef40cc5db8917b49e2eca93d7091dbd4a3cd740b2fd4dfa1795f7b9187db"),
    ("rosters", "roster_2026.csv.gz", "fd8f7ac630b3c60f66512328134eb1823a2aad2a45dd392cdefbd719b4a5fe30"),
    ("depth_charts", "depth_charts_2026.csv.gz", "219b5ff0d7954632f3f5aabdefab49e444cc0129a94c1dd49a0b342de07de765"),
    ("pbp", "play_by_play_2024.csv.gz", "23370d5d10f8104d80d46a1fc5e61f4f6f5a3263fe96fe2dd629913cfcb08c06"),
    ("pbp", "play_by_play_2025.csv.gz", "2f135887790a013fd004e609e37096bb4816d5cc80b9f19122e1bad478961978"),
)

CURRENT_FILES = {"games.csv.gz", "roster_2026.csv.gz", "depth_charts_2026.csv.gz"}
CURRENT_REQUIRED_COLUMNS = {
    "games.csv.gz": {"season", "week", "game_type", "away_team", "home_team"},
    "roster_2026.csv.gz": {"gsis_id", "full_name", "team", "position", "status"},
    "depth_charts_2026.csv.gz": {"gsis_id", "pos_abb", "pos_rank", "dt"},
}
CURRENT_MINIMUM_ROWS = {
    "games.csv.gz": 250,
    "roster_2026.csv.gz": 1500,
    "depth_charts_2026.csv.gz": 1000,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_current_asset(path: Path) -> int:
    required = CURRENT_REQUIRED_COLUMNS[path.name]
    try:
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            missing = required - fields
            if missing:
                raise RuntimeError(
                    f"{path.name} is missing required columns: {sorted(missing)}")
            count = sum(1 for _ in reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise RuntimeError(f"{path.name} is not a valid gzip CSV: {exc}") from None
    minimum = CURRENT_MINIMUM_ROWS[path.name]
    if count < minimum:
        raise RuntimeError(f"{path.name} has {count} rows; expected at least {minimum}")
    return count


def capture(cache: Path, refresh_current: bool = False) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    assets = []
    for release, filename, expected in CATALOG:
        url = f"{BASE}/{release}/{filename}"
        target = cache / filename
        response_headers: dict[str, str] = {}
        if refresh_current and filename in CURRENT_FILES and target.exists():
            target.unlink()
        if not target.exists() or sha256(target) != expected:
            request = urllib.request.Request(
                url, headers={"User-Agent": "LineupBeat-Week1-Capture/1.0"}
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                response_headers = {
                    key.lower(): value
                    for key, value in response.headers.items()
                    if key.lower() in {"content-type", "etag", "last-modified"}
                }
                fd, temp_name = tempfile.mkstemp(prefix=f".{filename}.", dir=cache)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        while chunk := response.read(1024 * 1024):
                            handle.write(chunk)
                    actual = sha256(Path(temp_name))
                    if actual != expected and not (
                            refresh_current and filename in CURRENT_FILES):
                        raise RuntimeError(
                            f"Digest mismatch for {filename}: {actual} != {expected}"
                        )
                    os.replace(temp_name, target)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
        actual = sha256(target)
        if actual != expected and not (
                refresh_current and filename in CURRENT_FILES):
            raise RuntimeError(f"Cached digest mismatch for {filename}")
        current_rows = (validate_current_asset(target)
                        if refresh_current and filename in CURRENT_FILES else None)
        assets.append(
            {
                "filename": filename,
                "release": release,
                "source_url": url,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "response_sha256": actual,
                "release_sha256": actual if current_rows is not None else expected,
                "bytes": target.stat().st_size,
                "response_headers": response_headers,
                "validated_rows": current_rows,
                "license": LICENSE,
                "provenance": (
                    "nflverse/nflverse-data release asset; schedules are maintained "
                    "by Lee Sharpe's nfldata project and accessed through nflreadr"
                    if release == "schedules"
                    else "nflverse/nflverse-data release asset accessed through nflreadr"
                ),
            }
        )
        print(f"captured {filename}: {actual} ({target.stat().st_size} bytes)")
    return {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Lineup Beat development-only Week 1 intelligence",
        "license_review": LICENSE,
        "assets": assets,
        "unavailable": {
            "official_final_game_statuses": (
                "Official final Week 1 game designations are not yet available for every game; "
                "the current ESPN status layer is captured separately."
            ),
            "odds": (
                "THE_ODDS_API_KEY was unavailable in every normal local/development environment; "
                "zero requests were made."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--refresh-current", action="store_true",
        help="refresh and structurally validate schedule, roster and depth assets",
    )
    args = parser.parse_args()
    manifest = args.manifest or args.cache / "capture_manifest.json"
    atomic_json(manifest, capture(args.cache, args.refresh_current))
    print(f"manifest: {manifest}")


if __name__ == "__main__":
    main()
