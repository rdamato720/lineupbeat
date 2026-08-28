#!/usr/bin/env python3
"""Locked regressions for the facts-only Wire replacement."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wire import facts
import wire_facts_inbox


def candidate(player, pid, evidence, origin="X", ownership="INDEPENDENT",
              url="https://x.com/test/status/1", published="2026-08-28T12:00:00+00:00"):
    return {"candidate_id": pid + url[-1], "player": player, "player_id": pid,
            "team": "NE", "position": "WR", "source_name": "Test source",
            "source_id": "test", "source_class": origin, "ownership": ownership,
            "author": "Reporter", "source_url": url, "published_at": published,
            "evidence": evidence, "origin": origin}


class FactsOnlyTests(unittest.TestCase):
    def test_ambiguous_pronoun_cannot_become_aj_brown_fact(self):
        row = candidate("A.J. Brown", "aj", "Both players have become the WR1 and WR2 of this room.")
        fact, reason = facts.extract(row)
        self.assertIsNone(fact)
        self.assertEqual(reason, "no_current_named_fact_sentence")

    def test_old_pittman_transaction_is_rejected(self):
        row = candidate("Michael Pittman Jr.", "mp",
                        "Earlier in the offseason, the Steelers acquired Michael Pittman Jr.",
                        origin="ARTICLE", ownership="TEAM_OWNED")
        self.assertIsNone(facts.extract(row)[0])

    def test_preseason_only_cowing_injury_is_rejected(self):
        row = candidate("Jacob Cowing", "jc",
                        "Jacob Cowing didn't play in the preseason finale due to a groin injury.")
        self.assertIsNone(facts.extract(row)[0])

    def test_independent_article_is_discovery_only(self):
        row = candidate("Cedric Tillman", "ct", "Cedric Tillman was cut by the Browns.",
                        origin="ARTICLE", ownership="INDEPENDENT")
        self.assertEqual(facts.extract(row)[1], "independent_article_is_discovery_only")

    def test_direct_x_trade_is_accepted(self):
        text = "Trade: Dolphins traded WR Tutu Atwell to the Rams for RB Jarquez Hunter."
        fact, reason = facts.extract(candidate("Tutu Atwell", "ta", text))
        self.assertEqual(reason, "accepted")
        self.assertEqual(fact["event_type"], "TRADE")
        self.assertIn("Tutu Atwell", fact["bullet"])

    def test_surname_is_an_explicit_subject(self):
        text = "Vrabel said Kayshon Boutte likely would've been inactive this season."
        fact, _ = facts.extract(candidate("Kayshon Boutte", "kb", text))
        self.assertIsNotNone(fact)
        self.assertEqual(fact["event_type"], "ROLE")

    def test_duplicate_trade_keeps_more_complete_fact(self):
        short, _ = facts.extract(candidate(
            "Jarquez Hunter", "jh", "The Rams traded away Jarquez Hunter on Thursday.",
            url="https://x.com/a/1"))
        detailed, _ = facts.extract(candidate(
            "Jarquez Hunter", "jh",
            "The Rams traded Jarquez Hunter to the Dolphins for wide receiver Tutu Atwell.",
            url="https://x.com/b/2"))
        kept, duplicate = facts.deduplicate([short, detailed])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(duplicate), 1)
        self.assertIn("Dolphins", kept[0]["bullet"])

    def test_official_direct_role_change_is_accepted(self):
        text = "Kyle Williams is in line to have a larger role and be the top backup at X receiver."
        fact, _ = facts.extract(candidate("Kyle Williams", "kw", text,
                                         origin="ARTICLE", ownership="TEAM_OWNED"))
        self.assertIsNotNone(fact)
        self.assertEqual(fact["event_type"], "ROLE")

    def test_compact_issue_contains_no_impact_copy_or_publish_command(self):
        payload = {"proposal_count": 0, "duplicate_count": 0, "proposals": [],
                   "rejection_counts": {}}
        issue = wire_facts_inbox.render(payload)
        self.assertIn("Fantasy Football News Updates You Need to Know", issue)
        self.assertNotIn("Lineup Beat impact", issue)
        self.assertNotIn("wire_publications", issue)


if __name__ == "__main__":
    unittest.main()
