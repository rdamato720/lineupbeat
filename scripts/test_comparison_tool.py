#!/usr/bin/env python3
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import build_comparison_tool as tool
import build_consistency as consistency


class ComparisonToolTests(unittest.TestCase):
    def test_consistency_metrics_are_reproducible(self):
        got = consistency.metrics([5.0, 10.0, 15.0, 20.0], "RB")
        self.assertEqual(got["average"], 12.5)
        self.assertEqual(got["median"], 12.5)
        self.assertEqual(got["floor_p25"], 8.8)
        self.assertEqual(got["ceiling_p75"], 16.2)
        self.assertEqual(got["boom_rate"], 50)

    def test_published_consistency_artifact_has_provenance(self):
        payload = json.loads(tool.CONSISTENCY.read_text())
        self.assertEqual(payload["season"], 2025)
        self.assertEqual(payload["source"], "nflverse weekly player stats")
        self.assertGreaterEqual(len(payload["players"]), 600)

    def test_player_pool_and_recommendation(self):
        players = tool.player_payload()
        self.assertGreaterEqual(len(players), 200)
        by = {p["slug"]: p for p in players}
        got = tool.recommendation(by["bijan-robinson"], by["jahmyr-gibbs"], "ppr")
        self.assertIn(got["winner_slug"], {"bijan-robinson", "jahmyr-gibbs"})
        self.assertIn(got["confidence"], {"Slight", "Moderate", "Strong"})

    def test_hub_and_pair_pages_are_indexable(self):
        players = tool.player_payload()
        by = {p["slug"]: p for p in players}
        built = tool.formats.source_updated(tool.formats.SOURCE)
        hub = tool.html(players, built, pairs=tool.popular_pairs(players))
        pair = tool.html(players, built, by["bijan-robinson"], by["jahmyr-gibbs"])
        self.assertIn("Who Should I Draft?", hub)
        self.assertIn("weekly floor", hub)
        self.assertIn("WebApplication", hub)
        self.assertIn("bijan-robinson-vs-jahmyr-gibbs", pair)
        self.assertIn("Lineup Beat PPR pick", pair)


if __name__ == "__main__":
    unittest.main()
