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

    def test_missing_adp_conference_and_images_are_explicit(self):
        self.assertFalse(self.data["adp_available"])
        self.assertFalse(self.data["conference_available"])
        self.assertTrue(all(p["adp"] is None and p["photo"] is None and
                            p["team_logo"] is None for p in self.data["players"]))

    def test_single_format_does_not_claim_reversals(self):
        self.assertEqual(self.data["available_formats"], ["yahoo"])
        self.assertTrue(all(not compare(
            self.data["players"][i], self.data["players"][i + 1],
            DecisionContext("weekly", 2026, "yahoo", week=1))["format_flips"]
                            for i in range(3)))


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

    def test_fallback_branding_and_mobile_layout(self):
        self.assertIn("cdr-crest", college_decision_room.JS)
        self.assertIn("@media(max-width:780px)", build_decision_room.CSS)

    def test_college_payload_is_not_in_initial_nfl_markup(self):
        nfl = decision_data.load_season()
        rendered = build_decision_room.render(nfl)
        self.assertNotIn('"CFP_', rendered)
        self.assertIn('/data/decision-room-college.json', rendered)

    def test_isolated_payload_size_is_reasonable(self):
        encoded = json.dumps(college_decision_data.load_weekly(),
                             separators=(",", ":")).encode()
        self.assertLess(len(encoded), 1_500_000)


if __name__ == "__main__":
    unittest.main()
