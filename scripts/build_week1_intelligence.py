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
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_projections  # noqa: E402
import decision_data  # noqa: E402
import build_nfl_trusted_season as trusted_season  # noqa: E402

CACHE = ROOT / ".cache" / "week1-intelligence"
OUTPUT = ROOT / "data" / "week1" / "2026" / "v1.2"
DEFAULT_MARKET_INPUT = CACHE / "nfl_market_consensus.json"
DEFAULT_INJURY_INPUT = CACHE / "espn_injuries.json"
POSITIONS = ("QB", "RB", "WR", "TE")
TEAM_ALIASES = {"LA": "LAR", "JAC": "JAX", "WSH": "WAS", "OAK": "LV",
                "SD": "LAC", "STL": "LAR"}
NFL_TEAM_NAMES = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN",
    "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE",
    "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}
PROP_COMPONENTS = {
    "player_pass_yds": "passing_yards",
    "player_pass_tds": "passing_tds",
    "player_rush_yds": "rushing_yards",
    "player_receptions": "receptions",
    "player_reception_yds": "receiving_yards",
}
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


def blended_player_share(historical_share: float | None,
                         season_share: float) -> float:
    """Anchor current weekly roles to the reviewed current-season baseline.

    Prior-season usage is useful shrinkage, but it must not outweigh the
    reviewed role for rookies, players on new teams, or players whose role has
    materially changed.
    """
    if historical_share is None:
        return season_share
    return .25 * historical_share + .75 * season_share


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def depth_workload_factor(position: str, rank: int | None) -> float:
    """Discount uncertain reserve roles without re-cutting listed starters."""
    if not rank or rank <= 1:
        return 1.0
    by_position = {
        "QB": {2: .18, 3: .05},
        "RB": {2: 1.0, 3: .42, 4: .22},
        "WR": {2: 1.0, 3: .62, 4: .40, 5: .25},
        "TE": {2: 1.0, 3: .38, 4: .22},
    }
    table = by_position[position]
    return table.get(rank, min(table.values()))


def load_market_input(path: Path) -> dict:
    payload = json.loads(path.read_text())
    if payload.get("schema") != "lineupbeat-private-nfl-market-consensus-v1":
        raise ValueError("unexpected private NFL market schema")
    if not 1 <= int(payload.get("prop_event_count") or 0) <= 16:
        raise ValueError("private NFL market capture has no usable Week 1 prop events")
    if not payload.get("events") or not payload.get("props"):
        raise ValueError("private NFL market capture is empty")
    return payload


def load_injury_input(path: Path) -> dict:
    payload = json.loads(path.read_text())
    if payload.get("schema") != "lineupbeat-private-espn-injury-status-v1":
        raise ValueError("unexpected private ESPN injury schema")
    if payload.get("season") != 2026 or payload.get("team_count") != 32:
        raise ValueError("private ESPN injury capture lacks full-league coverage")
    if len(payload.get("records") or []) < 20:
        raise ValueError("private ESPN injury capture is unexpectedly small")
    return payload


def injury_index(payload: dict) -> dict[tuple[str, str, str], dict]:
    index = {}
    for row in payload["records"]:
        key = (
            decision_data.normalize_player_name(row["name"]),
            team(row["team"]),
            row["position"],
        )
        if key in index:
            raise ValueError(f"duplicate normalized injury identity: {key}")
        index[key] = row
    return index


def market_indexes(payload: dict, slate_by_team: dict[str, dict]) -> tuple[dict, dict, dict]:
    """Resolve Week 1 market rows without exposing raw quotes or fuzzy identity."""
    by_team = {}
    event_teams = {}
    for row in payload["events"]:
        home = NFL_TEAM_NAMES.get(row.get("home_team"))
        away = NFL_TEAM_NAMES.get(row.get("away_team"))
        if not home or not away:
            continue
        if (home not in slate_by_team or away not in slate_by_team
                or slate_by_team[home]["opponent"] != away
                or slate_by_team[away]["opponent"] != home):
            continue
        if row.get("quality") != "HIGH" or min(
                int(row.get("total_book_count") or 0),
                int(row.get("spread_book_count") or 0)) < 3:
            continue
        event_teams[row["event_id"]] = {home, away}
        by_team[home] = {
            "event_id": row["event_id"], "game_total": row.get("game_total"),
            "team_spread": row.get("home_spread"),
            "team_implied_total": row.get("home_implied_total"),
            "book_count": min(row["total_book_count"], row["spread_book_count"]),
            "quality": row["quality"],
        }
        by_team[away] = {
            "event_id": row["event_id"], "game_total": row.get("game_total"),
            "team_spread": -float(row["home_spread"]) if row.get("home_spread") is not None else None,
            "team_implied_total": row.get("away_implied_total"),
            "book_count": min(row["total_book_count"], row["spread_book_count"]),
            "quality": row["quality"],
        }
    if len(by_team) != 32 or len(event_teams) != 16:
        raise ValueError(f"private market Week 1 coverage is {len(event_teams)} games/{len(by_team)} teams")

    props = defaultdict(list)
    for row in payload["props"]:
        if row.get("event_id") not in event_teams:
            continue
        if (row.get("market_key") not in PROP_COMPONENTS
                or row.get("consensus_line") is None
                or row.get("quality") != "HIGH"
                or int(row.get("book_count") or 0) < 3):
            continue
        key = decision_data.normalize_player_name(row.get("player_name") or "")
        if key:
            props[key].append(row)
    return by_team, props, event_teams


