#!/usr/bin/env python3
"""Safety regressions for hash-bound digest publication."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wire import digest_approval
import wire_digest_inbox
import wire_digest_approve


def update():
    return {"player_id": "ta", "player": "Tutu Atwell", "team": "MIA",
            "position": "WR", "event_type": "TRANSACTION",
            "bullet": "Tutu Atwell was traded to the Rams.",
            "evidence_quote": "The Dolphins traded Tutu Atwell to the Rams.",
            "source_url": "https://x.com/test/status/1", "author": "Reporter",
            "source_name": "Test", "published_at": "2026-08-28T12:00:00Z",
            "report_id": "report:one"}


class DigestApprovalTests(unittest.TestCase):
    def manifest(self):
        return digest_approval.make_manifest(
            [update()], "2026-08-28T12:00:00Z", "a" * 64, "b" * 64, 0, 1, .01)

    def test_manifest_round_trip_binds_visible_bullet(self):
        manifest = self.manifest()
        issue = wire_digest_inbox.render(manifest)
        self.assertEqual(digest_approval.decode(issue), manifest)
        self.assertIn(manifest["batch_id"][:12], issue)

    def test_tampered_manifest_fails(self):
        manifest = self.manifest()
        manifest["updates"][0]["bullet"] = "Changed after hashing."
        self.assertIn("digest manifest hash mismatch",
                      digest_approval.validate_manifest(manifest))

    def test_commands_are_exact_and_numbered(self):
        self.assertEqual(digest_approval.parse_commands(
            "approve 1\n", 1)["approved"], [1])
        edited = digest_approval.parse_commands(
            "edit 1 | Tutu Atwell returned to the Rams.", 1)
        self.assertEqual(edited["edits"][1], "Tutu Atwell returned to the Rams.")
        with self.assertRaises(ValueError):
            digest_approval.parse_commands("looks good", 1)

    def test_only_allowlisted_actor_and_label_can_approve(self):
        event = {"action": "created", "sender": {"login": "rdamato720"},
                 "issue": {"state": "open", "labels": [{"name": digest_approval.LABEL}]},
                 "comment": {"body": "approve all"}}
        self.assertEqual(digest_approval.validate_event(event)[1], "approve all")
        event["sender"]["login"] = "someone-else"
        with self.assertRaises(ValueError):
            digest_approval.validate_event(event)

    def test_publication_store_starts_empty_and_separate(self):
        payload = json.loads((ROOT / "data/wire_digest_publications.json").read_text())
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["publications"], [])

    def test_homepage_has_simple_digest_renderer(self):
        source = (ROOT / "scripts/wire_homepage_replacement.py").read_text()
        self.assertIn("def render_digest", source)
        self.assertIn("Fantasy football news updates you need to know", source)
        self.assertIn("wdigest", source)

    def test_workflow_is_comment_gated_and_rebuilds_after_publication(self):
        workflow = (ROOT / ".github/workflows/wire-digest-approve.yml").read_text()
        self.assertIn("issue_comment", workflow)
        self.assertIn("github.actor == 'rdamato720'", workflow)
        self.assertIn("wire-digest-inbox", workflow)
        self.assertIn("wire_digest_approve.py", workflow)
        self.assertIn("refresh.yml", workflow)

    def test_authorized_comment_appends_only_selected_bullet(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pubs = root / "publications.json"
            ledger = root / "ledger.json"
            result = root / "result.json"
            event_path = root / "event.json"
            pubs.write_text(json.dumps({"schema_version": "wire-digest-publications-v1",
                                        "count": 0, "publications": []}) + "\n")
            ledger.write_text(json.dumps({"schema_version": digest_approval.LEDGER_SCHEMA,
                                          "count": 0, "receipts": []}) + "\n")
            import hashlib
            manifest = digest_approval.make_manifest(
                [update()], "2026-08-28T12:00:00Z", "a" * 64,
                hashlib.sha256(pubs.read_bytes()).hexdigest(), 0, 1, .01)
            event_path.write_text(json.dumps({
                "action": "created", "sender": {"login": "rdamato720"},
                "issue": {"number": 9, "state": "open",
                          "labels": [{"name": digest_approval.LABEL}],
                          "body": wire_digest_inbox.render(manifest)},
                "comment": {"id": 10, "body": "approve 1"}}))
            player = SimpleNamespace(full_name="Tutu Atwell", team="MIA", position="WR")
            registry = SimpleNamespace(by_id={"ta": player})
            with patch.object(wire_digest_approve, "PUBLICATIONS", pubs), \
                    patch.object(wire_digest_approve, "LEDGER", ledger), \
                    patch.object(wire_digest_approve, "RESULT", result), \
                    patch.object(wire_digest_approve.players, "load", return_value=registry), \
                    patch.object(sys, "argv", ["wire_digest_approve.py", "--event", str(event_path)]):
                self.assertEqual(wire_digest_approve.main(), 0)
            published = json.loads(pubs.read_text())
            self.assertEqual(published["count"], 1)
            self.assertEqual(published["publications"][0]["bullet"],
                             "Tutu Atwell was traded to the Rams.")
            self.assertEqual(json.loads(result.read_text())["published"], 1)


if __name__ == "__main__":
    unittest.main()
