#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import build_decision_room as page
import decision_data
from decision_engine import (DecisionContext, closest_calls, compare,
                             confidence, convictions, eligible_opponents,
                             value_signals)


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
        self.assertEqual(confidence(0), "True Toss-Up")
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

    def test_exact_tie_has_no_recommendation(self):
        self.b["formats"]["ppr"]["projected_points"] = 250.0
        result = compare(self.a, self.b, self.context)
        self.assertTrue(result["is_tie"])
        self.assertIsNone(result["winner"])
        self.assertEqual(result["confidence"], "True Toss-Up")

    def test_display_rounded_tie_has_no_recommendation(self):
        self.a["formats"]["ppr"]["projected_points"] = 250.04
        self.b["formats"]["ppr"]["projected_points"] = 250.03
        result = compare(self.a, self.b, self.context)
        self.assertTrue(result["is_tie"])
        self.assertIsNone(result["winner"])
        self.assertEqual(result["gap"], 0.0)

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

    def test_values_and_fades_are_separate(self):
        self.a["adp"] = 35.0
        self.b["adp"] = 1.0
        values, fades = value_signals([self.a, self.b], "ppr")
        self.assertEqual([r["player"]["id"] for r in values], ["a"])
        self.assertEqual([r["player"]["id"] for r in fades], ["b"])

    def test_player_two_defaults_to_same_position_unless_cross_position(self):
        qb = player("q", "Quarter Back", "QB",
                    {"ppr": 200, "half_ppr": 200, "non_ppr": 200},
                    {"ppr": 30, "half_ppr": 30, "non_ppr": 30})
        self.assertTrue(all(p["position"] == "RB" for p in
                            eligible_opponents([self.a, self.b, qb], "a")))
        self.assertIn("q", [p["id"] for p in
                            eligible_opponents([self.a, self.b, qb], "a", True)])

    def test_missing_adp_does_not_create_market_claim(self):
        self.a["adp"] = None
        result = compare(self.a, self.b, self.context)
        self.assertEqual(result["market_alignment"], "unavailable")

    def test_weekly_context_requires_a_validated_week(self):
        with self.assertRaises(ValueError):
            DecisionContext("weekly", 2026, "ppr")


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

    def test_searchable_accessible_selectors_and_market_sections_render(self):
        for text in ('role="combobox"', 'role="listbox"',
                     'Compare across positions', "Our Values", "Our Fades"):
            self.assertIn(text, self.html)
        self.assertNotIn("Lineup Beat Convictions", self.html)

    def test_tie_copy_is_present_and_does_not_claim_a_higher_projection(self):
        self.assertIn("No clear edge", self.html)
        self.assertIn("True Toss-Up", self.html)
        self.assertIn("when the displayed projections are equal", self.html)

    def test_no_weekly_projection_or_probability_claims(self):
        lowered = self.html.split('</main>', 1)[0].lower()
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

    def test_inject_replaces_news_hero_and_splits_complete_wire(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "index.html"
            cards = "".join(f'<article class="tile wire" data-publication-id="p{i}"></article>'
                            for i in range(6))
            target.write_text('<html><head><title>Old</title><meta name="description" content="old">'
                              '<style id="wire-css">#wire .tiles{display:block}</style></head><body>'
                              '<section class="lb-hero" id="hero">Old</section>'
                              '<section class="hero medhero"></section>'
                              '<!-- LB WIRE REPLACEMENT START --><section id="wire"><p>6 reviewed reports</p>'
                              f'<div class="tiles">{cards}</div></section>'
                              '<script>window.__LB_WIRE_REPLACEMENT__=true;</script>'
                              '<!-- LB WIRE REPLACEMENT END --></body></html>')
            page.inject(target)
            rendered = target.read_text()
            self.assertNotIn(">Old</section>", rendered)
            self.assertIn('id="decision-room"', rendered)
            self.assertIn('id="wire"', rendered)
            self.assertIn('id="livelist"', rendered)
            self.assertIn('id="liveago"', rendered)
            self.assertEqual(rendered.count('class="tile wire"'), 4)
            self.assertIn("Fantasy Football Decision Room | Lineup Beat", rendered)
            dedicated = Path(tmp) / "decision-room" / "reviewed-wire" / "index.html"
            self.assertEqual(dedicated.read_text().count('class="tile wire"'), 6)
            self.assertIn('role="combobox"', rendered)

    def test_development_workflow_builds_before_protection(self):
        workflow = (page.decision_data.ROOT / ".github/workflows/dev-site.yml").read_text()
        self.assertLess(workflow.index("build_decision_room.py"),
                        workflow.index("dev_site.py protect"))
        self.assertIn("verify_deploy_artifact.py site --decision-room", workflow)


if __name__ == "__main__":
    unittest.main()