def blend_market_component(model_value: float, market_line: float) -> float:
    """Apply a conservative 25% blend after bounding the market anchor."""
    if model_value <= 0 or market_line < 0:
        return model_value
    bounded = clamp(float(market_line), model_value * .75, model_value * 1.25)
    return model_value * .75 + bounded * .25


def season_prior_rows() -> list[dict]:
    sheets = build_projections.read_sheet(ROOT / "data" / "projections.xlsx")
    return [row for position in POSITIONS for row in sheets[position]]


def current_week1_base(roster_rows: list[dict]) -> dict:
    """Resolve the reviewed season baseline against the current Week 1 roster.

    The old adapter inherited the preseason Decision Room's much smaller
    three-format intersection.  Weekly projections instead start from every
    current active QB/RB/WR/TE with one exact reviewed workbook match.
    """
    trusted = trusted_season.workbook_rows()
    display = json.loads(decision_data.DISPLAY.read_text())["players"]
    history_payload = json.loads(decision_data.HISTORY.read_text())
    history = {row["player_id"]: row["formats"] for row in history_payload["players"]}
    identities = json.loads(decision_data.IDENTITIES.read_text())["players"]
    editorial = decision_data._editorial(decision_data.identity_index(identities))
    active = [row for row in roster_rows
              if row.get("status") == "ACT" and row.get("position") in POSITIONS]
    players = []
    withheld = []
    for row in active:
        club, position = team(row.get("team")), row["position"]
        key = trusted_season.identity_key(row.get("full_name", ""), club, position)
        source = trusted.get(key)
        if source is None:
            withheld.append({
                "player_id": row.get("gsis_id"), "name": row.get("full_name"),
                "team": club, "position": position,
                "reason": "No single exact current name + team + position match in the reviewed baseline",
            })
            continue
        pid = row.get("gsis_id")
        if not pid:
            raise ValueError(f"current active player lacks a stable GSIS id: {row.get('full_name')}")
        show = display.get(pid, {})
        photo = row.get("headshot_url") or (
            f"https://a.espncdn.com/i/headshots/nfl/players/full/{row['espn_id']}.png"
            if row.get("espn_id") else decision_data._photo(show)
        )
        players.append({
            "id": pid, "slug": decision_data.slug(source["name"]),
            "name": source["name"], "team": club, "position": position,
            "adp": show.get("adp"), "photo": photo,
            "team_logo": f"https://a.espncdn.com/i/teamlogos/nfl/500/{club.lower()}.png",
            "history": history.get(pid, {}),
            "history_season": history_payload["season"] if pid in history else None,
            "formats": {fmt: {"projected_points": points}
                        for fmt, points in source["formats"].items()},
        })
    players.sort(key=lambda p: (p["position"], p["name"], p["id"]))
    return {
        "players": players,
        "withheld_players": sorted(withheld, key=lambda p: (p["position"], p["team"], p["name"])),
        "unresolved_players": [],
        "editorial_opinions": editorial,
        "population": {
            "projection_source": len(active), "identity_resolved": len(active),
            "identity_unresolved": 0, "ranked_production": len(players),
            "identity_resolved_not_ranked": len(withheld),
        },
        "sources": {
            "projections": {
                "label": "Lineup Beat reviewed 2026 season baseline",
                "updated_at": "2026-08-30T16:00:00Z",
            }
        },
    }


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


