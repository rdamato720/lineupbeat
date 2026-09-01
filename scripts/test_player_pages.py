#!/usr/bin/env python3
"""Regressions for approved Wire enrichment on canonical player pages."""

from __future__ import annotations

import copy
import html
import json
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_pages as pages


class PlayerPageTests(unittest.TestCase):
    def setUp(self):
        self.old_projections = pages.PROJECTIONS
        self.old_projection_updated = pages.PROJECTION_UPDATED
        self.old_related = pages.RELATED_BY_TEAM

    def tearDown(self):
        pages.PROJECTIONS = self.old_projections
        pages.PROJECTION_UPDATED = self.old_projection_updated
        pages.RELATED_BY_TEAM = self.old_related

    def test_publication_gate_groups_only_final_approved_wording(self):
        grouped = pages.load_wire_impacts()
        publications = [item for rows in grouped.values() for item in rows]
        expected = json.loads(
            (ROOT / "data" / "wire_publications.json").read_text())
        self.assertEqual(len(publications), expected["count"])
        self.assertTrue(all(item.get("player_id") for item in publications))
        self.assertTrue(all(
            str(item.get("reviewer_action", "")).startswith("APPROVE")
            for item in publications))
        self.assertTrue(all(item.get("public_evidence_summary_approved_by")
                            for item in publications))

    def test_invalid_publication_file_fails_before_player_rendering(self):
        payload = json.loads(
            (ROOT / "data" / "wire_publications.json").read_text())
        payload = copy.deepcopy(payload)
        payload["publications"][0]["reviewer_action"] = "PENDING"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "publications.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "may never render"):
                pages.load_wire_impacts(path)

    def test_fantasy_analysis_is_labelled_as_analysis(self):
        publication = pages.load_wire_impacts()["00-0041562"][0]
        self.assertEqual(publication["content_type"], "FANTASY_ANALYSIS")
        rendered = pages.wire_impact_block([publication])
        self.assertIn("Fantasy analysis", rendered)
        self.assertNotIn(">What changed<", rendered)

    def test_gsis_wire_identity_maps_strictly_to_sleeper_page(self):
        publication = pages.load_wire_impacts()["00-0036389"][0]
        roster = {
            "nfl-6904": {
                "id": "nfl-6904", "name": "Jalen Hurts", "team": "PHI",
                "position": "QB", "adp": "79.6",
            },
        }
        mapped = pages.wire_impacts_by_page(
            {"00-0036389": [publication]}, roster)
        self.assertEqual(list(mapped), ["nfl-6904"])
        self.assertEqual(mapped["nfl-6904"][0]["player_id"], "00-0036389")

        ambiguous = dict(roster)
        ambiguous["nfl-other"] = dict(roster["nfl-6904"], id="nfl-other")
        unmapped = pages.wire_impacts_by_page(
            {"00-0036389": [publication]}, ambiguous)
        self.assertEqual(list(unmapped), ["00-0036389"])

    def test_freshness_ignores_projection_date_without_a_projection(self):
        publication = pages.load_wire_impacts()["00-0036389"][0]
        pages.PROJECTIONS = {}
        pages.PROJECTION_UPDATED = "2026-08-30"
        modified = pages.player_last_updated(
            "Jalen Hurts", [], [publication])
        self.assertEqual(modified.isoformat(), publication["updated_at"])

    def test_player_page_shows_latest_impact_context_and_related_links(self):
        publication = pages.load_wire_impacts()["00-0036389"][0]
        pages.PROJECTION_UPDATED = "2026-08-22"
        pages.PROJECTIONS = {
            "jalen-hurts": {
                "ppr": 335.2, "half": 329.1, "std": 323.0,
                "rank": 3, "pos": "QB", "line": {},
            },
        }
        pages.RELATED_BY_TEAM = {
            "PHI": [
                {"name": "Jalen Hurts", "pos": "QB", "rank": 3,
                 "ppr": 335.2},
                {"name": "DeVonta Smith", "pos": "WR", "rank": 18,
                 "ppr": 202.4},
            ],
        }
        player = {
            "id": "00-0036389", "name": "Jalen Hurts", "team": "PHI",
            "pos": "QB", "meta": {"adp": "41.2", "depth_pos": "QB",
                                      "depth_order": "1"},
        }
        nuggets = [{
            "published_at": "2026-08-24T20:00:00+00:00",
            "category": "practice", "claim": "Hurts led team drills.",
            "attributions": json.dumps([{
                "url": "https://example.com/practice",
                "source_name": "Example Reporter",
            }]),
        }]

        rendered = pages.player_page(
            player, nuggets, "https://lineupbeat.com", [publication])

        self.assertIn("Latest approved decision context", rendered)
        self.assertIn(html.escape(publication["public_evidence_summary"]),
                      rendered)
        self.assertIn(html.escape(publication["lineupbeat_impact"]), rendered)
        self.assertNotIn(publication["reporter_found"], rendered)
        self.assertIn("Current ADP", rendered)
        self.assertIn("QB3", rendered)
        self.assertIn('/nfl/rankings/qb/', rendered)
        self.assertIn('/nfl/devonta-smith/', rendered)
        self.assertIn("Additional approved decision context", rendered)
        self.assertIn("Last updated", rendered)
        expected_date = (publication.get("updated_at") or
                         publication["published_at"])[:10]
        self.assertIn(f'datetime="{expected_date}', rendered)
        self.assertIn(f'"dateModified": "{expected_date}', rendered)


if __name__ == "__main__":
    unittest.main()
