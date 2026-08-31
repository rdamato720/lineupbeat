#!/usr/bin/env python3
"""Offline Tank01 dark-launch transport and audit regressions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wire import tank01  # noqa: E402
import wire_tank01_dark as dark  # noqa: E402


class Tank01Tests(unittest.TestCase):
    def test_news_request_asks_for_fantasy_news_and_top_headlines(self):
        calls = []
        tank01.fetch_news(
            key="test-rapidapi-secret-12345",
            transport=lambda url, headers, timeout: calls.append((url, headers, timeout)) or {},
        )
        self.assertEqual(len(calls), 1)
        url, _, _ = calls[0]
        self.assertEqual(
            url,
            f"https://{tank01.HOST}/getNFLNews?fantasyNews=true&topNews=true",
        )

    def test_documented_body_list_normalizes(self):
        payload = {"statusCode": 200, "body": [{
            "newsID": "n-1", "title": "Alec Pierce returned to practice",
            "description": "Alec Pierce was activated from PUP.",
            "link": "https://example.com/pierce", "source": "Example",
            "publishedAt": "2026-08-31T12:00:00Z", "playerID": "123",
            "teamAbv": "IND",
        }]}
        items = tank01.extract_items(payload)
        row = tank01.normalize(items[0])
        self.assertEqual(row["story_id"], "n-1")
        self.assertEqual(row["provider_player_ids"], ["123"])
        self.assertEqual(row["teams"], ["IND"])
        self.assertEqual(row["published_at"], "2026-08-31T12:00:00+00:00")

    def test_nested_map_and_json_body_are_supported(self):
        payload = {"statusCode": "200", "body": json.dumps({"news": {
            "a": {"headline": "Player update", "url": "https://example.com/a"}
        }})}
        self.assertEqual(len(tank01.extract_items(payload)), 1)

    def test_unknown_schema_fails_instead_of_looking_quiet(self):
        with self.assertRaisesRegex(tank01.Tank01Error, "no recognized"):
            tank01.extract_items({"statusCode": 200, "body": {"unexpected": []}})

    def test_provider_error_inside_http_200_fails(self):
        with self.assertRaisesRegex(tank01.Tank01Error, "statusCode 429"):
            tank01.extract_items({"statusCode": 429, "error": "quota"})

    def test_http_200_empty_news_result_is_valid(self):
        payload = {
            "statusCode": 200,
            "body": [],
            "error": "Your query returned no results.",
        }
        self.assertEqual(tank01.extract_items(payload), [])

    def test_http_200_error_without_body_still_fails(self):
        with self.assertRaisesRegex(tank01.Tank01Error, "Tank01 error: quota"):
            tank01.extract_items({"statusCode": 200, "error": "quota"})

    def test_key_is_scrubbed_from_transport_failure(self):
        key = "test-rapidapi-secret-12345"
        def broken(_url, _headers, _timeout):
            raise RuntimeError(f"authorization failed for {key}")
        with self.assertRaises(tank01.Tank01Error) as caught:
            tank01.fetch_news(key=key, transport=broken)
        self.assertNotIn(key, str(caught.exception))

    def test_key_is_scrubbed_from_nested_response(self):
        key = "test-rapidapi-secret-12345"
        payload = {"body": [{"debug": f"key={key}"}], "held": key}
        scrubbed = tank01.scrub_payload(payload, key)
        self.assertNotIn(key, json.dumps(scrubbed))

    def test_exact_full_name_match_only(self):
        aliases = {
            "alec pierce": [{"player_id": "ap", "full_name": "Alec Pierce",
                             "team": "IND", "position": "WR"}],
            "pierce": [{"player_id": "ap", "full_name": "Alec Pierce",
                        "team": "IND", "position": "WR"}],
        }
        full = dark.match_players({"headline": "Alec Pierce returned", "summary": ""}, aliases)
        bare = dark.match_players({"headline": "Pierce returned", "summary": ""}, aliases)
        self.assertEqual([row["player_id"] for row in full], ["ap"])
        self.assertEqual(bare, [])

    def test_attempt_is_banked_before_failed_transport(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = argparse.Namespace(
                state=root / "state.json", raw=root / "raw.json",
                report_json=root / "report.json", report_md=root / "report.md",
                days=7, max_requests=3,
            )
            original = os.environ.get(tank01.KEY_ENV)
            os.environ[tank01.KEY_ENV] = "test-rapidapi-secret-12345"
            try:
                with self.assertRaises(tank01.Tank01Error):
                    dark.run(args, transport=lambda *_: (_ for _ in ()).throw(RuntimeError("down")),
                             at=datetime(2026, 8, 31, tzinfo=timezone.utc))
            finally:
                if original is None:
                    os.environ.pop(tank01.KEY_ENV, None)
                else:
                    os.environ[tank01.KEY_ENV] = original
            state = json.loads(args.state.read_text())
            self.assertEqual(state["requests_attempted"], 1)
            self.assertEqual(state["attempts"][0]["outcome"], "FAILED")
            self.assertTrue(args.report_json.exists())
            self.assertTrue(args.report_md.exists())

    def test_successful_capture_stays_in_isolated_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = argparse.Namespace(
                state=root / "state.json", raw=root / "raw.json",
                report_json=root / "report.json", report_md=root / "report.md",
                days=7, max_requests=3,
            )
            payload = {"statusCode": 200, "body": [{
                "newsID": "n-1", "title": "Alec Pierce returned to practice",
                "description": "Alec Pierce was activated from PUP.",
                "link": "https://example.com/pierce", "source": "Example",
                "publishedAt": "2026-08-31T12:00:00Z",
            }]}
            original = os.environ.get(tank01.KEY_ENV)
            os.environ[tank01.KEY_ENV] = "test-rapidapi-secret-12345"
            try:
                report = dark.run(args, transport=lambda *_: payload,
                                  at=datetime(2026, 8, 31, 13, tzinfo=timezone.utc))
            finally:
                if original is None:
                    os.environ.pop(tank01.KEY_ENV, None)
                else:
                    os.environ[tank01.KEY_ENV] = original
            state = json.loads(args.state.read_text())
            self.assertEqual(state["attempts"][0]["outcome"], "SUCCESS")
            self.assertEqual(state["observations"][0]["item_count"], 1)
            self.assertEqual(report["stories_observed"], 1)
            self.assertTrue(args.raw.exists())
            self.assertFalse(report["published"])
            self.assertEqual(report["model_calls"], 0)

    def test_empty_news_capture_counts_as_successful_observation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = argparse.Namespace(
                state=root / "state.json", raw=root / "raw.json",
                report_json=root / "report.json", report_md=root / "report.md",
                days=7, max_requests=3,
            )
            payload = {
                "statusCode": 200,
                "body": [],
                "error": "Your query returned no results.",
            }
            original = os.environ.get(tank01.KEY_ENV)
            os.environ[tank01.KEY_ENV] = "test-rapidapi-secret-12345"
            try:
                report = dark.run(args, transport=lambda *_: payload,
                                  at=datetime(2026, 8, 31, 13, tzinfo=timezone.utc))
            finally:
                if original is None:
                    os.environ.pop(tank01.KEY_ENV, None)
                else:
                    os.environ[tank01.KEY_ENV] = original
            state = json.loads(args.state.read_text())
            self.assertEqual(state["attempts"][0]["outcome"], "SUCCESS")
            self.assertEqual(state["observations"][0]["item_count"], 0)
            self.assertEqual(report["observations"], 1)
            self.assertEqual(report["stories_observed"], 0)
            self.assertFalse(report["published"])
            self.assertEqual(report["model_calls"], 0)

    def test_request_ceiling_prevents_transport(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = argparse.Namespace(
                state=root / "state.json", raw=root / "raw.json",
                report_json=root / "report.json", report_md=root / "report.md",
                days=7, max_requests=1,
            )
            state = dark.empty_state("2026-08-31T00:00:00+00:00")
            state["requests_attempted"] = 1
            state["attempts"] = [{"attempted_at": state["started_at"], "outcome": "SUCCESS"}]
            dark.save_json(args.state, state)
            calls = []
            report = dark.run(args, transport=lambda *_: calls.append(1),
                              at=datetime(2026, 8, 31, 1, tzinfo=timezone.utc))
            self.assertEqual(calls, [])
            self.assertTrue(report["window"]["complete"])

    def test_old_provider_item_is_not_called_a_current_miss(self):
        story = {
            "published_at": "2026-08-20T00:00:00+00:00",
            "first_seen_at": "2026-08-31T00:00:00+00:00",
            "fantasy_players": [{"full_name": "Alec Pierce"}],
        }
        self.assertEqual(dark.coverage(story, [], [])["status"], "STALE_AT_FIRST_SEEN")

    def test_report_is_explicitly_private_and_zero_publication(self):
        state = dark.empty_state("2026-08-31T00:00:00+00:00")
        report = dark.build_report(state, datetime(2026, 8, 31, tzinfo=timezone.utc), 7, 180)
        text = dark.markdown(report)
        self.assertTrue(report["dark_launch"])
        self.assertFalse(report["published"])
        self.assertEqual(report["model_calls"], 0)
        self.assertIn("Nothing in this report can publish", text)
        self.assertIn("Publications: 0", text)


if __name__ == "__main__":
    unittest.main()
