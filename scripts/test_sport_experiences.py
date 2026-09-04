#!/usr/bin/env python3
"""Contracts for distinct NFL/College navigation and local College logos."""

from __future__ import annotations

import hashlib
import json
import re
import struct
import unittest
from pathlib import Path

import college_decision_data
import college_team_logos
import seo

ROOT = Path(__file__).resolve().parents[1]


class SportExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = college_team_logos.load_registry()
        weekly = json.loads((ROOT / "data/college/2026/week-1/v1.0/college_week1_site_projections_2026.json").read_text())
        season = json.loads((ROOT / "data/college/2026/v1.1/college_site_projections_2026.json").read_text())
        cls.week_ids = {p["teamId"] for p in weekly["players"]}
        cls.season_ids = {t["id"] for t in season["teams"]}

    def test_sport_specific_navigation(self):
        nfl = seo.site_nav("data", "nfl")
        college = seo.site_nav("rankings", "college")
        nfl_views = re.search(r'<nav class="views".*?</nav>', nfl, re.S).group(0)
        college_views = re.search(r'<nav class="views".*?</nav>', college, re.S).group(0)
        self.assertIn('href="/nfl/data/" aria-current="page">Fantasy Data</a>', nfl)
        self.assertIn('href="/league-history/">League History</a>', nfl_views)
        self.assertIn("Search NFL players", nfl)
        self.assertNotIn("Week 1 Rankings", nfl_views)
        self.assertNotIn("Fantasy Data", college_views)
        self.assertNotIn("League History", college_views)
        self.assertIn('href="/college-fantasy-football/week-1/" aria-current="page">Week 1 Rankings</a>', college)
        self.assertIn('href="/college-fantasy-football/projections/">Season Projections</a>', college)
        self.assertNotIn('href="/nfl/rankings/"', college_views)
        self.assertNotIn('href="/nfl/projections/"', college_views)

    def test_switching_keeps_activity_and_search_context(self):
        nfl = seo.site_nav("rankings", "nfl")
        college = seo.site_nav("projections", "college")
        self.assertIn('href="/college-fantasy-football/week-1/" aria-pressed="false">COLLEGE</a>', nfl)
        self.assertIn('href="/nfl/projections/" aria-pressed="false">NFL</a>', college)
        self.assertIn("Search 2,205 College players", college)
        header = college.split("</header>", 1)[0]
        self.assertNotIn('id="site-player-list"', header)

    def test_every_college_team_has_verified_local_png(self):
        self.assertEqual(len(self.registry), 68)
        self.assertEqual(self.week_ids, self.week_ids & self.registry.keys())
        self.assertEqual(self.season_ids, self.season_ids & self.registry.keys())
        for team_id, row in self.registry.items():
            path = ROOT / "site" / row["local_asset_path"].lstrip("/")
            blob = path.read_bytes()
            self.assertEqual(blob[:8], b"\x89PNG\r\n\x1a\n", team_id)
            self.assertEqual(hashlib.sha256(blob).hexdigest(), row["sha256"], team_id)
            self.assertEqual(len(blob), row["file_size"], team_id)
            self.assertEqual(struct.unpack(">II", blob[16:24]), (row["width"], row["height"]), team_id)
            self.assertTrue(row["source_page"].startswith("https://www.espn.com/college-football/team/"))
            self.assertTrue(row["original_asset_url"].startswith("https://"))

    def test_college_payload_uses_only_local_college_logos(self):
        payload = college_decision_data.load_weekly()
        self.assertEqual(len(payload["players"]), 2205)
        for player in payload["players"]:
            self.assertTrue(player["team_logo"].startswith("/assets/college-teams/CFF_"))
            self.assertNotIn("/nfl/", player["team_logo"])
            self.assertNotIn("teamlogos/nfl", player["team_logo"])

    def test_shared_design_tokens_apply_to_both_sports(self):
        for sport in ("nfl", "college"):
            header = seo.site_nav("decision", sport)
            self.assertIn('id="shared-shell-css"', header)
            self.assertIn('--agate:"Barlow Condensed"', header)
            self.assertIn('--text:"Source Serif 4"', header)
            self.assertIn("--signal:#C6F53C", header)

    def test_league_history_has_a_first_class_navigation_state(self):
        header = seo.site_nav("league_history", "nfl")
        self.assertIn(
            'href="/league-history/" aria-current="page">League History</a>',
            header,
        )


if __name__ == "__main__":
    unittest.main()
