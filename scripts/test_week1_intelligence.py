#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import statistics
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_decision_room
import build_week1_intelligence as model
import college_decision_data
import decision_data
from test_nfl_workload_allocation import NFLWorkloadAllocationTests


IDENTITY_VARIANTS = {
    "James Cook III": ("James Cook", "00-0037248", "BUF", "RB"),
    "Travis Etienne Jr.": ("Travis Etienne", "00-0036973", "NO", "RB"),
    "Michael Pittman Jr.": ("Michael Pittman", "00-0036252", "PIT", "WR"),
    "Kyle Pitts Sr.": ("Kyle Pitts", "00-0036970", "ATL", "TE"),
    "Aaron Jones Sr.": ("Aaron Jones", "00-0033293", "MIN", "RB"),
    "Tre' Harris": ("Tre’ Harris", "00-0040727", "LAC", "WR"),
    "KC Concepcion": ("K.C. Concepcion", "00-0041547", "CLE", "WR"),
    "Mike Washington Jr.": ("Mike Washington", "00-0040878", "LV", "RB"),
    "Omar Cooper Jr.": ("Omar Cooper", "00-0041511", "NYJ", "WR"),
    "Brian Robinson Jr.": ("Brian Robinson", "00-0037746", "ATL", "RB"),
    "Oronde Gadsden": ("Oronde Gadsden II", "00-0040189", "LAC", "TE"),
}


