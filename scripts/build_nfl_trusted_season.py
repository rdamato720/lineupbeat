#!/usr/bin/env python3
"""Build the development-only trusted-current 2026 season release.

The reviewed production workbook remains the numerical source.  The v1.5
release contributes current active identities, teams, positions, photos and
stable IDs only.  A player is published only when normalized name plus exact
team and position resolves to one workbook row.  There is no fuzzy matching,
blending, redistribution or low-confidence model fallback.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "data/projections.xlsx"
CURRENT = ROOT / "data/nfl_season/2026/v1.5-final/season_projections.json"
OUTPUT = ROOT / "data/nfl_season/2026/v1.6-trusted-current"
VERSION = "v1.6-trusted-current"
POSITIONS = ("QB", "RB", "WR", "TE")
EXPECTED_COUNTS = {"QB": 37, "RB": 108, "WR": 171, "TE": 108}


def dump(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode().lower()
    parts = re.sub(r"[^a-z0-9]+", " ", text).strip().split()
    while parts and parts[-1] in {"jr", "sr", "ii", "iii", "iv", "v"}:
        parts.pop()
    return " ".join(parts)


def identity_key(name: str, team: str, position: str) -> tuple[str, str, str]:
    return normalized_name(name), (team or "").strip().upper(), position.upper()


def number(row, index: int) -> float:
    value = row[index]
    if value is None or value == "":
        raise ValueError(f"blank numeric cell at column {index + 1}")
    return float(value)


def workbook_rows() -> dict[tuple[str, str, str], dict]:
    workbook = openpyxl.load_workbook(WORKBOOK, data_only=True, read_only=True)
    found: defaultdict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for position in POSITIONS:
        sheet = workbook[position]
        headers = [str(cell.value or "").strip().lower() for cell in sheet[1]]

        def column(*names: str) -> int:
            for name in names:
                if name.lower() in headers:
                    return headers.index(name.lower())
            raise ValueError(f"{position}: missing required column {names}")

        columns = {
            "name": column("player"), "team": column("team"),
            "ppr": column("ppr"), "half_ppr": column("half ppr"),
            "non_ppr": column("non-ppr"),
            "carries": column("rush att"), "rushing_yards": column("rush yds"),
            "rushing_tds": column("rush td"), "fumbles_lost_total": column("fumbles lost"),
        }
        if position == "QB":
            columns.update({
                "attempts": column("pass att"), "completions": column("comp"),
                "passing_yards": column("pass yds"), "passing_tds": column("pass td"),
                "passing_interceptions": column("int"),
            })
        else:
            columns.update({
                "targets": column("targets"), "receptions": column("receptions"),
                "receiving_yards": column("rec yds"), "receiving_tds": column("rec td"),
            })

        for raw in sheet.iter_rows(min_row=2, values_only=True):
            if not raw[columns["name"]]:
                continue
            name = str(raw[columns["name"]]).strip()
            team = str(raw[columns["team"]] or "").strip().upper()
            stats = {field: 0.0 for field in (
                "attempts", "completions", "passing_yards", "passing_tds",
                "passing_interceptions", "carries", "rushing_yards", "rushing_tds",
                "targets", "receptions", "receiving_yards", "receiving_tds",
                "fumbles_lost_total",
            )}
            for field in stats:
                if field in columns:
                    stats[field] = number(raw, columns[field])
            formats = {
                "ppr": number(raw, columns["ppr"]),
                "half_ppr": number(raw, columns["half_ppr"]),
                "non_ppr": number(raw, columns["non_ppr"]),
            }
            found[identity_key(name, team, position)].append({
                "name": name, "team": team, "position": position,
                "stat_projection": stats, "formats": formats,
            })
    duplicates = {key: rows for key, rows in found.items() if len(rows) != 1}
    if duplicates:
        raise ValueError(f"ambiguous trusted workbook identities: {sorted(duplicates)}")
    return {key: rows[0] for key, rows in found.items()}


def score(stats: dict, reception_weight: float) -> float:
    return (
        stats["passing_yards"] * 0.04 + stats["passing_tds"] * 4
        - stats["passing_interceptions"] * 2 + stats["rushing_yards"] * 0.1
        + stats["rushing_tds"] * 6 + stats["receiving_yards"] * 0.1
        + stats["receiving_tds"] * 6 - stats["fumbles_lost_total"] * 2
        + stats["receptions"] * reception_weight
    )


def validate_player(player: dict) -> None:
    stats = player["stat_projection"]
    if any(value < 0 for value in stats.values()):
        raise ValueError(f"negative component: {player['name']}")
    if stats["completions"] > stats["attempts"] or stats["receptions"] > stats["targets"]:
        raise ValueError(f"opportunity contradiction: {player['name']}")
    for fmt, weight in (("ppr", 1.0), ("half_ppr", 0.5), ("non_ppr", 0.0)):
        # The reviewed workbook displays stat components to one decimal while
        # its point totals retain the author's underlying precision.  Across
        # the trusted current population that display-rounding residual is at
        # most 0.58 points; anything above 0.60 is a real scoring conflict.
        if abs(player["formats"][fmt] - score(stats, weight)) > 0.60:
            raise ValueError(f"scoring mismatch: {player['name']} {fmt}")


def rankings(players: list[dict]) -> dict:
    formats = {}
    for fmt in ("ppr", "half_ppr", "non_ppr"):
        ordered = sorted(players, key=lambda p: (-p["formats"][fmt], p["gsis_id"]))
        position_counts = Counter()
        rows = []
        for overall_rank, player in enumerate(ordered, 1):
            position_counts[player["position"]] += 1
            rows.append({
                "fantasy_points": player["formats"][fmt],
                "gsis_id": player["gsis_id"], "name": player["name"],
                "overall_rank": overall_rank, "player_id": player["player_id"],
                "position": player["position"],
                "position_rank": position_counts[player["position"]],
                "team": player["team"], "url": player["url"],
            })
        formats[fmt] = {"rows": rows}
    return {
        "formats": formats, "manual_adjustments": 0,
        "method": "descending trusted projected points; stable GSIS id breaks ties",
    }


def build() -> tuple[dict, dict, dict]:
    trusted = workbook_rows()
    current = json.loads(CURRENT.read_text())
    current_players = current["players"]
    if len(current_players) != 505 or any(p["status"] != "ACT" for p in current_players):
        raise ValueError("current active identity source changed")

    players = []
    withheld = []
    for base in current_players:
        key = identity_key(base["name"], base["team"], base["position"])
        source = trusted.get(key)
        if source is None:
            withheld.append({
                "gsis_id": base["gsis_id"], "name": base["name"],
                "team": base["team"], "position": base["position"],
                "offensive_role": base.get("offensive_role"),
                "reason": "No single exact current name + team + position match in the reviewed baseline",
            })
            continue
        player = {
            key: base.get(key) for key in (
                "gsis_id", "player_id", "sleeper_id", "espn_id", "name", "team",
                "position", "status", "url", "canonical_slug", "offensive_role",
                "role_confidence", "years_exp", "birth_date", "headshot_url",
            )
        }
        player.update({
            "active_for_projection": True,
            "disposition": "trusted_baseline_exact_current_active_match",
            "methodology_version": VERSION,
            "data_cutoff": current["metadata"]["cutoff_utc"],
            "projected_games_active": None,
            "stat_projection": source["stat_projection"],
            "formats": source["formats"],
            "evidence_limitation_flags": [
                "Published only after exact current active identity, team and position reconciliation",
                "Projection values retained from the reviewed August 30 baseline",
                "Current injury reporting is not incorporated",
                "No v1.5 fallback, benchmark copying or sportsbook adjustment",
            ],
        })
        validate_player(player)
        players.append(player)

    counts = Counter(p["position"] for p in players)
    if len(players) != 424 or dict(counts) != EXPECTED_COUNTS:
        raise ValueError(f"trusted population changed: {len(players)} {dict(counts)}")
    if len(withheld) != 81:
        raise ValueError(f"withheld population changed: {len(withheld)}")

    model = {
        "metadata": {
            "schema_version": "lineupbeat-nfl-trusted-current-v1",
            "methodology_version": VERSION,
            "season": 2026,
            "status": "DEVELOPMENT_REVIEW_ONLY",
            "projection_values_cutoff": "2026-08-30T16:00:00Z",
            "current_roster_cutoff": current["metadata"]["cutoff_utc"],
            "cutoff_utc": current["metadata"]["cutoff_utc"],
            "trusted_population": len(players),
            "current_active_population": len(current_players),
            "withheld_low_confidence_population": len(withheld),
            "position_counts": dict(counts),
            "recommendations_enabled": False,
            "production_deployment_authorized": False,
            "private_benchmark_or_adp_tuning": False,
            "external_provider_requests": 0,
            "model_api_calls": 0,
            "model_api_cost_usd": 0,
            "limitations": [
                "Current injury reporting is unavailable",
                "Eighty-one current players without a reviewed exact-match projection are withheld",
                "Week 1 sportsbook markets are not season-projection inputs",
                "Week 1 and My Team recommendations remain disabled",
            ],
        },
        "players": sorted(players, key=lambda p: p["gsis_id"]),
    }
    rank = rankings(model["players"])
    exclusion = {
        "method": "exact normalized name plus exact current team and position; no fuzzy matching",
        "current_active_population": len(current_players),
        "trusted_population": len(players),
        "withheld_population": len(withheld),
        "position_counts": dict(Counter(p["position"] for p in withheld)),
        "players": sorted(withheld, key=lambda p: (p["position"], p["team"], p["name"])),
    }
    return model, rank, exclusion


def main() -> None:
    model, rank, exclusion = build()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    outputs = {
        "season_projections.json": model,
        "season_rankings.json": rank,
        "withheld_players.json": exclusion,
    }
    for name, value in outputs.items():
        (OUTPUT / name).write_text(dump(value))
    freeze = {
        "version": VERSION,
        "source_workbook": str(WORKBOOK.relative_to(ROOT)),
        "source_workbook_sha256": sha256(WORKBOOK),
        "current_active_source": str(CURRENT.relative_to(ROOT)),
        "current_active_source_sha256": sha256(CURRENT),
        "season_sha256": sha256(OUTPUT / "season_projections.json"),
        "rankings_sha256": sha256(OUTPUT / "season_rankings.json"),
        "withheld_sha256": sha256(OUTPUT / "withheld_players.json"),
    }
    (OUTPUT / "release_freeze.json").write_text(dump(freeze))
    print(dump({
        "version": VERSION, "trusted": len(model["players"]),
        "withheld": exclusion["withheld_population"],
        "position_counts": model["metadata"]["position_counts"],
        "provider_requests": 0,
    }), end="")


if __name__ == "__main__":
    main()
