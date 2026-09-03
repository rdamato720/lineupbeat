#!/usr/bin/env python3
"""Integrity and publication tests for 2026 college Week 1 v1.0."""
import csv
import hashlib
import json
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REL = ROOT / "data/college/2026/week-1/v1.0"
SEASON = ROOT / "data/college/2026/v1.1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CollegeWeek1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((REL / "manifest.json").read_text())
        cls.site = json.loads((REL / "college_week1_site_projections_2026.json").read_text())
        with (REL / "provenance/college_week1_player_projections_2026_v1.0.csv").open(newline="") as handle:
            cls.rows = list(csv.DictReader(handle))

    def test_release_remains_an_immutable_source_archive(self):
        self.assertEqual(self.manifest["version"], "college_week1_2026_v1.0")
        self.assertEqual(digest(REL / "manifest.json"),
                         "9b3436d1df2869c9b02b1e6bb905f5a430ee331dd2625cd62264ee79f216fca2")

    def test_source_release_and_file_hashes(self):
        self.assertEqual(self.manifest["source_release"], "2026/v1.1")
        self.assertEqual(self.manifest["source_manifest_sha256"], digest(SEASON / "manifest.json"))
        locations = {
            "college_week1_player_projections_2026_v1.0.csv": REL / "provenance",
            "college_week1_schedule_2026.json": REL / "provenance",
            "college_week1_site_projections_2026.json": REL,
        }
        for name, directory in locations.items():
            path = directory / name
            self.assertEqual(self.manifest["files"][name]["bytes"], path.stat().st_size)
            self.assertEqual(self.manifest["files"][name]["sha256"], digest(path))

    def test_counts_and_rank_sequences(self):
        self.assertEqual(self.site["counts"], {"players": 2205, "teams": 64, "games": 55})
        self.assertEqual(len(self.rows), 2205)
        self.assertEqual(len({r["player_id"] for r in self.rows}), 2205)
        groups = defaultdict(list)
        for row in self.rows:
            groups[row["position"]].append(int(row["position_rank"]))
        for ranks in groups.values():
            self.assertEqual(sorted(ranks), list(range(1, len(ranks) + 1)))
        self.assertEqual(sorted(int(r["overall_rank"]) for r in self.rows), list(range(1, 2206)))

    def test_scoring_recomputes_and_values_are_nonnegative(self):
        for row in self.rows:
            n = lambda key: float(row[key])
            expected = (n("passing_yards") * .04 + n("passing_td") * 4
                        - n("interceptions") + n("rushing_yards") * .1
                        + n("rushing_td") * 6 + n("receptions")
                        + n("receiving_yards") * .1 + n("receiving_td") * 6)
            self.assertAlmostEqual(n("fantasy_points"), expected, places=9)
            for key in ("pass_attempts", "completions", "passing_yards",
                        "passing_td", "interceptions", "rush_attempts",
                        "rushing_yards", "rushing_td", "receptions",
                        "receiving_yards", "receiving_td", "fantasy_points"):
                self.assertGreaterEqual(n(key), 0, (row["player_name"], key))

    def test_site_output_matches_provenance(self):
        site = {r["id"]: r for r in self.site["players"]}
        for row in self.rows:
            item = site[row["player_id"]]
            self.assertEqual(item["rank"], int(row["position_rank"]))
            self.assertEqual(item["overallRank"], int(row["overall_rank"]))
            self.assertEqual(item["pts"], round(float(row["fantasy_points"]), 1))

    def test_public_pages_exist(self):
        base = ROOT / "site/college-fantasy-football/week-1"
        for segment in ("", "qb", "rb", "wr", "te"):
            page = base / segment / "index.html"
            self.assertTrue(page.exists(), page)
            text = page.read_text()
            self.assertIn("Week 1", text)
            self.assertIn("Yahoo scoring", text)
        season = (ROOT / "site/college-fantasy-football/projections/index.html").read_text()
        self.assertIn('/college-fantasy-football/week-1/', season)

    def test_overview_search_reaches_players_beyond_preview_rows(self):
        week = (ROOT / "site/college-fantasy-football/week-1/index.html").read_text()
        season = (ROOT / "site/college-fantasy-football/projections/index.html").read_text()
        self.assertIn("Search all 2,205 players", week)
        self.assertIn("Search all 2,351 players", season)
        self.assertIn("Kevin Sperry", week)
        self.assertIn("Kevin Sperry", season)
        self.assertIn("event.key==='Enter'", week)
        self.assertIn("event.key==='Enter'", season)


if __name__ == "__main__":
    unittest.main()