class NFLWeek1ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = decision_data.load_weekly()
        cls.backtest = json.loads((model.OUTPUT / "nfl_backtest_2025.json").read_text())
        cls.provenance = json.loads((model.OUTPUT / "provenance.json").read_text())

    def test_schedule_and_identity_stop_conditions_pass(self):
        self.assertGreaterEqual(len(self.payload["players"]), 350)
        self.assertEqual(len(self.payload["excluded_players"]), 0)
        self.assertEqual(len({p["id"] for p in self.payload["players"]}),
                         len(self.payload["players"]))
        self.assertEqual(len({p["team"] for p in self.payload["players"]}), 32)
        self.assertEqual(len({p["game_id"] for p in self.payload["players"]}), 16)
        self.assertTrue(all(p["opponent"] and p["kickoff"] for p in self.payload["players"]))

    def test_full_projection_source_population_is_reported_honestly(self):
        population = self.payload["population"]
        player_count = len(self.payload["players"])
        withheld_count = len(self.payload["withheld_players"])
        self.assertEqual(population["projection_source"], player_count + withheld_count)
        self.assertEqual(population["identity_resolved"], population["projection_source"])
        self.assertEqual(population["identity_unresolved"], 0)
        self.assertEqual(population["ranked_production"], player_count)
        self.assertEqual(population["identity_resolved_not_ranked"], withheld_count)
        self.assertEqual(population["ranked_active_projected"], player_count)
        self.assertEqual(population["ranked_excluded"], 0)
        self.assertEqual(len(self.payload["unresolved_players"]), 0)

    def test_all_documented_identity_variants_resolve_deterministically(self):
        players = {player["name"]: player for player in self.payload["players"]}
        for source_name, (identity_name, player_id, team, position) in IDENTITY_VARIANTS.items():
            self.assertEqual(decision_data.normalize_player_name(source_name),
                             decision_data.normalize_player_name(identity_name))
            player = players[source_name]
            self.assertEqual((player["id"], player["team"], player["position"]),
                             (player_id, team, position), source_name)
            identity = player["identity_resolution"]
            self.assertEqual(identity["stable_gsis_id"], player_id)
            self.assertTrue(identity["roster_record"])
            self.assertTrue(identity["season_prior_stat_line"])

    def test_normalized_identity_index_fails_on_ambiguity(self):
        with self.assertRaisesRegex(ValueError, "ambiguous normalized identity"):
            decision_data.identity_index([
                {"player_id": "one", "full_name": "Example Player Jr.",
                 "team": "BUF", "position": "WR"},
                {"player_id": "two", "full_name": "Example Player",
                 "team": "BUF", "position": "WR"},
            ])

    def test_scoring_reconciles_from_components(self):
        for player in self.payload["players"]:
            for fmt, reception in (("ppr", 1), ("half_ppr", .5), ("non_ppr", 0)):
                self.assertEqual(
                    player["formats"][fmt]["projected_points"],
                    round(model.score(player["stat_projection"], reception), 1),
                    player["name"],
                )

    def test_weekly_values_are_not_season_values_divided_by_constant(self):
        season = model.trusted_season.workbook_rows()
        ratios = [p["formats"]["half_ppr"]["projected_points"] /
                  max(.1, season[model.trusted_season.identity_key(
                      p["name"], p["team"], p["position"]
                  )]["formats"]["half_ppr"])
                  for p in self.payload["players"]]
        self.assertGreater(statistics.pstdev(ratios), .005)
        self.assertIsNone(self.payload["methodology"]["season_total_divisor"])

    def test_current_offensive_depth_is_used_in_workload(self):
        players = {p["name"]: p for p in self.payload["players"]}
        self.assertEqual(players["Jalen Hurts"]["role"]["depth_rank"], 1)
        self.assertEqual(players["Jalen Hurts"]["role"]["workload_factor"], 1.0)
        self.assertEqual(model.depth_workload_factor("QB", 2), .18)
        for position in ("RB", "WR", "TE"):
            self.assertEqual(model.depth_workload_factor(position, 2), 1.0)
            self.assertEqual(model.depth_workload_factor(position, 3), 1.0)

    def test_reviewed_current_season_role_outweighs_prior_usage(self):
        season_share = .30
        historical_share = .10
        blended = model.blended_player_share(historical_share, season_share)
        self.assertAlmostEqual(blended, .25)
        self.assertLess(abs(blended - season_share),
                        abs(blended - historical_share))
        self.assertEqual(model.blended_player_share(None, season_share),
                         season_share)

    def test_backtest_is_leakage_safe_and_reports_baseline_by_position(self):
        self.assertEqual(self.backtest["future_rows_used"], 0)
        self.assertEqual(self.backtest["evaluation_type"], "proxy_context_adjustment_backtest")
        self.assertFalse(self.backtest["production_formula_reproduced"])
        self.assertIn("does not reproduce the deployed production formula", self.backtest["limitation"])
        self.assertGreater(self.backtest["predictions"], 5000)
        for position in model.POSITIONS:
            row = self.backtest["by_position"][position]
            self.assertGreater(row["predictions"], 0)
            self.assertGreater(row["proxy_mae"], 0)
            self.assertGreater(row["baseline_mae"], 0)

    def test_week1_boards_publish_same_ranks_and_projection_components(self):
        import build_nfl_week1_boards as boards
        for kind in ("rankings", "projections"):
            page = boards.render(self.payload, kind, ROOT / "site")
            self.assertIn(f'https://lineupbeat.com/nfl/week-1/{kind}/', page)
            embedded = json.loads(re.search(
                r'<script id="week1-data" type="application/json">(.*?)</script>',
                page, re.S).group(1))
            self.assertEqual(embedded["updated_at"], self.payload["updated_at"])
            by_id = {p["id"]: p for p in embedded["players"]}
            for player in self.payload["players"]:
                self.assertEqual(by_id[player["id"]]["formats"], player["formats"])
                row = re.search(r'<tr data-id="' + re.escape(player["id"]) +
                                r'">(.*?)</tr>', page, re.S).group(1)
                if kind == "projections":
                    values = re.findall(r'<td>(-?[0-9.]+)</td>', row)
                    self.assertEqual(values, [f'{player["stat_projection"][k]:.1f}'
                                              for k, _ in boards.STATS])
            self.assertEqual(len(by_id), len(self.payload["players"]))
            self.assertNotIn('bookmaker_key', page)

    def test_week1_player_images_and_pending_results(self):
        import build_nfl_week1_boards as boards
        for kind in ('rankings','projections'):
            page=boards.render(self.payload,kind,ROOT/'site')
            self.assertEqual(page.count('class="wb-photo"'),len(self.payload['players']))
            for player in self.payload['players']:
                row=re.search(r'<tr data-id="'+re.escape(player['id'])+r'">(.*?)</tr>',page,re.S).group(1)
                self.assertIn(html.escape(player.get('photo') or '/assets/player-placeholder.svg',quote=True),row)
            self.assertIn('COMING AFTER WEEK 1',page)
            self.assertIn('Barlow+Condensed',page)
            self.assertIn('this.onerror=null',page)
            self.assertNotIn('7.6',boards.PENDING_RESULTS)

    def test_week1_board_filters_keep_source_ranks_and_switch_scoring(self):
        import build_nfl_week1_boards as boards
        # Execute the shipped controller with a small DOM adapter. No browser
        # dependency or network; assertions use the actual weekly source.
        harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const els={};
for(const id of ['wb-format','wb-position','wb-team','wb-search','wb-count','wb-caption','wb-empty'])
 els[id]={value:id==='wb-format'?'half_ppr':'',listeners:{},addEventListener(n,f){this.listeners[n]=f;}};
