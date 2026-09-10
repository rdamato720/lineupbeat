import copy,json,unittest
from pathlib import Path
from weekly_availability import apply_reports,CONFIG,ROOT
from validate_daily_fantasy_refresh import points

class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads((ROOT/'data/week1/2026/v1.2/nfl_week1_projections.json').read_text())
        self.reports=json.loads(CONFIG.read_text())['reports']
    def test_out_zeroes_every_format_and_keeps_other_points(self):
        before=copy.deepcopy(self.data);after=apply_reports(self.data,self.reports)
        old={p['id']:p for p in before['players']}
        for p in after['players']:
            if p['id']==self.reports[0]['player_id']:
                self.assertEqual(p['availability']['status'],'Out');self.assertFalse(any(p['stat_projection'].values()))
                self.assertTrue(all(x['projected_points']==0 for x in p['formats'].values()))
            else:
                self.assertEqual(p['stat_projection'],old[p['id']]['stat_projection'])
                for fmt in p['formats']:self.assertEqual(p['formats'][fmt]['projected_points'],old[p['id']]['formats'][fmt]['projected_points'])
        for fmt in ('ppr','half_ppr','non_ppr'):
            self.assertEqual(sorted(p['formats'][fmt]['overall_rank'] for p in after['players']),list(range(1,len(after['players'])+1)))
        self.assertEqual(apply_reports(copy.deepcopy(after),self.reports),after)
    def test_other_week_untouched(self):
        self.data['week']=2;before=copy.deepcopy(self.data);self.assertEqual(apply_reports(self.data,self.reports),before)
    def test_wrong_identity_or_game_refused(self):
        for field,value in [('name','Other Player'),('team','KC'),('position','WR'),('game_id','2026_02_LAC_LV')]:
            reports=copy.deepcopy(self.reports);reports[0][field]=value
            with self.assertRaises(ValueError):apply_reports(copy.deepcopy(self.data),reports)
    def test_stale_feed_cannot_restore_reported_out_player(self):
        p=next(p for p in self.data['players'] if p['id']==self.reports[0]['player_id']);p['availability']['status']='Doubtful'
        result=apply_reports(self.data,self.reports);p=next(p for p in result['players'] if p['id']==self.reports[0]['player_id'])
        self.assertEqual(p['availability']['status'],'Out');self.assertEqual(p['availability']['feed_status_before_report'],'Doubtful')

if __name__=='__main__':unittest.main()
