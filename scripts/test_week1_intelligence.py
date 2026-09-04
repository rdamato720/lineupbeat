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


IDENTITY_VARIANTS = {
    "James Cook III": ("James Cook", "00-0037248", "BUF", "RB"),
    "Travis Etienne Jr.": ("Travis Etienne", "00-0036973", "NO", "RB"),
    "Michael Pittman Jr.": ("Michael Pittman", "00-0036252", "PIT", "WR"),
    "Kyle Pitts Sr.": ("Kyle Pitts", "00-0036970", "ATL", "TE"),
    "Aaron Jones Sr.": ("Aaron Jones", "00-0033293", "MIN", "RB"),
    "Tre' Harris": ("Tre’ Harris", "00-0040727", "LAC", "WR"),
    "KC Concepcion": ("K.C. Concepcion", "00-0041547", "CLE", "WR"),
    "Mike Washington Jr.": ("Mike Washington", "00-0040878", "LV", "RB"),
    "Omar Cooper Jr.": ("Omar Cooper", "00-0041511", "NYJ", "WR"),
    "Brian Robinson Jr.": ("Brian Robinson", "00-0037746", "ATL", "RB"),
    "Oronde Gadsden": ("Oronde Gadsden II", "00-0040189", "LAC", "TE"),
}


class NFLWeek1ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = decision_data.load_weekly()
        cls.backtest = json.loads((model.OUTPUT / "nfl_backtest_2025.json").read_text())
        cls.provenance = json.loads((model.OUTPUT / "provenance.json").read_text())

    def test_schedule_and_identity_stop_conditions_pass(self):
        self.assertEqual(len(self.payload["players"]), 182)
        self.assertEqual(len(self.payload["excluded_players"]), 6)
        self.assertEqual(len({p["id"] for p in self.payload["players"]}), 182)
        self.assertEqual(len({p["team"] for p in self.payload["players"]}), 32)
        self.assertTrue(all(p["opponent"] and p["kickoff"] for p in self.payload["players"]))

    def test_full_projection_source_population_is_reported_honestly(self):
        population = self.payload["population"]
        self.assertEqual(population["projection_source"], 615)
        self.assertEqual(population["identity_resolved"], 581)
        self.assertEqual(population["identity_unresolved"], 34)
        self.assertEqual(population["ranked_production"], 188)
        self.assertEqual(population["identity_resolved_not_ranked"], 393)
        self.assertEqual(population["ranked_active_projected"], 182)
        self.assertEqual(population["ranked_excluded"], 6)
        self.assertEqual(len(self.payload["unresolved_players"]), 34)

    def test_all_documented_identity_variants_resolve_deterministically(self):
        players = {player["name"]: player for player in self.payload["players"]}
        for source_name, (identity_name, player_id, team, position) in IDENTITY_VARIANTS.items():
            self.assertEqual(decision_data.normalize_player_name(source_name),
                             decision_data.normalize_player_name(identity_name))
            player = players[source_name]
            self.assertEqual((player["id"], player["team"], player["position"]),
                             (player_id, team, position), source_name)
            identity = player["identity_resolution"]
            self.assertEqual(identity["stable_gsis_id"], player_id)
            self.assertTrue(identity["roster_record"])
            self.assertTrue(identity["season_prior_stat_line"])

    def test_normalized_identity_index_fails_on_ambiguity(self):
        with self.assertRaisesRegex(ValueError, "ambiguous normalized identity"):
            decision_data.identity_index([
                {"player_id": "one", "full_name": "Example Player Jr.",
                 "team": "BUF", "position": "WR"},
                {"player_id": "two", "full_name": "Example Player",
                 "team": "BUF", "position": "WR"},
            ])

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
        self.assertEqual(self.backtest["evaluation_type"], "proxy_context_adjustment_backtest")
        self.assertFalse(self.backtest["production_formula_reproduced"])
        self.assertIn("does not reproduce the deployed production formula", self.backtest["limitation"])
        self.assertGreater(self.backtest["predictions"], 5000)
        for position in model.POSITIONS:
            row = self.backtest["by_position"][position]
            self.assertGreater(row["predictions"], 0)
            self.assertGreater(row["proxy_mae"], 0)
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
        self.assertIn("no D/ST projection is included", html)
        self.assertEqual(self.payload["limitations"]["dst_model"],
                         "unavailable; model population is QB/RB/WR/TE only")
        self.assertFalse(self.payload["limitations"]["predictive_lift_claim"])
        self.assertEqual(self.payload["limitations"]["matchup_context"],
                         "2025 prior-season context")

    def test_browser_engine_uses_weekly_call_and_flip_boundaries(self):
        html = build_decision_room.render(self.payload)
        self.assertIn('if(weekly){if(g<=.5||q<=3)return"Toss-Up"', html)
        self.assertIn('boundary=weekly?Math.max(.5,reference*.03)', html)
        self.assertIn("recommendationsAuthorized=D.recommendation_state?.enabled===true", html)
        self.assertIn("No lineup recommendation is issued.", html)
        self.assertIn("Projection favors ${safe(w.name)}", html)


class CollegeWeek1EnrichmentTests(unittest.TestCase):
    def test_all_active_reconciled_projections_and_components_are_preserved(self):
        payload = college_decision_data.load_weekly()
        self.assertEqual(len(payload["players"]), 2205)
        config = json.loads((ROOT / "data" / "college" / "config.json").read_text())
        source = (ROOT / "data" / "college" /
                  config["activeCollegeWeeklyProjectionVersion"] /
                  "college_week1_site_projections_2026.json")
        raw = json.loads(source.read_text())
        by_id = {p["id"]: p for p in payload["players"]}
        for row in raw["players"]:
            player = by_id[row["id"]]
            self.assertEqual(player["formats"]["yahoo"]["projected_points"], row["pts"])
            self.assertEqual(player["expected_opportunity"]["carries"], row["rushAtt"])
            self.assertEqual(player["expected_opportunity"]["receptions"], row["rec"])
            self.assertEqual(player["implied_total"], row["impliedTotal"])

    def test_college_market_copy_is_delayed_consensus_context(self):
        payload = college_decision_data.load_weekly()
        self.assertEqual(payload["market"]["state"],
                         "available_delayed_market_context")
        self.assertEqual(payload["market"]["data_delay_seconds"], 30)
        self.assertEqual(payload["market"]["player_coverage"]["playersWithNumericEvidence"], 112)
        self.assertIn("Sportsbook environment",
                      build_decision_room.college_decision_room.JS)
        self.assertIn("exact player-component markets for 112 players",
                      build_decision_room.college_decision_room.SHELL)
        self.assertIn("Expected opportunity", build_decision_room.college_decision_room.JS)


if __name__ == "__main__":
    unittest.main()