const rows=source.players.map(p=>({dataset:{id:p.id},hidden:false,cells:{},querySelector(s){return this.cells[s]??={textContent:''};}}));
els['wb-rows']={rows:[...rows],appendChild(r){this.rows=this.rows.filter(x=>x!==r);this.rows.push(r);}};
els['week1-data']={textContent:JSON.stringify(source)};
vm.runInNewContext(process.argv[1],{document:{getElementById:id=>els[id]}});
const set=(id,value,event='change')=>{els[id].value=value;els[id].listeners[event]();};
set('wb-format','ppr');
let top=[...source.players].sort((a,b)=>a.formats.ppr.overall_rank-b.formats.ppr.overall_rank)[0];
assert.equal(els['wb-rows'].rows[0].dataset.id,top.id);
set('wb-position','RB');set('wb-team','NE');
let visible=rows.filter(r=>!r.hidden);
assert.equal(visible.length,source.players.filter(p=>p.position==='RB'&&p.team==='NE').length);
assert.ok(visible.length>0);
for(const r of visible){let p=source.players.find(p=>p.id===r.dataset.id);assert.equal(r.cells['[data-col=rank]'].textContent,p.formats.ppr.position_rank);}
const selected=source.players.find(p=>p.id===visible[0].dataset.id);
set('wb-search',selected.name,'input');
assert.equal(rows.filter(r=>!r.hidden).length,1);
assert.equal(rows.find(r=>!r.hidden).cells['[data-col=points]'].textContent,selected.formats.ppr.projected_points.toFixed(1));
set('wb-search','no such player','input');assert.equal(rows.filter(r=>!r.hidden).length,0);assert.equal(els['wb-empty'].hidden,false);
set('wb-search','','input');set('wb-position','');set('wb-team','');set('wb-format','non_ppr');
assert.equal(rows.filter(r=>!r.hidden).length,source.players.length);
for(const r of rows){let p=source.players.find(p=>p.id===r.dataset.id);assert.equal(r.cells['[data-col=points]'].textContent,p.formats.non_ppr.projected_points.toFixed(1));}
"""
        source = {"players": [{k:p[k] for k in ("id", "name", "team", "position", "formats")}
                              for p in self.payload["players"]]}
        result = subprocess.run(["node", "-e", harness, boards.JS], input=json.dumps(source),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_provider_and_license_record_are_honest(self):
        calls = self.provenance["provider_requests"]
        self.assertEqual(calls["odds"], 1)
        # Props are captured only for games inside the configured horizon;
        # a full Week 1 game consensus does not require 16 prop requests.
        self.assertIs(type(calls["player_props"]), int)
        self.assertGreater(calls["player_props"], 0)
        self.assertLessEqual(calls["player_props"],
                             self.provenance["market_coverage"]["games"])
        self.assertEqual(calls["model_api"], 0)
        self.assertIsNone(calls["cost_usd"])
        self.assertGreaterEqual(calls["provider_credits_used"], 0)
        self.assertGreaterEqual(calls["provider_credits_remaining"], 0)
        self.assertEqual(calls["provider_credits_used"]
                         + calls["provider_credits_remaining"], 500)
        self.assertFalse(calls["raw_market_data_public"])
        if self.payload.get("sources", {}).get("injuries", {}).get("updated_at"):
            self.assertEqual(calls["injuries"], 1)
        self.assertEqual(len(self.provenance["assets"]), 11)
        self.assertEqual(self.provenance["license_review"]["spdx"], "CC-BY-4.0")
        self.assertTrue(self.provenance["license_review"]["attribution_required"])
        self.assertFalse(self.provenance["license_review"]["share_alike"])

    def test_private_market_consensus_is_bounded_and_redacted(self):
        self.assertEqual(self.payload["schema_version"], "lineupbeat-nfl-week1-v1.2")
        self.assertEqual(self.provenance["market_coverage"]["games"], 16)
        self.assertEqual(self.provenance["market_coverage"]["teams"], 32)
        adjusted = [p for p in self.payload["players"]
                    if p["market"]["player_components"]]
        self.assertGreater(len(adjusted), 0)
        self.assertEqual(
            len(adjusted),
            self.provenance["market_coverage"]["qualified_player_projections"],
        )
        self.assertTrue(all(p["data_coverage"]["betting_market"]
                            for p in self.payload["players"]))
        self.assertTrue(all(p["market"]["quality"] == "HIGH"
                            and p["market"]["game_book_count"] >= 3
                            and not p["market"]["raw_lines_public"]
                            for p in self.payload["players"]))
        for player in adjusted:
            market = player["market"]
            self.assertEqual(set(market["consensus_lines"]),
                             set(market["player_components"]))
            self.assertTrue(all(isinstance(line, (int, float))
                                for line in market["consensus_lines"].values()))
        public = json.dumps(self.payload)
        for forbidden in ("bookmaker_key", "american_price", "apiKey",
                          "DraftKings", "FanDuel", "Caesars"):
            self.assertNotIn(forbidden, public)

    def test_nfl_product_surfaces_weekly_evidence_and_missing_inputs(self):
        html = build_decision_room.render(self.payload)
        for text in ("Our Week 1 projection", "What the market says", "Opponent matchup",
                     "Expected opportunity", "Availability", "Available sources",
                     "How the numbers compare", "Betting-market context included", "Pass TDs",
                     "Rush yards"):
            self.assertIn(text, html)
        injuries_ready = bool(
            self.payload.get("sources", {}).get("injuries", {}).get("updated_at")
        )
        self.assertIn(
            "Questionable and doubtful players keep their projected points unless confirmed unavailable" if injuries_ready
            else "Current Week 1 injury status is unavailable",
            html,
        )
        self.assertNotIn("Signals are capped and blended at 25%", html)
        self.assertNotIn("zero odds requests were made", html)
        self.assertNotIn("Odds were not requested", html)
        self.assertIn("Market included", html)
        self.assertNotIn("Weekly lineup decisions will become available", html)
        self.assertNotIn("D.sources.projections", html)
        self.assertIn("no D/ST projection is included", html)
        self.assertEqual(self.payload["limitations"]["dst_model"],
                         "unavailable; model population is QB/RB/WR/TE only")
        self.assertFalse(self.payload["limitations"]["predictive_lift_claim"])
        self.assertIn("private multi-book consensus covers 16 games",
                      self.payload["limitations"]["sportsbook_evidence"])
        self.assertEqual(self.payload["limitations"]["matchup_context"],
                         "2025 prior-season context")

    def test_current_injury_policy_is_conservative_when_available(self):
        if not self.payload.get("sources", {}).get("injuries", {}).get("updated_at"):
            self.skipTest("the committed release predates current injury capture")
        tagged = []
        unavailable = []
        for player in self.payload["players"]:
            availability = player["availability"]
            self.assertNotIn("comment", availability)
            if availability["status"] in {"Questionable", "Doubtful"}:
                tagged.append(player)
                self.assertEqual(availability["projection_factor"], 1.0)
                self.assertFalse(availability["projection_adjusted"])
            if availability["status"] in {"Out", "Injured Reserve", "Suspension"}:
                unavailable.append(player)
                self.assertEqual(availability["projection_factor"], 0.0)
                self.assertTrue(availability["projection_adjusted"])
                self.assertTrue(all(
                    row["projected_points"] == 0.0
                    for row in player["formats"].values()
                ))
        self.assertTrue(tagged)

    def test_browser_engine_uses_weekly_call_and_flip_boundaries(self):
        html = build_decision_room.render(self.payload)
        self.assertIn('if(weekly){if(g<=.5||q<=3)return"Toss-Up"', html)
        self.assertIn('boundary=weekly?Math.max(.5,reference*.03)', html)
        self.assertIn("recommendationsAuthorized=D.recommendation_state?.enabled===true", html)
        self.assertIn("This compares projected points; it is not a start/sit recommendation.", html)
        self.assertIn("Higher projection: ${safe(w.name)}", html)


class CollegeWeek1EnrichmentTests(unittest.TestCase):
    def test_all_active_reconciled_projections_and_components_are_preserved(self):
        payload = college_decision_data.load_week1()
        self.assertEqual(len(payload["players"]), 2205)
        config = json.loads((ROOT / "data" / "college" / "config.json").read_text())
        source = (ROOT / "data" / "college" /
                  "2026/week-1/v1.1" /
                  "college_week1_site_projections_2026.json")
        raw = json.loads(source.read_text())
        by_id = {p["id"]: p for p in payload["players"]}
        for row in raw["players"]:
            player = by_id[row["id"]]
            self.assertEqual(player["formats"]["yahoo"]["projected_points"], row["pts"])
            self.assertEqual(player["expected_opportunity"]["carries"], row["rushAtt"])
            self.assertEqual(player["expected_opportunity"]["receptions"], row["rec"])
            self.assertEqual(player["implied_total"], row["impliedTotal"])

    def test_college_market_copy_is_delayed_consensus_context(self):
        payload = college_decision_data.load_week1()
        self.assertEqual(payload["market"]["state"],
                         "available_delayed_market_context")
        self.assertEqual(payload["market"]["data_delay_seconds"], 30)
        self.assertEqual(payload["market"]["player_coverage"]["playersWithNumericEvidence"], 112)
        self.assertIn("Sportsbook environment",
                      build_decision_room.college_decision_room.JS)
        self.assertIn("57 of 65 teams",
                      build_decision_room.college_decision_room.JS)
        self.assertIn("Expected opportunity", build_decision_room.college_decision_room.JS)


if __name__ == "__main__":
    unittest.main()
