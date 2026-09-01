#!/usr/bin/env python3
"""Contracts for the development-only, evidence-first comparison engine."""

from __future__ import annotations

import unittest

import build_decision_room
import college_decision_data
import college_decision_room
import decision_data
from decision_engine import (DecisionContext, compare, confidence,
                             adp_availability, editorial_for_pair,
                             evidence_stack, scoring_sensitivity)


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

    def test_raw_leader_change_inside_all_toss_up_formats_is_not_reversal(self):
        a, b = fixture("a", 100.9), fixture("b", 100.0)
        a["formats"]["half_ppr"]["projected_points"] = 99.6
        b["formats"]["half_ppr"]["projected_points"] = 100.0
        a["formats"]["non_ppr"]["projected_points"] = 101.6
        result = scoring_sensitivity(
            a, b, DecisionContext("season", 2026, "half_ppr"))
        self.assertEqual(result["state"], "all_toss_up_raw_leader_change")
        self.assertTrue(result["all_toss_up"])
        self.assertTrue(result["raw_leader_changed"])
        self.assertFalse(result["meaningful_reversal"])

    def test_adp_availability_names_one_missing_player(self):
        a, b = fixture("available", 100, adp=25.0), fixture("missing", 99)
        status = adp_availability(a, b)
        self.assertEqual(status["state"], "one_missing")
        self.assertEqual(status["missing_player_names"], ["Missing"])

    def test_adp_availability_names_both_missing_players(self):
        status = adp_availability(fixture("first", 100), fixture("second", 99))
        self.assertEqual(status["state"], "both_missing")
        self.assertEqual(status["missing_player_names"], ["First", "Second"])


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

    def test_missing_adp_history_and_sos_reduce_coverage(self):
        stack = evidence_stack(
            fixture("a", 100), fixture("b", 90),
            DecisionContext("season", 2026, "ppr"))
        self.assertEqual(stack["categories"]["adp"], "unavailable")
        self.assertEqual(stack["categories"]["history"], "unavailable")
        self.assertEqual(stack["categories"]["schedule_sos"], "unavailable")
        self.assertLess(stack["data_coverage"]["present"],
                        stack["data_coverage"]["total"])
        self.assertNotIn("data_quality", stack)

    def test_jeanty_taylor_is_strong_projection_edge_but_split_case(self):
        players = {p["id"]: p for p in self.nfl["players"]}
        stack = evidence_stack(
            players["00-0040122"], players["00-0036223"],
            DecisionContext("season", 2026, "half_ppr"),
            self.nfl["editorial_opinions"], self.nfl["sources"])
        self.assertEqual(stack["result"]["confidence"], "Strong Edge")
        self.assertEqual(stack["result"]["winner"]["name"], "Jonathan Taylor")
        self.assertEqual(stack["evidence_agreement"]["state"], "Split")
        jeanty = stack["evidence_agreement"]["by_player"]["00-0040122"]
        self.assertIn("Current ranks", jeanty)
        self.assertIn("Dated Lineup Beat opinion", jeanty)

    def test_chase_nacua_reconciles_projection_ranks_history_and_opinion(self):
        players = {p["id"]: p for p in self.nfl["players"]}
        stack = evidence_stack(
            players["00-0036900"], players["00-0039075"],
            DecisionContext("season", 2026, "half_ppr"),
            self.nfl["editorial_opinions"], self.nfl["sources"])
        self.assertEqual(stack["result"]["confidence"], "Lean")
        self.assertEqual(stack["result"]["winner"]["name"], "Puka Nacua")
        self.assertEqual(stack["evidence_agreement"]["state"], "Split")
        self.assertEqual(
            stack["evidence_agreement"]["by_player"]["00-0036900"],
            ["Current ranks", "Dated Lineup Beat opinion"])
        self.assertEqual(
            stack["evidence_agreement"]["by_player"]["00-0039075"],
            ["Projection edge", "Prior-year consistency"])

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
        for label in ("Lineup Beat call", "Why", "Case for each player",
                      "What changes the call",
                      "Data coverage and evidence agreement"):
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

    def test_renderers_separate_projection_edge_from_overall_call(self):
        nfl = build_decision_room.render(decision_data.load_season())
        self.assertIn("Lineup Beat call", nfl)
        self.assertIn("Projection edge", nfl)
        self.assertIn("Split case", nfl)
        self.assertIn("Evidence agreement", nfl)
        self.assertNotIn("Confidence and data quality", nfl)

    def test_dynamic_agreement_summary_starts_with_a_capital_letter(self):
        nfl = build_decision_room.render(decision_data.load_season())
        self.assertIn("function sentenceStart(v)", nfl)
        self.assertIn("sentenceStart(clauses.join('; '))", nfl)
        self.assertNotIn("return `${clauses.join('; ')}. Evidence agreement", nfl)

    def test_current_ranks_uses_plural_verb_in_dynamic_summary(self):
        nfl = build_decision_room.render(decision_data.load_season())
        self.assertIn("category!=='current ranks'?'favors':'favor'", nfl)
        self.assertIn("agreementVerb(x.groups[p.id])", nfl)

    def test_missing_adp_copy_handles_one_and_both_players(self):
        nfl = build_decision_room.render(decision_data.load_season())
        self.assertIn("one-missing", nfl)
        self.assertIn("both-missing", nfl)
        self.assertIn("validated ADP is unavailable for ${safe(x.missing[0].name)}", nfl)
        self.assertIn("validated ADP is unavailable for both", nfl)

    def test_college_terminal_name_does_not_add_duplicate_punctuation(self):
        self.assertIn("function terminalName", college_decision_room.JS)
        self.assertIn("/[.!?]$/.test", college_decision_room.JS)
        self.assertNotIn("${safe(r.name)}.`", college_decision_room.JS)


if __name__ == "__main__":
    unittest.main()
