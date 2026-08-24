#!/usr/bin/env python3
"""Cached-X-to-Wire bridge regressions. No network, key, model, or spend."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import wire_x_import as bridge
from wire.store import WireStore


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CachedXBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source_db = self.root / "beatwire.db"
        self.wire_db = self.root / "wire.db"
        self.sources = self.root / "nfl.yaml"
        self.sources.write_text("""\
sources:
  - id: nfl-buf-tapi-test
    kind: twitterapi
    handle: TestWriter
    name: Test Writer
    outlet: Test Outlet
    teams: [BUF]
  - id: nfl-natl-tapi-test
    kind: twitterapi
    handle: NationalWriter
    name: National Writer
    teams: []
  - id: nfl-buf-tapi-disabled
    kind: twitterapi
    handle: DisabledWriter
    name: Disabled Writer
    enabled: false
    teams: [BUF]
""")
        conn = sqlite3.connect(self.source_db)
        conn.execute("""
            CREATE TABLE items (
              item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
              url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
              body TEXT NOT NULL DEFAULT '', published_at TEXT,
              fetched_at TEXT NOT NULL)
        """)
        self.now = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
        fresh = (self.now - timedelta(hours=1)).isoformat()
        old = (self.now - timedelta(hours=80)).isoformat()
        rows = [
            ("one", "nfl-buf-tapi-test",
             "https://x.com/TestWriter/status/1001", "",
             "At practice, Josh Allen took first-team reps and completed "
             "four passes during team drills.", fresh, fresh),
            ("two", "nfl-buf-tapi-test",
             "https://x.com/TestWriter/status/1002", "",
             "Josh Allen was a full participant on the practice report "
             "after returning to the field for team drills.", fresh, fresh),
            ("old", "nfl-buf-tapi-test",
             "https://x.com/TestWriter/status/1003", "",
             "At practice, Josh Allen took first-team reps during team drills "
             "in an older report.", old, old),
            ("bad-url", "nfl-buf-tapi-test",
             "https://example.com/TestWriter/status/1004", "",
             "At practice, Josh Allen took first-team reps during team drills "
             "in a post with the wrong host.", fresh, fresh),
            ("national", "nfl-natl-tapi-test",
             "https://x.com/NationalWriter/status/1005", "",
             "At practice, Josh Allen took first-team reps during team drills "
             "in a national post.", fresh, fresh),
            ("disabled", "nfl-buf-tapi-disabled",
             "https://x.com/DisabledWriter/status/1006", "",
             "At practice, Josh Allen took first-team reps during team drills "
             "in a disabled post.", fresh, fresh),
        ]
        conn.executemany("INSERT INTO items VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_bridge(self, **changes):
        args = {
            "source_db": self.source_db, "wire_db": self.wire_db,
            "source_registry": self.sources,
            "publications": bridge.PUBLICATIONS, "hours": 72,
            "limit": 100, "now": self.now,
        }
        args.update(changes)
        return bridge.import_cached_x(**args)

    def test_import_is_review_only_and_authority_cautious(self):
        source_before = sha(self.source_db)
        publications_before = sha(bridge.PUBLICATIONS)
        stats = self.run_bridge()
        self.assertEqual(stats["configured_team_sources"], 1)
        self.assertEqual(stats["national_sources_deferred"], 1)
        self.assertEqual(stats["researched_authority_sources"], 0)
        self.assertEqual(stats["cached_rows"], 4)
        self.assertEqual(stats["inside_window"], 3)
        self.assertEqual(stats["invalid_url"], 1)
        self.assertEqual(stats["items_imported"], 2)
        self.assertEqual(stats["candidates"], 2)
        self.assertEqual(stats["new"], 2)
        self.assertEqual(stats["model_calls_made"], 0)
        self.assertEqual(stats["publications_applied"], 0)
        self.assertEqual(sha(self.source_db), source_before)
        self.assertEqual(sha(bridge.PUBLICATIONS), publications_before)

        store = WireStore(self.wire_db)
        rows = [dict(row) for row in store.evidence()]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["player_name"] for row in rows}, {"Josh Allen"})
        self.assertEqual({row["team"] for row in rows}, {"BUF"})
        self.assertEqual({row["position"] for row in rows}, {"QB"})
        self.assertEqual({row["source_type"] for row in rows}, {"x"})
        self.assertEqual({row["review_status"] for row in rows}, {"PENDING"})
        self.assertEqual({row["evidence_class"] for row in rows}, {"UNCERTAIN"})
        reasons = " ".join(
            " ".join(row["classification_reasons"])
            if isinstance(row["classification_reasons"], list)
            else str(row["classification_reasons"])
            for row in rows)
        self.assertIn("no researched firsthand Wire authority", reasons)
        self.assertIn("not the club's own official designation", reasons)
        notes = {row["note"] for row in store.conn.execute(
            "SELECT note FROM wire_source_items")}
        self.assertEqual(notes, {bridge.extractor.X_BRIDGE_NOTE})
        store.conn.close()

    def test_exact_researched_reporter_match_can_be_firsthand(self):
        authority_sources = self.root / "authority-sources.yaml"
        authority_sources.write_text("""\
