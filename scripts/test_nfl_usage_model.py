"""Release gates for forecast independence, time boundaries and six ranking views."""
import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import build_nfl_week2 as builder
import decision_data
import nfl_weekly_rankings as rankings
from nfl_usage_model import History, FORMATS, STATS, CONTEXT, predict, score

ROOT=Path(__file__).resolve().parents[1]


def small_history():
    h=History.__new__(History)
    h.games=[{'season':'2025','week':'2','game_type':'REG','game_id':'2025_02_DET_BUF',
              'gameday':'2025-09-14','gametime':'13:00','home_team':'BUF','away_team':'DET'}]
    h.player=defaultdict(list);h.teams=defaultdict(list);h.team_games={};h._features={};h.actual={}
    h.rosters={2025:[]};depth=[]
    for club,opp in (('BUF','DET'),('DET','BUF')):
        total={k:0.0 for k in (*STATS,*CONTEXT)}
        total.update(season=2025,week=1,game_id='2025_01_DET_BUF',team=club,opponent=opp,
                     attempts=30,completions=20,passing_yards=240,passing_tds=2,carries=25,
                     rushing_yards=110,rushing_tds=1,targets=28,receptions=20,receiving_yards=240,
                     receiving_tds=2,neutral_passes=15,neutral_plays=25,goal_line_carries=2,red_zone_targets=4,end_zone_targets=1)
        h.teams[club].append(total);h.team_games[(total['game_id'],club)]=total
        for pos,rank in (('QB',1),('QB',2),('RB',1),('WR',1),('TE',1)):
            pid=club+pos+str(rank)
            h.rosters[2025].append({'gsis_id':pid,'full_name':pid,'team':club,'position':pos,'status':'ACT','week':'1','game_type':'REG'})
            depth.append({'id':pid,'team':club,'rank':rank})
            stat={k:0.0 for k in (*STATS,*CONTEXT)}
            stat.update(season=2025,week=1,game_id=total['game_id'],team=club,opponent=opp,position=pos,offense_pct=.8)
            if pos=='QB' and rank==1:
                stat.update(attempts=30,completions=20,passing_yards=240,passing_tds=2,carries=3,rushing_yards=15)
            if pos=='RB':
                stat.update(carries=22,rushing_yards=95,rushing_tds=1,targets=4,receptions=3,receiving_yards=20,goal_line_carries=2)
            if pos=='WR':
                stat.update(targets=16,receptions=10,receiving_yards=150,receiving_tds=1,red_zone_targets=3,end_zone_targets=1)
            if pos=='TE':
                stat.update(targets=8,receptions=7,receiving_yards=70,receiving_tds=1,red_zone_targets=1)
            h.player[pid].append(stat)
    h.depth={2025:{'2025-09-10T12:00:00Z':depth}}
    return h


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload=decision_data.load_weekly(week=2)
        cls.model=json.loads(builder.MODEL.read_text())
        cls.validation=json.loads(builder.MODEL.with_name('validation.json').read_text())

    def test_all_stats_and_team_totals_reconcile(self):
        builder.validate_payload(self.payload)
        for p in self.payload['players']:
            s=p['stat_projection']
            self.assertAlmostEqual(score(s,1)-score(s,0),s['receptions'])
            self.assertAlmostEqual(score(s,.5)-score(s,0),s['receptions']*.5)

    def test_training_matches_published_code_and_has_separate_season(self):
        self.assertEqual(self.model['code_sha256'],builder.digest((ROOT/'scripts/nfl_usage_model.py').read_bytes()))
        self.assertEqual(self.model['ranking_code_sha256'],builder.digest((ROOT/'scripts/nfl_weekly_rankings.py').read_bytes()))
        self.assertEqual(self.model['workload_code_sha256'],builder.digest((ROOT/'scripts/nfl_workload_model.py').read_bytes()))
        self.assertTrue(self.validation['gates_passed'])
        self.assertTrue(self.validation['frozen_integration_replay_identical'])
        self.assertEqual(self.validation['model_sha256'],builder.digest(builder.MODEL.read_bytes()))
        self.assertEqual(self.model['training_season'],2024)
        self.assertEqual(self.validation['holdout_season'],2025)
        self.assertEqual(self.validation['holdout']['matched']+self.validation['holdout']['ungraded'],self.validation['holdout']['population'])
        self.assertGreater(self.validation['holdout']['ungraded'],0)

    def test_target_and_future_stats_rosters_and_depth_cannot_change_features(self):
        history=small_history();expected=copy.deepcopy(history.features(2025,2))
        for rows in (*history.player.values(),*history.teams.values()):
            poison={**rows[-1],'week':2,'attempts':9000,'passing_yards':99999}
            rows.append(poison);rows.append({**poison,'week':3})
        history.rosters[2025].append({**history.rosters[2025][0],'week':'2','full_name':'Postgame rename','status':'RES'})
        history.depth[2025]['2025-09-15T12:00:00Z']=[{'id':'BUFQB1','team':'BUF','rank':99}]
        history._features.clear()
        self.assertEqual(history.features(2025,2),expected)
        self.assertEqual(predict(history.features(2025,2),self.model['parameters']),predict(expected,self.model['parameters']))

    def test_unavailable_qb_returns_all_opportunities_to_available_players(self):
        features=small_history().features(2025,2)
        features['players'][0]['unavailable']=False
        starter=next(p for p in features['players'] if p['id']=='BUFQB1');starter['unavailable']=True
        result=predict(features,self.model['parameters']);by={p['id']:p for p in result['players']}
        self.assertFalse(any(by['BUFQB1']['stat_projection'].values()))
        self.assertEqual(by['BUFQB2']['projected_qb_role'],1)
        self.assertAlmostEqual(by['BUFQB2']['stat_projection']['attempts'],result['team_workload_budgets']['BUF']['attempts'],delta=.001)

    def test_q_and_d_are_informational_and_injury_joins_are_exact(self):
        p={'name':'Example Player Jr.','team':'BUF','position':'RB'}
        for status in ('Questionable','Doubtful','Out','Injured Reserve','Suspension'):
            injuries={'source_url':'https://example.test','fetched_at':'2026-09-15T12:00:00Z','records':[{**p,'status':status}]}
            found=builder.availability(p,injuries,builder.injury_index(injuries))
            self.assertEqual(found['projection_adjusted'],status in ('Out','Injured Reserve','Suspension'))
            other=builder.availability({**p,'team':'DET'},injuries,builder.injury_index(injuries))
            self.assertEqual(other['status'],'Active')
        injuries['records'].append({**injuries['records'][0],'name':'Example Player'})
        with self.assertRaisesRegex(ValueError,'Ambiguous'):builder.injury_index(injuries)

    def test_six_rank_views_preserve_points_and_qb_position_order(self):
        payload=self.payload;r=payload['rankings']
        rankings.validate(payload,r,builder.digest((builder.OUT/'projections.json').read_bytes()))
        qbs=[p for p in r['players'] if p['position']=='QB' and p['eligible']]
        for fmt in FORMATS:
            self.assertTrue(any(p['formats'][fmt]['leagues']['superflex']['overall_rank']<p['formats'][fmt]['leagues']['one_qb']['overall_rank'] for p in qbs))
        baseline={p['id']:p for p in payload['players']}
        self.assertTrue(any(p['formats']['ppr']['leagues']['one_qb']['position_rank']!=baseline[p['id']]['formats']['ppr']['position_rank'] for p in r['players'] if p['eligible']))
        malformed=copy.deepcopy(r);malformed['players'][0]['team']='BAD'
        with self.assertRaisesRegex(ValueError,'identity'):rankings.validate(payload,malformed,r['projection_sha256'])

    def test_new_roster_identities_do_not_require_a_preseason_row(self):
        old={p['id'] for p in json.loads(builder.PREVIOUS.read_text())['players']}
        added=[p for p in self.payload['players'] if p['id'] not in old]
        self.assertGreater(len(added),0)
        self.assertTrue(any(p['stat_projection']['targets']>0 for p in added))
        self.assertEqual(self.payload['population']['identity_resolved_not_ranked'],0)

    def test_bad_capture_is_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            value={'season':2026,'forecast_week':2,'stats_through_week':1,'captured_at':'2026-09-15T12:00:00Z','assets':[]}
            (folder/'capture_manifest.json').write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'complete input catalog'):
                builder.verify_capture(folder,datetime(2026,9,15,13,tzinfo=timezone.utc))
            value['captured_at']='2026-09-12T12:00:00Z';(folder/'capture_manifest.json').write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'stale'):
                builder.verify_capture(folder,datetime(2026,9,15,13,tzinfo=timezone.utc))

    def test_all_closed_games_do_not_get_new_forecast_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'projections.json').write_text(json.dumps(self.payload))
            with patch.object(builder,'OUT',path),patch.object(builder,'verify_capture',side_effect=AssertionError('closed week must not be rebuilt')):
                result=builder.build(path,now=datetime(2026,9,23,tzinfo=timezone.utc))
            self.assertEqual(result['updated_at'],self.payload['updated_at'])

    def test_client_filters_and_shared_links_for_all_six_views(self):
        import nfl_week2_pages
        data={'kind':'rankings','players':self.payload['players'],'rankings':self.payload['rankings']}
        script=ROOT/'scripts/test_nfl_week2_client.mjs'
        result=subprocess.run(['node',str(script)],input=json.dumps({'data':data,'code':nfl_week2_pages.JS}),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
