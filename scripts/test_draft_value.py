#!/usr/bin/env python3
import unittest

import build_draft_value as draft


class DraftValueTests(unittest.TestCase):
    def test_signal_boundaries(self):
        self.assertEqual(draft.signal_for(5), "Strong Value")
        self.assertEqual(draft.signal_for(2), "Value")
        self.assertEqual(draft.signal_for(0), "Fair Price")
        self.assertEqual(draft.signal_for(-2), "Pricey")
        self.assertEqual(draft.signal_for(-5), "Overpriced")

    def test_hub_surfaces_current_edges_and_comparison_actions(self):
        projections = draft.read_projections(draft.ROOT / "data/projections.xlsx")
        adp, meta, formats = draft.read_adp()
        boards = {fmt: draft.build_board(projections, adp, fmt)
                  for fmt in ("ppr", "half", "std")}
        body, _built = draft.build_html(boards, meta, "", "", "", formats)

        self.assertIn("Find the value", body)
        self.assertIn("Current draft edges", body)
        self.assertEqual(body.count('class="dvleader"'), 4)
        self.assertIn("/nfl/who-should-i-draft/?player1=", body)
        self.assertIn("Full draft-value board", body)
        self.assertIn("ADP through", body)

    def test_only_matching_adp_scoring_formats_are_offered(self):
        _adp, _meta, formats = draft.read_adp()
        self.assertEqual(formats, ["ppr"])


if __name__ == "__main__":
    unittest.main()
