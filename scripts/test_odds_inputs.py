#!/usr/bin/env python3

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("odds_inputs.py")
SPEC = importlib.util.spec_from_file_location("odds_inputs", MODULE_PATH)
odds = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(odds)


def outcome(name, price, point=None, description=None):
    row = {"name": name, "price": price}
    if point is not None:
        row["point"] = point
    if description is not None:
        row["description"] = description
    return row


def fixture():
    books = []
    for key, total, spread, home_price, away_price, pass_line in (
        ("draftkings", 47.5, -3.0, -155, 135, 274.5),
        ("fanduel", 48.0, -3.5, -160, 140, 275.5),
        ("caesars", 47.5, -3.0, -150, 130, 274.5),
    ):
        books.append({
            "key": key,
            "last_update": "2026-08-30T20:00:00Z",
            "markets": [
                {"key": "totals", "outcomes": [
                    outcome("Over", -110, total), outcome("Under", -110, total)]},
                {"key": "spreads", "outcomes": [
                    outcome("Home Team", -110, spread),
                    outcome("Away Team", -110, -spread)]},
                {"key": "h2h", "outcomes": [
                    outcome("Home Team", home_price), outcome("Away Team", away_price)]},
                {"key": "player_pass_yds", "outcomes": [
                    outcome("Over", -115, pass_line, "Example Quarterback"),
                    outcome("Under", -105, pass_line, "Example Quarterback")]},
                {"key": "player_anytime_td", "outcomes": [
                    outcome("Example Runner", 125)]},
            ],
        })
    return {
        "id": "event-1",
        "sport_key": "americanfootball_nfl",
        "commence_time": "2026-09-10T00:20:00Z",
        "home_team": "Home Team",
        "away_team": "Away Team",
        "bookmakers": books,
    }


class ConsensusTests(unittest.TestCase):
    def test_event_consensus_and_implied_totals(self):
        row = odds.event_consensus(fixture())
        self.assertEqual(row["game_total"], 47.5)
        self.assertEqual(row["home_spread"], -3.0)
        self.assertAlmostEqual(row["home_implied_total"], 25.25)
        self.assertAlmostEqual(row["away_implied_total"], 22.25)
        self.assertEqual(row["quality"], "HIGH")
        self.assertEqual(row["total_book_count"], 3)

    def test_props_consensus_and_devig(self):
        rows = odds.props_consensus(fixture())
        by_key = {(row["market_key"], row["player_name"]): row for row in rows}
        passing = by_key[("player_pass_yds", "Example Quarterback")]
        self.assertEqual(passing["consensus_line"], 274.5)
        self.assertEqual(passing["book_count"], 3)
        self.assertEqual(passing["quality"], "HIGH")
        self.assertTrue(0.50 < passing["fair_over_probability"] < 0.53)
        touchdown = by_key[("player_anytime_td", "Example Runner")]
        self.assertIsNone(touchdown["consensus_line"])
        self.assertEqual(touchdown["book_count"], 3)

    def test_private_storage_and_read_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "runtime.db"
            conn = odds.connect(db)
            client = type("Client", (), {"credits_used": 9, "credits_remaining": 491})()
            snapshot_id = odds.store_snapshot(
                conn, "americanfootball_nfl", [fixture()], [fixture()], client)
            self.assertGreater(snapshot_id, 0)
            self.assertEqual(len(odds.latest_team_inputs(conn, "americanfootball_nfl")), 1)
            players = odds.latest_player_inputs(conn, "americanfootball_nfl")
            self.assertEqual({row["player_name"] for row in players},
                             {"Example Quarterback", "Example Runner"})
            info = odds.latest_snapshot_info(
                conn, "americanfootball_nfl", require_props=True)
            self.assertEqual(info["snapshot_id"], snapshot_id)
            self.assertEqual(info["player_count"], 2)
            self.assertEqual(info["prop_count"], 2)
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("odds_quotes", tables)

    def test_api_key_is_scrubbed_from_errors(self):
        client = odds.OddsClient("super-secret-value")
        message = client._safe_error(
            "https://example.test?apiKey=super-secret-value failed")
        self.assertNotIn("super-secret-value", message)
        self.assertIn("[redacted]", message)


if __name__ == "__main__":
    unittest.main()
