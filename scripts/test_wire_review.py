#!/usr/bin/env python3
"""Focused regression tests for the repaired review path."""

from __future__ import annotations

import sys
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wire import currentness
from wire import evidence_integrity as integ
from wire import human_review
from wire import independent_review as review
from wire import openai_promotion
from wire import players
from wire import relevance
from wire import semantic
import wire_review_package as package
import wire_backfill as backfill
import wire_semantic_eval as semantic_eval
from wire.providers.openai import OpenAIProviderError, OpenAISemanticProvider


class ReviewRepairTests(unittest.TestCase):
    def test_expanded_batch_workflow_is_capped_and_non_publishing(self):
        workflow = (ROOT / ".github" / "workflows" / "refresh.yml").read_text()
        batch = workflow.split("  wire-review-batch:", 1)[1].split(
            "\n  refresh:", 1)[0]
        self.assertIn('--cap", "0.40", "--max-calls", "20"', batch)
        self.assertIn("wire_independent_review.py --cap 0.40 --max-calls 20",
                      batch)
        self.assertIn("calls > 40", batch)
        self.assertIn("cost > 1.00", batch)
        self.assertIn("publication file changed during review batch", batch)
        self.assertIn("Clear stale review outputs from the runner", batch)
        self.assertIn("wire_extract.py --limit 1000", batch)
        self.assertIn("github.event_name == 'pull_request'", batch)
        self.assertIn("github.event_name == 'workflow_dispatch'", batch)
        self.assertIn("wire-review-selection-v1", batch)
        self.assertIn('"approval_statement": "approved"', batch)
        self.assertIn('"approved_cost_usd": 1.0', batch)
        self.assertIn('"approved_max_calls": 40', batch)
        self.assertIn("selection has no valid plan digest; 0 API calls", batch)
        self.assertIn("Verify review provider before discovery", batch)
        self.assertIn("0 Responses API calls", batch)
        self.assertIn("generator did not complete the exact approved cohort", batch)
        self.assertIn("exact approved cohort is incomplete", batch)
        self.assertIn('mode = "banked" if banked else "review"', batch)
        self.assertIn("Confirm banked review receipt", batch)
        self.assertIn("steps.review_request.outputs.mode != 'banked'", batch)
        self.assertNotIn("wire_publish.py", batch)
        self.assertNotIn("wrangler", batch)

    def test_local_approved_runner_is_capped_and_non_publishing(self):
        runner = (ROOT / "scripts" / "wire_review_approved.py").read_text()
        self.assertIn('"approved_cost_usd": 1.0', runner)
        self.assertIn('"approved_max_calls": 40', runner)
        self.assertIn('"generator_cap_usd": 0.40', runner)
        self.assertIn('"reviewer_cap_usd": 0.40', runner)
        self.assertIn('"publications_authorized": 0', runner)
        self.assertIn('"deployment_authorized": False', runner)
        self.assertIn("generator.authenticate()", runner)
        self.assertIn("publication file changed during review", runner)
        self.assertNotIn("wire_publish.py", runner)
        self.assertNotIn("wrangler", runner)

    def test_named_human_suppression_receipt_is_valid_and_banked(self):
        receipt, errors = human_review.validate_ledger()
        self.assertEqual(errors, [])
        self.assertEqual(receipt["approved_by"], "Ralph Damato")
        self.assertEqual(receipt["action"], "APPROVE_SUPPRESSIONS")
        self.assertEqual(receipt["candidate_count"], 5)
        self.assertEqual(receipt["publication_count_before"],
                         receipt["publication_count_after"])

    def test_model_name_cannot_satisfy_human_review_gate(self):
        payload = json.loads(human_review.LEDGER.read_text())
        payload["receipts"][0]["approved_by"] = "OpenAI"
        payload["receipts"][0]["approved_by_handle"] = "model"
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "human.json"
            ledger.write_text(json.dumps(payload))
            _, errors = human_review.validate_ledger(ledger)
        self.assertTrue(any("human approver" in x for x in errors), errors)

    def test_openai_eval_requires_explicit_limits_before_calls(self):
        errors = semantic_eval.live_limit_errors(["rules", "openai"], 23,
                                                 None, None)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("--cap" in x for x in errors))
        self.assertTrue(any("--max-calls" in x for x in errors))
        self.assertTrue(semantic_eval.live_limit_errors(
            ["openai"], 1, float("nan"), 1))

    def test_openai_eval_refuses_partial_call_limit_before_calls(self):
        errors = semantic_eval.live_limit_errors(["openai"], 23, 0.50, 22)
        self.assertEqual(len(errors), 1)
        self.assertIn("0 API calls", errors[0])
        self.assertEqual(
            semantic_eval.live_limit_errors(["openai"], 23, 0.50, 23), [])
        self.assertEqual(
            semantic_eval.live_limit_errors(["rules"], 23, None, None), [])

    def test_openai_promotion_fails_if_observed_spend_crosses_cap(self):
        summary = {
            "available": True, "locked_gold_items": 1,
            "correct_num": 1, "correct_den": 1,
            "precision_num": 1, "precision_den": 1,
            "recall_num": 1, "recall_den": 1,
            "abstain_num": 0, "abstain_den": 1,
            "cost_usd_total": 0.11, "cap_usd": 0.10,
            "calls": 1, "max_calls": 1,
        }
        gate = semantic_eval.promotion_gate(
            summary, [{"id": "clean", "errors": [],
                       "validation_failures": []}])
        self.assertFalse(gate["passed"])
        self.assertFalse(gate["checks"]["observed_spend_within_cap"])

    def test_openai_auth_probe_rejects_a_well_shaped_invalid_key(self):
        key = "sk-proj-" + "A" * 40

        class BadModels:
            @staticmethod
            def retrieve(_model):
                raise RuntimeError("401 for " + key)

        class BadClient:
            def __init__(self, api_key):
                self.models = BadModels()

        fake_openai = types.SimpleNamespace(OpenAI=BadClient)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": key}), \
                mock.patch.dict(sys.modules, {"openai": fake_openai}):
            provider = OpenAISemanticProvider()
            self.assertTrue(provider.available())
            with self.assertRaises(OpenAIProviderError) as caught:
                provider.authenticate()
        self.assertNotIn(key, str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    def test_transport_auth_probe_makes_no_extra_request(self):
        calls = []
        provider = OpenAISemanticProvider(
            transport=lambda prompt: calls.append(prompt))
        self.assertTrue(provider.authenticate())
        self.assertEqual(calls, [])

    def test_openai_promotion_receipt_is_valid_and_non_publishing(self):
        receipt, errors = openai_promotion.validate()
        self.assertEqual(errors, [])
        self.assertEqual(receipt["status"], "QUALIFIED")
        self.assertEqual(receipt["correct"], receipt["graded"])
        self.assertFalse(receipt["publishing_authorized"])
        self.assertFalse(receipt["deployment_triggered"])

    def test_openai_promotion_receipt_detects_report_and_publication_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "eval.json"
            report.write_bytes(openai_promotion.EVAL.read_bytes())
            payload = json.loads(report.read_text())
            payload["providers"]["openai"]["summary"]["correct_num"] = 22
            report.write_text(json.dumps(payload))
            _, errors = openai_promotion.validate(eval_path=report)
            self.assertTrue(any("eval_report_sha256" in x for x in errors),
                            errors)

            publications = root / "publications.json"
            publications.write_bytes(
                openai_promotion.QUALIFIED_PUBLICATIONS.read_bytes())
            payload = json.loads(publications.read_text())
            payload["count"] += 1
            publications.write_text(json.dumps(payload))
            _, errors = openai_promotion.validate(
                publications_path=publications)
            self.assertTrue(any("publications_sha256" in x for x in errors),
                            errors)

    def test_generator_prompt_withholds_article_title(self):
        evidence = ("Without two of the top receivers, Christian Watson and "
                    "Matthew Golden played 11 snaps apiece.")
        prompt = semantic.build_prompt(
            evidence,
            {"article_title": "Packers Preseason Win at Denver",
             "source_name": "Packers On SI"},
            [{"player_id": "watson", "player_name": "Christian Watson",
              "team": "GB", "position": "WR"}])
        self.assertIn(evidence, prompt)
        self.assertNotIn("Packers Preseason Win at Denver", prompt)
        self.assertNotIn("Preseason", prompt)

    def test_suppression_approval_is_not_an_action_disagreement(self):
        item = {
            "assessment": {"decision": "NO_FANTASY_IMPACT",
                           "fantasy_mechanism": "NO_FANTASY_IMPACT",
                           "validation_failures": []},
            "evidence_integrity": {"blocks_automatic_approval": False},
            "independent_reviewer": {"effective_verdict": "AUTO_APPROVE"},
        }
        self.assertFalse(package.publishable(item))
        self.assertFalse(package.action_disagreement(item))
        self.assertFalse(package.assessment_disagreement(item))

    def test_rejected_suppression_is_an_assessment_not_action_disagreement(self):
        item = {
            "assessment": {"decision": "ABSTAIN",
                           "fantasy_mechanism": "NO_FANTASY_IMPACT",
                           "validation_failures": ["unsupported context"]},
            "evidence_integrity": {"blocks_automatic_approval": False},
            "independent_reviewer": {"effective_verdict": "REJECT"},
        }
        self.assertFalse(package.action_disagreement(item))
        self.assertTrue(package.assessment_disagreement(item))

    def test_paid_ledger_merges_legacy_results_and_never_drops_ids(self):
        state = {"results": [
            {"candidate": {"candidate_id": "legacy-paid"}}]}
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "paid.json"
            ledger.write_text(json.dumps({
                "candidate_ids": ["banked-paid"]}))
            with mock.patch.object(backfill, "PAID", ledger):
                known = backfill.paid_candidate_ids(state)
                self.assertEqual(known, {"legacy-paid", "banked-paid"})
                backfill.record_paid_candidate("new-paid", known)
                saved = json.loads(ledger.read_text())
                self.assertEqual(saved["count"], 3)
                self.assertEqual(set(saved["candidate_ids"]),
                                 {"legacy-paid", "banked-paid", "new-paid"})

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

    def test_observed_dark_launch_cohort_is_filtered_before_model_spend(self):
        def row(name, text, klass="FIRSTHAND_OBSERVATION"):
            return {"player_name": name, "evidence_text": text,
                    "evidence_class": klass}

        blocked = [
            row("Ty Chandler",
                "RB Ty Chandler also added 21 rushing yards on six carries.",
                "FIRSTHAND_OBSERVATION"),
            row("Ameer Abdullah",
                "Mullens led a drive that would have ended in a touchdown "
                "if not for an Ameer Abdullah drop."),
            row("Fernando Mendoza",
                "Fernando Mendoza is coming from a different scheme, and "
                "people wondered about the rookie before Kubiak evaluated him."),
            row("Sam Darnold",
                "I’m not sure how Sam Darnold can be considered a bottom half "
                "of the league starter; the list becomes even more dubious."),
            row("Kyler Murray",
                "Saturday was a chance for McCarthy to reclaim momentum after "
                "losing the starting job to Kyler Murray."),
            row("Jared Goff",
                "Veteran quarterback Jared Goff caught up with a former "
                "teammate during pre-game warmups."),
            row("Mack Hollins",
                "We got a few snaps out of Mack Hollins, but it was primarily "
                "backups."),
            row("RJ Harvey",
                'Payton said Nix "was solid" in an efficient opening drive '
                "capped by a touchdown pass to running back RJ Harvey.",
                "DIRECT_QUOTATION"),
        ]
        for candidate in blocked:
            with self.subTest(player=candidate["player_name"]):
                self.assertFalse(backfill.pre_model_claim_gate(candidate)[0])

    def test_ambiguous_usage_and_explicit_absence_still_reach_review(self):
        cases = [
            {"player_name": "Jacoby Brissett",
             "evidence_text": "Jacoby Brissett and other starting players "
                              "won't be suiting up in Arizona.",
             "evidence_class": "FIRSTHAND_OBSERVATION"},
            {"player_name": "Christian Watson",
             "evidence_text": "Christian Watson and Matthew Golden played "
                              "11 snaps apiece.",
             "evidence_class": "FIRSTHAND_OBSERVATION"},
            {"player_name": "Gardner Minshew",
             "evidence_text": "LaFleur said Gardner Minshew will not play.",
             "evidence_class": "DIRECT_QUOTATION"},
            {"player_name": "Kyler Murray",
             "evidence_text": "Kyler Murray will not be seeing the field "
                              "this weekend.",
             "evidence_class": "FIRSTHAND_OBSERVATION"},
        ]
        for candidate in cases:
            with self.subTest(player=candidate["player_name"]):
                self.assertTrue(backfill.pre_model_claim_gate(candidate)[0])

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
            "evidence_classification_is_supported": True,
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
