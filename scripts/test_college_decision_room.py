#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import build_decision_room
import college_decision_data
import college_decision_room
import decision_data
from decision_engine import DecisionContext, compare, eligible_opponents


class CollegeDecisionDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = college_decision_data.load_weekly()

    def test_validated_weekly_horizon_and_counts(self):
        self.assertEqual((self.data["mode"], self.data["season"], self.data["week"]),
                         ("weekly", 2026, 1))
        self.assertEqual(self.data["title"], "College Week 1 Decision Room")
        self.assertEqual(self.data["counts"]["players"], 2205)
        self.assertEqual(self.data["counts"]["teams"], 64)

    def test_identity_is_complete_and_isolated_from_nfl(self):
        ids = {p["id"] for p in self.data["players"]}
        nfl_ids = {p["id"] for p in decision_data.load_season()["players"]}
        self.assertEqual(len(ids), 2205)
        self.assertTrue(all(pid.startswith("CFP_") for pid in ids))
        self.assertTrue(ids.isdisjoint(nfl_ids))

    def test_college_comparison_and_flip_threshold(self):
        a, b = self.data["players"][:2]
        result = compare(a, b, DecisionContext("weekly", 2026, "yahoo", week=1))
        self.assertEqual(result["gap"], round(abs(
            a["formats"]["yahoo"]["projected_points"] -
            b["formats"]["yahoo"]["projected_points"]), 1))
        self.assertEqual(result["runner_up_gain_to_flip"], round(result["gap"] + .1, 1))

    def test_same_position_filtering(self):
        chosen = self.data["players"][0]
        rows = eligible_opponents(self.data["players"], chosen["id"])
        self.assertTrue(rows)
        self.assertTrue(all(p["position"] == chosen["position"] for p in rows))

    def test_missing_adp_conference_and_player_images_are_explicit(self):
        self.assertFalse(self.data["adp_available"])
        self.assertFalse(self.data["conference_available"])
        self.assertTrue(all(p["adp"] is None and p["photo"] is None
                            for p in self.data["players"]))
        self.assertTrue(all(p["team_logo"].startswith("/assets/college-teams/CFF_")
                            for p in self.data["players"]))

    def test_single_format_does_not_claim_reversals(self):
        self.assertEqual(self.data["available_formats"], ["yahoo"])
        self.assertTrue(all(not compare(
            self.data["players"][i], self.data["players"][i + 1],
            DecisionContext("weekly", 2026, "yahoo", week=1))["format_flips"]
                            for i in range(3)))

    def test_delayed_market_context_covers_every_modeled_team(self):
        market = self.data["market"]
        contexts = self.data["market_context_by_team"]
        self.assertEqual(market["state"], "available_delayed_market_context")
        self.assertEqual(market["data_delay_seconds"], 30)
        self.assertEqual(len(contexts), 64)
        self.assertTrue(all(row["state"] == "available"
                            and row["spread_book_count"] >= 1
                            and row["total_book_count"] >= 1
                            for row in contexts.values()))

    def test_featured_college_market_context_is_exact(self):
        contexts = self.data["market_context_by_team"]
        florida = contexts["CFF_FLA"]
        ole_miss = contexts["CFF_MISS"]
        self.assertEqual((florida["team_spread"], florida["game_total"],
                          florida["team_implied_total"]), (-26.5, 59.5, 43.0))
        self.assertEqual((ole_miss["team_spread"], ole_miss["game_total"],
                          ole_miss["team_implied_total"]), (-6.5, 55.0, 30.75))
        self.assertTrue(florida["blowout_risk"])
        self.assertFalse(ole_miss["blowout_risk"])
        self.assertEqual(florida["consensus_books"],
                         ["Pinnacle", "DraftKings", "FanDuel"])

    def test_player_market_evidence_is_exact_and_bounded(self):
        players = {(p["name"], p["team"]): p for p in self.data["players"]}
        self.assertEqual(players[("Keelon Russell", "Alabama")]["player_market"]["components"],
                         ["passing_touchdowns", "passing_yards"])
        self.assertEqual(players[("Cam Coleman", "Texas")]["player_market"]["components"],
                         ["receiving_yards"])
        self.assertEqual(players[("Jadan Baugh", "Florida")]["player_market"]["state"],
                         "unavailable")
        self.assertEqual(self.data["market"]["player_coverage"][
            "playersWithNumericEvidence"], 112)


class CollegeDecisionRenderingTests(unittest.TestCase):
    def test_url_state_and_honest_labels(self):
        self.assertIn('/decision-room/college/', college_decision_room.SHELL)
        self.assertIn("new URLSearchParams(location.search)", college_decision_room.JS)
        self.assertIn("/decision-room/college", college_decision_room.JS)
        self.assertIn("Week 1 projections", college_decision_room.SHELL)
        self.assertNotIn("College Season Decision Room", college_decision_room.SHELL)

    def test_no_adp_probability_floor_or_ceiling_claims(self):
        text = (college_decision_room.SHELL + college_decision_room.JS).lower()
        self.assertIn("validated college adp is not available", text)
        for forbidden in ("win probability", "% chance", "floor", "ceiling"):
            self.assertNotIn(forbidden, text)

    def test_decision_combines_market_and_workload_without_overclaiming(self):
        text = college_decision_room.SHELL + college_decision_room.JS
        self.assertIn("Sportsbook environment", text)
        self.assertIn("Expected opportunity", text)
        self.assertIn("Blowout risk", text)
        self.assertIn("exact player-component markets", text)
        self.assertIn("market input is not an outcome or guarantee", text)
        self.assertIn("30-second-delayed", text)
        self.assertNotIn("The recommendation follows the higher validated projection", text)

    def test_fallback_branding_and_mobile_layout(self):
        self.assertIn("cdr-crest", college_decision_room.JS)
        self.assertIn("@media(max-width:780px)", build_decision_room.CSS)

    def test_sub_tenth_gaps_do_not_round_to_a_false_tie(self):
        self.assertIn("gapText=v=>Number(v)<.1?'&lt;0.1':num(v)",
                      college_decision_room.JS)
        self.assertIn("gapText(x.gap)", college_decision_room.JS)

    def test_college_payload_is_not_in_initial_nfl_markup(self):
        nfl = decision_data.load_season()
        rendered = build_decision_room.render(nfl)
        self.assertNotIn('"CFP_', rendered)
        self.assertNotIn('/data/decision-room-college.json', rendered)
        self.assertIn('/data/decision-room-college.json', college_decision_room.JS)

    def test_isolated_payload_size_is_reasonable(self):
        encoded = json.dumps(college_decision_data.load_weekly(),
                             separators=(",", ":")).encode()
        self.assertLess(len(encoded), 1_500_000)

    def test_home_feature_requires_two_player_market_records(self):
        source = (Path(__file__).with_name("build_decision_room.py")).read_text()
        self.assertIn('cp[r["a"]].get("player_market", {}).get("components")', source)
        self.assertIn("Player market evidence", source)


if __name__ == "__main__":
    unittest.main()
