#!/usr/bin/env python3
"""Reallocate an existing owned snapshot without recapturing private markets.

Use the snapshot's recorded model output (including its bounded market
adjustments) and digest-verified historical team volumes. Source timestamps
are preserved. This is a model repair, not a fresh provider capture.
"""
import argparse
import hashlib
import json
from pathlib import Path

import build_week1_intelligence as model
from capture_week1_inputs import CATALOG
from nfl_workload_allocation import finalize, historical_role_priors
from weekly_availability import apply_reports


def unblend(value: float, line: float) -> float:
    """Invert the existing monotone bounded blend for a recorded component."""
    low, high = 0.0, max(1.0, value * 2)
    for _ in range(60):
        middle = (low + high) / 2
        if model.blend_market_component(middle, line) < value:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def repair_model_inputs(payload: dict, player24: list[dict], player25: list[dict]) -> None:
    history = model.player_history(player24 + player25)
    priors = {(r['name'], model.team(r['team']), r['pos']): r for r in model.season_prior_rows()}
    denominators = {"passing_yards": "attempts", "passing_interceptions": "attempts",
                    "rushing_yards": "carries", "receptions": "targets", "receiving_yards": "targets"}
    for player in payload['players']:
        if player.get('availability', {}).get('projection_adjusted'):
            continue
        prior = priors[(player['name'], player['team'], player['position'])]
        records = history[player['id']].get(2024, []) + history[player['id']].get(2025, [])
        role_factor = player['role']['workload_factor'] if player['position'] != 'QB' else 1.0
        lines = player.get('market', {}).get('consensus_lines', {})
        adjustments = {}
        for key, value in player['stat_projection'].items():
            component = unblend(value, lines[key]) if key in lines and value > 0 else value
            component /= role_factor
            if key in denominators:
                denominator = denominators[key]
                hden = sum(model.num(r, denominator) for r in records)
                hval = sum(model.num(r, key) for r in records) / hden if hden else None
                pden = model.projected_component(prior, denominator)
                pval = model.projected_component(prior, key) / pden if pden else None
                old_rate = model.weighted(hval, pval)
                new_rate = model.efficiency_rate(hval, pval, hden, denominator)
                if old_rate > 0:
                    component *= new_rate / old_rate
                if abs(new_rate - max(0.0, old_rate)) > .00001:
                    adjustments[key] = {"historical_opportunities": hden,
                                        "old_rate": old_rate, "new_rate": new_rate}
            component = max(0.0, component)
            if key in lines:
                component = model.blend_market_component(component, lines[key])
            player['stat_projection'][key] = component
        if player['position'] != 'QB':
            player['role']['previous_workload_factor'] = role_factor
            player['role']['workload_factor'] = 1.0
        if adjustments:
            player['efficiency_repair'] = adjustments


def repair(snapshot: Path, cache: Path, output: Path) -> dict:
    hashes = {name: digest for _, name, digest in CATALOG}
    inputs = {}
    for name in [f"stats_{kind}_week_{year}.csv.gz" for kind in ("team", "player") for year in (2024, 2025)]:
        digest = hashlib.sha256((cache / name).read_bytes()).hexdigest()
        if digest != hashes[name]:
            raise ValueError(f"historical input digest mismatch: {name}")
        inputs[name] = digest
    model.CACHE = cache
    teams = {year: model.team_averages(model.rows(f"stats_team_week_{year}.csv.gz"), year)
             for year in (2024, 2025)}
    raw = snapshot.read_bytes()
    payload = json.loads(raw)
    if payload.get("methodology", {}).get("workload_allocation"):
        raise ValueError("snapshot already uses team workload allocation; use its normal rebuild")
    player24, player25 = model.rows("stats_player_week_2024.csv.gz"), model.rows("stats_player_week_2025.csv.gz")
    repair_model_inputs(payload, player24, player25)
    budgets = {club: {key: model.weighted(teams[2025][club][key], teams[2024][club][key])
                      for key in ("attempts", "carries", "targets")}
               for club in {p["team"] for p in payload["players"]}}
    apply_reports(payload)
    finalize(payload, budgets, historical_role_priors(player24, player25))
    payload['methodology']['role_policy'] = (
        "Reviewed current role weights with limited current-team historical shrinkage; "
        "normalized across available players without a duplicate non-QB depth discount."
    )
    payload['methodology']['efficiency_policy'] = (
        "Historical efficiency weight grows with sample size to the existing 70% ceiling "
        "at 200 pass attempts or 100 carries/targets; otherwise favor the reviewed prior. "
        "Thresholds are modeling assumptions, not backtested optimal values."
    )
    payload.setdefault("model_repairs", []).append({
        "kind": "team-workload-v1", "source_snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "historical_inputs_sha256": inputs,
        "new_provider_calls": 0, "new_model_calls": 0,
        "policy": "Reallocate recorded owned model inputs; preserve provider timestamps. No comparison projections consumed.",
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(output)
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = repair(args.snapshot, args.cache, args.output)
    print(f"Reallocated {len(payload['players'])} players across {len(payload['team_workload_budgets'])} teams; zero provider calls")