def build(market_path: Path | None = None,
          injury_path: Path | None = None) -> tuple[dict, dict, dict, dict]:
    manifest = json.loads((CACHE / "capture_manifest.json").read_text())
    schedule = rows("games.csv.gz")
    slate, slate_by_team = validate_schedule(schedule)
    market_path = market_path or Path(
        os.environ.get("LINEUPBEAT_NFL_MARKET_CONSENSUS", DEFAULT_MARKET_INPUT)
    )
    market_payload = load_market_input(market_path)
    injury_path = injury_path or Path(
        os.environ.get("LINEUPBEAT_ESPN_INJURIES", DEFAULT_INJURY_INPUT)
    )
    injury_payload = load_injury_input(injury_path)
    injuries = injury_index(injury_payload)
    market_by_team, props_by_name, event_teams = market_indexes(
        market_payload, slate_by_team
    )
    implied_median = statistics.median(
        float(row["team_implied_total"]) for row in market_by_team.values()
        if row.get("team_implied_total") is not None
    )
    p24, p25 = rows("stats_player_week_2024.csv.gz"), rows("stats_player_week_2025.csv.gz")
    t24, t25 = rows("stats_team_week_2024.csv.gz"), rows("stats_team_week_2025.csv.gz")
    roster_rows, depth_rows = rows("roster_2026.csv.gz"), rows("depth_charts_2026.csv.gz")
    snap24, snap25 = rows("snap_counts_2024.csv.gz"), rows("snap_counts_2025.csv.gz")
    pbp25 = rows("play_by_play_2025.csv.gz")
    # The 2024 PBP asset is captured and licensed for the backtest audit; the
    # current matchup artifact intentionally uses only the labeled 2025 prior.
    matchup = defense_context(p25, pbp25, schedule)
    backtest_result = backtest(p24, p25, schedule)

    base = current_week1_base(roster_rows)
    history = player_history(p24 + p25)
    roster = {row["gsis_id"]: row for row in roster_rows if row.get("gsis_id")}
    if any(player["id"] not in roster for player in base["players"]):
        raise ValueError("current projection identity failed stable GSIS roster reconciliation")
    depth_by_player = defaultdict(list)
    for row in depth_rows:
        pid = row.get("gsis_id")
        if pid:
            depth_by_player[pid].append(row)
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
        injury = injuries.get((
            decision_data.normalize_player_name(player["name"]), club, pos
        ))
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
        offensive_depth = [row for row in depth_by_player.get(pid, [])
                           if row.get("pos_abb") == pos]
        depth = max(offensive_depth, key=lambda row: row.get("dt", ""), default={})
        depth_rank = int(float(depth["pos_rank"])) if depth.get("pos_rank") else None
        role_factor = depth_workload_factor(pos, depth_rank)
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
            share = blended_player_share(hist_share, season_share)
            shares[key] = share
            stat[key] = max(0.0, base_volume * share * role_factor)

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
            share = blended_player_share(hist_share, prior_share)
            stat[td_key] = max(0.0, team_volume * share * role_factor)
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
        game_market = market_by_team[club]
        # Team scoring environment is informative but noisy. Shrink its
        # implied-total signal to 25% and apply it only to touchdown rates.
        raw_team_factor = float(game_market["team_implied_total"]) / implied_median
        team_market_factor = 1 + (clamp(raw_team_factor, .85, 1.15) - 1) * .25
        for key in ("passing_tds", "rushing_tds", "receiving_tds"):
            stat[key] *= team_market_factor

        applied_props = []
        applied_prop_lines = {}
        prop_book_counts = []
        seen_components = set()
        for market_row in props_by_name.get(
                decision_data.normalize_player_name(player["name"]), []):
            if club not in event_teams.get(market_row["event_id"], set()):
                continue
            component = PROP_COMPONENTS[market_row["market_key"]]
            if component in seen_components:
                raise ValueError(f"duplicate qualified prop component for {player['name']}: {component}")
            seen_components.add(component)
            stat[component] = blend_market_component(
                stat[component], float(market_row["consensus_line"])
            )
            applied_props.append(component)
            applied_prop_lines[component] = round(float(market_row["consensus_line"]), 1)
            prop_book_counts.append(int(market_row["book_count"]))
        stat["receptions"] = min(stat["receptions"], stat["targets"])
        confirmed_unavailable = bool(
            injury and injury.get("confirmed_unavailable")
        )
        # Q and D are visibility signals, not projection multipliers. Only a
        # confirmed unavailable status zeroes the player's Week 1 stat line.
        if confirmed_unavailable:
            for key in stat:
                stat[key] = 0.0
        rounded_stat = {key: round(value, 3) for key, value in stat.items()}
        formats = {}
        for fmt, reception_value in (("ppr", 1.0), ("half_ppr", .5), ("non_ppr", 0.0)):
            formats[fmt] = {"projected_points": round(score(rounded_stat, reception_value), 1)}
        coverage = {
            "historical_weekly": bool(hist25 or hist24), "opportunity": True,
            "team_volume": True, "current_roster": True,
            "depth_chart": bool(depth), "snap_participation": bool(snaps[pid]),
            "opponent_matchup": opponent in matchup, "current_injury_report": True,
            "betting_market": True,
        }
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
                                 "depth_rank": depth_rank,
                                 "workload_factor": role_factor,
                                 "2025_average_offense_snap_pct": round(mean(snaps[pid]), 3) if snaps[pid] else None},
                        "availability": {
                            "state": ((injury or {}).get("status") or "Active").lower().replace(" ", "_"),
                            "status": (injury or {}).get("status") or "Active",
                            "tag": (injury or {}).get("abbreviation"),
                            "injury_type": (injury or {}).get("injury_type"),
                            "updated_at": (injury or {}).get("updated_at") or injury_payload["fetched_at"],
                            "source": "ESPN injury status",
                            "source_url": injury_payload["source_url"],
                            "projection_adjusted": confirmed_unavailable,
                            "projection_factor": 0.0 if confirmed_unavailable else 1.0,
                            "policy": (
                                "confirmed unavailable; Week 1 projection set to zero"
                                if confirmed_unavailable else
                                "status displayed; Questionable and Doubtful do not change the projection"
                            ),
                        },
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
                        "market": {
                            "state": "player_and_game_consensus" if applied_props else "game_consensus",
                            "quality": "HIGH",
                            "game_book_count": int(game_market["book_count"]),
                            "player_book_count": min(prop_book_counts) if prop_book_counts else None,
                            "player_components": sorted(applied_props),
                            "consensus_lines": dict(sorted(applied_prop_lines.items())),
                            "team_environment_factor": round(team_market_factor, 3),
                            "updated_at": market_payload["fetched_at"],
                            "raw_lines_public": False,
                        },
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

    market_player_count = sum(
        bool(player["market"]["player_components"]) for player in players
    )
    injury_player_count = sum(player["availability"]["status"] != "Active"
                              for player in players)
    unavailable_player_count = sum(player["availability"]["projection_adjusted"]
                                   for player in players)
    payload = {"schema_version": "lineupbeat-nfl-week1-v1.2", "mode": "weekly", "season": 2026, "week": 1,
               "updated_at": market_payload["fetched_at"], "players": players, "excluded_players": excluded,
               "population": {
                   **base["population"],
                   "ranked_active_projected": len(players),
                   "ranked_excluded": len(excluded),
               },
               "unresolved_players": base["unresolved_players"],
               "withheld_players": base["withheld_players"],
               "identity_method": "stable GSIS id plus exact normalized name, current team and position; no fuzzy matching",
               "limitations": {
                   "sportsbook_evidence": (
                       f"private multi-book consensus covers 16 games; qualified exact-player "
                       f"components adjusted {market_player_count} projections; raw quotes and lines are not public"
                   ),
                   "current_injury_report": (
                       "current ESPN status tags included; Questionable and Doubtful do not change "
                       "projections; confirmed Out, IR, or suspended statuses set Week 1 to zero"
                   ),
                   "dst_model": "unavailable; model population is QB/RB/WR/TE only",
                   "predictive_lift_claim": False,
                   "matchup_context": "2025 prior-season context",
               },
               "available_formats": ["ppr", "half_ppr", "non_ppr"],
               "editorial_opinions": base["editorial_opinions"],
               "schedule_sos_available": True,
               "sources": {"model": {"label": "Lineup Beat-owned Week 1 model", "updated_at": market_payload["fetched_at"]},
                           "season_prior": base["sources"]["projections"],
                           "history": {"label": "nflverse weekly player/team statistics", "updated_at": "2025 regular season"},
                           "matchup": {"label": "nflverse 2025 prior-season defensive context", "updated_at": manifest["captured_at"]},
                           "market": {"label": "Private multi-book consensus; aggregate adjustments only",
                                      "updated_at": market_payload["fetched_at"]},
                           "injuries": {"label": "ESPN current injury status",
                                        "updated_at": injury_payload["fetched_at"],
                                        "url": injury_payload["source_url"]}},
               "methodology": {"season_total_divisor": None,
                               "summary": "Current Week 1 roster and offensive depth chart × historical team weekly volume × reviewed season-prior player shares with 25% prior-season usage shrinkage; historical and season-prior efficiencies; bounded 2025 opponent and venue adjustments; conservatively shrunk private multi-book game and exact-player consensus inputs.",
                               "role_policy": "The reviewed 2026 role supplies 75% of a player's opportunity share when usable 2025 current-team history exists, with that history limited to 25% shrinkage. Listed QB backups and players below the second RB, WR, or TE depth slot receive an additional reserve-role discount; RB2, WR2, and TE2 do not receive a duplicate workload cut.",
                               "market_policy": "High-quality consensus requires at least three books. Team implied-total effects are capped and shrunk to 25%; exact player components are capped to within 25% of the independent model and blended at 25%. Anytime-touchdown prices do not move projections because a one-sided price cannot be safely de-vigged. Raw quotes, prices, lines, and sportsbook identities are never published.",
                               "injury_policy": "Current status tags are displayed for context. Questionable and Doubtful carry a 1.0 projection factor. Only confirmed Out, Injured Reserve, or suspended statuses set the Week 1 projection to zero.",
                               "scoring": "0.04/pass yard, 4/pass TD, -2/interception, 0.1/rush or receiving yard, 6/rush or receiving TD, -2/fumble lost, plus format reception points.",
                               "recommendation_guardrail": "A point difference alone cannot create an unqualified recommendation."}}
    matchup_payload = {"schema_version": "lineupbeat-nfl-matchup-2025-v1", "season": 2025,
                       "label": "2025 prior-season defensive context",
                       "definitions": {"rush_explosive": "10+ rushing yards", "pass_explosive": "completed pass gaining 20+ yards",
                                       "success": "nflverse play-level success field", "red_zone": "scrimmage play at or inside opponent 20",
                                       "opponent_adjustment": "game-level position PPR allowed divided by that offense's 2025 position average, then rescaled to league average"},
                       "teams": matchup}
    provenance = {"schema_version": "lineupbeat-week1-provenance-v1.1", "generated_at": market_payload["fetched_at"],
                  "license_review": manifest["license_review"], "assets": manifest["assets"],
                  "provider_requests": {
                      "odds": 1, "player_props": int(market_payload["prop_event_count"]),
                      "injuries": 1, "model_api": 0, "cost_usd": None,
                      "provider_credits_used": market_payload.get("credits_used"),
                      "provider_credits_remaining": market_payload.get("credits_remaining"),
                      "raw_market_data_public": False,
                  },
                  "market_coverage": {
                      "games": len(event_teams), "teams": len(market_by_team),
                      "captured_prop_rows": len(market_payload["props"]),
                      "qualified_player_projections": market_player_count,
                      "captured_at": market_payload["fetched_at"],
                  },
                  "availability_coverage": {
                      "teams": injury_payload["team_count"],
                      "captured_status_rows": len(injury_payload["records"]),
                      "matched_projected_players": injury_player_count,
                      "confirmed_unavailable_players": unavailable_player_count,
                      "captured_at": injury_payload["fetched_at"],
                  },
                  "unavailable": {**manifest["unavailable"],
                                  "odds": "available privately; raw market data is intentionally not published"}}
    from weekly_availability import apply_reports
    apply_reports(payload)
    provenance["reviewed_availability_reports"] = payload.get("reviewed_availability_reports", [])
    provenance["availability_coverage"]["confirmed_unavailable_players"] = sum(p["availability"]["projection_adjusted"] for p in payload["players"])
    return payload, matchup_payload, backtest_result, provenance


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--market-input", type=Path, default=None)
    parser.add_argument("--injury-input", type=Path, default=None)
    args = parser.parse_args()
    projection, matchup, backtest_result, provenance = build(
        args.market_input, args.injury_input
    )
    write_json(args.output / "nfl_week1_projections.json", projection)
    write_json(args.output / "nfl_matchup_context_2025.json", matchup)
    write_json(args.output / "nfl_backtest_2025.json", backtest_result)
    write_json(args.output / "provenance.json", provenance)
    print(f"NFL Week 1: {len(projection['players'])} players; excluded {len(projection['excluded_players'])}")
    print(json.dumps(backtest_result["by_position"], indent=2))


if __name__ == "__main__":
    main()
