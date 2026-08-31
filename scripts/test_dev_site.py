#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import dev_site


TEMPLATE = """<!doctype html><html><head><title>Test</title></head><body>
<script>const DATA = /*__DATA__*/ {"generated_at":new Date().toISOString(),"sports":{},"players":[]};</script>
</body></html>"""


class DevSiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.site = self.root / "site"
        self.template = self.root / "template.html"
        self.template.write_text(TEMPLATE)
        self.feed = self.root / "feed.json"
        self.feed.write_text(json.dumps({
            "generated_at": "2026-08-31T00:00:00Z",
            "sports": {"nfl": {"nuggets": [{"id": 1}]}},
            "players": [{"id": "nfl-1"}],
        }))

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_uses_supplied_public_feed(self):
        dev_site.seed(self.feed, self.template, self.site)
        page = (self.site / "index.html").read_text()
        self.assertIn('"nfl-1"', page)
        self.assertNotIn("/*__DATA__*/", page)
        self.assertEqual(
            json.loads((self.site / "data" / "feed.json").read_text())["players"][0]["id"],
            "nfl-1",
        )

    def test_seed_rejects_wrong_feed_shape(self):
        self.feed.write_text('{"sports": [], "players": []}')
        with self.assertRaises(SystemExit):
            dev_site.seed(self.feed, self.template, self.site)

    def test_hydrate_db_uses_only_public_reports(self):
        database = self.root / "dev.db"
        dev_site.hydrate_db(self.feed, database)
        connection = sqlite3.connect(database)
        row = connection.execute(
            "SELECT sport, player_id, claim FROM nuggets"
        ).fetchone()
        connection.close()
        self.assertEqual(row, ("nfl", None, ""))

    def test_protection_is_idempotent_and_covers_every_page(self):
        dev_site.seed(self.feed, self.template, self.site)
        nested = self.site / "nfl" / "player" / "index.html"
        nested.parent.mkdir(parents=True)
        nested.write_text("<html><head></head><body>Player</body></html>")
        dev_site.protect(self.site, "develop")
        dev_site.protect(self.site, "develop")
        dev_site.verify(self.site)
        for path in (self.site / "index.html", nested):
            text = path.read_text()
            self.assertEqual(text.count('id="lb-dev-banner"'), 1)
            self.assertEqual(text.count('id="lb-dev-style"'), 1)
            self.assertIn(dev_site.ROBOTS_META, text)

    def test_protection_overrides_existing_robots_meta(self):
        self.site.mkdir()
        page = self.site / "index.html"
        page.write_text(
            '<html><head><meta name="robots" content="index, follow"></head>'
            '<body>Home</body></html>'
        )
        dev_site.protect(self.site, "feature/test")
        text = page.read_text()
        self.assertNotIn("index, follow", text)
        self.assertIn("feature/test", text)

    def test_protection_removes_analytics_but_keeps_application_scripts(self):
        self.site.mkdir()
        page = self.site / "index.html"
        page.write_text(
            '<html><head><script>window.appReady=true;</script>'
            '<script type="module" src="https://static.cloudflareinsights.com/beacon.min.js" '
            'data-cf-beacon="token"></script></head><body>'
            '<script>rdt("track", "PageVisit");</script>'
            '<script>twq("config", "test");</script></body></html>'
        )
        dev_site.protect(self.site, "develop")
        text = page.read_text()
        self.assertIn("window.appReady=true", text)
        for needle in dev_site.TRACKING_NEEDLES:
            self.assertNotIn(needle, text.lower())
        dev_site.verify(self.site)

    def test_verify_rejects_tracking_in_a_protected_page(self):
        self.site.mkdir()
        page = self.site / "index.html"
        page.write_text("<html><head></head><body></body></html>")
        dev_site.protect(self.site, "develop")
        page.write_text(page.read_text().replace(
            "</body>", '<script>twq("config", "test")</script></body>'
        ))
        with self.assertRaises(SystemExit):
            dev_site.verify(self.site)

    def test_verify_rejects_unprotected_artifact(self):
        self.site.mkdir()
        (self.site / "index.html").write_text("<html><head></head><body></body></html>")
        with self.assertRaises(SystemExit):
            dev_site.verify(self.site)


if __name__ == "__main__":
    unittest.main()
