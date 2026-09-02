#!/usr/bin/env python3
"""Final model integrity gates. Local fixtures only."""
import copy
import json
import unittest
from collections import Counter
import build_nfl_season_final as model

class FinalSeasonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs=json.loads((model.OUTPUT/'inputs.json').read_text())
        cls.candidate,cls.qb,cls.te=model.build(cls.inputs)
        cls.players=cls.candidate['players'];cls.by_id={p['gsis_id']:p for p in cls.players}
        cls.rankings=model.ranks(cls.players)

    def test_identity_active_population(self):
        self.assertEqual(len(self.by_id),505)
        self.assertEqual(Counter(p['position'] for p in self.players),{'QB':87,'RB':114,'WR':183,'TE':121})
        self.assertTrue(all(p['status']=='ACT' and p['active_for_projection'] for p in self.players))
        self.assertEqual(len({p['url'] for p in self.players}),505)
        self.assertEqual(len({p['player_id'] for p in self.players}),505)

    def test_duplicate_identity_refused(self):
        inputs=copy.deepcopy(self.inputs);inputs['players'][0]=copy.deepcopy(inputs['players'][1])
        with self.assertRaises(ValueError):model.build(inputs)

    def test_32_team_reconciliation_and_nonnegative_bounds(self):
        report=model.validate(self.candidate,self.inputs)
        self.assertEqual(report['status'],'PASS',report['failures']);self.assertEqual(len(report['teams']),32)
        for team in report['teams']:
            for metric,value in team['differences'].items():self.assertLessEqual(abs(value),.01,(team['team'],metric))
            for room in team['position_room_shares'].values():self.assertTrue(all(0<=s<=1.000001 for s in room.values()))
        for p in self.players:
            s=p['stat_projection'];self.assertLessEqual(s['completions'],s['attempts']+.00001);self.assertLessEqual(s['receptions'],s['targets']+.00001)

    def test_all_scoring_formats_exact(self):
        for p in self.players:
            for fmt,weight in model.FORMATS.items():self.assertEqual(p['formats'][fmt],model.score(p['stat_projection'],weight))
            self.assertAlmostEqual(p['formats']['ppr']-p['formats']['non_ppr'],p['stat_projection']['receptions'],places=8)

    def test_rank_order_unique_complete(self):
        for fmt in model.FORMATS:
            rows=self.rankings['formats'][fmt]['rows']
            self.assertEqual({r['gsis_id'] for r in rows},set(self.by_id))
            self.assertEqual([r['overall_rank'] for r in rows],list(range(1,506)))
            self.assertEqual([r['fantasy_points'] for r in rows],sorted([r['fantasy_points'] for r in rows],reverse=True))
            for pos in ('QB','RB','WR','TE'):
                group=[r for r in rows if r['position']==pos];self.assertEqual([r['position_rank'] for r in group],list(range(1,len(group)+1)))

    def test_qb_absence_and_conditional_role_separated(self):
        features={x['base']['gsis_id']:x for x in self.inputs['players']}
        for audit in self.qb:
            self.assertFalse(audit['sole_qb_fallback'])
            self.assertAlmostEqual(sum(audit['season_shares'].values()),1)
            self.assertGreaterEqual(audit['conditional_primary_share'],.98)
            self.assertLessEqual(audit['conditional_primary_share'],1)
            room=copy.deepcopy([p for p in self.players if p['team']==audit['team'] and p['position']=='QB'])
            primary=next(p for p in room if p['gsis_id']==audit['primary_id'])
            primary['projected_games_active']-=1
            shares,other=model.qb_distribution(room,features,self.inputs['team_budgets'][audit['team']])
            self.assertEqual(other['conditional_primary_share'],audit['conditional_primary_share'])
            self.assertLess(shares[audit['primary_id']],audit['season_shares'][audit['primary_id']])
            self.assertTrue(all(share>0 for share in audit['season_shares'].values()))

    def test_availability_and_approved_rb_wr_preserved(self):
        for source in self.inputs['players']:
            old=source['base'];new=self.by_id[old['gsis_id']]
            self.assertEqual(old['projected_games_active'],new['projected_games_active'])
            self.assertEqual(old['availability_rate'],new['availability_rate'])
            if old['position'] in ('RB','WR'):self.assertEqual(old['stat_projection'],new['stat_projection'])
            for metric,rate in new['conditional_per_active_game'].items():self.assertAlmostEqual(rate*new['projected_games_active'],new['stat_projection'][metric],places=8)

    def test_qb_rush_independent_and_decomposed(self):
        for source in self.inputs['players']:
            old=source['base'];new=self.by_id[old['gsis_id']]
            if old['position']!='QB':continue
            for metric in ('carries','rushing_yards','rushing_tds'):self.assertEqual(old['stat_projection'][metric],new['stat_projection'][metric])
            r=new['rushing_usage'];self.assertAlmostEqual(r['scramble_carries']+r['designed_carries']+r['unclassified_carries'],new['stat_projection']['carries'])

    def test_te_preserves_room_and_ignores_snap_weight(self):
        for audit in self.te:
            self.assertEqual(audit['weights']['snap_participation'],0)
            self.assertAlmostEqual(sum(audit['final_target_shares'].values()),1)
            for m,total in audit['preserved_room_budgets'].items():self.assertAlmostEqual(sum(p['stat_projection'][m] for p in self.players if p['team']==audit['team'] and p['position']=='TE'),total,places=4)
        inputs=copy.deepcopy(self.inputs)
        for p in inputs['players']:p['features']['snap_participation']=100
        other,_,_=model.build(inputs)
        self.assertEqual(model.dump(other),model.dump(self.candidate))

    def test_general_rookie_and_missing_prior_fallback(self):
        for p in self.players:
            self.assertTrue(all(v>=0 for v in p['stat_projection'].values()))
        self.assertTrue(any(x['base']['years_exp']=='0' for x in self.inputs['players']))
        self.assertTrue(any(not x['features']['production_prior_present'] for x in self.inputs['players']))
        self.assertTrue(any(x['features']['current_depth_rank'] is None for x in self.inputs['players']))

    def test_queue_complete_and_no_holds(self):
        queue=model.review(self.candidate,self.inputs,self.rankings)
        self.assertEqual(len(queue['players']),505)
        for p in queue['players']:
            self.assertIn(p['disposition'],('Approved','Corrected by the general formula','Explicitly disclosed low-evidence projection'))
            if p['gsis_id'] in self.inputs['tier1_ids']:self.assertTrue(p['queue_required'])
            if any(abs(v)>=30 for v in p['format_changes'].values()):self.assertTrue(p['queue_required'])
            if p['name'] in ('James Cook','Tony Pollard','Rico Dowdle','Bhayshul Tuten'):self.assertTrue(p['queue_required'])

    def test_deterministic_rebuild(self):
        second,qb,te=model.build(self.inputs)
        self.assertEqual(model.dump(second),model.dump(self.candidate));self.assertEqual(qb,self.qb);self.assertEqual(te,self.te)

    def test_no_benchmark_network_or_recommendations(self):
        source=(model.ROOT/'scripts/build_nfl_season_final.py').read_text()
        for token in ('requests.', 'urlopen(', 'load_benchmark', 'PRIVATE_BENCHMARK'):self.assertNotIn(token,source)
        self.assertFalse(self.candidate['metadata']['recommendations_enabled'])
        self.assertFalse(self.candidate['metadata']['private_benchmark_or_adp_tuning'])

if __name__=='__main__':unittest.main(verbosity=2)
