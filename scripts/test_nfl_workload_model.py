"""Check time boundaries, missingness and reconciled learned opportunities."""
import copy
import unittest

from nfl_usage_model import predict, STATS
from nfl_workload_model import estimate, feature_rows, observations, tree_predict
from test_nfl_usage_model import small_history

BASE = {'player_prior_games':3,'team_prior_games':8,'efficiency_prior':30,
        'scoring_opportunity_weight':.25,'opponent_weight':0,'neutral_weight':.15,'snap_weight':0}


def constant_model(value, weights):
    head = {'features':['prior_n'],'intercept':value,'learning_rate':.05,'trees':[]}
    return {'heads':{'carries':head,'targets':head},'weights':weights}


class WorkloadTests(unittest.TestCase):
    def test_future_games_cannot_change_usage_features(self):
        h = small_history()
        features = h.features(2025,2)
        p = features['players'][0]
        before = observations(h,p,2025,2)
        for rows in (*h.player.values(),*h.teams.values()):
            rows.append({**rows[-1],'week':2,'carries':99,'targets':99,'offense_pct':1})
            rows.append({**rows[-1],'week':3,'carries':999,'targets':999,'offense_pct':1})
        self.assertEqual(before,observations(h,p,2025,2))

    def test_missing_snap_and_recorded_zero_are_different(self):
        h = small_history()
        p = h.features(2025,2)['players'][0]
        h.player[p['id']] = []
        h.snap = {}
        missing = observations(h,p,2025,2)
        h.snap[(p['id'],'2025_01_DET_BUF')] = {'offense_pct':0,'offense_snaps':0,'team':p['team']}
        recorded = observations(h,p,2025,2)
        self.assertEqual(missing['last_team_game_observed'],0)
        self.assertEqual(recorded['last_team_game_observed'],1)
        self.assertEqual(recorded['last_team_snap'],0)

    def test_opponent_snap_cannot_become_current_team_role(self):
        h = small_history()
        p = h.features(2025,2)['players'][0]
        h.player[p['id']] = []
        h.snap = {(p['id'],'2025_01_DET_BUF'):{'offense_pct':1,'team':'OTHER'}}
        self.assertEqual(observations(h,p,2025,2)['last_team_game_observed'],0)

    def test_transfer_keeps_observations_but_marks_old_team(self):
        h = small_history()
        p = h.features(2025,2)['players'][0]
        for r in h.player[p['id']]:
            r['team'] = 'OLD'
        obs = observations(h,p,2025,2)
        self.assertEqual(obs['changed_team'],1)
        self.assertEqual(obs['recent_old_team_fraction'],1)
        self.assertEqual(obs['last_team_game_observed'],0)

    def test_zero_weight_is_exact_control(self):
        f = small_history().features(2025,2)
        expected = predict(f,BASE)
        actual = predict(f,{**BASE,'workload_model':constant_model(.2,{'carries':0,'targets':0})})
        self.assertEqual(expected,actual)

    def test_learned_roles_preserve_team_totals_and_unavailable_zeros(self):
        f = small_history().features(2025,2)
        f['players'][0]['unavailable'] = True
        c = {**BASE,'workload_model':constant_model(.12,{'carries':.5,'targets':.5})}
        out = predict(f,c)
        for club,budget in out['team_workload_budgets'].items():
            rows = [p['stat_projection'] for p in out['players'] if p['team'] == club]
            for key in ('attempts','carries','targets','passing_tds','rushing_tds'):
                self.assertAlmostEqual(sum(r[key] for r in rows),budget[key],places=3)
            for a,b in (('passing_yards','receiving_yards'),('completions','receptions')):
                self.assertAlmostEqual(sum(r[a] for r in rows),sum(r[b] for r in rows),places=3)
        unavailable = next(p for p in out['players'] if p['id'] == f['players'][0]['id'])
        self.assertFalse(any(unavailable['stat_projection'].values()))

    def test_features_have_no_player_identity_or_actual_scores(self):
        f = small_history().features(2025,2)
        vectors = feature_rows(f,BASE)
        for row in vectors.values():
            self.assertTrue(all(isinstance(v,(float,int)) for v in row.values()))
            self.assertFalse({'id','name','actual','projected_points','baseline'} & row.keys())

    def test_missing_usage_and_qbs_retain_previous_role(self):
        f = small_history().features(2025,2)
        p = next(p for p in f['players'] if p['position'] == 'TE')
        p['workload_observations']['last_team_game_observed'] = 0
        estimated = estimate(f,{**BASE,'workload_model':constant_model(0,{'carries':1,'targets':1})})
        self.assertEqual(estimated[p['id']],{})
        for p in f['players']:
            if p['position'] == 'QB':
                self.assertEqual(estimated[p['id']],{})

    def test_exported_tree_float32_boundary(self):
        tree = [[0,1.00000001,1,2,0],[-2,0,-1,-1,3],[-2,0,-1,-1,5]]
        model = {'features':['x'],'intercept':2,'learning_rate':.5,'trees':[tree]}
        self.assertEqual(tree_predict(model,{'x':1.00000002}),3.5)
        self.assertEqual(tree_predict(model,{'x':1.01}),4.5)


if __name__ == '__main__':
    unittest.main()
