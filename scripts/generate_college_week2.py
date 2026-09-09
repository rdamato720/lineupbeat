#!/usr/bin/env python3
"""Reproducible Week 2 release from frozen public inputs and season priors.

Roles take precedence over season rank. Opportunity shares use 70% prior /
30% latest game, while efficiency retains 90% prior. These are conservative
one-game update heuristics, not parameters fitted to the Week 1 outcomes.
Passing/receiving budgets reconcile. Injury designations are conditional for
questionable players; out and emergency-only players have zero workload.
No provider requests, player props, or inferred probability of playing.
"""
import csv
import hashlib
import json
import math
import unicodedata
import re
from collections import defaultdict
from pathlib import Path
from generate_college_week1 import STATS, clamp, project, yahoo_points

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / 'data/college/2026/week-2/v1.0'
SEASON = ROOT / 'data/college/2026/v1.1'
KEYS = dict(zip(STATS, ('passAtt','comp','passYds','passTd','int','rushAtt',
                       'rushYds','rushTd','rec','recYds','recTd')))
BLOCKED = {'Out', 'Emergency only'}


def norm(s):
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    return re.sub(r'\b(jr|sr|ii|iii|iv|v)\b', '', re.sub(r'[^a-z0-9 ]', '', s.lower())).replace(' ', '')


def shares(values):
    values = [max(0, v) for v in values]
    total = sum(values)
    return [v / total if total else 0 for v in values]


