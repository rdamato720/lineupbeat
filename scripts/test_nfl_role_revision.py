"""Causal/time-boundary checks for role and quarterback usage inputs."""
import copy
import json
import unittest

from nfl_role_candidate import History, predict
from test_nfl_usage_model import small_history
import build_nfl_week2 as builder


class RoleRevisionTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(builder.MODEL.read_text())['parameters']
        history = small_history()
        history.__class__ = History
        self.features = history.features(2025,2)

    def assert_conserved(self, result):
        for club, budget in result['team_workload_budgets'].items():
            stats = [p['stat_projection'] for p in result['players'] if p['team']==club]
            for field in ('attempts','carries','targets','passing_tds','rushing_tds'):
                self.assertAlmostEqual(sum(s[field] for s in stats),budget[field],delta=.002)
            for a,b in (('completions','receptions'),('passing_yards','receiving_yards'),('passing_tds','receiving_tds')):
                self.assertAlmostEqual(sum(s[a]-s[b] for s in stats),0,delta=.002)

    def test_new_team_prior_changes_role_without_changing_team_opportunities(self):
        p = next(p for p in self.features['players'] if p['id']=='BUFWR1')
        p['prior_n']=12;p['same_team_prior_n']=0
        for key in p['shares']['prior']:
            p['shares']['prior'][key]=.1 if key!='carries' else 0
        ordinary = predict(self.features,self.config)
        changed = predict(self.features,{**self.config,'transfer_prior_factor':.25})
        before = next(p for p in ordinary['players'] if p['id']=='BUFWR1')
        after = next(p for p in changed['players'] if p['id']=='BUFWR1')
        self.assertGreater(after['stat_projection']['targets'],before['stat_projection']['targets'])
        self.assertEqual(changed['team_workload_budgets'],ordinary['team_workload_budgets'])
        self.assert_conserved(changed)

    def test_current_qb_volume_affects_passing_budget_with_fixed_total_plays(self):
        p = next(p for p in self.features['players'] if p['id']=='BUFQB1')
        p['qb_full_games']={'n':10,'attempts':38,'total_attempts':380,'passing_tds':25}
        ordinary = predict(self.features,self.config)
        changed = predict(self.features,{**self.config,'qb_volume_weight':.5,'qb_td_weight':.5})
        b = ordinary['team_workload_budgets']['BUF'];a = changed['team_workload_budgets']['BUF']
        self.assertGreater(a['attempts'],b['attempts'])
        self.assertAlmostEqual(a['attempts']+a['carries'],b['attempts']+b['carries'])
        self.assert_conserved(changed)

    def test_unavailable_qb_cannot_supply_the_replacement_passing_environment(self):
        config = {**self.config,'qb_volume_weight':.75,'qb_td_weight':.75}
        p = next(p for p in self.features['players'] if p['id']=='BUFQB1')
        p['unavailable']=True
        first = predict(self.features,config)
        p['qb_full_games']={'n':100,'attempts':900,'total_attempts':90000,'passing_tds':80000}
        second = predict(self.features,config)
        self.assertEqual(first['team_workload_budgets'],second['team_workload_budgets'])
        self.assertFalse(any(next(p for p in second['players'] if p['id']=='BUFQB1')['stat_projection'].values()))
        self.assert_conserved(second)

    def test_future_plays_cannot_change_role_or_full_participation_inputs(self):
        h = small_history();h.__class__ = History
        before = copy.deepcopy(h.features(2025,2))
        for pid,rows in h.player.items():
            row={**rows[-1],'week':2,'season':2025,'attempts':500,'passing_tds':100,'offense_pct':1}
            rows.append(row)
        h._features.clear()
        self.assertEqual(before,h.features(2025,2))


if __name__=='__main__':unittest.main()
