#!/usr/bin/env python3
"""Offline regressions for the event-centric Wire V2 dark launch."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wire import v2
from wire.v2_draft import build_prompt, validate
import wire_v2_draft
import wire_v2_inbox


def candidate(candidate_id: str, evidence: str, minute: int = 0,
              player: str = "Alec Pierce", player_id: str = "00-pierce") -> dict:
    return {
        "candidate_id": candidate_id,
        "player": player, "player_id": player_id,
        "team": "IND", "position": "WR",
        "source_name": "Test source", "source_id": "test",
        "source_class": "X", "ownership": "INDEPENDENT",
        "author": "Test reporter", "source_url": f"https://example.com/{candidate_id}",
        "published_at": f"2026-08-27T14:{minute:02d}:00+00:00",
        "evidence": evidence, "origin": "X",
    }


def proposal(basis: str) -> dict:
    return {
        "decision": "PROPOSE", "event_type": "AVAILABILITY",
        "what_changed": "Alec Pierce was activated from the PUP list.",
        "lineupbeat_impact": (
            "Pierce can begin ramping up, but his Week 1 workload remains uncertain."),
        "direction": "POSITIVE", "evidence_basis": basis,
        "limitations": ["Practice participation has not been confirmed."],
        "confidence": 0.9, "reason": "A concrete availability change.",
    }


class FakeProvider:
    def __init__(self):
        self.calls = []

    def authenticate(self):
        return True

    def draft(self, event):
        self.calls.append(event["event_id"])
        basis = event["sources"][0]["evidence"]
        return proposal(basis), {
            "provider": "fake", "model": "fake", "cost_usd": 0.01,
        }


class WireV2Tests(unittest.TestCase):
    def test_seven_pup_reports_become_one_event(self):
        rows = [
            candidate("one", "Alec Pierce is back.", 0),
            candidate("two", "Alec Pierce is coming off PUP after ankle surgery.", 1),
            candidate("three", "The Colts removed Alec Pierce from the PUP list.", 2),
            candidate("four", "Alec Pierce was activated off PUP today.", 3),
            candidate("five", "Indy is taking WR Alec Pierce off PUP.", 4),
            candidate("six", "Pierce came off the PUP list following rehab.", 5),
            candidate("seven", "Alec Pierce is off PUP and can ramp up.", 6),
        ]
        events = v2.cluster(rows)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_count"], 7)
        self.assertEqual(len(events[0]["sources"]), 7)

    def test_new_injury_and_later_return_stay_separate(self):
        injured = candidate(
            "injury", "Alec Pierce left practice hurt with an ankle injury.", 0)
        returned = candidate(
            "return", "Alec Pierce returned after the ankle injury.", 30)
        self.assertFalse(v2.same_event(injured, returned)[0])
        self.assertEqual(len(v2.cluster([injured, returned])), 2)

    def test_ambiguous_bridge_cannot_merge_injury_and_return(self):
        injured = candidate(
            "injury", "Alec Pierce left practice hurt with an ankle injury.", 0)
        ambiguous = candidate(
            "bridge", "Alec Pierce continues his ankle rehabilitation.", 10)
        returned = candidate(
            "return", "Alec Pierce returned after the ankle injury.", 20)
        events = v2.cluster([injured, ambiguous, returned])
        self.assertEqual(len(events), 2)
        self.assertEqual(sorted(event["source_count"] for event in events), [1, 2])

    def test_prompt_keeps_sources_separate(self):
        event = v2.cluster([
            candidate("one", "Alec Pierce is coming off PUP."),
            candidate("two", "The Colts activated Alec Pierce off PUP.", 1),
        ])[0]
        prompt = build_prompt(event)
        self.assertIn("SOURCE 1", prompt)
        self.assertIn("SOURCE 2", prompt)
        self.assertIn("SOURCE COUNT: 2", prompt)

    def test_minimal_validator_requires_exact_grounding(self):
        event = v2.cluster([
            candidate("one", "The Colts activated Alec Pierce off PUP.")
        ])[0]
        good = proposal("The Colts activated Alec Pierce off PUP.")
        self.assertEqual(validate(good, event), [])
        bad = {**good, "evidence_basis": "Pierce returned to full practice."}
        self.assertIn("evidence_basis is not an exact supplied excerpt",
                      validate(bad, event))

    def test_one_provider_call_per_event_and_cache_state(self):
        reports = [
            candidate("one", "Alec Pierce is coming off PUP after ankle surgery."),
            candidate("two", "The Colts activated Alec Pierce off PUP.", 1),
        ]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = Namespace(
                hours=24, max_calls=10, cap=1.0, model="fake",
                state=root / "state.json", output=root / "batch.json",
                x_db=root / "x.db")
            provider = FakeProvider()
            with patch.object(wire_v2_draft.capture, "article_candidates",
                              return_value=reports), \
                    patch.object(wire_v2_draft.capture, "x_candidates",
                                 return_value=[]), \
                    patch.object(wire_v2_draft.players, "load", return_value=object()), \
                    patch.object(wire_v2_draft, "now_utc", return_value=datetime(
                        2026, 8, 27, 15, tzinfo=timezone.utc)):
                first = wire_v2_draft.run(args, provider=provider)
                second = wire_v2_draft.run(args, provider=provider)
                with patch.object(wire_v2_draft.capture, "article_candidates",
                                  return_value=[candidate(
                                      "three", "Alec Pierce is off PUP and can ramp up.", 2)]):
                    third = wire_v2_draft.run(args, provider=provider)
            self.assertEqual(first["raw_candidate_count"], 2)
            self.assertEqual(first["event_count"], 1)
            self.assertEqual(first["reports_merged"], 1)
            self.assertEqual(first["model_calls"], 1)
            self.assertEqual(first["proposal_count"], 1)
            self.assertEqual(second["fresh_candidate_count"], 0)
            self.assertEqual(third["cross_run_duplicate_count"], 1)
            self.assertEqual(third["model_calls"], 0)
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(wire_v2_draft.capture.BEAT_DB, args.x_db)
            state = json.loads(args.state.read_text())
            self.assertEqual(set(state["candidate_ids"]), {"one", "two", "three"})
            self.assertEqual(len(state["events"]), 1)

    def test_dark_inbox_has_no_publication_command(self):
        payload = {
            "reviewed_event_count": 1, "reports_merged": 1,
            "cost_usd": 0.01, "outcomes": [],
            "proposals": [{
                "player": "Alec Pierce", "team": "IND", "position": "WR",
                "reader_label": "Trending up", "event_type": "AVAILABILITY",
                "what_changed": "Alec Pierce was activated from PUP.",
                "lineupbeat_impact": "Monitor his practice workload.",
                "evidence_basis": "Alec Pierce was activated from PUP.",
                "limitations": [],
                "primary_source": {"author": "Reporter", "source_name": "Outlet",
                                   "url": "https://example.com/one"},
                "sources": [{"author": "Reporter", "source_name": "Outlet",
                             "url": "https://example.com/one"}],
            }],
        }
        body = wire_v2_inbox.render(payload)
        self.assertIn("Nothing here can publish", body)
        self.assertNotIn("approve all", body.lower())
        self.assertNotIn("LINEUP_BEAT_WIRE_MANIFEST", body)

    def test_v2_scripts_never_name_the_publication_file(self):
        for path in (ROOT / "scripts" / "wire_v2_draft.py",
                     ROOT / "scripts" / "wire_v2_inbox.py"):
            source = path.read_text()
            self.assertNotIn("wire_publications.json", source)
            self.assertNotIn("wire_mobile_approve", source)

    def test_dark_workflow_is_manual_read_only_and_capped(self):
        source = (ROOT / ".github" / "workflows" /
                  "wire-v2-dark-launch.yml").read_text()
        self.assertIn("workflow_dispatch:", source)
        self.assertNotIn("schedule:", source)
        self.assertIn("contents: read", source)
        self.assertNotIn("contents: write", source)
        self.assertIn("V2_MAX_CALLS must be 1-40", source)
        self.assertIn("V2_CAP_USD must be >0 and <=1", source)
        self.assertNotIn("wire_mobile_approve", source)
        self.assertNotIn("git push", source)
        self.assertNotIn("wire_publications.json", source)


if __name__ == "__main__":
    unittest.main()
