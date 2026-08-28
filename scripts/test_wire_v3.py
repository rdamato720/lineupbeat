#!/usr/bin/env python3
"""Regressions for the story-first Wire V3 quality boundary."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wire import v3
from wire.v3_draft import SYSTEM, build_prompt, validate
import wire_v3_inbox


def row(cid, player, pid, evidence, url="https://example.com/story", position="WR"):
    return {"candidate_id": cid, "player": player, "player_id": pid,
            "team": "NE", "position": position, "source_name": "Test",
            "source_id": "test", "ownership": "INDEPENDENT", "author": "Reporter",
            "source_url": url, "published_at": "2026-08-27T14:00:00+00:00",
            "evidence": evidence, "origin": "X"}


def card(pid, player, basis, event_type="ROLE"):
    return {"player_id": pid, "event_type": event_type, "direction": "NEUTRAL",
            "what_changed": f"{player} received a direct material update.",
            "lineupbeat_impact": "The development is worth monitoring, with the remaining uncertainty intact.",
            "evidence_basis": basis, "limitations": [], "confidence": .8}


class WireV3Tests(unittest.TestCase):
    def test_one_source_roundup_becomes_one_story(self):
        evidence = "Players not dressing: A.J. Brown, Drake Maye, Mack Hollins, Romeo Doubs, Kayshon Boutte."
        rows = [row(str(i), name, str(i), evidence) for i, name in enumerate(
            ["A.J. Brown", "Drake Maye", "Mack Hollins", "Romeo Doubs", "Kayshon Boutte"], 1)]
        stories = v3.cluster(rows)
        self.assertEqual(len(stories), 1)
        self.assertEqual(len(stories[0]["players"]), 5)
        self.assertTrue(v3.is_broad_roster_list(stories[0]))
        result = {"decision": "PROPOSE", "cards": [card("1", "A.J. Brown", evidence)], "reason": ""}
        self.assertTrue(any("broad roster" in failure for failure in validate(result, stories[0])))

    def test_preseason_starter_dump_cannot_be_role_news(self):
        evidence = "Preseason offensive starters: Tommy DeVito, Kyle Williams, Efton Chism, CJ Dippre, Lan Larison."
        names = ["Tommy DeVito", "Kyle Williams", "Efton Chism", "CJ Dippre", "Lan Larison"]
        story = v3.cluster([row(str(i), name, str(i), evidence, position="QB" if i == 1 else "WR")
                            for i, name in enumerate(names, 1)])[0]
        result = {"decision": "PROPOSE", "cards": [card("1", "Tommy DeVito", evidence)], "reason": ""}
        failures = validate(result, story, {"1": "WATCHLIST"})
        self.assertTrue(any("preseason lineup" in failure for failure in failures))

    def test_duplicate_legal_reports_merge_cross_url(self):
        left = row("a", "Josh Jacobs", "jj", "Josh Jacobs was charged with misdemeanor battery.",
                   "https://one.example/report", "RB")
        right = row("b", "Josh Jacobs", "jj", "Court records show misdemeanor battery charges against Josh Jacobs.",
                    "https://two.example/report", "RB")
        self.assertEqual(len(v3.cluster([left, right])), 1)

    def test_direct_return_is_valid(self):
        evidence = "The Colts activated Alec Pierce from PUP and he returned to practice."
        story = v3.cluster([row("a", "Alec Pierce", "ap", evidence, position="WR")])[0]
        result = {"decision": "PROPOSE", "cards": [card("ap", "Alec Pierce", evidence, "AVAILABILITY")],
                  "reason": "Direct return."}
        self.assertEqual(validate(result, story, {"ap": "ROSTERABLE"}), [])

    def test_reserve_qb_injury_is_not_a_card(self):
        evidence = "Will Howard was evaluated for a possible head injury."
        story = v3.cluster([row("a", "Will Howard", "wh", evidence, position="QB")])[0]
        result = {"decision": "PROPOSE", "cards": [card("wh", "Will Howard", evidence, "AVAILABILITY")],
                  "reason": "Availability."}
        self.assertTrue(any("reserve quarterback" in failure
                            for failure in validate(result, story, {"wh": "WATCHLIST"})))

    def test_direct_trade_is_valid(self):
        evidence = "The Patriots traded Kayshon Boutte to the Texans."
        story = v3.cluster([row("a", "Kayshon Boutte", "kb", evidence)])[0]
        result = {"decision": "PROPOSE", "cards": [card("kb", "Kayshon Boutte", evidence, "TRANSACTION")],
                  "reason": "Direct transaction."}
        self.assertEqual(validate(result, story, {"kb": "WATCHLIST"}), [])

    def test_second_card_requires_distinct_player_and_basis(self):
        evidence = "Alec Pierce returned to practice. Michael Pittman returned to practice."
        story = v3.cluster([row("a", "Alec Pierce", "ap", evidence),
                            row("b", "Michael Pittman", "mp", evidence)])[0]
        result = {"decision": "PROPOSE", "cards": [card("ap", "Alec Pierce", evidence, "AVAILABILITY"),
                  card("mp", "Michael Pittman", evidence, "AVAILABILITY")], "reason": ""}
        self.assertIn("second card lacks a distinct player and evidence basis",
                      validate(result, story, {"ap": "ROSTERABLE", "mp": "ROSTERABLE"}))

    def test_analysis_article_cannot_repackage_old_availability(self):
        evidence = "The Seahawks will be without Zach Charbonnet for roughly half of this season."
        report = row("a", "Zach Charbonnet", "zc", evidence, position="RB")
        report["source_name"] = "Fantasy On SI"
        story = v3.cluster([report])[0]
        result = {"decision": "PROPOSE", "cards": [
            card("zc", "Zach Charbonnet", evidence, "AVAILABILITY")], "reason": ""}
        self.assertTrue(any("does not establish a new availability" in failure
                            for failure in validate(result, story, {"zc": "ROSTERABLE"})))

    def test_vague_injury_and_invented_body_part_fail(self):
        evidence = "The wild card is the health of Savion Williams, who was injured at Denver."
        story = v3.cluster([row("a", "Savion Williams", "sw", evidence)])[0]
        result = {"decision": "PROPOSE", "cards": [
            card("sw", "Savion Williams", evidence, "AVAILABILITY")], "reason": ""}
        result["cards"][0]["what_changed"] = "Savion Williams injured his ankle at Denver."
        failures = validate(result, story, {"sw": "ROSTERABLE"})
        self.assertTrue(any("too vague" in failure for failure in failures))
        self.assertTrue(any("ankle injury detail" in failure for failure in failures))

    def test_prompt_and_issue_are_explicitly_review_only(self):
        story = v3.cluster([row("a", "Alec Pierce", "ap", "Alec Pierce returned to practice.")])[0]
        self.assertIn("REPORT COUNT: 1", build_prompt(story))
        self.assertIn("Return one card by default", SYSTEM)
        payload = {"reviewed_story_count": 0, "reports_merged": 0, "cost_usd": 0,
                   "proposals": [], "outcomes": []}
        issue = wire_v3_inbox.render(payload)
        self.assertIn("Nothing here can publish", issue)
        self.assertNotIn("wire_publications", issue)

    def test_rejection_diagnostics_are_collapsed(self):
        payload = {"reviewed_story_count": 1, "reports_merged": 0, "cost_usd": 0,
                   "proposals": [], "outcomes": [{"players": ["Example Player"],
                   "decision": "IGNORE", "reason": "No new development",
                   "validation_failures": []}]}
        issue = wire_v3_inbox.render(payload)
        self.assertIn("<details><summary>Stories not proposed and diagnostics</summary>", issue)
        self.assertIn("</details>", issue)


if __name__ == "__main__":
    unittest.main()
