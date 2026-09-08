#!/usr/bin/env python3
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import prepare_public_release
import seo


class PublicReleaseTests(unittest.TestCase):
    def test_production_navigation_omits_connectors(self):
        with patch.dict(os.environ, {"LINEUPBEAT_RELEASE_TARGET": "production"}):
            shell = seo.site_nav(None, "nfl") + seo.site_footer()
        self.assertNotIn("/my-team/", shell)
        self.assertNotIn("/my-league/", shell)
        self.assertNotIn("My Fantasy", shell)
        self.assertIn("/decision-room/nfl/", shell)

    def test_development_navigation_keeps_connectors(self):
        with patch.dict(os.environ, {"LINEUPBEAT_RELEASE_TARGET": "development"}):
            shell = seo.site_nav(None, "nfl") + seo.site_footer()
        self.assertIn("/my-team/", shell)
        self.assertIn("/my-league/", shell)

    def test_public_page_builder_changes_trigger_production_deploy(self):
        workflow = (Path(__file__).resolve().parents[1]
                    / ".github" / "workflows" / "refresh.yml").read_text()
        self.assertIn('- "scripts/build_pages.py"', workflow)

    def test_pruner_removes_routes_and_rejects_leaks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "index.html").write_text('<a href="/nfl/rankings/">Rankings</a>')
            for route in prepare_public_release.HIDDEN_ROUTES:
                path = root / route
                path.mkdir()
                (path / "index.html").write_text(route)
            with patch.dict(os.environ, {"LINEUPBEAT_RELEASE_TARGET": "production"}):
                prepare_public_release.prepare(root)
            self.assertTrue(all(not (root / route).exists()
                                for route in prepare_public_release.HIDDEN_ROUTES))

            (root / "connector.js").write_text("fetch('/api/leagues/history')")
            with patch.dict(os.environ, {"LINEUPBEAT_RELEASE_TARGET": "production"}):
                with self.assertRaisesRegex(RuntimeError, "connector references"):
                    prepare_public_release.prepare(root)

    def test_pruner_scrubs_connector_links_from_retained_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            page = root / "nfl" / "durability" / "index.html"
            page.parent.mkdir(parents=True)
            (root / "index.html").write_text("<html>public</html>")
            page.write_text(
                '<details class="navgroup"><summary>My Fantasy</summary>'
                '<div><a href="/my-team/">My Team</a>'
                '<a href="/my-league/">My League</a></div></details>'
                '<footer><a href="/my-team/">My Team</a><br>'
                '<a href="/decision-room/nfl/">NFL Decision Room</a></footer>'
            )
            with patch.dict(os.environ, {"LINEUPBEAT_RELEASE_TARGET": "production"}):
                prepare_public_release.prepare(root)
            rendered = page.read_text()
            self.assertNotIn("My Fantasy", rendered)
            self.assertNotIn("/my-team/", rendered)
            self.assertNotIn("/my-league/", rendered)
            self.assertIn("/decision-room/nfl/", rendered)


if __name__ == "__main__":
    unittest.main()
