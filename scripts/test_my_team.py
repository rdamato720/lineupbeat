#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_chrome_store_bundle
import build_my_team
from espn_my_team_adapter import FLEX_ALLOWED, adapt_espn_payload
from my_team_adapter import classify_projection_gap, validate_normalized_league


def raw_espn() -> dict:
    return {
        "provider": "espn",
        "league": {
            "id": "123", "name": "Test League", "season": 2026,
            "scoringSettings": {"receptionPoints": 0.5},
            "cookie": "must-not-survive",
        },
        "team": {"id": "7", "name": "Test Team", "manager": "Private Manager"},
        "password": "must-not-survive",
        "sessionToken": "must-not-survive",
        "roster": [
            {"providerPlayerId": "1", "name": "Travis Etienne Jr.", "team": "NO",
             "position": "RB", "lineupSlot": "RB"},
            {"providerPlayerId": "2", "name": "Bench Receiver", "team": "BUF",
             "position": "WR", "lineupSlot": "BE"},
            {"providerPlayerId": "3", "name": "Bills D/ST", "team": "BUF",
             "position": "D/ST", "lineupSlot": "D/ST"},
            {"providerPlayerId": "4", "name": "Reserve Back", "team": "NYJ",
             "position": "RB", "lineupSlot": "IR"},
        ],
    }


class NeutralContractTests(unittest.TestCase):
    def test_contract_accepts_a_non_espn_provider_without_provider_code(self):
        payload = adapt_espn_payload(raw_espn())
        payload["provider"] = "future-provider"
        payload["connectionType"] = "read_only_api"
        self.assertEqual(validate_normalized_league(payload), [])

    def test_contract_requires_explicit_unresolved_reason(self):
        payload = adapt_espn_payload(raw_espn())
        player = payload["roster"]["starters"][1]
        self.assertEqual(player["matchStatus"], "unsupported_position")
        player["unresolvedReason"] = None
        errors = validate_normalized_league(payload)
        self.assertTrue(any(error.path.endswith("unresolvedReason") for error in errors))

    def test_weekly_call_thresholds_are_deterministic(self):
        self.assertEqual(classify_projection_gap(10.0, 9.9), "Toss-Up")
        self.assertEqual(classify_projection_gap(20.0, 19.4), "Lean")
        self.assertEqual(classify_projection_gap(20.0, 17.5), "Edge")
        self.assertEqual(classify_projection_gap(20.0, 15.0), "Strong Edge")


class ESPNAdapterTests(unittest.TestCase):
    def test_espn_adapter_groups_roster_and_preserves_scoring(self):
        payload = adapt_espn_payload(raw_espn())
        self.assertEqual(payload["league"]["scoring"]["format"], "half_ppr")
        self.assertEqual([len(payload["roster"][key]) for key in ("starters", "bench", "reserve")], [2, 1, 1])
        self.assertEqual(payload["roster"]["starters"][1]["matchStatus"], "unsupported_position")
        self.assertIn("not supported", payload["roster"]["starters"][1]["unresolvedReason"])

    def test_private_and_credential_fields_are_dropped(self):
        encoded = json.dumps(adapt_espn_payload(raw_espn()))
        for forbidden in ("Private Manager", "must-not-survive", "password", "sessionToken", "cookie"):
            self.assertNotIn(forbidden, encoded)

    def test_documented_flex_labels_have_only_explicit_eligibility(self):
        self.assertEqual(FLEX_ALLOWED, {
            "FLEX": ["RB", "WR", "TE"],
            "RB/WR/TE": ["RB", "WR", "TE"],
            "WR/RB/TE": ["RB", "WR", "TE"],
            "RB/WR": ["RB", "WR"],
            "WR/RB": ["RB", "WR"],
            "WR/TE": ["WR", "TE"],
            "RB/TE": ["RB", "TE"],
            "OP": ["QB", "RB", "WR", "TE"],
            "SUPERFLEX": ["QB", "RB", "WR", "TE"],
        })
        raw = raw_espn()
        raw["roster"][0]["lineupSlot"] = "UNKNOWN FLEX"
        payload = adapt_espn_payload(raw)
        unknown = next(slot for slot in payload["startingLineupSlots"]
                       if slot["slotId"] == "UNKNOWN FLEX")
        self.assertEqual(unknown["allowedPositions"], [])


