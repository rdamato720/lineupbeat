#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import espn_injury_inputs as injuries


def injury(name: str, team: str, position: str, status: str,
           abbreviation: str, player_id: str) -> dict:
    return {
        "id": f"injury-{player_id}",
        "date": "2026-09-07T12:30Z",
        "status": status,
        "type": {"abbreviation": abbreviation},
        "details": {"type": "Ankle", "returnDate": "2026-09-13"},
        "shortComment": "copyrighted provider commentary must not survive",
        "longComment": "copyrighted provider analysis must not survive",
        "athlete": {
            "displayName": name,
            "position": {"abbreviation": position},
            "team": {"abbreviation": team},
            "links": [{"href": f"https://www.espn.com/nfl/player/_/id/{player_id}/{name.lower().replace(' ', '-') }"}],
        },
    }


def corpus() -> dict:
    groups = [{"id": str(index), "displayName": f"Team {index}", "injuries": []}
              for index in range(32)]
    expected = [
        injury("Example Runner", "ARI", "RB", "Questionable", "Q", "1001"),
        injury("Example Receiver", "WSH", "WR", "Out", "O", "1002"),
        injury("Example Tight End", "SEA", "TE", "Doubtful", "D", "1003"),
    ]
    groups[0]["injuries"].extend(expected)
    groups[1]["injuries"].append(
        injury("Healthy Quarterback", "BUF", "QB", "Active", "A", "1004"))
    groups[2]["injuries"].append(
        injury("Defensive Player", "DAL", "LB", "Questionable", "Q", "1005"))
    # The production boundary requires a realistic minimum number of rows.
    for index in range(17):
        groups[3 + index]["injuries"].append(
            injury(f"Reserve Player {index}", "NE", "RB", "Injured Reserve",
                   "IR", str(2000 + index)))
    return {
        "timestamp": "2026-09-07T13:00:00Z",
        "status": "success",
        "season": {"year": 2026},
        "injuries": groups,
    }


class ESPNInjuryInputTests(unittest.TestCase):
    def test_locked_semantic_corpus_has_perfect_precision_and_recall(self):
        payload = injuries.normalize_payload(corpus())
        expected = {
            ("Example Runner", "ARI", "RB", "Questionable"),
            ("Example Receiver", "WAS", "WR", "Out"),
            ("Example Tight End", "SEA", "TE", "Doubtful"),
        }
        relevant = {
            (row["name"], row["team"], row["position"], row["status"])
            for row in payload["records"] if row["name"].startswith("Example")
        }
        true_positive = len(relevant & expected)
        precision = true_positive / len(relevant)
        recall = true_positive / len(expected)
        self.assertEqual((true_positive, len(relevant), len(expected)), (3, 3, 3))
        self.assertEqual((precision, recall), (1.0, 1.0))
        print("ESPN injury semantic corpus precision 3/3; recall 3/3")

    def test_only_status_facts_survive_and_out_is_unavailable(self):
        payload = injuries.normalize_payload(corpus())
        encoded = str(payload)
        self.assertNotIn("copyrighted provider", encoded)
        receiver = next(row for row in payload["records"]
                        if row["name"] == "Example Receiver")
        runner = next(row for row in payload["records"]
                      if row["name"] == "Example Runner")
        self.assertTrue(receiver["confirmed_unavailable"])
        self.assertFalse(runner["confirmed_unavailable"])
        self.assertEqual(receiver["team"], "WAS")
        self.assertEqual(receiver["espn_id"], "1002")

    def test_partial_team_response_fails_closed(self):
        raw = corpus()
        raw["injuries"].pop()
        with self.assertRaisesRegex(ValueError, "31 teams"):
            injuries.normalize_payload(raw)

    def test_unknown_status_fails_closed(self):
        raw = copy.deepcopy(corpus())
        raw["injuries"][0]["injuries"][0]["status"] = "Maybe"
        with self.assertRaisesRegex(ValueError, "unsupported ESPN injury status"):
            injuries.normalize_payload(raw)


if __name__ == "__main__":
    unittest.main()
