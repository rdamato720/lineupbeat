#!/usr/bin/env python3

import importlib.util
import unittest
from pathlib import Path


PATH = Path(__file__).with_name("generate_college_week1.py")
SPEC = importlib.util.spec_from_file_location("college_week1", PATH)
week = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(week)


class WeeklyMarketTests(unittest.TestCase):
    def test_frozen_compact_schedule_is_reusable(self):
        schedule = {"games": [{
            "event_id": "espn-1", "date": "2026-09-05T00:00:00Z",
            "home": "Texas", "away": "Texas State",
            "home_abbr": "TEX", "away_abbr": "TXST",
            "over_under": 61.5, "home_spread": -24.5,
            "source": "https://example.test/game",
        }]}
        games, compact = week.scheduled_games(schedule, {"Texas", "Texas State"})
        self.assertEqual(len(compact), 1)
        self.assertEqual(games["Texas"]["opponent"], "Texas State")
        self.assertAlmostEqual(games["Texas"]["implied"], 43.0)
        self.assertAlmostEqual(games["Texas State"]["implied"], 18.5)

    def test_consensus_game_overlay_replaces_single_book_line(self):
        games = {
            "Texas": {
                "opponent": "Texas State", "home": True,
                "implied": 35.0, "opponent_implied": 20.0,
            },
            "Texas State": {
                "opponent": "Texas", "home": False,
                "implied": 20.0, "opponent_implied": 35.0,
            },
        }
        market = [{
            "event_id": "market-1", "home_team": "Texas Longhorns",
            "away_team": "Texas State Bobcats", "game_total": 61.5,
            "home_implied_total": 43.0, "away_implied_total": 18.5,
            "quality": "HIGH",
        }]
        self.assertEqual(week.overlay_game_markets(games, market), 1)
        self.assertEqual(games["Texas"]["implied"], 43.0)
        self.assertEqual(games["Texas State"]["implied"], 18.5)

    def test_high_quality_prop_blends_but_does_not_copy(self):
        rows = [{
            "player_id": "p1", "player_name": "Example Runner",
            "team_name": "Texas", "opponent": "Texas State", "home": True,
            "passing_yards": 0.0, "passing_td": 0.0, "interceptions": 0.0,
            "rushing_yards": 80.0, "rushing_td": .5, "receptions": 2.0,
            "receiving_yards": 20.0, "receiving_td": .2,
        }]
        props = [{
            "event_id": "market-1", "home_team": "Texas Longhorns",
            "away_team": "Texas State Bobcats", "market_key": "player_rush_yds",
            "player_name": "Example Runner", "consensus_line": 100.0,
            "quality": "HIGH",
        }]
        adjustments, players = week.apply_player_markets(rows, props)
        self.assertEqual((adjustments, players), (1, 1))
        self.assertEqual(rows[0]["rushing_yards"], 86.0)
        self.assertNotEqual(rows[0]["rushing_yards"], 100.0)

    def test_low_quality_prop_cannot_move_projection(self):
        row = {
            "player_id": "p1", "player_name": "Example Runner",
            "team_name": "Texas", "opponent": "Texas State", "home": True,
            "passing_yards": 0.0, "passing_td": 0.0, "interceptions": 0.0,
            "rushing_yards": 80.0, "rushing_td": .5, "receptions": 2.0,
            "receiving_yards": 20.0, "receiving_td": .2,
        }
        props = [{
            "home_team": "Texas Longhorns", "away_team": "Texas State Bobcats",
            "market_key": "player_rush_yds", "player_name": "Example Runner",
            "consensus_line": 120.0, "quality": "LOW",
        }]
        self.assertEqual(week.apply_player_markets([row], props), (0, 0))
        self.assertEqual(row["rushing_yards"], 80.0)


if __name__ == "__main__":
    unittest.main()
