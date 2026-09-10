"""Conserve the model's team opportunities and reconcile its passing offense.

This module consumes only Lineup Beat model output and its historical team
budgets. It never reads a comparison projection or calls a provider.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

VERSION = "team-workload-v1"
FAMILIES = {
    "attempts": ("attempts", "passing_yards", "passing_tds", "passing_interceptions"),
    "carries": ("carries", "rushing_yards", "rushing_tds"),
    "targets": ("targets", "receptions", "receiving_yards", "receiving_tds"),
}


def historical_role_priors(player24: list[dict], player25: list[dict]) -> dict:
    """Observed RB workload slots, including zero third-back usage, by game.

    Workload rank is a fallback for a newly promoted depth-chart back whose
    season role is effectively absent. It is not a claim that depth rank
    always predicts workload rank, nor a fitted predictive-lift claim.
    """
    years = []
    for rows in (player24, player25):
        games = defaultdict(list)
        for row in rows:
            if row.get("season_type") == "REG":
                games[(row["team"], row["week"])].append(row)
        fractions = {rank: [] for rank in (1, 2, 3)}
        rates = defaultdict(float)
        for group in games.values():
            total = sum(float(row.get("carries") or 0) for row in group)
            rb = sorted((row for row in group if row.get("position") == "RB"),
                        key=lambda row: -float(row.get("carries") or 0))
            if total <= 0:
                continue
            for rank in fractions:
                carries = float(rb[rank - 1].get("carries") or 0) if len(rb) >= rank else 0.0
                fractions[rank].append(carries / total)
            for row in rb:
                for key in ("carries", "rushing_yards", "rushing_tds"):
                    rates[key] += float(row.get(key) or 0)
        if not rates["carries"] or not fractions[1]:
            raise ValueError("missing historical RB workload evidence")
        years.append({
            "carry_share_by_rank": {str(rank): statistics.median(values) for rank, values in fractions.items()},
            "yards_per_carry": rates["rushing_yards"] / rates["carries"],
            "tds_per_carry": rates["rushing_tds"] / rates["carries"],
            "team_games": len(fractions[1]),
        })
    return {
        "carry_share_by_rank": {str(rank): .3 * years[0]["carry_share_by_rank"][str(rank)]
                                + .7 * years[1]["carry_share_by_rank"][str(rank)] for rank in (1, 2, 3)},
        "yards_per_carry": .3 * years[0]["yards_per_carry"] + .7 * years[1]["yards_per_carry"],
        "tds_per_carry": .3 * years[0]["tds_per_carry"] + .7 * years[1]["tds_per_carry"],
        "team_games": sum(y["team_games"] for y in years),
        "policy": "For an available depth-chart RB1/RB2/RB3 with under one modeled carry, seed the observed 2024/2025 median workload-rank share and league RB efficiency before normalization. No external projections used.",
    }


def allocate(players: list[dict], budgets: dict, role_priors: dict | None = None) -> dict:
    """Allocate full team budgets among available players, then reconcile.

    Preserve the independent model's relative shares and per-opportunity rates.
    A non-QB depth multiplier duplicates the role already represented in the
    reviewed shares, so remove it from legacy inputs. QB reserve discounts
    remain relative weights; their removed attempts return to the team pool.
    Stored inputs make reallocation deterministic when an availability report
    is applied again. Raw market quotes are neither needed nor stored here.
    """
    groups = defaultdict(list)
    for player in players:
        groups[player["team"]].append(player)
        if "allocation_input" not in player:
            stat = dict(player["stat_projection"])
            factor = float(player.get("role", {}).get("workload_factor", 1.0))
            if not math.isfinite(factor) or factor <= 0:
                raise ValueError(f"invalid role weight for {player['name']}")
            if player["position"] != "QB":
                stat = {key: value / factor for key, value in stat.items()}
            # Tiny negative historical yardage rates are not weekly workloads.
            if any(not math.isfinite(value) for value in stat.values()):
                raise ValueError(f"nonfinite model input for {player['name']}")
            player["allocation_input"] = {key: max(0.0, value) for key, value in stat.items()}
            player.setdefault("role", {})["removed_depth_discount"] = (
                factor if player["position"] != "QB" else 1.0
            )
        stat = dict(player["allocation_input"])
        if player.get("availability", {}).get("projection_adjusted"):
            stat = {key: 0.0 for key in stat}
        player["stat_projection"] = stat
        player["workload_allocation"] = {"version": VERSION, "factors": {}}

    audit = {}
    for club, group in sorted(groups.items()):
        if club not in budgets:
            raise ValueError(f"missing team workload budget: {club}")
        for player in group:
            rank = str(player.get("role", {}).get("depth_rank"))
            stat = player["stat_projection"]
            if (role_priors and player["position"] == "RB" and rank in ("1", "2", "3")
                    and not player.get("availability", {}).get("projection_adjusted")
                    and stat["carries"] < 1.0):
                carries = budgets[club]["carries"] * role_priors["carry_share_by_rank"][rank]
                if carries > stat["carries"]:
                    stat["carries"] = carries
                    stat["rushing_yards"] = carries * role_priors["yards_per_carry"]
                    stat["rushing_tds"] = carries * role_priors["tds_per_carry"]
                    player["workload_allocation"]["sparse_role_fallback"] = {
                        "depth_rank": int(rank), "seed_carries": carries,
                        "source": "2024/2025 observed median RB workload-rank share",
                    }
        before = {key: sum(p["stat_projection"][key] for p in group) for key in FAMILIES}
        for opportunity, components in FAMILIES.items():
            budget = float(budgets[club][opportunity])
            if not math.isfinite(budget) or budget < 0:
                raise ValueError(f"invalid {club} {opportunity} budget")
            if opportunity == "targets":
                budget = min(budget, float(budgets[club]["attempts"]))
            total = before[opportunity]
            if budget > 0 and total <= 0:
                raise ValueError(f"no available {club} players to allocate {opportunity}")
            factor = budget / total if total else 0.0
            for player in group:
                for key in components:
                    player["stat_projection"][key] *= factor
                player["workload_allocation"]["factors"][opportunity] = factor

        # QB production anchors the passing offense. Receiver-specific model
        # and bounded market adjustments determine relative receiving shares.
        # Reconciliation is separate from, and follows, those market blends.
        for passing, receiving in (("passing_yards", "receiving_yards"),
                                   ("passing_tds", "receiving_tds")):
            passing_total = sum(p["stat_projection"][passing] for p in group)
            receiving_total = sum(p["stat_projection"][receiving] for p in group)
            if passing_total > 0 and receiving_total <= 0:
                raise ValueError(f"no available {club} receiving weights for {receiving}")
            factor = passing_total / receiving_total if receiving_total else 0.0
            for player in group:
                player["stat_projection"][receiving] *= factor
                player["workload_allocation"]["factors"][receiving] = factor

        for player in group:
            stat = player["stat_projection"]
            stat["receptions"] = min(stat["receptions"], stat["targets"])
            old_opportunities = sum(player["allocation_input"][key] for key in FAMILIES)
            new_opportunities = sum(stat[key] for key in FAMILIES)
            stat["fumbles_lost_total"] = (
                player["allocation_input"].get("fumbles_lost_total", 0.0)
                * new_opportunities / old_opportunities if old_opportunities else 0.0
            )
            player["stat_projection"] = {key: round(value, 3) for key, value in stat.items()}
            player["expected_opportunity"] = {
                "pass_attempts": round(stat["attempts"], 1),
                "carries": round(stat["carries"], 1), "targets": round(stat["targets"], 1),
            }
        audit[club] = {
            "before_allocation": before,
            "budget": {key: (min(budgets[club][key], budgets[club]["attempts"])
                             if key == "targets" else budgets[club][key]) for key in FAMILIES},
            "after_allocation": {key: round(sum(p["stat_projection"][key] for p in group), 3)
                                 for key in (*FAMILIES, "passing_yards", "receiving_yards",
                                             "passing_tds", "receiving_tds")},
        }
    return audit


def problems(players: list[dict], budgets: dict) -> list[str]:
    """Validate actual output sums, not cached audit totals or a PASS flag."""
    errors = []
    groups = defaultdict(list)
    for player in players:
        groups[player["team"]].append(player)
        stat = player.get("stat_projection", {})
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in stat.values()):
            errors.append(f"{player['name']}: invalid or negative stat")
        if stat.get("receptions", 0) > stat.get("targets", 0) + .001:
            errors.append(f"{player['name']}: receptions exceed targets")
        if player.get("availability", {}).get("projection_adjusted") and any(stat.values()):
            errors.append(f"{player['name']}: unavailable player has workload")
    for club, group in groups.items():
        tolerance = len(group) * .001 + .001
        totals = {key: sum(p["stat_projection"].get(key, 0) for p in group)
                  for key in (*FAMILIES, "passing_yards", "receiving_yards", "passing_tds", "receiving_tds")}
        for key in FAMILIES:
            budget = budgets.get(club, {}).get(key)
            if budget is None or not math.isfinite(budget) or budget < 0:
                errors.append(f"{club}: missing or invalid {key} budget")
                continue
            if key == "targets":
                budget = min(budget, budgets[club].get("attempts", budget))
            if abs(totals[key] - budget) > tolerance:
                errors.append(f"{club}: {key} do not reconcile to team budget")
        for passing, receiving in (("passing_yards", "receiving_yards"), ("passing_tds", "receiving_tds")):
            if abs(totals[passing] - totals[receiving]) > tolerance:
                errors.append(f"{club}: {passing} and {receiving} do not reconcile")
    return errors


def finalize(payload: dict, budgets: dict | None = None, role_priors: dict | None = None) -> dict:
    """Reallocate, rescore and rerank every consumer's common source atomically."""
    from build_week1_intelligence import score
    budgets = budgets if budgets is not None else payload["team_workload_budgets"]
    payload["team_workload_budgets"] = budgets
    role_priors = role_priors if role_priors is not None else payload.get("workload_role_priors")
    if role_priors:
        payload["workload_role_priors"] = role_priors
    payload["team_workload_audit"] = allocate(payload["players"], budgets, role_priors)
    errors = problems(payload["players"], budgets)
    if errors:
        raise ValueError("; ".join(errors))
    for fmt, reception in (("ppr", 1.0), ("half_ppr", .5), ("non_ppr", 0.0)):
        counts = defaultdict(int)
        for player in payload["players"]:
            player["formats"][fmt]["projected_points"] = round(score(player["stat_projection"], reception), 1)
        for rank, player in enumerate(sorted(payload["players"], key=lambda p: (-p["formats"][fmt]["projected_points"], p["name"])), 1):
            counts[player["position"]] += 1
            player["formats"][fmt].update(overall_rank=rank, position_rank=counts[player["position"]])
    payload["players"].sort(key=lambda p: (p["position"], p["formats"]["half_ppr"]["position_rank"], p["name"]))
    payload.setdefault("methodology", {})["workload_allocation"] = VERSION
    payload["methodology"]["allocation_policy"] = (
        "Historical team attempts, carries and targets are fully allocated among available players. "
        "Reviewed relative role shares are retained without an additional non-QB depth discount. "
        "QB reserve weights remain relative shares. Per-opportunity rates and bounded market blends "
        "are applied before team allocation. Final receiving yards and TDs reconcile to the team's "
        "passing yards and TDs; reconciliation can change the final market-adjusted component. "
        "Confirmed unavailable players receive zero and their opportunity returns to the team pool."
    )
    return payload
