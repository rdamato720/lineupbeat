"""Validate a league archive and derive its permanent record book.

The importer owns platform-specific extraction.  This module deliberately
knows nothing about ESPN, Sleeper or Yahoo: every provider must produce the
same stable league, franchise, manager, season and matchup identities.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path


STARTING_ELO = 1500.0
ELO_K = 24.0
SEASON_REGRESSION = 0.30


def load_history(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text())
    validate_history(payload)
    return payload


def _unique_ids(rows: list[dict], label: str) -> set[str]:
    ids = [row.get("id") for row in rows]
    if any(not isinstance(value, str) or not value.strip() for value in ids):
        raise ValueError(f"{label} require non-empty string ids")
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {label} id")
    return set(ids)


def validate_history(payload: dict) -> None:
    if payload.get("schemaVersion") != "lineupbeat-league-history-v1":
        raise ValueError("unsupported league history schema")
    league = payload.get("league")
    if not isinstance(league, dict) or not league.get("id") or not league.get("name"):
        raise ValueError("league id and name are required")

    managers = payload.get("managers")
    franchises = payload.get("franchises")
    seasons = payload.get("seasons")
    matchups = payload.get("matchups")
    if not all(isinstance(value, list) for value in (managers, franchises, seasons, matchups)):
        raise ValueError("managers, franchises, seasons and matchups must be arrays")

    manager_ids = _unique_ids(managers, "manager")
    franchise_ids = _unique_ids(franchises, "franchise")
    season_years = [row.get("year") for row in seasons]
    if any(not isinstance(year, int) for year in season_years) or len(season_years) != len(set(season_years)):
        raise ValueError("season years must be unique integers")

    for franchise in franchises:
        if franchise.get("managerId") not in manager_ids:
            raise ValueError(f"unknown manager for franchise {franchise['id']}")
        if not franchise.get("name"):
            raise ValueError(f"franchise {franchise['id']} has no name")

    seen_games: set[str] = set()
    season_set = set(season_years)
    for game in matchups:
        game_id = game.get("id")
        if not isinstance(game_id, str) or not game_id or game_id in seen_games:
            raise ValueError("matchup ids must be unique non-empty strings")
        seen_games.add(game_id)
        if game.get("season") not in season_set:
            raise ValueError(f"matchup {game_id} references an unknown season")
        home, away = game.get("homeFranchiseId"), game.get("awayFranchiseId")
        if home not in franchise_ids or away not in franchise_ids or home == away:
            raise ValueError(f"matchup {game_id} has invalid franchises")
        if not isinstance(game.get("week"), int) or game["week"] < 1:
            raise ValueError(f"matchup {game_id} has an invalid week")
        for field in ("homeScore", "awayScore"):
            value = game.get(field)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"matchup {game_id} has an invalid {field}")

    for season in seasons:
        for field in ("championFranchiseId", "runnerUpFranchiseId", "scoringCrownFranchiseId"):
            value = season.get(field)
            if value is not None and value not in franchise_ids:
                raise ValueError(f"season {season['year']} has an unknown {field}")
        finishes = season.get("finishes", {})
        if set(finishes) - franchise_ids:
            raise ValueError(f"season {season['year']} has an unknown finish identity")


def _result(left: float, right: float) -> float:
    if left > right:
        return 1.0
    if left < right:
        return 0.0
    return 0.5


def _elo_update(left: float, right: float, left_score: float, right_score: float) -> tuple[float, float]:
    """Apply the margin-aware rating change used by the BGNCo reference app."""
    actual = _result(left_score, right_score)
    expected = 1 / (1 + 10 ** ((right - left) / 400))
    margin = abs(left_score - right_score)
    multiplier = math.log(max(margin, 1.0) + 1.0) / math.log(30.0)
    multiplier = min(max(multiplier, 0.5), 1.8)
    delta = ELO_K * multiplier * (actual - expected)
    return left + delta, right - delta


def _record(label: str, game: dict, franchise: dict[str, dict]) -> dict:
    home, away = game["homeFranchiseId"], game["awayFranchiseId"]
    hs, aws = game["homeScore"], game["awayScore"]
    winner, loser = (home, away) if hs >= aws else (away, home)
    return {
        "label": label,
        "season": game["season"],
        "week": game["week"],
        "playoff": bool(game.get("playoff")),
        "home": franchise[home]["name"],
        "away": franchise[away]["name"],
        "homeScore": hs,
        "awayScore": aws,
        "winner": franchise[winner]["name"],
        "loser": franchise[loser]["name"],
        "margin": abs(hs - aws),
        "total": hs + aws,
    }


def summarize_history(payload: dict) -> dict:
    validate_history(payload)
    managers = {row["id"]: row for row in payload["managers"]}
    franchises = {row["id"]: row for row in payload["franchises"]}
    seasons = {row["year"]: row for row in payload["seasons"]}
    games = sorted(payload["matchups"], key=lambda row: (
        row["season"], row["week"], row.get("sequence", 0), row["id"]))

    stats = {fid: {
        "franchiseId": fid,
        "franchise": row["name"],
        "manager": managers[row["managerId"]]["displayName"],
        "games": 0, "wins": 0, "losses": 0, "ties": 0,
        "pointsFor": 0.0, "pointsAgainst": 0.0,
        "expectedWins": 0.0, "titles": 0,
        "runnerUps": 0, "scoringCrowns": 0,
        "seasons": 0, "bestFinish": None,
        "elo": STARTING_ELO, "peakElo": STARTING_ELO,
        "longestWinStreak": 0, "longestLosingStreak": 0,
    } for fid, row in franchises.items()}

    for season in seasons.values():
        active = season.get("activeFranchiseIds", list(franchises))
        if season.get("complete", True):
            for fid in active:
                stats[fid]["seasons"] += 1
        for field, target in (("championFranchiseId", "titles"),
                              ("runnerUpFranchiseId", "runnerUps"),
                              ("scoringCrownFranchiseId", "scoringCrowns")):
            if season.get(field):
                stats[season[field]][target] += 1
        for fid, finish in season.get("finishes", {}).items():
            current = stats[fid]["bestFinish"]
            stats[fid]["bestFinish"] = finish if current is None else min(current, finish)

    by_week: dict[tuple[int, int], list[tuple[str, float]]] = defaultdict(list)
    for game in games:
        by_week[(game["season"], game["week"])].extend((
            (game["homeFranchiseId"], game["homeScore"]),
            (game["awayFranchiseId"], game["awayScore"]),
        ))
    for scores in by_week.values():
        for fid, score in scores:
            opponents = [other for other_id, other in scores if other_id != fid]
            if opponents:
                stats[fid]["expectedWins"] += sum(_result(score, other) for other in opponents) / len(opponents)

    h2h: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0}))
    streaks = {fid: {"win": 0, "loss": 0} for fid in franchises}
    games_by_season: dict[int, list[dict]] = defaultdict(list)
    for game in games:
        games_by_season[game["season"]].append(game)

    for season_index, season_year in enumerate(sorted(seasons)):
        if season_index:
            for row in stats.values():
                row["elo"] = STARTING_ELO + (row["elo"] - STARTING_ELO) * (1 - SEASON_REGRESSION)
        for game in games_by_season[season_year]:
            home, away = game["homeFranchiseId"], game["awayFranchiseId"]
            hs, aws = float(game["homeScore"]), float(game["awayScore"])
            outcome = _result(hs, aws)
            for fid, other, score, allowed, side in (
                    (home, away, hs, aws, outcome), (away, home, aws, hs, 1 - outcome)):
                row = stats[fid]
                row["games"] += 1
                row["pointsFor"] += score
                row["pointsAgainst"] += allowed
                bucket = "wins" if side == 1 else "losses" if side == 0 else "ties"
                row[bucket] += 1
                h2h[fid][other][bucket] += 1
                if side == 1:
                    streaks[fid]["win"] += 1
                    streaks[fid]["loss"] = 0
                elif side == 0:
                    streaks[fid]["loss"] += 1
                    streaks[fid]["win"] = 0
                else:
                    streaks[fid] = {"win": 0, "loss": 0}
                row["longestWinStreak"] = max(row["longestWinStreak"], streaks[fid]["win"])
                row["longestLosingStreak"] = max(row["longestLosingStreak"], streaks[fid]["loss"])

            home_elo, away_elo = _elo_update(stats[home]["elo"], stats[away]["elo"], hs, aws)
            stats[home]["elo"], stats[away]["elo"] = home_elo, away_elo
            stats[home]["peakElo"] = max(stats[home]["peakElo"], home_elo)
            stats[away]["peakElo"] = max(stats[away]["peakElo"], away_elo)

    rows = []
    for row in stats.values():
        decisions = row["wins"] + row["losses"] + row["ties"]
        row["winPct"] = (row["wins"] + row["ties"] * .5) / decisions if decisions else 0
        row["allPlayPct"] = row["expectedWins"] / row["games"] if row["games"] else 0
        row["luck"] = row["wins"] + row["ties"] * .5 - row["expectedWins"]
        row["pointsPerGame"] = row["pointsFor"] / row["games"] if row["games"] else 0
        rows.append(row)
    rows.sort(key=lambda row: (-row["titles"], -row["wins"], -row["pointsFor"], row["franchiseId"]))

    records = {}
    if games:
        team_weeks = []
        for game in games:
            for side in ("home", "away"):
                team_weeks.append((game[f"{side}Score"], game[f"{side}FranchiseId"], game))
        high = max(team_weeks, key=lambda item: item[0])
        low = min(team_weeks, key=lambda item: item[0])
        biggest = max(games, key=lambda game: abs(game["homeScore"] - game["awayScore"]))
        closest = min((game for game in games if game["homeScore"] != game["awayScore"]),
                      key=lambda game: abs(game["homeScore"] - game["awayScore"]))
        highest_total = max(games, key=lambda game: game["homeScore"] + game["awayScore"])
        records = {
            "highestWeek": {**_record("Highest single week", high[2], franchises),
                            "franchise": franchises[high[1]]["name"], "score": high[0]},
            "lowestWeek": {**_record("Lowest single week", low[2], franchises),
                           "franchise": franchises[low[1]]["name"], "score": low[0]},
            "biggestBlowout": _record("Biggest blowout", biggest, franchises),
            "closestGame": _record("Closest game", closest, franchises),
            "highestScoringGame": _record("Highest scoring game", highest_total, franchises),
        }

    return {
        "schemaVersion": "lineupbeat-league-record-book-v1",
        "league": payload["league"],
        "import": payload.get("import", {}),
        "constants": {"startingElo": STARTING_ELO, "eloK": ELO_K,
                      "seasonRegression": SEASON_REGRESSION,
                      "marginAwareElo": True},
        "counts": {"seasons": len(seasons), "franchises": len(franchises),
                   "games": len(games)},
        "franchises": rows,
        "seasons": [seasons[year] for year in sorted(seasons, reverse=True)],
        "records": records,
        "headToHead": {fid: dict(opponents) for fid, opponents in h2h.items()},
    }
