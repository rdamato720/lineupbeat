"""Deterministic fictional archive for the unlisted development prototype."""

from __future__ import annotations


MANAGERS = (
    ("f01", "Alex Morgan", "Fourth & Long"),
    ("f02", "Casey Lee", "Sunday Scaries"),
    ("f03", "Jordan Blake", "Goal Line Stand"),
    ("f04", "Morgan Reyes", "Waiver Royalty"),
    ("f05", "Taylor Quinn", "Two Minute Drill"),
    ("f06", "Devin Brooks", "Red Zone Rebels"),
    ("f07", "Riley Chen", "Bye Week Bandits"),
    ("f08", "Cameron Wells", "Gridiron Guild"),
    ("f09", "Avery Hart", "Monday Miracles"),
    ("f10", "Logan Price", "The Audible"),
)


def _round_robin(ids: list[str]) -> list[list[tuple[str, str]]]:
    rotation = ids[:]
    weeks = []
    for _ in range(len(ids) - 1):
        weeks.append([(rotation[i], rotation[-1 - i]) for i in range(len(ids) // 2)])
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]
    return weeks


def _score(year: int, week: int, franchise_index: int, side: int) -> float:
    whole = 86 + ((year * 7 + week * 13 + franchise_index * 19 + side * 11) % 77)
    cents = ((week * 17 + franchise_index * 23 + side * 29) % 50) * 2
    return whole + cents / 100


def demo_history() -> dict:
    """Return two complete fictional seasons with stable manager identities."""
    ids = [row[0] for row in MANAGERS]
    matchups = []
    seasons = []
    outcomes = {
        2024: {"champion": "f04", "runner": "f02", "crown": "f07"},
        2025: {"champion": "f01", "runner": "f06", "crown": "f03"},
    }
    finishes = {
        2024: ["f04", "f02", "f07", "f01", "f06", "f10", "f03", "f08", "f05", "f09"],
        2025: ["f01", "f06", "f03", "f08", "f04", "f09", "f02", "f05", "f10", "f07"],
    }

    for year in (2024, 2025):
        regular_weeks = _round_robin(ids)
        for week, pairings in enumerate(regular_weeks, start=1):
            for sequence, (left, right) in enumerate(pairings, start=1):
                li, ri = ids.index(left), ids.index(right)
                matchups.append({
                    "id": f"{year}-w{week:02d}-g{sequence:02d}",
                    "season": year,
                    "week": week,
                    "sequence": sequence,
                    "playoff": False,
                    "homeFranchiseId": left,
                    "awayFranchiseId": right,
                    "homeScore": _score(year, week, li, 0),
                    "awayScore": _score(year, week, ri, 1),
                })

        semifinalists = finishes[year][:4]
        playoff_pairs = ((semifinalists[0], semifinalists[3]),
                         (semifinalists[1], semifinalists[2]))
        for sequence, (left, right) in enumerate(playoff_pairs, start=1):
            matchups.append({
                "id": f"{year}-w10-g{sequence:02d}", "season": year, "week": 10,
                "sequence": sequence, "playoff": True,
                "homeFranchiseId": left, "awayFranchiseId": right,
                "homeScore": 151.0 + sequence + (year - 2024) * 2,
                "awayScore": 119.0 + sequence,
            })
        matchups.append({
            "id": f"{year}-w11-g01", "season": year, "week": 11,
            "sequence": 1, "playoff": True,
            "homeFranchiseId": outcomes[year]["champion"],
            "awayFranchiseId": outcomes[year]["runner"],
            "homeScore": 167.42 + (year - 2024) * 3,
            "awayScore": 158.18 - (year - 2024) * 2,
        })
        seasons.append({
            "year": year, "platform": "demo", "platformLeagueId": "fictional",
            "regularSeasonWeeks": 9, "activeFranchiseIds": ids,
            "championFranchiseId": outcomes[year]["champion"],
            "runnerUpFranchiseId": outcomes[year]["runner"],
            "scoringCrownFranchiseId": outcomes[year]["crown"],
            "finishes": {fid: place for place, fid in enumerate(finishes[year], start=1)},
            "complete": True,
        })

    return {
        "schemaVersion": "lineupbeat-league-history-v1",
        "league": {
            "id": "metro-demo", "name": "Metro Fantasy League", "sport": "nfl",
            "privacy": "unlisted", "sourcePlatform": "demo",
        },
        "import": {
            "source": "fictional-deterministic-demo",
            "capturedAt": "2026-09-03T00:00:00Z",
            "designReferenceUrl": "https://github.com/rdamato720/bgnco",
            "incomplete": [], "suspectedDuplicates": [],
        },
        "managers": [{"id": fid, "displayName": manager} for fid, manager, _ in MANAGERS],
        "franchises": [{"id": fid, "name": team, "managerId": fid, "aliases": [team]}
                       for fid, _, team in MANAGERS],
        "seasons": seasons,
        "matchups": matchups,
    }
