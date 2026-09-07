#!/usr/bin/env python3

import copy
import csv
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_daily_fantasy_refresh as validator
import capture_week1_inputs as capture


class DailyFantasyRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "data/week1/2026/v1.2/nfl_week1_projections.json"
        cls.payload = json.loads(path.read_text())

    def test_current_release_satisfies_publication_contract(self):
        updated = validator.datetime.fromisoformat(
            self.payload["updated_at"].replace("Z", "+00:00"))
        result = validator.validate(
            self.payload, self.payload, now=updated,
            require_injuries=bool(
                self.payload.get("sources", {}).get("injuries", {}).get("updated_at")
            ),
        )
        self.assertEqual(result["teams"], 32)
        self.assertEqual(result["games"], 16)
        self.assertGreaterEqual(result["players_with_props"], 50)

    def test_partial_refresh_is_rejected(self):
        candidate = copy.deepcopy(self.payload)
        candidate["players"] = [row for row in candidate["players"]
                                if row["team"] != "ARI"]
        updated = validator.datetime.fromisoformat(
            candidate["updated_at"].replace("Z", "+00:00"))
        with self.assertRaisesRegex(ValueError, "31 NFL teams"):
            validator.validate(candidate, self.payload, now=updated,
                               require_injuries=False)

    def test_private_sportsbook_identity_is_rejected(self):
        candidate = copy.deepcopy(self.payload)
        candidate["players"][0]["market"]["bookmaker_key"] = "secret"
        updated = validator.datetime.fromisoformat(
            candidate["updated_at"].replace("Z", "+00:00"))
        with self.assertRaisesRegex(ValueError, "private sportsbook detail"):
            validator.validate(candidate, self.payload, now=updated,
                               require_injuries=False)

    def test_current_asset_requires_expected_schema_and_population(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.csv.gz"
            fields = sorted(capture.CURRENT_REQUIRED_COLUMNS[path.name])
            with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows({field: "x" for field in fields}
                                 for _ in range(capture.CURRENT_MINIMUM_ROWS[path.name]))
            self.assertEqual(
                capture.validate_current_asset(path),
                capture.CURRENT_MINIMUM_ROWS[path.name])

    def test_current_asset_rejects_missing_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roster_2026.csv.gz"
            with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
                handle.write("gsis_id,full_name\n1,Example Player\n")
            with self.assertRaisesRegex(RuntimeError, "missing required columns"):
                capture.validate_current_asset(path)

    def test_default_branch_schedule_targets_development_once(self):
        workflow = (ROOT / ".github/workflows/fantasy-data-daily.yml").read_text()
        self.assertIn('cron: "0 10 * * *"', workflow)
        self.assertIn("FANTASY_DATA_BRANCH: develop", workflow)
        self.assertIn("ref: ${{ env.FANTASY_DATA_BRANCH }}", workflow)
        self.assertIn('git push origin "HEAD:$FANTASY_DATA_BRANCH"', workflow)
        self.assertNotIn('git push origin "HEAD:$GITHUB_REF_NAME"', workflow)
        self.assertEqual(workflow.count("gh workflow run dev-site.yml"), 1)
        self.assertIn('if: steps.publish.outputs.changed == \'true\'', workflow)
        self.assertIn("python scripts/espn_injury_inputs.py", workflow)


if __name__ == "__main__":
    unittest.main()
