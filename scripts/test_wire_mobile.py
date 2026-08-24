#!/usr/bin/env python3
"""Offline regressions for the scheduled monitor and phone approval gate."""

from __future__ import annotations

import copy
import json
import unittest
import types
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# The mobile boundary tests are offline and never invoke source capture. Keep
# them runnable in a minimal checkout where optional crawler dependencies have
# not yet been installed; CI installs the real packages before this suite.
sys.modules.setdefault("feedparser", types.SimpleNamespace())
sys.modules.setdefault("trafilatura", types.SimpleNamespace())

from wire import mobile_approval as mobile
from wire.mobile_draft import OpenAIMobileDraftProvider
import wire_mobile_inbox as inbox


def card() -> dict:
    return {
        "player": "Bijan Robinson", "player_id": "00-test",
        "team": "ATL", "position": "RB", "content_type": "REPORTING",
        "direction": "NEUTRAL", "mechanism": "OTHER", "strength": "LOW",
        "horizon": "SHORT_TERM", "projection_action": "NONE",
        "reader_label": "Worth noting",
        "public_summary": (
            "Jane Doe reported Bijan Robinson worked in a new package Monday."),
        "evidence": (
            "At Monday practice, Bijan Robinson took several snaps in a package "
            "the offense had not shown earlier in camp."),
        "commentary": (
            "The report adds a watch-list data point; role and workload remain "
            "unconfirmed."),
        "source": "Example News", "author": "Jane Doe",
        "date": "2026-08-24T12:00:00+00:00",
        "url": "https://example.com/report", "ownership": "INDEPENDENT",
        "evidence_candidate_id": "mobile:test:1",
        "reviewer_action": "PENDING", "public_summary_approved_by": "",
        "commentary_approved_by": "", "approved_at": "",
        "commentary_origin": "MODEL_DRAFT", "model_original_commentary": "",
        "readiness_failures": [],
    }


