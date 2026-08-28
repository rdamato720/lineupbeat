#!/usr/bin/env python3
"""Locked regressions for the batch-curated Wire digest."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wire import digest, digest_approval
import wire_digest_inbox


def candidate(player, pid, evidence, url="https://x.com/test/status/1",
              origin="X", ownership="INDEPENDENT"):
    return {"candidate_id": pid, "player": player, "player_id": pid,
            "team": "NE", "position": "WR", "source_name": "Test",
            "author": "Reporter", "source_url": url,
            "published_at": "2026-08-28T12:00:00+00:00", "evidence": evidence,
            "origin": origin, "ownership": ownership}


def update(report, pid, bullet, quote, event="ROLE"):
    return {"player_id": pid, "event_type": event, "bullet": bullet,
            "report_id": report["report_id"], "evidence_quote": quote,
            "reason": "Concrete development."}


class DigestTests(unittest.TestCase):
    def test_per_player_rows_collapse_back_to_one_report(self):
        evidence = "The Rams traded Jarquez Hunter to Miami for Tutu Atwell."
        reports = digest.collect([
            candidate("Jarquez Hunter", "jh", evidence),
            candidate("Tutu Atwell", "ta", evidence),
        ])
        self.assertEqual(len(reports), 1)
        self.assertEqual({row["player_id"] for row in reports[0]["identities"]}, {"jh", "ta"})

    def test_independent_articles_do_not_enter_batch(self):
        row = candidate("Cedric Tillman", "ct", "Cedric Tillman was waived.",
                        origin="ARTICLE", ownership="INDEPENDENT")
        self.assertEqual(digest.collect([row]), [])

    def test_ambiguous_both_players_selection_fails(self):
        evidence = "Both players have become the WR1 and WR2 of this room."
        report = digest.collect([candidate("A.J. Brown", "aj", evidence)])[0]
        payload = {"updates": [update(report, "aj", "A.J. Brown is the WR1.", evidence)],
                   "summary": ""}
        self.assertTrue(any("does not name" in failure
                            for failure in digest.validate(payload, [report])[1][0]["failures"]))

    def test_negated_puka_rumor_fails(self):
        evidence = "Sean McVay said the trade had nothing to do with a possible Puka Nacua suspension."
        report = digest.collect([candidate("Puka Nacua", "pn", evidence)])[0]
        payload = {"updates": [update(report, "pn", "Puka Nacua could be suspended.", evidence,
                                             "SUSPENSION")], "summary": ""}
        self.assertIn("negated or hypothetical event",
                      digest.validate(payload, [report])[1][0]["failures"])

    def test_old_transaction_fails(self):
        evidence = "Earlier in the offseason, the Steelers acquired Michael Pittman Jr."
        report = digest.collect([candidate("Michael Pittman Jr.", "mp", evidence,
                                           origin="ARTICLE", ownership="TEAM_OWNED")])[0]
        payload = {"updates": [update(report, "mp", "Michael Pittman Jr. joined Pittsburgh.",
                                             evidence, "TRANSACTION")], "summary": ""}
        self.assertIn("historical fact repeated as current news",
                      digest.validate(payload, [report])[1][0]["failures"])

    def test_preseason_only_injury_fails(self):
        evidence = "Jacob Cowing missed the preseason finale with a groin injury."
        report = digest.collect([candidate("Jacob Cowing", "jc", evidence)])[0]
        payload = {"updates": [update(report, "jc", "Jacob Cowing missed the preseason finale.",
                                             evidence, "AVAILABILITY")], "summary": ""}
        self.assertIn("preseason-only development",
                      digest.validate(payload, [report])[1][0]["failures"])

    def test_direct_trade_and_legal_updates_pass(self):
        trade = "The Dolphins traded Tutu Atwell to the Rams for Jarquez Hunter."
        legal = "Josh Jacobs was charged with two misdemeanors."
        reports = digest.collect([candidate("Tutu Atwell", "ta", trade),
                                  candidate("Josh Jacobs", "jj", legal,
                                            url="https://x.com/test/status/2")])
        by_player = {identity["player_id"]: report for report in reports
                     for identity in report["identities"]}
        payload = {"updates": [
            update(by_player["ta"], "ta", "Tutu Atwell was traded to the Rams.", trade, "TRANSACTION"),
            update(by_player["jj"], "jj", "Josh Jacobs was charged with two misdemeanors.", legal, "LEGAL")],
            "summary": ""}
        accepted, rejected = digest.validate(payload, reports)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(rejected, [])

    def test_high_signal_news_ranks_ahead_of_newer_practice_chatter(self):
        practice = digest.collect([candidate(
            "A.J. Brown", "aj", "A.J. Brown made a nice catch in practice.",
            url="https://x.com/test/status/9")], max_reports=10)[0]
        practice["published_at"] = "2026-08-28T13:00:00+00:00"
        trade = digest.collect([candidate(
            "Tutu Atwell", "ta", "The Dolphins traded Tutu Atwell to the Rams.",
            url="https://x.com/test/status/8")], max_reports=10)[0]
        trade["published_at"] = "2026-08-28T12:00:00+00:00"
        ranked = sorted([practice, trade], key=digest.report_priority, reverse=True)
        self.assertEqual(ranked[0]["report_id"], trade["report_id"])

    def test_prompt_requests_every_qualifying_development(self):
        self.assertIn("Select every qualifying concrete development", digest.SYSTEM)

    def test_issue_is_one_compact_digest(self):
        update_row = {"player_id": "ta", "player": "Tutu Atwell", "team": "MIA",
                      "position": "WR", "event_type": "TRANSACTION",
                      "bullet": "Tutu Atwell was traded to the Rams.",
                      "evidence_quote": "The Dolphins traded Tutu Atwell to the Rams.",
                      "source_url": "https://x.com/test/status/1", "author": "Reporter",
                      "source_name": "Test", "published_at": "2026-08-28T12:00:00Z",
                      "report_id": "report:one"}
        manifest = digest_approval.make_manifest(
            [update_row], "2026-08-28T12:00:00Z", "a" * 64, "b" * 64, 0, 1, .01)
        issue = wire_digest_inbox.render(manifest)
        self.assertIn("Approve, reject or edit", issue)
        self.assertNotIn("Lineup Beat impact", issue)
        self.assertNotIn("wire_publications", issue)


if __name__ == "__main__":
    unittest.main()
