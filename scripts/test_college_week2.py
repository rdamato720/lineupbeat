#!/usr/bin/env python3
"""Week 2 role, injury, scoring, identity, schedule and release invariants."""
import copy
import hashlib
import json
import unittest
from pathlib import Path
import college_decision_data
from college_releases import load_release, PINS
from generate_college_week2 import build, validate, BLOCKED, KEYS

ROOT = Path(__file__).resolve().parents[1]


class Week2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release, cls.data = load_release('2026/week-2/v1.0')
        cls.inputs = json.loads((cls.release/'inputs.json').read_text())
        cls.players = cls.data['players']
        cls.by_name = {(p['team'],p['name']):p for p in cls.players}

    def test_rebuild_matches_frozen_release(self):
        rebuilt, _ = build(self.inputs)
        self.assertEqual(rebuilt,self.players)
        validate(rebuilt,self.inputs)

    def test_starters_replace_prior_season_reserves(self):
        for team,starter,reserve in [('West Virginia','Michael Hawkins Jr.','Scotty Fox Jr.'),
                                     ('Kansas','Isaiah Marshall','Chase Jenkins'),
                                     ('Rutgers','AJ Surace','Dylan Lonergan')]:
            a,b=self.by_name[team,starter],self.by_name[team,reserve]
            self.assertGreater(a['passAtt'],25)
            self.assertLess(b['passAtt'],2)
        a,b=self.by_name['Vanderbilt','Blaze Berlowitz'],self.by_name['Vanderbilt','Jared Curtis']
        self.assertAlmostEqual(a['passAtt']/b['passAtt'],1.5,places=3)
        self.assertLess(a['passAtt'],20)
        self.assertGreater(b['passAtt'],5)

    def test_injuries_and_uncertainty(self):
        for p in self.players:
            if p['availability']['status'] in BLOCKED:
                self.assertEqual(p['pts'],0,p['name'])
                self.assertTrue(all(p[k]==0 for k in KEYS.values()))
        self.assertEqual(self.by_name['Missouri','Ahmad Hardy']['availability']['status'],'Out')
        for key in [('Kansas','Cam Pickett'),('UCF','Duane Thomas Jr.'),('Baylor','DJ Lagway')]:
            self.assertTrue(self.by_name[key]['availability']['conditional'])
            self.assertGreater(self.by_name[key]['pts'],0)
        self.assertEqual(self.by_name['Wake Forest','Carlos Hernandez']['availability']['status'],'Expected to play')

    def test_new_out_report_redistributes_without_changing_team_budget(self):
        inputs=copy.deepcopy(self.inputs)
        inputs['injuries'].append({'name':'LJ Martin','team':'BYU','status':'Out','source':'https://example.com/test','updated':'2026-09-09'})
        changed,_=build(inputs)
        before=[p for p in self.players if p['team']=='BYU']
        after=[p for p in changed if p['team']=='BYU']
        self.assertEqual(next(p for p in after if p['name']=='LJ Martin')['pts'],0)
        for stat in KEYS.values():
            self.assertAlmostEqual(sum(p[stat] for p in before),sum(p[stat] for p in after),delta=.03)

    def test_unresolved_starting_identity_fails_closed(self):
        inputs=copy.deepcopy(self.inputs)
        inputs['roleOverrides']['Kansas']['starter']='Unresolved Player'
        with self.assertRaisesRegex(ValueError,'starting quarterback unresolved'):
            build(inputs)

    def test_schedule_and_market_scope(self):
        self.assertEqual(self.data['counts'],{'players':2071,'teams':65,'games':49})
        self.assertTrue({'Florida State','Northwestern','Stanford'}.isdisjoint({p['team'] for p in self.players}))
        self.assertEqual(self.data['marketInput']['teamsWithLines'],57)
        self.assertEqual(self.data['marketInput']['playersWithNumericEvidence'],0)
        self.assertTrue(all(p['identitySource'].startswith('https://') for p in self.players))
        self.assertFalse(any(p['name']=='Hardley Gilmore IV' for p in self.players))

    def test_passing_receiving_and_rank_reconciliation(self):
        self.assertEqual([p['overallRank'] for p in self.players],list(range(1,2072)))
        for pos in ('QB','RB','WR','TE'):
            rows=[p for p in self.players if p['pos']==pos]
            self.assertEqual([p['rank'] for p in rows],list(range(1,len(rows)+1)))
        validate(self.players,self.inputs)

    def test_decision_room_uses_same_points_and_excludes_unresolved_featured_players(self):
        d=college_decision_data.load_weekly();self.assertEqual(d['week'],2)
        by_id={p['id']:p for p in self.players}
        for p in d['players']:
            self.assertEqual(p['formats']['yahoo']['projected_points'],by_id[p['id']]['pts'])
        for card in d['closest_calls']+d['strongest_edges']:
            for k in ('a','b'):
                self.assertIn(by_id[card[k]]['availability']['status'],{'No injury reported','Expected to play'})
        self.assertIsNone(d['market_context_by_team']['CFF_FLA']['team_implied_total'])
        self.assertEqual(d['market']['state'],'partial_game_context')

    def test_week1_release_stays_pinned(self):
        _,archive=load_release('2026/week-1/v1.1')
        self.assertEqual(archive['week'],1)
        self.assertEqual(archive['counts']['players'],2205)


if __name__=='__main__':unittest.main()
