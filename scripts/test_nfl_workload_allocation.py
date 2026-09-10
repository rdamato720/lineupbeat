import copy
import json
import unittest

import build_week1_intelligence as model
import nfl_workload_allocation as allocation
from repair_nfl_workloads import unblend


def player(name, position, **stats):
    values = {key: 0.0 for key in model.STAT_KEYS}
    values.update(stats)
    return {"id": name, "name": name, "team": "TEST", "position": position,
            "role": {"workload_factor": 1.0, "depth_rank": 1},
            "availability": {"projection_adjusted": False},
            "stat_projection": values,
            "formats": {fmt: {} for fmt in ("ppr", "half_ppr", "non_ppr")}}


class NFLWorkloadAllocationTests(unittest.TestCase):
    def fixture(self):
        players = [player("QB", "QB", attempts=20, passing_yards=140, passing_tds=1,
                          carries=4, rushing_yards=20),
                   player("RB", "RB", carries=12, rushing_yards=48, rushing_tds=.4,
                          targets=3, receptions=2, receiving_yards=12, receiving_tds=.1),
                   player("WR", "WR", targets=10, receptions=6, receiving_yards=80, receiving_tds=.5),
                   player("TE", "TE", targets=5, receptions=4, receiving_yards=40, receiving_tds=.4)]
        return {"players": players}, {"TEST": {"attempts": 30, "carries": 25, "targets": 28}}

    def test_conserves_opportunities_and_passing_identity(self):
        payload, budgets = self.fixture()
        allocation.finalize(payload, budgets)
        self.assertEqual(allocation.problems(payload["players"], budgets), [])
        stat = {p["name"]: p["stat_projection"] for p in payload["players"]}
        self.assertAlmostEqual(stat["QB"]["passing_yards"], 210)
        self.assertAlmostEqual(sum(s["receiving_yards"] for s in stat.values()), 210, places=2)
        self.assertAlmostEqual(sum(s["carries"] for s in stat.values()), 25, places=2)

    def test_out_player_returns_work_to_pool_and_stays_zero(self):
        payload, budgets = self.fixture()
        allocation.finalize(payload, budgets)
        wr = next(p for p in payload["players"] if p["name"] == "WR")
        te = next(p for p in payload["players"] if p["name"] == "TE")
        before = te["stat_projection"]["targets"]
        wr["availability"]["projection_adjusted"] = True
        allocation.finalize(payload)
        self.assertFalse(any(wr["stat_projection"].values()))
        self.assertGreater(te["stat_projection"]["targets"], before)
        self.assertEqual(allocation.problems(payload["players"], budgets), [])

    def test_repeated_availability_rebuild_is_byte_stable(self):
        payload, budgets = self.fixture()
        allocation.finalize(payload, budgets)
        before = json.dumps(payload, sort_keys=True)
        allocation.finalize(payload)
        self.assertEqual(before, json.dumps(payload, sort_keys=True))

    def test_no_available_receiver_fails_instead_of_losing_volume(self):
        payload, budgets = self.fixture()
        for p in payload["players"]:
            if p["position"] != "QB":
                p["availability"]["projection_adjusted"] = True
        with self.assertRaisesRegex(ValueError, "no available"):
            allocation.finalize(payload, budgets)

    def test_legacy_non_qb_depth_discount_is_removed_once(self):
        payload, budgets = self.fixture()
        wr = next(p for p in payload["players"] if p["name"] == "WR")
        wr["role"]["workload_factor"] = .5
        allocation.finalize(payload, budgets)
        self.assertEqual(wr["allocation_input"]["targets"], 20)
        allocation.finalize(payload)
        self.assertEqual(wr["allocation_input"]["targets"], 20)

    def test_sparse_promoted_back_gets_evidence_based_role(self):
        payload, budgets = self.fixture()
        reserve = player("reserve", "RB", carries=.1, rushing_yards=.4)
        reserve["role"]["depth_rank"] = 2
        payload["players"].append(reserve)
        prior = {"carry_share_by_rank": {"1": .55, "2": .2, "3": .02},
                 "yards_per_carry": 4.2, "tds_per_carry": .03}
        allocation.finalize(payload, budgets, prior)
        self.assertGreater(reserve["stat_projection"]["carries"], 1)
        self.assertIn("sparse_role_fallback", reserve["workload_allocation"])
        self.assertEqual(allocation.problems(payload["players"], budgets), [])

    def test_tampered_outputs_fail_even_if_audit_is_unchanged(self):
        payload, budgets = self.fixture()
        allocation.finalize(payload, budgets)
        bad = copy.deepcopy(payload)
        bad["players"][0]["stat_projection"]["receiving_yards"] += 10
        self.assertTrue(any("do not reconcile" in e for e in allocation.problems(bad["players"], budgets)))

    def test_small_efficiency_sample_does_not_dominate_prior(self):
        self.assertAlmostEqual(model.efficiency_rate(2, 4, 1, "carries"), 3.986)
        self.assertAlmostEqual(model.efficiency_rate(2, 4, 100, "carries"), 2.6)
        self.assertEqual(model.efficiency_rate(None, 4, 0, "carries"), 4)

    def test_saved_market_blend_can_be_replayed_without_provider(self):
        for value, line in ((100, 20), (100, 95), (100, 200), (.3, .5)):
            self.assertAlmostEqual(unblend(model.blend_market_component(value, line), line), value)

    def test_all_committed_teams_reconcile(self):
        payload = json.loads((model.OUTPUT / "nfl_week1_projections.json").read_text())
        self.assertEqual(len(payload["team_workload_budgets"]), 32)
        self.assertEqual(allocation.problems(payload["players"], payload["team_workload_budgets"]), [])


if __name__ == "__main__":
    unittest.main()
