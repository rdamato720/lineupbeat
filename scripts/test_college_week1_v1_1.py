#!/usr/bin/env python3
"""Integrity and regression tests for College Week 1 v1.1."""

import csv
import hashlib
import json
import unittest
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REL = ROOT / "data/college/2026/week-1/v1.1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CollegeWeek1V11Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((REL / "manifest.json").read_text())
        cls.site = json.loads((REL / "college_week1_site_projections_2026.json").read_text())
        with (REL / "provenance/college_week1_player_projections_2026_v1.1.csv").open(newline="") as handle:
            cls.rows = list(csv.DictReader(handle))

    def test_release_is_active_and_pinned(self):
        config = json.loads((ROOT / "data/college/config.json").read_text())
        self.assertEqual(config["activeCollegeWeeklyProjectionVersion"], "2026/week-1/v1.1")
        self.assertIn(digest(REL / "manifest.json"),
                      (ROOT / "scripts/build_college_week1.py").read_text())

    def test_manifest_and_counts(self):
        self.assertEqual(self.manifest["qa_status"], "PASS")
        self.assertEqual(self.site["counts"], {"players": 2205, "teams": 64, "games": 55})
        self.assertEqual(len(self.rows), 2205)
        for name, expected in self.manifest["files"].items():
            path = REL / ("provenance" if name.startswith("college_week1_player")
                          or name == "college_week1_schedule_2026.json" else "") / name
            self.assertEqual(path.stat().st_size, expected["bytes"])
            self.assertEqual(digest(path), expected["sha256"])

    def test_all_team_accounting_reconciles(self):
        teams = defaultdict(list)
        for row in self.rows:
            teams[row["team_id"]].append(row)
        self.assertEqual(len(teams), 64)
        for team, rows in teams.items():
            total = lambda key: sum(float(row[key]) for row in rows)
            self.assertAlmostEqual(total("passing_yards"), total("receiving_yards"), places=8, msg=team)
            self.assertAlmostEqual(total("completions"), total("receptions"), places=8, msg=team)
            self.assertAlmostEqual(total("passing_td"), total("receiving_td"), places=8, msg=team)

    def test_scoring_nonnegative_and_ranks_complete(self):
        positions = defaultdict(list)
        for row in self.rows:
            n = lambda key: float(row[key])
            expected = (n("passing_yards") * .04 + n("passing_td") * 4
                        - n("interceptions") + n("rushing_yards") * .1
                        + n("rushing_td") * 6 + n("receptions")
                        + n("receiving_yards") * .1 + n("receiving_td") * 6)
            self.assertAlmostEqual(n("fantasy_points"), expected, places=8)
            for key in ("pass_attempts", "completions", "passing_yards", "passing_td",
                        "interceptions", "rush_attempts", "rushing_yards", "rushing_td",
                        "receptions", "receiving_yards", "receiving_td", "fantasy_points"):
                self.assertGreaterEqual(n(key), 0, (row["player_name"], key))
            positions[row["position"]].append(int(row["position_rank"]))
        self.assertEqual(sorted(int(row["overall_rank"]) for row in self.rows),
                         list(range(1, 2206)))
        for ranks in positions.values():
            self.assertEqual(sorted(ranks), list(range(1, len(ranks) + 1)))

    def test_market_coverage_and_identity_are_fail_closed(self):
        audit = json.loads((REL / "projection_repair_audit.json").read_text())
        market = audit["market_input"]
        self.assertEqual(market["players_with_evidence"], 345)
        self.assertEqual(market["players_with_numeric_evidence"], 112)
        self.assertEqual(market["ambiguous_participants"], 0)
        self.assertEqual(market["unresolved_participants"], 180)
        self.assertFalse(audit["guardrails"]["fuzzy_identity_matching"])
        self.assertFalse(audit["guardrails"]["anytime_td_changes_projection"])

    def test_known_starter_and_receiver_repairs(self):
        rows = {(row["player_name"], row["team_name"]): row for row in self.rows}
        expected = {
            ("Austin Simmons", "Missouri"): ("passing_yards", 209.5),
            ("Keelon Russell", "Alabama"): ("passing_yards", 240.5),
            ("Davis Warren", "Stanford"): ("passing_yards", 161.5),
            ("Cam Coleman", "Texas"): ("receiving_yards", 58.5),
            ("Nick Marsh", "Indiana"): ("receiving_yards", 63.5),
            ("Makhi Hughes", "Houston"): ("rushing_yards", 84.5),
        }
        for identity, (field, value) in expected.items():
            self.assertEqual(float(rows[identity][field]), value)


if __name__ == "__main__":
    unittest.main()
