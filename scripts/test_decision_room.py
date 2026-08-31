#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import build_decision_room as page
import decision_data
from decision_engine import (DecisionContext, closest_calls, compare,
                             confidence, convictions)


def player(pid, name, pos, points, ranks, adp=None, photo=None):
    return {"id": pid, "slug": name.lower().replace(" ", "-"), "name": name,
            "team": "ATL", "position": pos, "adp": adp, "photo": photo,
            "team_logo": "https://example.test/team.png", "team_color": "#123456",
            "formats": {fmt: {"projected_points": value, "overall_rank": ranks[fmt],
                              "position_rank": ranks[fmt]}
                        for fmt, value in points.items()}}


class DecisionEngineTests(unittest.TestCase):
    def setUp(self):
        self.context = DecisionContext("season", 2026, "ppr")
        self.a = player("a", "Alpha Runner", "RB",
                        {"ppr": 250.0, "half_ppr": 230.0, "non_ppr": 210.0},
                        {"ppr": 10, "half_ppr": 12, "non_ppr": 15}, 18.0)
        self.b = player("b", "Beta Runner", "RB",
                        {"ppr": 244.0, "half_ppr": 232.0, "non_ppr": 214.0},
                        {"ppr": 14, "half_ppr": 9, "non_ppr": 11}, 11.0)

    def test_comparison_order_gap_and_confidence(self):
        result = compare(self.a, self.b, self.context)
        self.assertEqual(result["winner"]["id"], "a")
        self.assertEqual(result["gap"], 6.0)
        self.assertEqual(result["confidence"], "Lean")

    def test_confidence_boundaries(self):
        self.assertEqual(confidence(2.0), "Toss-Up")
        self.assertEqual(confidence(2.1), "Lean")
        self.assertEqual(confidence(12.0), "Clear Edge")

    def test_decision_flip_threshold_uses_published_precision(self):
        result = compare(self.a, self.b, self.context)
        self.assertEqual(result["runner_up_gain_to_flip"], 6.1)
        self.assertEqual(result["winner_decline_to_flip"], 6.1)

    def test_scoring_format_can_change_pick(self):
        result = compare(self.a, self.b, self.context)
        self.assertEqual(result["format_flips"], ["Half-PPR", "Non-PPR"])

    def test_closest_calls_are_same_position_and_gap_ordered(self):
        c = player("c", "Gamma Runner", "RB",
                   {"ppr": 243.8, "half_ppr": 220, "non_ppr": 200},
                   {"ppr": 15, "half_ppr": 16, "non_ppr": 17})
        calls = closest_calls([self.a, self.b, c], "ppr", limit=2)
        self.assertEqual([x["gap"] for x in calls], [0.2, 6.0])
        self.assertTrue(all(x["winner"]["position"] == x["runner_up"]["position"]
                            for x in calls))

    def test_convictions_use_projection_rank_against_adp(self):
        self.a["adp"] = 35.0
        self.b["adp"] = None
        rows = convictions([self.a, self.b], "ppr")
        self.assertEqual([r["player"]["id"] for r in rows], ["a"])
        self.assertEqual(rows[0]["rank_adp_delta"], 25.0)

    def test_missing_adp_does_not_create_market_claim(self):
        self.a["adp"] = None
        result = compare(self.a, self.b, self.context)
        self.assertEqual(result["market_alignment"], "unavailable")

    def test_weekly_context_is_unavailable(self):
        with self.assertRaises(ValueError):
            DecisionContext("weekly", 2026, "ppr", week=1)


class DecisionRoomRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = decision_data.load_season()
        cls.html = page.render(cls.payload)

    def test_validated_adapter_is_season_only(self):
        self.assertEqual(self.payload["mode"], "season")
        self.assertEqual(self.payload["season"], 2026)
        self.assertIsNone(self.payload["week"])
        self.assertGreaterEqual(len(self.payload["players"]), 150)

    def test_required_season_labels_and_empty_states_render(self):
        for text in ("2026 Preseason Decision Room",
                     "Draft Mode — based on full-season projections",
                     "Weekly lineup decisions will become available",
                     "Connect your league to see the decisions that matter on your roster.",
                     "No decisions have been recorded"):
            self.assertIn(text, self.html)

    def test_no_weekly_projection_or_probability_claims(self):
        lowered = self.html.lower()
        self.assertNotIn("week 1 projection", lowered)
        self.assertNotIn("win probability", lowered)
        self.assertNotIn("% chance", lowered)
        self.assertNotIn("floor", lowered)
        self.assertNotIn("ceiling", lowered)

    def test_missing_image_uses_team_logo(self):
        p = player("x", "No Photo", "WR",
                   {"ppr": 1, "half_ppr": 1, "non_ppr": 1},
                   {"ppr": 1, "half_ppr": 1, "non_ppr": 1}, adp=21, photo=None)
        markup = page.conviction_card({"player": p, "format": p["formats"]["ppr"],
                                       "rank_adp_delta": 20, "stance": "ahead"})
        self.assertIn('src="https://example.test/team.png"', markup)

    def test_inject_replaces_news_hero_but_keeps_wire(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "index.html"
            target.write_text('<html><head></head><body><section class="lb-hero" id="hero">Old</section>'
                              '<section class="hero medhero"></section><section id="wire">Beat</section></body></html>')
            page.inject(target)
            rendered = target.read_text()
            self.assertNotIn(">Old</section>", rendered)
            self.assertIn('id="decision-room"', rendered)
            self.assertIn('id="wire"', rendered)

    def test_development_workflow_builds_before_protection(self):
        workflow = (page.decision_data.ROOT / ".github/workflows/dev-site.yml").read_text()
        self.assertLess(workflow.index("build_decision_room.py"),
                        workflow.index("dev_site.py protect"))


if __name__ == "__main__":
    unittest.main()
