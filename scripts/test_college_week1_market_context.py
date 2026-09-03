#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

import build_college_week1_market_context as builder


ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "data/college/2026/week-1/market-context"
SOURCE = RELEASE / "therundown-2026-09-03.json"


class CollegeWeek1MarketContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(SOURCE.read_text())

    def test_manifest_and_scope(self):
        manifest = json.loads((RELEASE / "manifest.json").read_text())
        expected = manifest["files"][SOURCE.name]
        raw = SOURCE.read_bytes()
        self.assertEqual(len(raw), expected["bytes"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), expected["sha256"])
        self.assertEqual(self.payload["schemaVersion"],
                         "lineupbeat-college-week1-market-context-v1")
        self.assertEqual(self.payload["coverage"]["modeled_teams"], 64)
        self.assertEqual(self.payload["coverage"][
            "teams_with_spread_and_total"], 64)

    def test_consensus_math_and_safe_surface(self):
        for row in self.payload["teams"].values():
            self.assertEqual(row["team_implied_total"], round(
                (row["game_total"] - row["team_spread"]) / 2, 2))
            self.assertEqual(row["opponent_implied_total"], round(
                (row["game_total"] + row["team_spread"]) / 2, 2))
            self.assertGreaterEqual(row["consensus_book_count"], 1)
            self.assertNotIn("prices", row)
            self.assertNotIn("api_key", json.dumps(row).lower())

    def test_builder_has_no_network_or_secret_dependency(self):
        source = Path(builder.__file__).read_text()
        for forbidden in ("urllib", "requests.", "httpx", "THERUNDOWN_API_KEY",
                          "X-TheRundown-Key"):
            self.assertNotIn(forbidden, source)

    def test_featured_market_values(self):
        florida = self.payload["teams"]["CFF_FLA"]
        ole_miss = self.payload["teams"]["CFF_MISS"]
        self.assertEqual((florida["team_spread"], florida["game_total"],
                          florida["team_implied_total"]), (-26.5, 59.5, 43.0))
        self.assertEqual((ole_miss["team_spread"], ole_miss["game_total"],
                          ole_miss["team_implied_total"]), (-6.5, 55.0, 30.75))


if __name__ == "__main__":
    unittest.main()
