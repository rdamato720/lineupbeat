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

    def test_published_routes_survive_ranking_changes(self):
        players = tool.player_payload()
        # Move every player outside the adjacent-pair generation boundary.
        for player in players:
            if "ppr" in player["formats"]:
                player["formats"]["ppr"]["position_rank"] = 999
        paths = {row["path"] for row, a, b in tool.preserved_pairs(players)}
        expected = {row["path"] for row in json.loads((tool.ROOT / "data/comparison_routes.json").read_text())}
        self.assertEqual(paths, expected)
        self.assertEqual(len(paths), 161)
        # A removed player must not silently regain a recommendation.
        subset = [p for p in players if p["name"] != "Josh Jacobs"]
        missing = [(row, a, b) for row, a, b in tool.preserved_pairs(subset) if not a or not b]
        self.assertTrue(missing)
        self.assertIn("comparison is unavailable", tool.unavailable_pair_html(missing[0][0]))

    def test_weekly_link_matches_both_players(self):
        from html import unescape
        from urllib.parse import urlsplit, parse_qs
        import re
        from decision_data import load_weekly
        by = {p["slug"]: p for p in tool.player_payload()}
        a, b = by["bijan-robinson"], by["jahmyr-gibbs"]
        page = tool.html([a, b], tool.formats.source_updated(tool.formats.SOURCE), a, b)
        href = unescape(re.search(r'id="cmpweekly" href="([^"]+)"', page)[1])
        params = parse_qs(urlsplit(href).query)
        weekly = {p["id"]: p for p in load_weekly()["players"]}
        self.assertEqual(weekly[params["a"][0]]["name"], a["name"])
        self.assertEqual(weekly[params["b"][0]]["name"], b["name"])
        self.assertEqual(params["format"], ["ppr"])
        a["weekly_id"] = None
        page = tool.html([a, b], tool.formats.source_updated(tool.formats.SOURCE), a, b)
        self.assertIn('id="cmpweekly" href="/decision-room/nfl/"', page)

    def test_comparison_tool_is_discoverable(self):
        template = (tool.ROOT / "site" / "template.html").read_text()
        pages = (tool.ROOT / "scripts" / "build_pages.py").read_text()
        self.assertIn('href="/nfl/who-should-i-draft/" class="lb-btn', template)
        self.assertIn('href="/decision-room/nfl/"', pages)
        self.assertIn('href="/nfl/who-should-i-draft/"', pages)
        self.assertIn("COMPARE PLAYERS", pages)


if __name__ == "__main__":
    unittest.main()
