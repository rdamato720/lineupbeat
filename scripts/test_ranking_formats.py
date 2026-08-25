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
                         "2026-08-24")

    def test_top_200_and_position_ranks_are_complete(self):
        for key, rows in self.rows.items():
            ranked, _ = formats.rank(rows, key)
            self.assertEqual(sum(r["overall_rank"] is not None for r in ranked), 200)
            for pos in formats.base.POSITIONS:
                group = [r for r in ranked if r["position"] == pos]
                self.assertEqual(sorted(r["position_rank"] for r in group),
                                 list(range(1, len(group) + 1)))

    def test_superflex_uses_deeper_quarterback_replacement(self):
        _, ppr = formats.rank(self.rows["ppr"], "ppr")
        _, sf = formats.rank(self.rows["superflex"], "superflex")
        self.assertLess(sf["QB"], ppr["QB"])
        self.assertEqual(formats.FORMATS["superflex"]["replacement_qb"], 33)

    def test_menu_names_every_requested_product_without_fake_data_links(self):
        menu = formats.format_nav("/nfl/rankings/ppr/")
        for label, _, _ in formats.FORMAT_NAV:
            self.assertIn(label, menu)
        self.assertIn("Data required", menu)
        self.assertNotIn('href="/nfl/rankings/idp/', menu)
        self.assertNotIn('href="/nfl/rankings/dynasty/', menu)

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
