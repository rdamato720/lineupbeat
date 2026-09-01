#!/usr/bin/env python3
"""Contracts for the development-only, evidence-first comparison engine."""

from __future__ import annotations

import unittest

import build_decision_room
import college_decision_data
import college_decision_room
import decision_data
from decision_engine import (DecisionContext, compare, confidence,
                             editorial_for_pair, evidence_stack)


def fixture(pid: str, points: float, *, adp=None, history=None) -> dict:
    return {
        "id": pid, "name": pid.title(), "team": "ATL", "position": "RB",
        "adp": adp, "history": history or {},
        "formats": {
            "ppr": {"projected_points": points, "overall_rank": 10,
                    "position_rank": 5},
            "half_ppr": {"projected_points": points, "overall_rank": 10,
                         "position_rank": 5},
            "non_ppr": {"projected_points": points, "overall_rank": 10,
                        "position_rank": 5},
        },
    }


class ThresholdTests(unittest.TestCase):
    def test_season_thresholds_are_deterministic(self):
        expected = [(2.0, "Toss-Up"), (2.1, "Lean"), (6.0, "Lean"),
                    (6.1, "Edge"), (14.0, "Edge"), (14.1, "Strong Edge")]
        self.assertEqual([(gap, confidence(gap, 200, "season"))
                          for gap, _ in expected], expected)

    def test_weekly_thresholds_are_deterministic(self):
        expected = [(.5, "Toss-Up"), (.6, "Toss-Up"), (.7, "Lean"),
                    (1.4, "Lean"), (1.5, "Edge"), (3.0, "Edge"),
                    (3.1, "Strong Edge")]
        self.assertEqual([(gap, confidence(gap, 20, "weekly"))
                          for gap, _ in expected], expected)

    def test_point_one_and_rounded_ties_never_recommend(self):
        context = DecisionContext("season", 2026, "ppr")
        point_one = compare(fixture("a", 100.1), fixture("b", 100.0), context)
        rounded = compare(fixture("a", 100.04), fixture("b", 100.03), context)
        for result in (point_one, rounded):
            self.assertEqual(result["confidence"], "Toss-Up")
            self.assertEqual(result["call"], "No clear edge")
            self.assertIsNone(result["winner"])
            self.assertIsNone(result["recommendation"])
        self.assertTrue(rounded["is_tie"])

    def test_scoring_format_reversal_is_explicit(self):
        a, b = fixture("a", 210), fixture("b", 200)
        a["formats"]["half_ppr"]["projected_points"] = 190
        b["formats"]["half_ppr"]["projected_points"] = 205
        result = compare(a, b, DecisionContext("season", 2026, "ppr"))
        self.assertIn("Half-PPR", result["format_flips"])


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nfl = decision_data.load_season()
        cls.college = college_decision_data.load_weekly()

    def test_exactly_six_dated_editorial_interventions_resolve(self):
        opinions = self.nfl["editorial_opinions"]
        self.assertEqual(len(opinions), 6)
        self.assertTrue(all(row["historical_only"] and
                            row["evidence_date"] == "2026-08-18"
                            for row in opinions))
        pairs = {(row["subject_id"], row["preferred_over_id"])
                 for row in opinions}
        self.assertEqual(len(pairs), 6)

    def test_editorial_opinion_appears_only_for_documented_pair(self):
        players = {p["id"]: p for p in self.nfl["players"]}
        row = self.nfl["editorial_opinions"][0]
        found = editorial_for_pair(players[row["subject_id"]],
                                   players[row["preferred_over_id"]],
                                   self.nfl["editorial_opinions"])
        self.assertEqual(found["opinion_id"], row["opinion_id"])
        unrelated = next(p for p in players.values() if p["id"] not in {
            row["subject_id"], row["preferred_over_id"]})
        self.assertIsNone(editorial_for_pair(players[row["subject_id"]],
                                             unrelated,
                                             self.nfl["editorial_opinions"]))

    def test_stale_editorial_evidence_is_labeled(self):
        players = {p["id"]: p for p in self.nfl["players"]}
        row = self.nfl["editorial_opinions"][0]
        stack = evidence_stack(
            players[row["subject_id"]], players[row["preferred_over_id"]],
            DecisionContext("season", 2026, "half_ppr"),
            self.nfl["editorial_opinions"], self.nfl["sources"])
        self.assertTrue(stack["editorial_stale"])
        self.assertEqual(stack["categories"]["editorial"], "present")

    def test_missing_adp_history_and_sos_reduce_quality(self):
        stack = evidence_stack(
            fixture("a", 100), fixture("b", 90),
            DecisionContext("season", 2026, "ppr"))
        self.assertEqual(stack["categories"]["adp"], "unavailable")
        self.assertEqual(stack["categories"]["history"], "unavailable")
        self.assertEqual(stack["categories"]["schedule_sos"], "unavailable")
        self.assertNotEqual(stack["data_quality"], "High")

    def test_nfl_and_college_inputs_are_isolated(self):
        nfl_ids = {p["id"] for p in self.nfl["players"]}
        college_ids = {p["id"] for p in self.college["players"]}
        self.assertTrue(nfl_ids.isdisjoint(college_ids))
        self.assertEqual(self.college["available_formats"], ["yahoo"])
        self.assertEqual(self.college["editorial_opinions"], [])
        self.assertFalse(self.nfl["schedule_sos_available"])
        self.assertFalse(self.college["schedule_sos_available"])


class LayoutContracts(unittest.TestCase):
    def test_v2_stack_and_responsive_layouts_are_rendered(self):
        nfl = build_decision_room.render(decision_data.load_season())
        for label in ("The Call", "Why", "Case for each player",
                      "What changes the call", "Confidence and data quality"):
            self.assertIn(label, nfl)
            self.assertIn(label, college_decision_room.JS)
        for selector in (".dr-why-grid", ".dr-case-grid", ".dr-quality-grid",
                         "@media(max-width:780px)", "@media(max-width:430px)"):
            self.assertIn(selector, build_decision_room.CSS)

    def test_toss_up_renderer_has_no_contradictory_recommendation(self):
        nfl = build_decision_room.render(decision_data.load_season())
        self.assertIn("cls==='Toss-Up'?null:lead", nfl)
        self.assertIn("w=c==='Toss-Up'?null:lead", college_decision_room.JS)
        self.assertIn("No clear edge", nfl)
        self.assertIn("No clear edge", college_decision_room.JS)


if __name__ == "__main__":
    unittest.main()