class MobileWireTests(unittest.TestCase):
    def manifest(self):
        return mobile.make_manifest(
            [card()], generated_at="2026-08-24T12:00:00+00:00",
            publication_sha256="a" * 64, publication_count=72,
            source_batch_sha256="b" * 64,
            model_calls=1, cost_usd=0.001)

    def test_manifest_round_trip_binds_exact_wording(self):
        manifest = self.manifest()
        encoded = mobile.encode_manifest(manifest)
        self.assertEqual(mobile.decode_manifest(encoded), manifest)
        changed = copy.deepcopy(manifest)
        changed["cards"][0]["commentary"] = "Different text."
        with self.assertRaisesRegex(ValueError, "hash"):
            mobile.encode_manifest(changed)

    def test_phone_commands_are_closed_and_support_edits(self):
        parsed = mobile.parse_commands(
            "approve 1,3\nreject 2\nedit 3 | New summary. | New impact.", 3)
        self.assertEqual(parsed["approved"], [1, 3])
        self.assertEqual(parsed["rejected"], [2])
        self.assertEqual(parsed["edits"][3]["commentary"], "New impact.")
        with self.assertRaises(ValueError):
            mobile.parse_commands("publish everything", 3)
        with self.assertRaises(ValueError):
            mobile.parse_commands("approve 4", 3)

    def test_only_ralph_on_a_trusted_labeled_issue_can_approve(self):
        event = {
            "action": "created", "sender": {"login": "rdamato720"},
            "issue": {"number": 12, "user": {"login": "github-actions[bot]"},
                      "labels": [{"name": "wire-inbox"}],
                      "body": mobile.encode_manifest(self.manifest())},
            "comment": {"id": 99, "body": "approve all"},
        }
        issue, comment = mobile.validate_event(event)
        self.assertEqual(issue["number"], 12)
        self.assertEqual(comment, "approve all")
        unauthorized = copy.deepcopy(event)
        unauthorized["sender"]["login"] = "someone-else"
        with self.assertRaisesRegex(ValueError, "not authorized"):
            mobile.validate_event(unauthorized)
        pull_request = copy.deepcopy(event)
        pull_request["issue"]["pull_request"] = {"url": "https://example.com"}
        with self.assertRaisesRegex(ValueError, "issue"):
            mobile.validate_event(pull_request)

    def test_issue_body_contains_exact_copy_evidence_and_commands(self):
        manifest = self.manifest()
        body = inbox.render(manifest)
        self.assertIn(card()["public_summary"], body)
        self.assertIn(card()["commentary"], body)
        self.assertIn(card()["evidence"], body)
        self.assertIn("approve all", body)
        self.assertEqual(mobile.decode_manifest(body), manifest)

    def test_draft_provider_is_structured_non_publishing_copy(self):
        response = {
            "decision": "CARD", "content_type": "REPORTING",
            "public_summary": card()["public_summary"],
            "lineupbeat_impact": card()["commentary"],
            "direction": "NEUTRAL", "mechanism": "OTHER",
            "strength": "LOW", "horizon": "SHORT_TERM",
            "limitations": ["workload unknown"], "confidence": 0.7,
            "reason": "useful context",
        }
        prompts = []

        def transport(prompt):
            prompts.append(prompt)
            return response, {"input_tokens": 100, "output_tokens": 50}

        provider = OpenAIMobileDraftProvider(transport=transport)
        result, meta = provider.draft(
            card()["evidence"],
            {"author": "Jane Doe", "source_name": "Example News",
             "ownership": "INDEPENDENT", "published_at": card()["date"],
             "source_url": card()["url"]},
            {"player": "Bijan Robinson", "player_id": "00-test",
             "team": "ATL", "position": "RB"})
        self.assertEqual(result["decision"], "CARD")
        self.assertEqual(meta["provider"], "openai")
        self.assertGreater(meta["cost_usd"], 0)
        self.assertIn("COMPLETE EVIDENCE", prompts[0])

    def test_capture_only_is_source_scoped_and_skips_extraction(self):
        cli = (ROOT / "beatwire" / "cli.py").read_text()
        pipeline = (ROOT / "beatwire" / "pipeline.py").read_text()
        self.assertIn('r.add_argument("--capture-only"', cli)
        self.assertIn('r.add_argument("--kind"', cli)
        self.assertIn("sources = [s for s in sources if s.kind in kinds]",
                      pipeline)
        self.assertIn("if fresh and not capture_only:", pipeline)
        self.assertIn("Registry(sport, load_players=not capture_only)", pipeline)
        registry = (ROOT / "beatwire" / "registry.py").read_text()
        self.assertIn("if load_players else []", registry)

    def test_workflows_are_capped_and_human_gated(self):
        monitor = (ROOT / ".github/workflows/wire-monitor.yml").read_text()
        approval = (ROOT / ".github/workflows/wire-mobile-approve.yml").read_text()
        self.assertIn("WIRE_MOBILE_AUTODRAFT == 'true'", monitor)
        self.assertIn("--max-calls \"$MOBILE_MAX_CALLS\"", monitor)
        self.assertIn("--cap \"$MOBILE_RUN_CAP\"", monitor)
        self.assertIn("--capture-only", monitor)
        self.assertIn("wire-mobile-x.db", monitor)
        self.assertNotIn("wire_publish.py", monitor)
        draft = (ROOT / "scripts" / "wire_mobile_draft.py").read_text()
        self.assertIn('"nfl", load_players=False', draft)
        self.assertIn("github.actor == 'rdamato720'", approval)
        self.assertIn("wire_mobile_approve.py", approval)
        self.assertIn("--publish", approval)
        self.assertIn("skip_fetch=true", approval)
        apply_script = (ROOT / "scripts" / "wire_mobile_approve.py").read_text()
        self.assertIn("visible issue wording does not match", apply_script)


if __name__ == "__main__":
    unittest.main()
