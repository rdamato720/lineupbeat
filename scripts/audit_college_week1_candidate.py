#!/usr/bin/env python3
"""Deterministic QA for a private-market college Week 1 candidate."""

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


PRIVATE_MARKET_TOKENS = (
    "american_price", "bookmaker_key", "consensus_line", "fair_over_probability",
    "game_total", "home_spread", "implied_team_total", "line_dispersion",
    "odds_quotes",
)
SCORING_FIELDS = (
    "passing_yards", "passing_td", "interceptions", "rushing_yards",
    "rushing_td", "receptions", "receiving_yards", "receiving_td",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rows(release, manifest):
    names = [name for name in manifest["files"] if name.endswith(".csv")]
    if len(names) != 1:
        raise AssertionError(f"expected one projection CSV, found {names}")
    with (release / "provenance" / names[0]).open(newline="") as handle:
        return list(csv.DictReader(handle)), names[0]


def points(row):
    n = lambda key: float(row[key])
    return (
        n("passing_yards") * .04 + n("passing_td") * 4 - n("interceptions")
        + n("rushing_yards") * .1 + n("rushing_td") * 6 + n("receptions")
        + n("receiving_yards") * .1 + n("receiving_td") * 6
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--min-prop-players", type=int, default=0)
    parser.add_argument("--report")
    args = parser.parse_args()

    release = Path(args.release)
    baseline = Path(args.baseline)
    manifest_path = release / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    site_path = release / "college_week1_site_projections_2026.json"
    site = json.loads(site_path.read_text())
    rows, csv_name = load_rows(release, manifest)

    assert manifest["status"] in {"CANDIDATE", "PUBLISHED"}
    assert manifest["qa_status"] == "PASS"
    assert manifest["source_release"] == "2026/v1.1"
    assert site["counts"] == {"players": 2205, "teams": 64, "games": 55}
    assert len(rows) == 2205
    assert len({row["player_id"] for row in rows}) == 2205

    calibration = manifest.get("private_market_calibration") or {}
    assert calibration.get("published_odds") is False
    assert int(calibration.get("game_snapshot_id") or 0) > 0
    assert calibration.get("game_snapshot_fetched_at")
    assert int(calibration.get("game_events_overlaid") or 0) > 0
    assert int(calibration.get("players_with_prop_adjustments") or 0) >= args.min_prop_players

    paths = {
        csv_name: release / "provenance" / csv_name,
        "college_week1_schedule_2026.json": release / "provenance" / "college_week1_schedule_2026.json",
        "college_week1_site_projections_2026.json": site_path,
    }
    for name, path in paths.items():
        assert manifest["files"][name]["bytes"] == path.stat().st_size
        assert manifest["files"][name]["sha256"] == digest(path)
        lowered = path.read_text().lower()
        leaked = [token for token in PRIVATE_MARKET_TOKENS if token in lowered]
        assert not leaked, (name, leaked)

    ranks = defaultdict(list)
    site_by_id = {row["id"]: row for row in site["players"]}
    for row in rows:
        assert all(float(row[key]) >= 0 for key in SCORING_FIELDS)
        assert abs(float(row["fantasy_points"]) - points(row)) < 1e-8
        ranks[row["position"]].append(int(row["position_rank"]))
        item = site_by_id[row["player_id"]]
        assert item["rank"] == int(row["position_rank"])
        assert item["overallRank"] == int(row["overall_rank"])
        assert item["pts"] == round(float(row["fantasy_points"]), 1)
    for values in ranks.values():
        assert sorted(values) == list(range(1, len(values) + 1))
    assert sorted(int(row["overall_rank"]) for row in rows) == list(range(1, 2206))

    baseline_manifest = json.loads((baseline / "manifest.json").read_text())
    baseline_rows, _ = load_rows(baseline, baseline_manifest)
    before = {row["player_id"]: row for row in baseline_rows}
    movers = []
    changed = 0
    for row in rows:
        old = before[row["player_id"]]
        delta = float(row["fantasy_points"]) - float(old["fantasy_points"])
        rank_delta = int(old["position_rank"]) - int(row["position_rank"])
        if abs(delta) > 1e-9:
            changed += 1
        movers.append({
            "player_id": row["player_id"], "player": row["player_name"],
            "team": row["team_name"], "position": row["position"],
            "old_points": round(float(old["fantasy_points"]), 3),
            "new_points": round(float(row["fantasy_points"]), 3),
            "point_delta": round(delta, 3), "position_rank_delta": rank_delta,
        })
    assert changed > 0
    movers.sort(key=lambda row: (-abs(row["point_delta"]), row["player"]))
    report = {
        "status": "PASS", "release": manifest["version"],
        "changed_players": changed, "calibration": calibration,
        "largest_point_changes": movers[:40],
    }
    report_path = Path(args.report) if args.report else release / "candidate_audit.json"
    report_path.write_text(json.dumps(report, indent=1) + "\n")
    print(
        f"candidate PASS: {changed} players changed; "
        f"{calibration['game_events_overlaid']} games overlaid; "
        f"{calibration['players_with_prop_adjustments']} prop-matched players"
    )


if __name__ == "__main__":
    main()
