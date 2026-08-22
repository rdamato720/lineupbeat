#!/usr/bin/env python3
"""Focused regression tests for the repaired review path."""

from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wire import currentness
from wire import evidence_integrity as integ
from wire import independent_review as review
from wire import players
from wire import relevance
import wire_review_package as package


class ReviewRepairTests(unittest.TestCase):
    def test_evidence_hash_excludes_player_metadata(self):
        text = "The player worked with the first team."
        self.assertEqual(integ.sha256_text(text), integ.sha256_text(text))
        self.assertNotEqual(
            integ.request_sha256({"evidence_text": text, "player_id": "A"}),
            integ.request_sha256({"evidence_text": text, "player_id": "B"}))

    def test_four_way_hash_match_and_request_hash_separation(self):
        text = "Complete evidence."
        sha = integ.sha256_text(text)
        record = integ.build_record(
            text, generator_evidence_sha=sha, reviewer_evidence_sha=sha,
            human_evidence_sha=sha, generator_request_sha="request-a",
            reviewer_request_sha="request-b")
        self.assertTrue(record["hashes_match"])
        self.assertFalse(record["blocks_automatic_approval"])

    def test_missing_punctuation_is_not_incomplete(self):
        body = "A complete publisher paragraph without punctuation\nNext paragraph."
        end = body.index("\n")
        complete, reasons = integ.source_boundary_complete(body, 0, end)
        self.assertTrue(complete, reasons)

    def test_midword_cut_is_incomplete(self):
        complete, reasons = integ.source_boundary_complete("abcdef", 0, 3)
        self.assertFalse(complete)
        self.assertIn("span ends inside a word", reasons)

    def test_rolling_page_needs_span_time(self):
        result = currentness.automatic_currentness(
            "https://example.com/news/updates-offseason-2026")
        self.assertFalse(result["eligible"])
        self.assertTrue(currentness.automatic_currentness(
            "https://example.com/news/updates-offseason-2026",
            "2026-08-21T12:10:00Z")["eligible"])

    def test_claim_subject_conflict_blocks_only_auto_approve(self):
        auto_payload = self.valid_review_payload()
        auto_payload["passage_names_a_different_subject"] = True
        auto = review.enforce(
            auto_payload,
            identity_resolved=True, integrity_ok=True)
        self.assertEqual(auto["effective_verdict"], "HUMAN_REVIEW")
        reject_payload = self.valid_review_payload()
        reject_payload["verdict"] = "REJECT"
        reject_payload["passage_names_a_different_subject"] = True
        rejected = review.enforce(
            reject_payload,
            identity_resolved=True, integrity_ok=True)
        self.assertEqual(rejected["effective_verdict"], "REJECT")

    def test_unresolved_identity_blocks_without_rejecting(self):
        result = review.enforce(self.valid_review_payload(),
                                identity_resolved=False, integrity_ok=True)
        self.assertEqual(result["effective_verdict"], "HUMAN_REVIEW")

    def test_extra_roster_field_is_refused_by_schema(self):
        payload = self.valid_review_payload()
        payload["identity_conflicts_with_supplied_registry"] = True
        result = review.enforce(payload, identity_resolved=True,
                                integrity_ok=True)
        self.assertEqual(result["effective_verdict"], "HUMAN_REVIEW")
        self.assertTrue(any("unexpected fields" in x
                            for x in result["enforcement_reasons"]))

    def test_watchlist_injury_alone_does_not_create_relevance(self):
        registry = {"players": {"p": {
            "relevance_tier": relevance.WATCHLIST,
            "relevance_reason": "outside normal redraft boundary"}}}
        result = relevance.assess(
            "p", "RB", "Dameon Pierce did not practice with a hamstring.",
            registry)
        self.assertFalse(result["eligible"])

    def test_package_rejects_stale_identity_reused_for_every_card(self):
        registry = players.load()
        candidates = [p for p in registry.players
                      if p.player_id and p.position in {"QB", "RB", "WR", "TE"}]
        self.assertGreaterEqual(len(candidates), 2)
        first, second = candidates[:2]
        sha = integ.sha256_text("Evidence.")

        def item(player, shown):
            return {
                "candidate": {"candidate_id": player.player_id,
                              "player_id": player.player_id,
                              "player_name": player.full_name,
                              "team": player.team,
                              "position": player.position,
                              "evidence_text": "Evidence."},
                "supplied_identity": {
                    "player_id": shown.player_id,
                    "player_name": shown.full_name,
                    "team": shown.team, "position": shown.position},
                "evidence_integrity": {
                    "hashes_match": True, "evidence_text": "Evidence.",
                    "evidence_sha256": sha},
            }

        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            package.validate_items(
                [item(first, first), item(second, first)], registry)

    def test_package_renders_each_items_own_identity(self):
        registry = players.load()
        selected = [p for p in registry.players if p.player_id and
                    p.position in {"QB", "RB", "WR", "TE"}][:2]
        self.assertEqual(len(selected), 2)
        items = []
        for p in selected:
            evidence = f"{p.full_name} worked with the first team."
            sha = integ.sha256_text(evidence)
            items.append({
                "candidate": {"candidate_id": "c-" + p.player_id,
                              "player_id": p.player_id,
                              "player_name": p.full_name, "team": p.team,
                              "position": p.position,
                              "evidence_text": evidence},
                "supplied_identity": {"player_id": p.player_id,
                    "player_name": p.full_name, "team": p.team,
                    "position": p.position},
                "evidence_integrity": integ.build_record(
                    evidence, generator_evidence_sha=sha,
                    reviewer_evidence_sha=sha, human_evidence_sha=sha,
                    generator_request_sha="g", reviewer_request_sha="r"),
                "assessment": {"decision": "INTERPRET",
                    "fantasy_mechanism": "FIRST_TEAM_REPS",
                    "direction": "POSITIVE", "impact_strength": "LOW",
                    "impact_horizon": "SHORT_TERM",
                    "projection_action": "NONE",
                    "fantasy_commentary": "First-team work was observed.",
                    "validation_failures": []},
                "independent_reviewer": review.enforce(
                    self.valid_review_payload(), identity_resolved=True,
                    integrity_ok=True),
                "source_url": "https://example.com/report",
                "source_name": "Example", "author": "Reporter",
                "ownership": "INDEPENDENT", "published_at": "2026-08-22",
            })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, out_html, out_json = (root / "input.json",
                                           root / "review.html",
                                           root / "review.json")
            source.write_text(json.dumps({"run_status": "VALID",
                                          "cost_usd": 0, "items": items}))
            with mock.patch.object(sys, "argv", ["wire_review_package.py",
                    "--source", str(source), "--html", str(out_html),
                    "--json", str(out_json)]):
                self.assertEqual(package.main(), 0)
            rendered = out_html.read_text()
            for p in selected:
                self.assertIn(p.player_id, rendered)
                self.assertIn(p.full_name, rendered)
            self.assertIn("Open source", rendered)

    @staticmethod
    def valid_review_payload():
        return {
            "verdict": "AUTO_APPROVE", "subject_is_correct": True,
            "mechanism_is_supported": True,
            "direction_is_supported": True,
            "commentary_overstates": False,
            "commentary_repeats_evidence": False,
            "inference_not_in_evidence": False,
            "performance_only_no_role_information": False,
            "passage_names_a_different_subject": False,
            "disagreement_summary": "supported",
        }


if __name__ == "__main__":
    unittest.main()
