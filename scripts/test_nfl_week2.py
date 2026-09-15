"""Release checks for weekly identity, grading, workload conservation and readers."""
import json, unittest, math, re
from datetime import datetime
from pathlib import Path
import decision_data
from build_week1_intelligence import score
from nfl_weekly_results import summarize
ROOT=Path(__file__).resolve().parents[1]
class WeeklyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=decision_data.load_weekly(week=2)
        cls.r=json.loads((ROOT/'data/nfl_weekly/2026/week-1/results.json').read_text())
    def test_schedule_identity_scoring(self):
        p=self.p;self.assertEqual(len({x['team'] for x in p['players']}),32)
        self.assertEqual(len({x['id'] for x in p['players']}),len(p['players']))
        for x in p['players']:
            self.assertIn('_02_',x['game_id'])
            self.assertNotEqual(x['opponent'],x['team'])
            for fmt,rv in [('half_ppr',.5),('ppr',1),('non_ppr',0)]:
                self.assertAlmostEqual(x['formats'][fmt]['projected_points'],round(score(x['stat_projection'],rv),1))
            self.assertFalse(x['data_coverage']['betting_market'])
            self.assertNotIn('consensus_lines',x['market'])
            if x['availability']['status'] in ('Questionable','Doubtful'):
                self.assertFalse(x['availability']['projection_adjusted'])
    def test_team_workloads_and_current_qb(self):
        for club,b in self.p['team_workload_budgets'].items():
            group=[p for p in self.p['players'] if p['team']==club]
            for key in ('attempts','carries','targets'):
                self.assertAlmostEqual(sum(p['stat_projection'][key] for p in group),b[key],delta=.02)
            self.assertAlmostEqual(sum(p['stat_projection']['passing_yards'] for p in group),sum(p['stat_projection']['receiving_yards'] for p in group),delta=.02)
        atl={p['name']:p for p in self.p['players'] if p['team']=='ATL' and p['position']=='QB'}
        self.assertGreater(atl['Michael Penix Jr.']['stat_projection']['attempts'],atl['Tua Tagovailoa']['stat_projection']['attempts']*10)
    def test_every_forecast_precedes_its_game(self):
        for p in self.r['rows']:
            stamp=self.r['forecasts'][p['forecast_commit']]['committed_at']
            self.assertLess(datetime.fromisoformat(stamp),datetime.fromisoformat(p['kickoff'].replace('Z','+00:00')))
        self.assertGreaterEqual(len(self.r['forecasts']),2)
    def test_missing_is_not_zero_and_summary_reconciles(self):
        for fmt in ('half_ppr','ppr','non_ppr'):
            top=[p for p in self.r['rows'] if p['formats'][fmt]['rank']<=30]
            self.assertEqual(len(top),120)
            self.assertEqual(summarize(top,fmt),self.r['summary'][fmt]['top30'])
            for p in self.r['rows']:
                if not p['matched']:self.assertIsNone(p['formats'][fmt]['actual'])
        self.assertEqual(self.r['summary']['half_ppr']['top30']['matched'],119)
    def test_weekly_readers(self):
        import build_nfl_week1_boards as b
        from nfl_results_page import scorecard
        for kind in ('rankings','projections'):
            page=b.render(self.p,kind,ROOT/'site',scorecard())
            self.assertIn(f'https://lineupbeat.com/nfl/week-2/{kind}/',page)
            self.assertIn('6.4',page);self.assertIn('/nfl/week-1/results/',page)
            self.assertNotIn('COMING AFTER WEEK 1',page)
            self.assertEqual('>Pass Yds</th>' in page,kind=='projections')
        import build_decision_room as d
        page=d.render(self.p)
        self.assertIn('Week 2 projected points',page)
        self.assertIn('/nfl/week-2/rankings/',page)
        self.assertNotIn('Week 1 projected points',page)
        embedded=json.loads(re.search(r'<script id="dr-data" type="application/json">(.*?)</script>',page,re.S).group(1).replace('\\/', '/'))
        self.assertEqual(embedded['sources'],self.p['sources'])
    def test_refresh_locks_started_games(self):
        import copy
        import build_nfl_week2 as builder
        old=copy.deepcopy(self.p);candidate=copy.deepcopy(self.p)
        for p in candidate['players']:
            p['stat_projection']['rushing_yards']+=1
        refreshed=builder.lock_started(candidate,old,datetime.fromisoformat('2026-09-18T10:00:00+00:00'))
        old_by_id={p['id']:p for p in old['players']}
        self.assertEqual(set(refreshed['locked_teams']),{'BUF','DET'})
        for p in refreshed['players']:
            prior=old_by_id[p['id']]
            if p['team'] in ('BUF','DET'):
                self.assertEqual(p['stat_projection'],prior['stat_projection'])
                self.assertEqual(p['forecast_updated_at'],prior['forecast_updated_at'])
            else:self.assertEqual(p['stat_projection']['rushing_yards'],prior['stat_projection']['rushing_yards']+1)
    def test_week1_archive_not_rebuilt(self):
        old=decision_data.load_weekly(week=1)
        self.assertEqual(old['updated_at'],'2026-09-11T14:03:45.253593+00:00')
        self.assertEqual(old['week'],1)
if __name__=='__main__':unittest.main()