sources:
  - id: nfl-gb-tapi-billhubernfl
    kind: twitterapi
    handle: BillHuberNFL
    name: BillHuberNFL
    outlet: X
    teams: [GB]
""")
        fresh = (self.now - timedelta(hours=1)).isoformat()
        conn = sqlite3.connect(self.source_db)
        conn.execute(
            "INSERT INTO items VALUES (?,?,?,?,?,?,?)",
            ("authority", "nfl-gb-tapi-billhubernfl",
             "https://x.com/BillHuberNFL/status/2001", "",
             "At practice, Jordan Love took every first-team rep and "
             "completed four passes during team drills.", fresh, fresh))
        conn.execute(
            "INSERT INTO items VALUES (?,?,?,?,?,?,?)",
            ("authority-designation", "nfl-gb-tapi-billhubernfl",
             "https://x.com/BillHuberNFL/status/2002", "",
             "Jordan Love was listed as a full participant on the official "
             "practice report after team drills.", fresh, fresh))
        conn.commit()
        conn.close()

        authority_db = self.root / "authority-wire.db"
        stats = self.run_bridge(
            source_registry=authority_sources, wire_db=authority_db)
        self.assertEqual(stats["researched_authority_sources"], 1)
        self.assertEqual(stats["items_imported"], 2)
        self.assertEqual(stats["new"], 2)

        store = WireStore(authority_db)
        rows = [dict(row) for row in store.evidence()]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["player_name"] for row in rows}, {"Jordan Love"})
        self.assertEqual({row["source_author_or_channel"] for row in rows},
                         {"Bill Huber"})
        observation = next(
            row for row in rows if row["source_url"].endswith("/2001"))
        designation = next(
            row for row in rows if row["source_url"].endswith("/2002"))
        self.assertEqual(observation["evidence_class"],
                         "FIRSTHAND_OBSERVATION")
        self.assertEqual(designation["evidence_class"], "UNCERTAIN")
        self.assertIn("not the club's own official designation", " ".join(
            designation["classification_reasons"]))
        self.assertEqual({row["review_status"] for row in rows}, {"PENDING"})
        store.conn.close()

        # Repointing the configured source ID to another handle fails closed:
        # configuration identity alone never grants reporter authority.
        mismatched = self.root / "mismatched-sources.yaml"
        mismatched.write_text(authority_sources.read_text().replace(
            "BillHuberNFL", "NotBillHuber"))
        mismatch_db = self.root / "mismatch-wire.db"
        stats = self.run_bridge(
            source_registry=mismatched, wire_db=mismatch_db)
        self.assertEqual(stats["researched_authority_sources"], 0)
        self.assertEqual(stats["invalid_url"], 2)
        self.assertEqual(stats["items_imported"], 0)

    def test_import_is_idempotent_and_preserves_review_decisions(self):
        first = self.run_bridge()
        self.assertEqual(first["new"], 2)
        store = WireStore(self.wire_db)
        candidate_id = store.evidence()[0]["candidate_id"]
        store.conn.execute(
            "UPDATE wire_evidence SET review_status='REJECTED' "
            "WHERE candidate_id=?", (candidate_id,))
        store.conn.commit()
        store.conn.close()

        second = self.run_bridge()
        self.assertEqual(second["new"], 0)
        store = WireStore(self.wire_db)
        status = store.conn.execute(
            "SELECT review_status FROM wire_evidence WHERE candidate_id=?",
            (candidate_id,)).fetchone()[0]
        self.assertEqual(status, "REJECTED")
        self.assertEqual(len(store.evidence()), 2)
        store.conn.close()

    def test_dry_run_and_missing_cache_create_no_database(self):
        dry_db = self.root / "dry-wire.db"
        stats = self.run_bridge(wire_db=dry_db, dry_run=True)
        self.assertEqual(stats["candidates"], 2)
        self.assertEqual(stats["new"], 0)
        self.assertFalse(dry_db.exists())

        missing_wire = self.root / "missing-wire.db"
        stats = self.run_bridge(
            source_db=self.root / "does-not-exist.db", wire_db=missing_wire)
        self.assertEqual(stats["cache_missing"], 1)
        self.assertFalse(missing_wire.exists())

    def test_bridge_has_no_fetch_or_secret_path(self):
        source = (ROOT / "scripts" / "wire_x_import.py").read_text()
        self.assertNotIn("TWITTERAPI_IO_KEY", source)
        self.assertNotIn("SORSA_API_KEY", source)
        self.assertNotIn("tapi.fetch", source)
        self.assertNotIn("sorsa.fetch", source)
        self.assertIn("mode=ro", source)
        self.assertIn("researched_authorities", source)
        extractor_source = (ROOT / "scripts" / "wire_extract.py").read_text()
        self.assertIn("COALESCE(note, '') != ?", extractor_source)
        self.assertIn("X_BRIDGE_NOTE", extractor_source)


if __name__ == "__main__":
    unittest.main()