def build(inputs):
    with (SEASON/'provenance/college_player_projections_2026_v1.1.csv').open() as handle:
        priors = list(csv.DictReader(handle))
    with (SEASON/'provenance/college_team_projections_2026_v1.1.csv').open() as handle:
        teams = list(csv.DictReader(handle))
    baseline, _, _ = project(priors, teams, inputs['gamesByTeam'], inputs['capturedAt'])
    base = {r['player_id']: r for r in baseline}
    by_team = defaultdict(list)
    for p in inputs['players']:
        by_team[p['team']].append(p)
    injuries = {(x['team'], norm(x['name'])): x for x in inputs['injuries']}
    factors = {(x['team'], norm(x['name'])): x['factor'] for x in inputs['roleFactors']}
    output, team_audit = [], {}
    for team, people in sorted(by_team.items()):
        game, last = inputs['gamesByTeam'][team], inputs['latestGames'][team]
        source_rows = [r for r in baseline if r['team_name'] == team]
        totals = {KEYS[k]: sum(r[k] for r in source_rows) for k in STATS}
        prior = [{KEYS[k]: base.get(p['id'], {}).get(k, 0) for k in STATS} for p in people]
        actual = [p.get('latestStats', {}) for p in people]
        availability = [injuries.get((team, norm(p['name'])), {}) for p in people]
        active = [i for i, a in enumerate(availability) if a.get('status') not in BLOCKED]
        qbs = [i for i in active if people[i]['pos'] == 'QB']
        override = inputs['roleOverrides'].get(team, {})
        starter_name = override.get('starter', last['starter'])
        starters = [i for i in qbs if norm(people[i]['name']) == norm(starter_name)]
        if len(starters) != 1:
            raise ValueError(f'{team}: starting quarterback unresolved: {starter_name}')
        starter = starters[0]
        stat = [{k: 0.0 for k in KEYS.values()} for _ in people]
        margin = abs((game.get('implied') or 0) - (game.get('opponent_implied') or 0))
        backup_share = .04 if margin < 14 else .08 if margin < 21 else .15
        qb_share = {i: 0.0 for i in qbs}
        shared = override.get('sharedWith')
        if shared:
            second = next(i for i in qbs if norm(people[i]['name']) == norm(shared))
            qb_share[starter], qb_share[second] = .60, .40
        else:
            backups = [i for i in qbs if i != starter]
            # Prefer a current game passer, then the season backup allocation.
            backups.sort(key=lambda i: (-actual[i].get('passAtt', 0), -prior[i]['passAtt'], people[i]['id']))
            qb_share[starter] = 1 - backup_share if backups else 1
            if backups:
                qb_share[backups[0]] = backup_share
        for i, share in qb_share.items():
            for k in ('passAtt', 'comp', 'passYds', 'passTd', 'int'):
                stat[i][k] = totals[k] * share
        # Carry opportunities blend team shares; quarterback carries follow
        # the current quarterback role, not the former season starter.
        prior_rush = [prior[i]['rushAtt'] for i in range(len(people))]
        qb_rush_pool = sum(prior[i]['rushAtt'] for i, p in enumerate(people) if p['pos'] == 'QB')
        for i, p in enumerate(people):
            if p['pos'] == 'QB':
                prior_rush[i] = qb_rush_pool * qb_share.get(i, 0)
        for opportunity, prior_values in (('rushAtt', prior_rush), ('rec', [p['rec'] for p in prior])):
            allowed = [i for i in active if opportunity != 'rushAtt' or people[i]['pos'] != 'QB' or qb_share.get(i, 0) > 0]
            # Actual quarterback runs also move to the projected starter(s)
            # when an injury/role change has replaced last week's starter.
            obs = [actual[i].get(opportunity, 0) for i in range(len(people))]
            if opportunity == 'rushAtt' and (override or norm(starter_name) != norm(last['starter'])):
                pool = sum(obs[i] for i, p in enumerate(people) if p['pos'] == 'QB')
                for i, p in enumerate(people):
                    if p['pos'] == 'QB':
                        obs[i] = pool * qb_share.get(i, 0)
            pv = shares([prior_values[i] * factors.get((team, norm(people[i]['name'])), 1) for i in allowed])
            av = shares([obs[i] for i in allowed])
            blend = [.7 * p + .3 * a for p, a in zip(pv, av)] if sum(av) else pv
            allocation = shares(blend)
            budget = totals['comp'] if opportunity == 'rec' else totals['rushAtt']
            for i, share in zip(allowed, allocation):
                stat[i][opportunity] = budget * share
        # Shrink individual yards/opportunity; normalize to the team's
        # matchup-adjusted total rather than extrapolating one explosive game.
        for opportunity, yards, td in (('rushAtt', 'rushYds', 'rushTd'), ('rec', 'recYds', 'recTd')):
            weights, td_weights = [], []
            for i, p in enumerate(people):
                default = (3.0 if p['pos'] == 'QB' else 4.5) if opportunity == 'rushAtt' else (8.0 if p['pos'] == 'RB' else 12.0)
                rate = prior[i][yards] / prior[i][opportunity] if prior[i][opportunity] >= 1 else default
                observed = actual[i].get(yards, 0) / max(actual[i].get(opportunity, 0), 1)
                rate = .9 * rate + .1 * observed if actual[i].get(opportunity, 0) >= 3 else rate
                rate = clamp(rate, .5 if opportunity == 'rushAtt' else 4, 7 if opportunity == 'rushAtt' else 20)
                weights.append(stat[i][opportunity] * rate)
                prior_td_rate = prior[i][td] / max(prior[i][opportunity], 1)
                default_td_rate = .04 if opportunity == 'rushAtt' else .08
                td_weights.append(stat[i][opportunity] * (.5 * prior_td_rate + .5 * default_td_rate))
            yard_budget = totals['passYds'] if opportunity == 'rec' else totals[yards]
            td_budget = totals['passTd'] if opportunity == 'rec' else totals[td]
            for i, (ys, ts) in enumerate(zip(shares(weights), shares(td_weights))):
                stat[i][yards], stat[i][td] = yard_budget * ys, td_budget * ts
        for i, p in enumerate(people):
            a = availability[i]
            state = a.get('status', 'No injury reported')
            row = {k: p[k] for k in ('id','name','team','teamId','pos')}
            row.update({
                'opponent': game['opponent'], 'home': game['home'], 'gameDate': game['date'],
                'impliedTotal': game.get('implied'),
                'availability': {'status': state, 'source': a.get('source'), 'reportedAt': a.get('updated'),
                                 'checkedAt': inputs['capturedAt'], 'conditional': state == 'Questionable'},
                'identitySource': p['identitySource'],
                'role': ('Shared quarterback' if shared and qb_share.get(i, 0) else 'Projected starter' if i == starter else 'Reserve quarterback' if p['pos'] == 'QB' else None),
                'confidence': 'Low' if state != 'No injury reported' or not base.get(p['id']) else 'Medium',
                **{k: round(v, 3) for k, v in stat[i].items()},
            })
            row['pts'] = round(yahoo_points({k: row[v] for k, v in KEYS.items()}), 1)
            output.append(row)
        team_audit[team] = {'priorGame': last['eventId'], 'starter': people[starter]['name'],
                            'starterSource': override.get('source', last['source']),
                            'starterBasis': 'current_report' if override else 'latest_game',
                            'passingShares': {people[i]['id']: s for i, s in qb_share.items() if s},
                            'teamBudgets': totals, 'marketAvailable': game.get('implied') is not None}
    output.sort(key=lambda p: (-p['pts'], p['name'], p['id']))
    position_ranks = defaultdict(int)
    for rank, p in enumerate(output, 1):
        position_ranks[p['pos']] += 1
        p['overallRank'], p['rank'] = rank, position_ranks[p['pos']]
    validate(output, inputs)
    return output, team_audit


