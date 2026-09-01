#!/usr/bin/env python3
"""Build the development-only Lineup Beat NFL Week 1 evidence artifacts.

The model owns its weekly stat line.  Full-season projections contribute
within-team shares and efficiency priors only; no season total is divided by
17 (or by any assumed games-played value).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_projections  # noqa: E402
import decision_data  # noqa: E402

CACHE = ROOT / ".cache" / "week1-intelligence"
OUTPUT = ROOT / "data" / "week1" / "2026" / "v1.0"
POSITIONS = ("QB", "RB", "WR", "TE")
TEAM_ALIASES = {"LA": "LAR", "JAC": "JAX", "WSH": "WAS", "OAK": "LV",
                "SD": "LAC", "STL": "LAR"}
STAT_KEYS = ("attempts", "passing_yards", "passing_tds", "passing_interceptions",
             "carries", "rushing_yards", "rushing_tds", "receptions", "targets",
             "receiving_yards", "receiving_tds", "fumbles_lost_total")


def team(value: str | None) -> str:
    value = str(value or "").upper()
    return TEAM_ALIASES.get(value, value)


def num(row: dict, key: str) -> float:
    try:
        value = row.get(key)
        return float(value) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def rows(name: str) -> list[dict]:
    with gzip.open(CACHE / name, "rt", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def score(stat: dict, reception_value: float) -> float:
    return (
        num(stat, "passing_yards") * .04
        + num(stat, "passing_tds") * 4
        - num(stat, "passing_interceptions") * 2
        + num(stat, "rushing_yards") * .1
        + num(stat, "rushing_tds") * 6
        + num(stat, "receiving_yards") * .1
        + num(stat, "receiving_tds") * 6
        + num(stat, "receptions") * reception_value
        - num(stat, "fumbles_lost_total") * 2
        + (num(stat, "passing_2pt_conversions")
           + num(stat, "rushing_2pt_conversions")
           + num(stat, "receiving_2pt_conversions")) * 2
        + num(stat, "special_teams_tds") * 6
    )


def mean(values: list[float], default: float = 0.0) -> float:
    return statistics.fmean(values) if values else default


def weighted(v25: float | None, v24: float | None) -> float:
    if v25 is not None and v24 is not None:
        return .7 * v25 + .3 * v24
    return v25 if v25 is not None else (v24 if v24 is not None else 0.0)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def season_prior_rows() -> list[dict]:
    sheets = build_projections.read_sheet(ROOT / "data" / "projections.xlsx")
    return [row for position in POSITIONS for row in sheets[position]]


def projected_component(row: dict, key: str) -> float:
    mapping = {
        "attempts": "patt", "passing_yards": "payd", "passing_tds": "patd",
        "passing_interceptions": "int", "carries": "ruatt",
        "rushing_yards": "ruyd", "rushing_tds": "rutd", "targets": "targets",
        "receptions": "rec", "receiving_yards": "recyd", "receiving_tds": "rectd",
        "fumbles_lost_total": "fl",
    }
    return float(row.get(mapping[key]) or 0.0)


def validate_schedule(schedule: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    slate = [row for row in schedule if row.get("season") == "2026"
             and row.get("week") == "1" and row.get("game_type") == "REG"]
    clubs = [team(row[side]) for row in slate for side in ("away_team", "home_team")]
    if len(slate) != 16 or len(clubs) != 32 or len(set(clubs)) != 32:
        raise ValueError(f"invalid 2026 Week 1 schedule: {len(slate)} games/{len(set(clubs))} teams")
    by_team = {}
    for row in slate:
        away, home = team(row["away_team"]), team(row["home_team"])
        kickoff = f"{row['gameday']}T{row['gametime']}:00-04:00"
        by_team[away] = {"opponent": home, "home": False, "kickoff": kickoff,
                         "game_id": row["game_id"]}
        by_team[home] = {"opponent": away, "home": True, "kickoff": kickoff,
                         "game_id": row["game_id"]}
    return slate, by_team


def team_averages(team_rows: list[dict], season: int) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in team_rows:
        if row.get("season") == str(season) and row.get("season_type") == "REG":
            grouped[team(row["team"])].append(row)
    return {club: {key: mean([num(row, key) for row in games]) for key in STAT_KEYS}
            for club, games in grouped.items()}


def player_history(player_rows: list[dict]) -> dict[str, dict[int, list[dict]]]:
    result: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in player_rows:
        if row.get("season_type") != "REG" or row.get("position") not in POSITIONS:
            continue
        pid = row.get("player_id")
        if pid:
            result[pid][int(row["season"])].append(row)
    return result


def defense_context(player_rows_2025: list[dict], pbp_rows: list[dict],
                    schedule_2025: list[dict]) -> dict[str, dict]:
    games_by_def = defaultdict(set)
    for row in schedule_2025:
        if row.get("season") == "2025" and row.get("game_type") == "REG":
            games_by_def[team(row["away_team"])].add(row["game_id"])
            games_by_def[team(row["home_team"])].add(row["game_id"])

    allowed = defaultdict(lambda: defaultdict(float))
    offense_games = defaultdict(lambda: defaultdict(set))
    offense_points = defaultdict(lambda: defaultdict(float))
    for row in player_rows_2025:
        if row.get("season_type") != "REG" or row.get("position") not in POSITIONS:
            continue
        defense, offense, pos = team(row.get("opponent_team")), team(row.get("team")), row["position"]
        points = score(row, 1.0)
        allowed[defense][pos] += points
        offense_points[offense][pos] += points
        offense_games[offense][pos].add(row.get("game_id"))
    offense_avg = {club: {pos: offense_points[club][pos] / max(1, len(offense_games[club][pos]))
                          for pos in POSITIONS} for club in offense_points}
    league_avg = {pos: mean([values.get(pos, 0.0) for values in offense_avg.values()])
                  for pos in POSITIONS}

    game_allowed = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for row in player_rows_2025:
        if row.get("season_type") == "REG" and row.get("position") in POSITIONS:
            game_allowed[team(row.get("opponent_team"))][row.get("game_id")][row["position"]] += score(row, 1.0)
    adjusted = defaultdict(dict)
    for defense, games in game_allowed.items():
        for pos in POSITIONS:
            ratios = []
            for game_id, vals in games.items():
                offense = next((team(r["team"]) for r in player_rows_2025
                                if r.get("game_id") == game_id and team(r.get("opponent_team")) == defense), None)
                expected = (offense_avg.get(offense, {}).get(pos) if offense else None)
                if expected and expected > 0:
                    ratios.append(vals.get(pos, 0.0) / expected)
            adjusted[defense][pos] = mean(ratios, 1.0) * league_avg[pos]

    play = defaultdict(lambda: {"rush_plays": 0, "rush_success": 0, "rush_explosive": 0,
                                "pass_plays": 0, "pass_success": 0, "pass_explosive": 0,
                                "red_zone_plays": 0, "red_zone_tds": 0})
    for row in pbp_rows:
        if row.get("season_type") != "REG" or not row.get("defteam"):
            continue
        defense = team(row["defteam"])
        if num(row, "rush_attempt") == 1 and num(row, "qb_kneel") == 0:
            play[defense]["rush_plays"] += 1
            play[defense]["rush_success"] += int(num(row, "success") == 1)
            play[defense]["rush_explosive"] += int(num(row, "yards_gained") >= 10)
        if num(row, "qb_dropback") == 1 and num(row, "qb_spike") == 0:
            play[defense]["pass_plays"] += 1
            play[defense]["pass_success"] += int(num(row, "success") == 1)
            play[defense]["pass_explosive"] += int(num(row, "complete_pass") == 1 and num(row, "yards_gained") >= 20)
        if row.get("posteam") and 0 < num(row, "yardline_100") <= 20 and row.get("play_type") in {"run", "pass"}:
            play[defense]["red_zone_plays"] += 1
            play[defense]["red_zone_tds"] += int(num(row, "touchdown") == 1)

    result = {}
    for defense in sorted(games_by_def):
        game_count = len(games_by_def[defense])
        p = play[defense]
        pos = {}
        for position in POSITIONS:
            raw = allowed[defense][position] / max(1, game_count)
            adj = adjusted[defense].get(position, raw)
            pos[position] = {"ppr_points_allowed_per_game": round(raw, 2),
                             "opponent_adjusted_ppr_allowed": round(adj, 2),
                             "league_average": round(league_avg[position], 2)}
        result[defense] = {
            "season": 2025, "games": game_count,
            "rushing": {"success_rate_allowed": round(p["rush_success"] / max(1, p["rush_plays"]), 4),
                         "explosive_rate_allowed": round(p["rush_explosive"] / max(1, p["rush_plays"]), 4),
                         "plays": p["rush_plays"]},
            "passing": {"success_rate_allowed": round(p["pass_success"] / max(1, p["pass_plays"]), 4),
                         "explosive_rate_allowed": round(p["pass_explosive"] / max(1, p["pass_plays"]), 4),
                         "plays": p["pass_plays"]},
            "red_zone_td_rate_allowed": round(p["red_zone_tds"] / max(1, p["red_zone_plays"]), 4),
            "red_zone_plays": p["red_zone_plays"], "position_fantasy_allowed": pos,
        }
    return result


def backtest(player24: list[dict], player25: list[dict], schedule: list[dict]) -> dict:
    prior = [row for row in player24 if row.get("season_type") == "REG" and row.get("position") in POSITIONS]
    actual = [row for row in player25 if row.get("season_type") == "REG" and row.get("position") in POSITIONS]
    home = {}
    for game in schedule:
        if game.get("season") == "2025" and game.get("game_type") == "REG":
            home[(game["game_id"], team(game["home_team"]))] = True
            home[(game["game_id"], team(game["away_team"]))] = False
    errors = defaultdict(lambda: {"proxy": [], "baseline": []})
    failures = []
    eligible = 0
    leakage_checks = 0
    for week in range(1, 19):
        train = prior + [row for row in actual if int(row["week"]) < week]
        leakage_checks += sum(1 for row in train if row.get("season") == "2025" and int(row["week"]) >= week)
        by_player = defaultdict(list)
        allowed = defaultdict(lambda: defaultdict(list))
        for row in train:
            by_player[row.get("player_id")].append(row)
            allowed[team(row.get("opponent_team"))][row["position"]].append(score(row, .5))
        league = {pos: mean([score(row, .5) for row in train if row["position"] == pos]) for pos in POSITIONS}
        for row in (r for r in actual if int(r["week"]) == week):
            eligible += 1
            history = by_player.get(row.get("player_id"), [])
            if len(history) < 2:
                continue
            recent = history[-8:]
            baseline = mean([score(r, .5) for r in recent])
            opp_values = allowed[team(row.get("opponent_team"))][row["position"]]
            shrink = min(1.0, len(opp_values) / 8.0)
            opp_ratio = mean(opp_values, league[row["position"]]) / max(.1, league[row["position"]])
            opp_factor = 1 + (clamp(opp_ratio, .85, 1.15) - 1) * shrink
            venue_factor = 1.01 if home.get((row.get("game_id"), team(row.get("team")))) else .99
            prediction = baseline * opp_factor * venue_factor
            observed = score(row, .5)
            errors[row["position"]]["proxy"].append(abs(prediction - observed))
            errors[row["position"]]["baseline"].append(abs(baseline - observed))
            failures.append({"player_id": row.get("player_id"), "player": row.get("player_display_name"),
                             "position": row["position"], "week": week,
                             "projected": round(prediction, 2), "actual": round(observed, 2),
                             "absolute_error": round(abs(prediction - observed), 2)})
    by_position = {}
    covered = 0
    for pos in POSITIONS:
        proxy_errors = errors[pos]["proxy"]
        covered += len(proxy_errors)
        by_position[pos] = {"predictions": len(proxy_errors),
                            "proxy_mae": round(mean(proxy_errors), 3),
                            "baseline_mae": round(mean(errors[pos]["baseline"]), 3)}
    if leakage_checks:
        raise ValueError("backtest leakage detected")
    return {"schema_version": "lineupbeat-week1-backtest-v1", "season": 2025,
            "scoring_format": "Half-PPR", "evaluation_population":
            "QB/RB/WR/TE weekly stat rows with at least two strictly prior appearances",
            "baseline": "mean Half-PPR points over the player's last eight prior appearances",
            "evaluation_type": "proxy_context_adjustment_backtest",
            "production_formula_reproduced": False,
            "proxy": (
                "last-eight-points baseline adjusted by prior opponent-position allowance "
                "and historical venue context"
            ),
            "limitation": (
                "This walk-forward test does not reproduce the deployed production formula's "
                "team volume, player shares, efficiencies, current depth, or season priors."
            ),
            "future_rows_used": 0, "eligible_appearances": eligible, "predictions": covered,
            "coverage_percent": round(100 * covered / max(1, eligible), 1),
            "by_position": by_position,
            "failure_cases": sorted(failures, key=lambda x: -x["absolute_error"])[:10]}


def build() -> tuple[dict, dict, dict, dict]:
    manifest = json.loads((CACHE / "capture_manifest.json").read_text())
    schedule = rows("games.csv.gz")
    slate, slate_by_team = validate_schedule(schedule)
    p24, p25 = rows("stats_player_week_2024.csv.gz"), rows("stats_player_week_2025.csv.gz")
    t24, t25 = rows("stats_team_week_2024.csv.gz"), rows("stats_team_week_2025.csv.gz")
    roster_rows, depth_rows = rows("roster_2026.csv.gz"), rows("depth_charts_2026.csv.gz")
    snap24, snap25 = rows("snap_counts_2024.csv.gz"), rows("snap_counts_2025.csv.gz")
    pbp25 = rows("play_by_play_2025.csv.gz")
    # The 2024 PBP asset is captured and licensed for the backtest audit; the
    # current matchup artifact intentionally uses only the labeled 2025 prior.
    matchup = defense_context(p25, pbp25, schedule)
    backtest_result = backtest(p24, p25, schedule)

    base = decision_data.load_season()
    history = player_history(p24 + p25)
    roster = {row["gsis_id"]: row for row in roster_rows if row.get("gsis_id")}
    if any(player["id"] not in roster for player in base["players"]):
        raise ValueError("current projection identity failed stable GSIS roster reconciliation")
    latest_depth = {}
    for row in depth_rows:
        pid = row.get("gsis_id")
        if pid and (pid not in latest_depth or row.get("dt", "") > latest_depth[pid].get("dt", "")):
            latest_depth[pid] = row
    pfr_to_id = {row.get("pfr_id"): row.get("gsis_id") for row in roster_rows if row.get("pfr_id") and row.get("gsis_id")}
    snaps = defaultdict(list)
    for row in snap24 + snap25:
        pid = pfr_to_id.get(row.get("pfr_player_id"))
        if pid and row.get("game_type") == "REG":
            snaps[pid].append(num(row, "offense_pct"))

    team24, team25 = team_averages(t24, 2024), team_averages(t25, 2025)
    prior_rows = season_prior_rows()
    prior_by_key = {(row["name"], team(row["team"]), row["pos"]): row for row in prior_rows}
    team_prior_totals = defaultdict(lambda: defaultdict(float))
    for row in prior_rows:
        for key in STAT_KEYS:
            team_prior_totals[team(row["team"])][key] += projected_component(row, key)

    players = []
    excluded = []
    for player in base["players"]:
        club, pid, pos = team(player["team"]), player["id"], player["position"]
        roster_row = roster[pid]
        if team(roster_row.get("team")) != club:
            raise ValueError(f"team identity mismatch for {player['name']}")
        if roster_row.get("status") != "ACT":
            excluded.append({"player_id": pid, "name": player["name"], "team": club,
                             "position": pos, "reason": f"nflverse roster status {roster_row.get('status')}; current injury report unavailable"})
            continue
        prior = prior_by_key.get((player["name"], club, pos))
        if not prior:
            raise ValueError(f"missing season-prior stat line for {player['name']}")
        hist25 = history[pid].get(2025, [])
        hist24 = history[pid].get(2024, [])
        current_team_history = [r for r in hist25 if team(r.get("team")) == club]
        stat = {}
        shares = {}
        for key in ("attempts", "carries", "targets"):
            base_volume = weighted(team25.get(club, {}).get(key), team24.get(club, {}).get(key))
            season_share = projected_component(prior, key) / max(.001, team_prior_totals[club][key])
            hist_total = sum(num(r, key) for r in current_team_history)
            team_total = sum(num(r, key) for r in t25 if team(r.get("team")) == club and r.get("season_type") == "REG")
            hist_share = hist_total / team_total if team_total and len(current_team_history) >= 4 else None
            share = .6 * hist_share + .4 * season_share if hist_share is not None else season_share
            shares[key] = share
            stat[key] = max(0.0, base_volume * share)

        def efficiency(numerator: str, denominator: str, prior_num: str, prior_den: str) -> float:
            historical = hist25 + hist24
            hden = sum(num(r, denominator) for r in historical)
            hval = sum(num(r, numerator) for r in historical) / hden if hden else None
            pden = projected_component(prior, prior_den)
            pval = projected_component(prior, prior_num) / pden if pden else None
            return weighted(hval, pval)

        stat["passing_yards"] = stat["attempts"] * efficiency("passing_yards", "attempts", "passing_yards", "attempts")
        stat["passing_interceptions"] = stat["attempts"] * efficiency("passing_interceptions", "attempts", "passing_interceptions", "attempts")
        stat["rushing_yards"] = stat["carries"] * efficiency("rushing_yards", "carries", "rushing_yards", "carries")
        stat["receptions"] = stat["targets"] * efficiency("receptions", "targets", "receptions", "targets")
        stat["receiving_yards"] = stat["targets"] * efficiency("receiving_yards", "targets", "receiving_yards", "targets")
        for td_key, volume_key in (("passing_tds", "attempts"), ("rushing_tds", "carries"), ("receiving_tds", "targets")):
            team_volume = weighted(team25.get(club, {}).get(td_key), team24.get(club, {}).get(td_key))
            prior_share = projected_component(prior, td_key) / max(.001, team_prior_totals[club][td_key])
            hist_total = sum(num(r, td_key) for r in current_team_history)
            team_total = sum(num(r, td_key) for r in t25 if team(r.get("team")) == club and r.get("season_type") == "REG")
            hist_share = hist_total / team_total if team_total and len(current_team_history) >= 4 else None
            share = .6 * hist_share + .4 * prior_share if hist_share is not None else prior_share
            stat[td_key] = max(0.0, team_volume * share)
        opportunities = stat["attempts"] + stat["carries"] + stat["targets"]
        historical = hist25 + hist24
        hist_opp = sum(num(r, "attempts") + num(r, "carries") + num(r, "targets") for r in historical)
        hist_fumbles = sum(num(r, "fumbles_lost_total") for r in historical)
        stat["fumbles_lost_total"] = opportunities * (hist_fumbles / hist_opp if hist_opp else 0.0)
        for key in ("passing_2pt_conversions", "rushing_2pt_conversions", "receiving_2pt_conversions", "special_teams_tds"):
            stat[key] = 0.0

        opponent = slate_by_team[club]["opponent"]
        pos_match = matchup[opponent]["position_fantasy_allowed"][pos]
        opp_ratio = pos_match["opponent_adjusted_ppr_allowed"] / max(.1, pos_match["league_average"])
        matchup_factor = clamp(opp_ratio, .90, 1.10)
        venue_factor = 1.01 if slate_by_team[club]["home"] else .99
        # Apply validated context only to efficiency outcomes, never to the
        # evidence-based opportunity counts.
        for key in ("passing_yards", "passing_tds", "rushing_yards", "rushing_tds",
                    "receiving_yards", "receiving_tds"):
            stat[key] *= matchup_factor * venue_factor
        rounded_stat = {key: round(value, 3) for key, value in stat.items()}
        formats = {}
        for fmt, reception_value in (("ppr", 1.0), ("half_ppr", .5), ("non_ppr", 0.0)):
            formats[fmt] = {"projected_points": round(score(rounded_stat, reception_value), 1)}
        coverage = {
            "historical_weekly": bool(hist25 or hist24), "opportunity": True,
            "team_volume": True, "current_roster": True,
            "depth_chart": pid in latest_depth, "snap_participation": bool(snaps[pid]),
            "opponent_matchup": opponent in matchup, "current_injury_report": False,
            "betting_market": False,
        }
        depth = latest_depth.get(pid, {})
        players.append({**{k: player[k] for k in ("id", "slug", "name", "team", "position", "adp", "photo", "team_logo", "history", "history_season")},
                        "formats": formats, "opponent": opponent,
                        "home": slate_by_team[club]["home"], "kickoff": slate_by_team[club]["kickoff"],
                        "game_id": slate_by_team[club]["game_id"],
                        "stat_projection": rounded_stat,
                        "expected_opportunity": {"pass_attempts": round(stat["attempts"], 1),
                                                 "carries": round(stat["carries"], 1),
                                                 "targets": round(stat["targets"], 1)},
                        "role": {"roster_status": roster_row.get("status"),
                                 "depth_position": depth.get("pos_abb"),
                                 "depth_rank": int(float(depth["pos_rank"])) if depth.get("pos_rank") else None,
                                 "2025_average_offense_snap_pct": round(mean(snaps[pid]), 3) if snaps[pid] else None},
                        "availability": {"state": "active_roster", "injury_report": "unavailable"},
                        "identity_resolution": {
                            "method": "normalized name plus exact team and position",
                            "stable_gsis_id": pid,
                            "roster_record": True,
                            "season_prior_stat_line": True,
                        },
                        "matchup": {"label": "2025 defensive context", "opponent": opponent,
                                    "position": pos, "projection_factor": round(matchup_factor, 3),
                                    **pos_match, "rushing": matchup[opponent]["rushing"],
                                    "passing": matchup[opponent]["passing"],
                                    "red_zone_td_rate_allowed": matchup[opponent]["red_zone_td_rate_allowed"]},
                        "market": {"state": "unavailable", "reason": "THE_ODDS_API_KEY unavailable; zero requests made"},
                        "data_coverage": coverage})

    for fmt in ("ppr", "half_ppr", "non_ppr"):
        ordered = sorted(players, key=lambda p: (-p["formats"][fmt]["projected_points"], p["name"]))
        position_counts = defaultdict(int)
        for overall, player in enumerate(ordered, 1):
            position_counts[player["position"]] += 1
            player["formats"][fmt]["overall_rank"] = overall
            player["formats"][fmt]["position_rank"] = position_counts[player["position"]]
    players.sort(key=lambda p: (p["position"], p["formats"]["half_ppr"]["position_rank"], p["name"]))
    ratios = [p["formats"]["half_ppr"]["projected_points"] /
              max(.1, next(x for x in base["players"] if x["id"] == p["id"])["formats"]["half_ppr"]["projected_points"])
              for p in players]
    if statistics.pstdev(ratios) < .005:
        raise ValueError("weekly outputs are effectively season totals divided by a constant")
    for player in players:
        for fmt, reception_value in (("ppr", 1.0), ("half_ppr", .5), ("non_ppr", 0.0)):
            if abs(player["formats"][fmt]["projected_points"] - round(score(player["stat_projection"], reception_value), 1)) > .01:
                raise ValueError(f"scoring reconciliation failed for {player['name']} {fmt}")

    payload = {"schema_version": "lineupbeat-nfl-week1-v1", "mode": "weekly", "season": 2026, "week": 1,
               "updated_at": manifest["captured_at"], "players": players, "excluded_players": excluded,
               "population": {
                   **base["population"],
                   "ranked_active_projected": len(players),
                   "ranked_excluded": len(excluded),
               },
               "unresolved_players": base["unresolved_players"],
               "identity_method": base["identity_method"],
               "limitations": {
                   "sportsbook_evidence": "unavailable; zero provider requests",
                   "current_injury_report": "unavailable",
                   "dst_model": "unavailable; model population is QB/RB/WR/TE only",
                   "predictive_lift_claim": False,
                   "matchup_context": "2025 prior-season context",
               },
               "available_formats": ["ppr", "half_ppr", "non_ppr"],
               "editorial_opinions": base["editorial_opinions"],
               "schedule_sos_available": True,
               "sources": {"model": {"label": "Lineup Beat-owned Week 1 model", "updated_at": manifest["captured_at"]},
                           "season_prior": base["sources"]["projections"],
                           "history": {"label": "nflverse weekly player/team statistics", "updated_at": "2025 regular season"},
                           "matchup": {"label": "nflverse 2025 prior-season defensive context", "updated_at": manifest["captured_at"]},
                           "market": {"label": "Unavailable — zero provider requests", "updated_at": None},
                           "injuries": {"label": "2026 injury report unavailable", "updated_at": None}},
               "methodology": {"season_total_divisor": None,
                               "summary": "Historical team weekly volume × blended historical/current-team and season-prior player shares; historical and season-prior efficiencies; bounded 2025 opponent and venue adjustments.",
                               "scoring": "0.04/pass yard, 4/pass TD, -2/interception, 0.1/rush or receiving yard, 6/rush or receiving TD, -2/fumble lost, plus format reception points.",
                               "recommendation_guardrail": "A point difference alone cannot create an unqualified recommendation."}}
    matchup_payload = {"schema_version": "lineupbeat-nfl-matchup-2025-v1", "season": 2025,
                       "label": "2025 prior-season defensive context",
                       "definitions": {"rush_explosive": "10+ rushing yards", "pass_explosive": "completed pass gaining 20+ yards",
                                       "success": "nflverse play-level success field", "red_zone": "scrimmage play at or inside opponent 20",
                                       "opponent_adjustment": "game-level position PPR allowed divided by that offense's 2025 position average, then rescaled to league average"},
                       "teams": matchup}
    provenance = {"schema_version": "lineupbeat-week1-provenance-v1", "generated_at": manifest["captured_at"],
                  "license_review": manifest["license_review"], "assets": manifest["assets"],
                  "provider_requests": {"odds": 0, "player_props": 0, "model_api": 0, "cost_usd": 0,
                                        "quota_headers": {}, "blocker": manifest["unavailable"]["odds"]},
                  "unavailable": manifest["unavailable"]}
    return payload, matchup_payload, backtest_result, provenance


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    projection, matchup, backtest_result, provenance = build()
    write_json(args.output / "nfl_week1_projections.json", projection)
    write_json(args.output / "nfl_matchup_context_2025.json", matchup)
    write_json(args.output / "nfl_backtest_2025.json", backtest_result)
    write_json(args.output / "provenance.json", provenance)
    print(f"NFL Week 1: {len(projection['players'])} players; excluded {len(projection['excluded_players'])}")
    print(json.dumps(backtest_result["by_position"], indent=2))


if __name__ == "__main__":
    main()
