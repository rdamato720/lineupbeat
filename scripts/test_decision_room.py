#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import build_decision_room as page
import dev_site
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
        self.assertEqual(confidence(0), "Toss-Up")
        self.assertEqual(confidence(2.0), "Toss-Up")
        self.assertEqual(confidence(2.1, 200), "Lean")
        self.assertEqual(confidence(6.1, 200), "Edge")
        self.assertEqual(confidence(14.1, 200), "Strong Edge")

    def test_decision_flip_threshold_uses_published_precision(self):
        result = compare(self.a, self.b, self.context)
        self.assertEqual(result["runner_up_gain_to_flip"], 6.1)
        self.assertEqual(result["winner_decline_to_flip"], 6.1)

    def test_scoring_format_can_change_pick(self):
        result = compare(self.a, self.b, self.context)
        self.assertEqual(result["format_flips"], ["Non-PPR"])
        self.assertIn("Half-PPR", result["format_classification_changes"])

    def test_exact_tie_has_no_recommendation(self):
        self.b["formats"]["ppr"]["projected_points"] = 250.0
        result = compare(self.a, self.b, self.context)
        self.assertTrue(result["is_tie"])
        self.assertIsNone(result["winner"])
        self.assertEqual(result["confidence"], "Toss-Up")

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
        self.assertTrue(all(x["player_a"]["position"] == x["player_b"]["position"]
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

    def test_home_cleaning_is_stable_with_development_banner(self):
        source = '<html><head><title>x</title></head><body><p>old</p></body></html>'
        home = '<main id="replacement">new</main>'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "index.html"
            path.write_text(page.clean_home_document(source, home))
            dev_site._protect_page(path, "develop")
            first = path.read_text()
            path.write_text(page.clean_home_document(first, home))
            dev_site._protect_page(path, "develop")
            self.assertEqual(first, path.read_text())

    def test_weekly_context_requires_a_validated_week(self):
        with self.assertRaises(ValueError):
            DecisionContext("weekly", 2026, "ppr")


class DecisionRoomRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = decision_data.load_weekly()
        cls.html = page.render(cls.payload)

    def test_validated_adapter_is_weekly(self):
        self.assertEqual(self.payload["mode"], "weekly")
        self.assertEqual(self.payload["season"], 2026)
        self.assertEqual(self.payload["week"], 1)
        self.assertGreaterEqual(len(self.payload["players"]), 150)

    def test_required_weekly_labels_and_empty_states_render(self):
        for text in ("2026 NFL Week 1 Decision Room",
                     "Lineup Beat-owned weekly projections",
                     "Odds and current injury reports are unavailable",
                     "Connect an ESPN roster locally to see supported Week 1 starter and bench decisions.",
                     'href="/my-team/"',
                     "No decisions have been recorded"):
            self.assertIn(text, self.html)

    def test_searchable_accessible_selectors_and_market_sections_render(self):
        for text in ('role="combobox"', 'role="listbox"',
                     'Compare across positions', "Opportunity and opponent context",
                     "What the market says"):
            self.assertIn(text, self.html)
        self.assertNotIn("Lineup Beat Convictions", self.html)

    def test_tie_copy_is_present_and_does_not_claim_a_higher_projection(self):
        self.assertIn("No clear edge", self.html)
        self.assertIn("Toss-Up", self.html)
        self.assertIn("inside the deterministic no-call band", self.html)
        self.assertNotIn("Recommend ${w.name}", self.html)

    def test_no_probability_floor_or_ceiling_claims(self):
        lowered = self.html.split('</main>', 1)[0].lower()
        self.assertIn("week 1 projection", lowered)
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
                              '<!-- LB WIRE REPLACEMENT END -->'
                              '<section id="roshero"><h2>My Roster</h2></section>'
                              '<script>const oldNav=["The Wire","My Roster","Fantasy Data"];</script>'
                              '</body></html>')
            page.inject(target)
            rendered = target.read_text()
            self.assertNotIn(">Old</section>", rendered)
            self.assertIn('id="lineup-beat-home"', rendered)
            self.assertNotIn('id="decision-room"', rendered)
            self.assertNotIn('id="wire"', rendered)
            self.assertNotIn('id="roshero"', rendered)
            self.assertNotIn('const oldNav', rendered)
            self.assertNotIn('My Roster', rendered)
            self.assertEqual(rendered.count('class="tile wire"'), 0)
            self.assertIn("Fantasy Football Decisions for NFL &amp; College | Lineup Beat", rendered)
            dedicated = Path(tmp) / "decision-room" / "reviewed-wire" / "index.html"
            self.assertEqual(dedicated.read_text().count('class="tile wire"'), 6)
            nfl = Path(tmp) / "decision-room" / "nfl" / "index.html"
            college = Path(tmp) / "decision-room" / "college" / "index.html"
            self.assertIn('role="combobox"', nfl.read_text())
            self.assertNotIn('id="livelist"', nfl.read_text())
            self.assertIn('id="college-decision-room"', college.read_text())

    def test_homepage_navigation_sections_and_mobile_structure(self):
        home = page.render_home(self.payload, page.college_decision_data.load_weekly())
        for label in ("NFL", "College", "Decision", "Rankings", "Projections",
                      "MAKE YOUR NEXT MOVE", "START WITH THE EVIDENCE",
                      "CLOSEST CALLS", "SCORING FORMAT MOVERS",
                      "Make the Week 1 call", "Find the Week 1 edge"):
            self.assertIn(label, home)
        self.assertNotIn("NFL or College", home)
        self.assertNotIn("Today’s Decision Board", home)
        self.assertNotIn("Choose your context", home)
        self.assertIn('class="topbar"', home)
        self.assertIn('aria-controls="navdrawer"', home)
        self.assertIn("@media(max-width:780px)", page.CSS)

    def test_featured_decision_is_close_non_tie_with_complete_art(self):
        result = page.featured_decision(
            self.payload["players"],
            DecisionContext("weekly", 2026, "half_ppr", 1))
        self.assertFalse(result["is_tie"])
        self.assertNotEqual(result["confidence"], "Toss-Up")
        for player in (result["winner"], result["runner_up"]):
            self.assertTrue(player["photo"])
            self.assertTrue(player["team_logo"])

    def test_homepage_routes_and_sport_specific_composition(self):
        home = page.render_home(self.payload, page.college_decision_data.load_weekly())
        self.assertIn('href="/decision-room/nfl/"', home)
        self.assertIn('href="/decision-room/college/"', home)
        self.assertNotIn('href="/decision-room/reviewed-wire/"', home)
        self.assertEqual(home.count('data-home-sport="nfl"'), 1)
        self.assertEqual(home.count('data-home-sport="college"'), 1)
        self.assertEqual(home.count('class="hp-call-card"'), 6)
        self.assertEqual(home.count('class="hp-mover-card"'), 3)
        self.assertEqual(home.count('class="hp-action"'), 9)
        self.assertIn('href="/my-team/"', home)
        self.assertEqual(home.count('class="hp-identity hp-college-identity"'), 10)
        self.assertEqual(home.count('class="hp-id-logo"'), 10)
        self.assertIn("Yahoo scoring", home)
        self.assertNotIn("College ADP", home.replace("College ADP is not available", ""))
        self.assertIn("pushState", home)
        self.assertIn("popstate", home)

    def test_sport_navigation_uses_canonical_context_routes(self):
        nfl = page.sport_header("nfl", "rankings", self.payload["players"])
        college = page.sport_header("college", "projections")
        self.assertIn('href="/nfl/rankings/" aria-current="page"', nfl)
        self.assertIn('href="/college-fantasy-football/week-1/"', nfl)
        self.assertIn('aria-label="Search NFL players"', nfl)
        self.assertIn('href="/college-fantasy-football/projections/" aria-current="page"', college)
        self.assertIn('href="/nfl/projections/"', college)
        self.assertIn("Search 2,205 College players", college)
        self.assertNotIn("The Beat", nfl + college)

    def test_home_has_production_visual_language_without_public_news(self):
        home = page.render_home(self.payload, page.college_decision_data.load_weekly())
        for marker in ("lb-decision-hero", "hp-action-grid", "hp-signal-grid",
                       "hp-call-grid", "lb-feature-card", "What changes the pick?"):
            self.assertIn(marker, home)
        for removed in ("Reviewed Updates", "The latest from The Beat",
                        "RECENT NEWS", "NEWS UPDATED"):
            self.assertNotIn(removed, home)

    def test_development_workflow_builds_before_protection(self):
        workflow = (page.decision_data.ROOT / ".github/workflows/dev-site.yml").read_text()
        if 'build_nfl_v15_development.py --development' in workflow:
            runner = (page.decision_data.ROOT / 'scripts/build_nfl_v15_development.py').read_text()
            self.assertLess(runner.index("run('build_decision_room')"),
                            runner.index('dev_site.protect('))
            self.assertLess(runner.index('dev_site.protect('), runner.index('dev_site.verify('))
        else:
            self.assertLess(workflow.index("build_decision_room.py"),
                            workflow.index("dev_site.py protect"))
        self.assertIn("verify_deploy_artifact.py site --decision-room", workflow)


if __name__ == "__main__":
    unittest.main()
