#!/usr/bin/env python3
from __future__ import annotations

import json
import statistics
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_decision_room
import build_week1_intelligence as model
import college_decision_data
import decision_data


class NFLWeek1ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = decision_data.load_weekly()
        cls.backtest = json.loads((model.OUTPUT / "nfl_backtest_2025.json").read_text())
        cls.provenance = json.loads((model.OUTPUT / "provenance.json").read_text())

    def test_schedule_and_identity_stop_conditions_pass(self):
        self.assertEqual(len(self.payload["players"]), 171)
        self.assertEqual(len(self.payload["excluded_players"]), 6)
        self.assertEqual(len({p["id"] for p in self.payload["players"]}), 171)
        self.assertEqual(len({p["team"] for p in self.payload["players"]}), 32)
        self.assertTrue(all(p["opponent"] and p["kickoff"] for p in self.payload["players"]))

    def test_scoring_reconciles_from_components(self):
        for player in self.payload["players"]:
            for fmt, reception in (("ppr", 1), ("half_ppr", .5), ("non_ppr", 0)):
                self.assertEqual(
                    player["formats"][fmt]["projected_points"],
                    round(model.score(player["stat_projection"], reception), 1),
                    player["name"],
                )

    def test_weekly_values_are_not_season_values_divided_by_constant(self):
        season = {p["id"]: p for p in decision_data.load_season()["players"]}
        ratios = [p["formats"]["half_ppr"]["projected_points"] /
                  season[p["id"]]["formats"]["half_ppr"]["projected_points"]
                  for p in self.payload["players"]]
        self.assertGreater(statistics.pstdev(ratios), .005)
        self.assertIsNone(self.payload["methodology"]["season_total_divisor"])

    def test_backtest_is_leakage_safe_and_reports_baseline_by_position(self):
        self.assertEqual(self.backtest["future_rows_used"], 0)
        self.assertGreater(self.backtest["predictions"], 5000)
        for position in model.POSITIONS:
            row = self.backtest["by_position"][position]
            self.assertGreater(row["predictions"], 0)
            self.assertGreater(row["model_mae"], 0)
            self.assertGreater(row["baseline_mae"], 0)

    def test_private_provider_and_license_record_are_honest(self):
        calls = self.provenance["provider_requests"]
        self.assertEqual(calls["odds"], 0)
        self.assertEqual(calls["player_props"], 0)
        self.assertEqual(calls["model_api"], 0)
        self.assertEqual(calls["cost_usd"], 0)
        self.assertEqual(len(self.provenance["assets"]), 11)
        self.assertEqual(self.provenance["license_review"]["spdx"], "CC-BY-4.0")
        self.assertTrue(self.provenance["license_review"]["attribution_required"])
        self.assertFalse(self.provenance["license_review"]["share_alike"])

    def test_nfl_product_surfaces_weekly_evidence_and_missing_inputs(self):
        html = build_decision_room.render(self.payload)
        for text in ("Our Week 1 projection", "What the market says", "Opponent matchup",
                     "Expected opportunity", "Availability", "Data coverage",
                     "Evidence agreement", "current injury reports are unavailable"):
            self.assertIn(text, html)
        self.assertNotIn("Weekly lineup decisions will become available", html)
        self.assertNotIn("D.sources.projections", html)

    def test_browser_engine_uses_weekly_call_and_flip_boundaries(self):
        html = build_decision_room.render(self.payload)
        self.assertIn('if(weekly){if(g<=.5||q<=3)return"Toss-Up"', html)
        self.assertIn('boundary=weekly?Math.max(.5,reference*.03)', html)


class CollegeWeek1EnrichmentTests(unittest.TestCase):
    def test_all_approved_projections_and_components_are_preserved(self):
        payload = college_decision_data.load_weekly()
        self.assertEqual(len(payload["players"]), 2205)
        source = ROOT / "data" / "college" / "2026" / "week-1" / "v1.0" / "college_week1_site_projections_2026.json"
        raw = json.loads(source.read_text())
        by_id = {p["id"]: p for p in payload["players"]}
        for row in raw["players"]:
            player = by_id[row["id"]]
            self.assertEqual(player["formats"]["yahoo"]["projected_points"], row["pts"])
            self.assertEqual(player["expected_opportunity"]["carries"], row["rushAtt"])
            self.assertEqual(player["expected_opportunity"]["receptions"], row["rec"])
            self.assertEqual(player["implied_total"], row["impliedTotal"])

    def test_college_market_copy_is_single_frozen_observation(self):
        payload = college_decision_data.load_weekly()
        self.assertEqual(payload["market"]["state"], "frozen_single_observation")
        self.assertIn("not multi-book consensus", build_decision_room.college_decision_room.JS)
        self.assertIn("Expected opportunity", build_decision_room.college_decision_room.JS)


if __name__ == "__main__":
    unittest.main()