class MyTeamArtifactTests(unittest.TestCase):
    def test_public_model_is_redacted_and_has_honest_limits(self):
        model = build_my_team.public_model()
        self.assertGreaterEqual(len(model["players"]), 350)
        self.assertEqual(len({row["team"] for row in model["players"]}), 32)
        self.assertEqual(model["supportedPositions"], ["QB", "RB", "WR", "TE"])
        self.assertEqual(model["limitations"]["dstModel"], "unsupported; no projection is guessed")
        self.assertFalse(model["limitations"]["predictiveLiftClaim"])
        self.assertFalse(model["limitations"]["independentCorroboration"])
        self.assertEqual(model["limitations"]["matchupContext"], "2025 prior-season context")
        self.assertNotIn("history", model["players"][0])
        self.assertNotIn("adp", model["players"][0])

    def test_page_exposes_three_working_local_providers_and_privacy_controls(self):
        page = build_my_team.render_page(build_my_team.public_model())
        self.assertIn("Connect Fantasy extension", page)
        self.assertIn("Disconnect &amp; clear", page)
        self.assertIn("Roster data never leaves this browser", page)
        self.assertEqual(page.count("Supported connection"), 3)
        self.assertIn("<h3>Yahoo</h3>", page)
        self.assertIn("<h3>CBS</h3>", page)
        self.assertNotIn("Cloudflare Web Analytics", page)
        self.assertIn('name="robots" content="noindex,nofollow"', page)
        self.assertIn('id="mt-demo"', page)
        self.assertIn("hidden>Load reviewer demo roster", page)

    def test_page_uses_homepage_product_examples_and_3d_art(self):
        page = build_my_team.render_page(build_my_team.public_model())
        self.assertIn("YOUR ROSTER.", page)
        self.assertIn("IN FOCUS.", page)
        self.assertIn('class="mt-preview-window"', page)
        self.assertIn('id="examples"', page)
        self.assertIn("See when the edge is real.", page)
        self.assertIn("See the whole team—not just one comparison.", page)
        self.assertGreaterEqual(page.count("/assets/homepage/"), 5)
        self.assertEqual(page.count('id="mt-connect"'), 1)
        self.assertNotIn('id="mt-team"', page)

    def test_private_team_dashboard_is_separate_from_the_landing_page(self):
        dashboard = build_my_team.render_team_page()
        runtime = (ROOT / "my-team" / "my-team.js").read_text()
        self.assertIn('data-my-team-view="dashboard"', dashboard)
        self.assertIn('id="mt-team"', dashboard)
        self.assertIn('id="mt-roster"', dashboard)
        self.assertIn('href="/my-team/"', dashboard)
        self.assertIn('class="mt-dashboard-header"', dashboard)
        self.assertIn('class="mt-dashboard-window"', dashboard)
        self.assertIn('class="mt-dashboard-bar"', dashboard)
        self.assertIn('class="mt-dashboard-actions"', dashboard)
        self.assertIn('/assets/homepage/team-3d.png', dashboard)
        self.assertIn('<h2>Lineup comparisons</h2>', dashboard)
        self.assertIn('<h2>Roster</h2>', dashboard)
        self.assertNotIn('ROSTER COMMAND CENTER', dashboard)
        self.assertNotIn('mt-dashboard-visual', dashboard)
        self.assertIn("location.replace('/my-team/team/')", runtime)
        self.assertIn("if(!dashboard){openDashboard();return}", runtime)

    def test_build_writes_public_model_support_and_validated_development_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            stale = site / "my-team" / "lineupbeat-espn-extension.zip"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"obsolete public package")
            build_my_team.build(
                site, build_chrome_store_bundle.DEVELOPMENT_ORIGIN
            )
            self.assertTrue((site / "my-team" / "index.html").exists())
            self.assertTrue((site / "my-team" / "team" / "index.html").exists())
            self.assertTrue((site / "my-team" / "extension" / "index.html").exists())
            privacy = site / "my-team" / "extension" / "privacy" / "index.html"
            self.assertTrue(privacy.exists())
            package = site / "my-team" / "lineupbeat-espn-extension.zip"
            self.assertTrue(package.exists())
            with zipfile.ZipFile(package) as archive:
                packaged = json.loads(archive.read("manifest.json"))
                worker = archive.read("background.js").decode()
                content = archive.read("content.js").decode()
                self.assertEqual(packaged["version"], "0.5.0")
                self.assertEqual(len(archive.namelist()), 12)
                self.assertEqual(
                    archive.namelist(),
                    list(build_my_team.build_chrome_store_bundle.RUNTIME_FILES),
                )
                self.assertIn(
                    "MY_TEAM_ORIGIN = 'https://lineupbeat-dev.pages.dev'", worker
                )
                self.assertIn(
                    "MY_TEAM_ORIGIN = 'https://lineupbeat-dev.pages.dev'", content
                )
            model = json.loads((site / "data" / "my-team-week1.json").read_text())
            self.assertEqual(model["schemaVersion"], "lineupbeat-my-team-week1-v1")
            self.assertIn("chrome.storage.local", privacy.read_text())
            self.assertIn("No automatic private-data upload", privacy.read_text())

    def test_extension_has_minimal_permissions_and_exact_site_access(self):
        manifest = json.loads((ROOT / "extensions" / "lineupbeat-espn" / "manifest.json").read_text())
        self.assertEqual(manifest["permissions"], ["storage"])
        self.assertEqual(manifest["host_permissions"],
                         ["https://lm-api-reads.fantasy.espn.com/*"])
        self.assertEqual(manifest["content_scripts"][0]["matches"],
                         ["https://fantasy.espn.com/football/*"])
        self.assertEqual(manifest["content_scripts"][0]["js"],
                         ["espn-roster-parser.js", "espn-history-parser.js", "content.js"])
        self.assertEqual(manifest["content_scripts"][1]["matches"],
                         ["https://football.fantasysports.yahoo.com/f1/*"])
        self.assertEqual(manifest["content_scripts"][1]["js"], ["yahoo-roster-parser.js", "content.js"])
        self.assertEqual(manifest["content_scripts"][2]["matches"],
                         ["https://*.football.cbssports.com/*", "https://www.cbssports.com/fantasy/football/*"])
        self.assertEqual(manifest["content_scripts"][3]["matches"],
                         ["https://lineupbeat-dev.pages.dev/my-team/*"])
        self.assertEqual(manifest["content_scripts"][4]["matches"],
                         ["https://lineupbeat-dev.pages.dev/league-history/*"])
        self.assertEqual(manifest["content_scripts"][5]["matches"], [
            "https://lineupbeat.com/my-team/*",
            "https://lineupbeat.com/league-history/*",
            "https://www.lineupbeat.com/my-team/*",
            "https://www.lineupbeat.com/league-history/*",
        ])
        encoded = json.dumps(manifest)
        self.assertNotIn("cookies", encoded)
        for forbidden in ("https://lineupbeat-dev.pages.dev/*", "https://lineupbeat.com/*",
                          "https://www.lineupbeat.com/*", "localhost", "127.0.0.1"):
            self.assertNotIn(forbidden, encoded)
        content = (ROOT / "extensions" / "lineupbeat-espn" / "content.js").read_text()
        self.assertNotIn("fetch(", content)
        self.assertNotIn("XMLHttpRequest", content)
        self.assertIn("Save roster locally for My Team", content)
        self.assertIn("Open My Team", content)
        self.assertIn("LB_SAVE_REVIEW_DEMO_ROSTER", content)
        self.assertIn("Copy safe diagnostics", content)
        self.assertIn("clipboard.writeText", content)
        self.assertNotIn("Send roster to Lineup Beat", content)

    def test_local_copy_never_implies_server_upload(self):
        guide = build_my_team.render_extension_guide()
        privacy = build_my_team.render_extension_privacy()
        readme = (ROOT / "extensions" / "lineupbeat-espn" / "README.md").read_text()
        for text in (guide, readme):
            self.assertIn("roster", text.lower())
            self.assertNotIn("Send roster to Lineup Beat", text)
        self.assertIn("Download version 0.5.0", guide)
        self.assertIn("chrome.storage.local", privacy)
        self.assertIn("No provider password, cookie value, session token", privacy)
        self.assertIn("Private provider data is not uploaded", privacy)
        self.assertIn("Clear each copy", privacy)

    def test_suffix_terminal_punctuation_regression(self):
        source = (ROOT / "scripts" / "build_decision_room.py").read_text()
        self.assertIn("terminalName=p=>terminalText(safe(p.name))", source)
        self.assertIn("ahead of ${terminalName(r)}", source)
        self.assertNotIn("ahead of ${safe(r.name)}.", source)

    def test_decision_copy_escapes_names_once(self):
        source = (ROOT / "my-team" / "my-team.js").read_text()
        self.assertIn("const reason=`${bench.name} projects", source)
        self.assertIn("${escape(reason)}</p>", source)
        self.assertNotIn("const reason=`${escape(bench.name)}", source)

    def test_matched_cards_use_canonical_identity_and_label_provider_status(self):
        source = (ROOT / "my-team" / "my-team.js").read_text()
        self.assertIn("LineupBeatLeagueAdapter.displayIdentity(player)", source)
        self.assertIn("Q:'Questionable'", source)
        self.assertIn("modelTag||player&&player.providerStatus", source)
        self.assertIn("modelTag?'Current injury status':'Provider status'", source)
        self.assertNotIn("<h3>${escape(player.name)}</h3>", source)

    def test_connected_value_layer_precedes_roster_and_uses_validated_model_fields(self):
        page = build_my_team.render_team_page()
        source = (ROOT / "my-team" / "my-team.js").read_text()
        self.assertLess(page.index('id="mt-outlook"'), page.index('id="mt-roster"'))
        self.assertLess(page.index('id="mt-decisions"'), page.index('id="mt-roster"'))
        for value in ("projectedPoints", "expectedOpportunity", "opponent",
                      "matchupFactor", "Player details →",
                      "Your strongest lineup is already set"):
            self.assertIn(value, source)
        self.assertNotIn("matched identity", source)

    def test_pollard_tuten_candidate_comes_from_public_model(self):
        players = {row["name"]: row for row in build_my_team.public_model()["players"]}
        release = json.loads((
            ROOT / "data/week1/2026/v1.2/nfl_week1_projections.json"
        ).read_text())
        source = {row["name"]: row for row in release["players"]}
        pollard = players["Tony Pollard"]
        tuten = players["Bhayshul Tuten"]
        self.assertEqual(
            pollard["formats"]["half_ppr"]["projectedPoints"],
            source["Tony Pollard"]["formats"]["half_ppr"]["projected_points"],
        )
        self.assertEqual(
            tuten["formats"]["half_ppr"]["projectedPoints"],
            source["Bhayshul Tuten"]["formats"]["half_ppr"]["projected_points"],
        )


if __name__ == "__main__":
    unittest.main()
