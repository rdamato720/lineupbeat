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

            (root / "about.html").write_text('<a href="/my-team/">Hidden</a>')
            with patch.dict(os.environ, {"LINEUPBEAT_RELEASE_TARGET": "production"}):
                with self.assertRaisesRegex(RuntimeError, "connector references"):
                    prepare_public_release.prepare(root)


if __name__ == "__main__":
    unittest.main()
