#!/usr/bin/env python3
"""Integrity tests for the immutable 2026 college projection v1.1 release."""

import csv
import hashlib
import json
import re
import unittest
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
V10 = ROOT / "data" / "college" / "2026" / "v1.0"
V11 = ROOT / "data" / "college" / "2026" / "v1.1"
NUMERIC = [
    "pass_attempts", "completions", "passing_yards", "passing_td",
    "interceptions", "rush_attempts", "rushing_yards", "rushing_td",
    "receptions", "receiving_yards", "receiving_td", "fantasy_points",
    "fantasy_points_per_game", "rushing_fantasy_points",
]


def read_csv(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def concentration(rows):
    rooms = defaultdict(list)
    for row in rows:
        if row["position"] == "RB":
            rooms[row["team_id"]].append(float(row["rush_attempts"]))
    numerator = denominator = 0.0
    for attempts in rooms.values():
        attempts.sort(reverse=True)
        numerator += sum(attempts[:2])
        denominator += sum(attempts)
    return numerator / denominator


class CollegeV11Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = read_csv(
            V10 / "provenance" / "college_player_projections_2026_v1.0.csv"
        )
        cls.new = read_csv(
            V11 / "provenance" / "college_player_projections_2026_v1.1.csv"
        )
        cls.old_by_id = {row["player_id"]: row for row in cls.old}
        cls.new_by_id = {row["player_id"]: row for row in cls.new}
        cls.manifest = json.loads((V11 / "manifest.json").read_text())
        cls.qa = json.loads(
            (V11 / "provenance" / "college_projection_qa_v1.1.json").read_text()
        )
        cls.site = json.loads((V11 / "college_site_projections_2026.json").read_text())

    def test_active_release_and_pinned_manifest(self):
        config = json.loads((ROOT / "data" / "college" / "config.json").read_text())
        self.assertEqual(config["activeCollegeProjectionVersion"], "2026/v1.1")
        builder = (ROOT / "scripts" / "build_college_projections.py").read_text()
        pinned = re.search(r'EXPECTED_SHA = "([0-9a-f]{64})"', builder).group(1)
        self.assertEqual(pinned, sha(V11 / "manifest.json"))

    def test_source_release_is_unchanged_and_identified(self):
        self.assertEqual(self.manifest["source_release"], "2026/v1.0")
        self.assertEqual(
            self.manifest["source_manifest_sha256"], sha(V10 / "manifest.json")
        )

    def test_manifest_file_digests(self):
        locations = {
            "college_player_projections_2026_v1.1.csv": V11 / "provenance",
            "college_team_projections_2026_v1.1.csv": V11 / "provenance",
            "college_projection_qa_v1.1.json": V11 / "provenance",
            "college_site_projections_2026.json": V11,
            "generate_college_v1_1.py": ROOT / "scripts",
        }
        for name, directory in locations.items():
            path = directory / name
            entry = self.manifest["files"][name]
            self.assertEqual(entry["bytes"], path.stat().st_size)
            self.assertEqual(entry["sha256"], sha(path))

    def test_counts_and_unique_players(self):
        self.assertEqual(len(self.new), 2351)
        self.assertEqual(len(self.new_by_id), 2351)
        self.assertEqual(len({row["team_id"] for row in self.new}), 68)
        self.assertEqual(
            {position: sum(row["position"] == position for row in self.new)
             for position in ("QB", "RB", "WR", "TE")},
            {"QB": 361, "RB": 493, "WR": 990, "TE": 507},
        )

    def test_only_rb_rushing_projection_values_changed(self):
        permitted = {
            "rush_attempts", "rushing_yards", "rushing_td",
            "fantasy_points", "fantasy_points_per_game",
            "rushing_fantasy_points",
        }
        for player_id, new in self.new_by_id.items():
            old = self.old_by_id[player_id]
            for field in NUMERIC:
                if new["position"] == "RB" and field in permitted:
                    continue
                self.assertEqual(float(new[field]), float(old[field]), (player_id, field))

    def test_rb_team_budgets_are_preserved(self):
        fields = ("rush_attempts", "rushing_yards", "rushing_td", "receptions")
        for field in fields:
            old = defaultdict(float)
            new = defaultdict(float)
            for row in self.old:
                if row["position"] == "RB":
                    old[row["team_id"]] += float(row[field])
            for row in self.new:
                if row["position"] == "RB":
                    new[row["team_id"]] += float(row[field])
            for team_id in old:
                self.assertAlmostEqual(old[team_id], new[team_id], places=9)

    def test_concentration_and_qa(self):
        self.assertAlmostEqual(concentration(self.old), 0.6709884856083221)
        self.assertGreaterEqual(concentration(self.new), 0.79)
        self.assertLessEqual(concentration(self.new), 0.81)
        self.assertEqual(self.qa["status"], "PASS")
        self.assertEqual(len(self.qa["reconciliation"]), 15)
        self.assertFalse(self.qa["blocking_failures"])
        self.assertLessEqual(max(self.qa["reconciliation"].values()), 1e-9)

    def test_site_matches_release(self):
        self.assertEqual(self.site["modelVersion"], "v1.1")
        self.assertEqual(len(self.site["players"]), 2351)
        self.assertEqual(len(self.site["teams"]), 68)
        site_by_id = {row["id"]: row for row in self.site["players"]}
        for player_id, row in self.new_by_id.items():
            self.assertEqual(site_by_id[player_id]["rank"], int(row["position_rank"]))
            self.assertEqual(site_by_id[player_id]["pts"], round(float(row["fantasy_points"]), 1))


if __name__ == "__main__":
    unittest.main()