def validate(rows, inputs):
    assert len({r['id'] for r in rows}) == len(rows) == len(inputs['players'])
    assert {r['team'] for r in rows} == set(inputs['gamesByTeam'])
    assert all(r['identitySource'].startswith('https://') for r in rows)
    for r in rows:
        assert r['pos'] in {'QB','RB','WR','TE'}
        assert '2026-09-10' <= r['gameDate'] < '2026-09-15'
        assert all(math.isfinite(r[k]) and r[k] >= 0 for k in KEYS.values())
        assert r['comp'] <= r['passAtt'] + .001
        assert abs(r['pts'] - yahoo_points({k:r[v] for k,v in KEYS.items()})) <= .050001
        if r['availability']['status'] in BLOCKED:
            assert not any(r[k] for k in KEYS.values()), r['name']
    for team in inputs['gamesByTeam']:
        subset = [r for r in rows if r['team'] == team]
        for a,b in [('comp','rec'),('passYds','recYds'),('passTd','recTd')]:
            assert abs(sum(r[a]-r[b] for r in subset)) < .03, (team,a,b)


def main():
    inputs = json.loads((RELEASE/'inputs.json').read_text())
    rows, audit = build(inputs)
    data = {'season':2026,'week':2,'modelVersion':'week2-v1.0','generatedAt':inputs['capturedAt'],
            'scoring':'Yahoo scoring rules','counts':{'players':len(rows),'teams':len(audit),'games':len(inputs['schedule']['games'])},
            'scoringComponents': {'passYds':.04,'passTd':4,'int':-1,'rushYds':.1,'rushTd':6,'rec':1,'recYds':.1,'recTd':6},
            'marketInput':{'provider':'ESPN scoreboard game lines','capturedAt':inputs['capturedAt'],
                           'teamsWithLines':sum(v['marketAvailable'] for v in audit.values()),'playersWithNumericEvidence':0},
            'players':rows}
    write = lambda path, obj: path.write_text(json.dumps(obj,separators=(',',':'))+'\n')
    write(RELEASE/'college_week2_site_projections_2026.json',data)
    write(RELEASE/'model_audit.json',{'teamRoles':audit,'opportunityPriorWeight':.7,'efficiencyPriorWeight':.9,
                                    'questionableScoring':'conditional_on_playing','scoringOmissions':['fumbles','return touchdowns','two-point conversions']})
    provenance = RELEASE/'provenance'; provenance.mkdir(exist_ok=True)
    write(provenance/'college_week2_schedule_2026.json',inputs['schedule'])
    files = [RELEASE/'inputs.json',RELEASE/'model_audit.json',RELEASE/'college_week2_site_projections_2026.json',provenance/'college_week2_schedule_2026.json']
    manifest = {'version':'college_week2_2026_v1.0','status':'PUBLISHED','generated_at':inputs['capturedAt'],
                'source_release':'2026/v1.1','source_manifest_sha256':hashlib.sha256((SEASON/'manifest.json').read_bytes()).hexdigest(),
                'qa_status':'PASS','counts':data['counts'],
                'files':{p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files}}
    (RELEASE/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(data['counts'],data['marketInput'])
    for pos in ('QB','RB','WR','TE'):
        print(pos,[(r['name'],r['team'],r['pts'],r['availability']['status']) for r in rows if r['pos']==pos][:12])


if __name__ == '__main__':
    main()
