#!/usr/bin/env python3
import unittest

import build_ranking_formats as formats


class RankingFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = formats.read_projection_formats(formats.SOURCE)

    def test_all_supported_formats_reconcile(self):
        self.assertEqual(set(self.rows), {"ppr", "non_ppr", "superflex"})
        self.assertTrue(all(len(rows) == 615 for rows in self.rows.values()))
        self.assertEqual(formats.source_updated(formats.SOURCE).date().isoformat(),
                         "2026-08-30")

    def test_top_200_and_position_ranks_are_complete(self):
        for key, rows in self.rows.items():
            ranked, _ = formats.rank(rows, key)
            self.assertEqual(sum(r["overall_rank"] is not None for r in ranked), 200)
            for pos in formats.base.POSITIONS:
                group = [r for r in ranked if r["position"] == pos]
                self.assertEqual(sorted(r["position_rank"] for r in group),
                                 list(range(1, len(group) + 1)))

    def test_superflex_uses_deeper_quarterback_replacement(self):
        ranked, sf = formats.rank(self.rows["superflex"], "superflex")
        _, ppr = formats.rank(self.rows["ppr"], "ppr")
        self.assertLess(sf["QB"], ppr["QB"])
        self.assertEqual(formats.FORMATS["superflex"]["replacement_qb"], 33)
        where = {r["player_name"]: r for r in ranked}
        self.assertLess(where["Bijan Robinson"]["overall_rank"],
                        where["Jahmyr Gibbs"]["overall_rank"])
        self.assertLess(where["Ja'Marr Chase"]["position_rank"],
                        where["Puka Nacua"]["position_rank"])
        self.assertEqual(where["Malik Nabers"]["position_rank"], 10)
        self.assertIn("QB33", formats.editorial_notes("superflex"))

    def test_ppr_uses_independent_documented_editorial_decisions(self):
        ranked, _ = formats.rank(self.rows["ppr"], "ppr")
        where = {r["player_name"]: r for r in ranked}
        self.assertLess(where["Bijan Robinson"]["overall_rank"],
                        where["Jahmyr Gibbs"]["overall_rank"])
        self.assertLess(where["Ja'Marr Chase"]["position_rank"],
                        where["Puka Nacua"]["position_rank"])
        self.assertEqual(where["Ashton Jeanty"]["manual_adjustment"], 16.0)
        self.assertEqual(where["Christian McCaffrey"]["manual_adjustment"], -48.0)
        self.assertEqual(where["Malik Nabers"]["position_rank"], 10)
        self.assertNotIn("Jeff", formats.editorial_notes("ppr"))

    def test_non_ppr_stays_projection_led_with_documented_role_calls(self):
        ranked, _ = formats.rank(self.rows["non_ppr"], "non_ppr")
        where = {r["player_name"]: r for r in ranked}
        self.assertLess(where["Jahmyr Gibbs"]["overall_rank"],
                        where["Bijan Robinson"]["overall_rank"])
        self.assertLess(where["Puka Nacua"]["position_rank"],
                        where["Ja'Marr Chase"]["position_rank"])
        self.assertEqual(where["Ashton Jeanty"]["manual_adjustment"], 12.0)
        self.assertEqual(where["Christian McCaffrey"]["manual_adjustment"], -33.0)
        self.assertNotIn("Jeff", formats.editorial_notes("non_ppr"))

    def test_menu_links_supported_products_and_omits_idp(self):
        menu = formats.format_nav("/nfl/rankings/ppr/")
        for label, _, _ in formats.FORMAT_NAV:
            self.assertIn(label, menu)
        self.assertNotIn('href="/nfl/rankings/idp/', menu)
        self.assertIn('href="/nfl/rankings/dynasty/', menu)

    def test_dynasty_uses_verified_age_curve_without_outside_ratings(self):
        ranked = formats.rank_dynasty(
            self.rows["ppr"], formats.read_roster_ages(formats.ROSTER))
        self.assertGreaterEqual(len(ranked), 500)
        self.assertEqual(sum(r["overall_rank"] is not None for r in ranked), 200)
        where = {r["player_name"]: r for r in ranked}
        self.assertLess(where["Bijan Robinson"]["overall_rank"], 5)
        self.assertLess(where["Bijan Robinson"]["overall_rank"],
                        where["Jahmyr Gibbs"]["overall_rank"])
        self.assertLess(where["Ashton Jeanty"]["position_rank"],
                        where["Christian McCaffrey"]["position_rank"])
        self.assertLessEqual(where["Ashton Jeanty"]["position_rank"], 3)
        self.assertLessEqual(where["Malik Nabers"]["position_rank"], 10)
        page = formats.render_dynasty(ranked, formats.source_updated(formats.SOURCE))
        self.assertIn("verified roster age", page)
        self.assertNotIn("RATING", page)

    def test_page_has_canonical_schema_faq_and_all_rows(self):
        ranked, _ = formats.rank(self.rows["superflex"], "superflex")
        page = formats.render(
            ranked, "superflex", "top-200-superflex",
            formats.base.eastern_now(), top_only=True)
        self.assertIn('<link rel="canonical" href="https://lineupbeat.com/nfl/rankings/top-200-superflex/">', page)
        self.assertIn('type="application/ld+json"', page)
        self.assertIn("What is a Superflex fantasy football league?", page)
        self.assertEqual(page.count(' data-name="'), 200)


if __name__ == "__main__":
    unittest.main()
