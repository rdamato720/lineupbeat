"""Publish one verified projection/ranking release from current captured facts."""
import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from capture_nfl_week2 import CATALOG, validate_current
from decision_data import normalize_player_name, slug
from espn_injury_inputs import UNAVAILABLE_STATUSES
from nfl_usage_model import History, predict, score, FORMATS, STATS, VERSION
import nfl_weekly_rankings as ranking_model

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data/nfl_weekly/2026/week-2/v2.0'
MODEL = ROOT/'data/nfl_weekly/model-v2/model.json'
PREVIOUS = ROOT/'data/nfl_weekly/2026/week-2/v1.0/projections.json'


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True)+'\n').encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def verify_capture(cache, now):
    manifest = json.loads((cache/'capture_manifest.json').read_text())
    if (manifest.get('season'), manifest.get('forecast_week'), manifest.get('stats_through_week')) != (2026,2,1):
        raise ValueError('Wrong capture week or season')
    stamp = datetime.fromisoformat(manifest['captured_at'].replace('Z','+00:00'))
    if not -300 <= (now-stamp).total_seconds() < 36*3600:
        raise ValueError('Capture is stale or future dated')
    expected = {name for _,name in CATALOG} | {'espn_injuries.json'}
    if {a['file'] for a in manifest['assets']} != expected or len(manifest['assets']) != len(expected):
        raise ValueError('Capture does not contain the complete input catalog')
    pending = {}
    for asset in manifest['assets']:
        body = (cache/asset['file']).read_bytes()
        if digest(body) != asset['sha256']:
            raise ValueError(f"Capture digest mismatch: {asset['file']}")
        if '2026' in asset['file'] or asset['file'] == 'games.csv.gz':
            pending[asset['file']] = body
    validate_current(pending, stamp)
    injuries = json.loads((cache/'espn_injuries.json').read_text())
    injury_stamp = datetime.fromisoformat(injuries['fetched_at'].replace('Z','+00:00'))
    if injuries.get('season') != 2026 or injuries.get('team_count') != 32 or not 0 <= (stamp-injury_stamp).total_seconds() < 86400:
        raise ValueError('Invalid current injury capture')
    return manifest, injuries


def injury_index(injuries):
    result = {}
    for row in injuries['records']:
        key = (normalize_player_name(row['name']),row['team'],row['position'])
        if key in result:
            raise ValueError('Ambiguous injury identity')
        result[key] = row
    return result


def availability(player, injuries, indexed):
    key = (normalize_player_name(player['name']),player['team'],player['position'])
    row = indexed.get(key,{})
    status = row.get('status','Active')
    unavailable = status in UNAVAILABLE_STATUSES
    return {'status':status, 'tag':row.get('abbreviation'), 'state':'unavailable' if unavailable else 'active',
            'projection_adjusted':unavailable, 'projection_factor':0.0 if unavailable else 1.0,
            'injury_type':row.get('injury_type'), 'source':'ESPN injury status',
            'source_url':injuries['source_url'], 'updated_at':injuries['fetched_at'],
            'policy':'Questionable and Doubtful are informational; only confirmed Out, IR or suspended players receive zero.'}


def lock_started(payload, previous, now):
    closed = {p['team'] for p in previous['players'] if datetime.fromisoformat(p['kickoff']) <= now}
    if not closed:
        return payload
    payload['players'] = ([p for p in payload['players'] if p['team'] not in closed]
                          + [p for p in previous['players'] if p['team'] in closed])
    for club in closed:
        payload['team_workload_budgets'][club] = previous['team_workload_budgets'][club]
    payload['locked_teams'] = sorted(closed)
    for fmt,rv in FORMATS.items():
        counts = defaultdict(int)
        for rank,p in enumerate(sorted(payload['players'],key=lambda p:(-score(p['stat_projection'],rv),p['id'])),1):
            counts[p['position']] += 1
            # Copy rather than mutate the previous release's format records.
            p['formats'] = {k:dict(v) for k,v in p['formats'].items()}
            p['formats'][fmt].update(overall_rank=rank,position_rank=counts[p['position']])
    return payload


def validate_payload(payload):
    players = payload['players']
    if len({p['id'] for p in players}) != len(players):
        raise ValueError('Duplicated weekly identity')
    if len({p['team'] for p in players}) != 32 or len({p['game_id'] for p in players}) != 16:
        raise ValueError('Incomplete weekly slate')
    for p in players:
        s = p['stat_projection']
        if set(s) != set(STATS) or any(not math.isfinite(v) or v < 0 for v in s.values()):
            raise ValueError('Invalid projected stat line')
        if s['completions'] > s['attempts']+.002 or s['receptions'] > s['targets']+.002:
            raise ValueError('Completions or receptions exceed opportunities')
        if p['availability']['projection_adjusted'] and any(s.values()):
            raise ValueError('Confirmed unavailable player has projected production')
        for fmt,rv in FORMATS.items():
            if p['formats'][fmt]['projected_points'] != round(score(s,rv),1):
                raise ValueError('Scoring does not reconcile')
        if '_02_' not in p['game_id'] or p['team'] == p['opponent']:
            raise ValueError('Wrong matchup')
    for club,budget in payload['team_workload_budgets'].items():
        group = [p['stat_projection'] for p in players if p['team'] == club]
        for field in ('attempts','carries','targets','passing_tds','rushing_tds'):
            if abs(sum(s[field] for s in group)-budget[field]) > .02:
                raise ValueError(f'{club} {field} does not reconcile')
        for a,b in (('completions','receptions'),('passing_yards','receiving_yards'),('passing_tds','receiving_tds')):
            if abs(sum(s[a]-s[b] for s in group)) > .02:
                raise ValueError(f'{club} {a}/{b} does not reconcile')


def build(cache, now=None):
    now = now or datetime.now(timezone.utc)
    previous_path = OUT/'projections.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
    if previous and all(datetime.fromisoformat(p['kickoff']) <= now for p in previous['players']):
        return previous
    if not previous and any(datetime.fromisoformat(p['kickoff']) <= now for p in json.loads(PREVIOUS.read_text())['players']):
        raise ValueError('Cannot migrate a projection model after a Week 2 game has started')
    manifest, injuries = verify_capture(cache, now)
    model = json.loads(MODEL.read_text())
    validation = json.loads(MODEL.with_name('validation.json').read_text())
    if model['version'] != VERSION or model['code_sha256'] != digest((ROOT/'scripts/nfl_usage_model.py').read_bytes()):
        raise ValueError('Model settings do not match the tested component engine')
    if model.get('ranking_code_sha256') != digest((ROOT/'scripts/nfl_weekly_rankings.py').read_bytes()):
        raise ValueError('Ranking settings do not match the tested ranking engine')
    if validation['model_sha256'] != digest(MODEL.read_bytes()) or not validation['production_component_engine_reproduced']:
        raise ValueError('Model has no matching historical validation')
    history = History(cache, years=(2024,2025,2026))
    features = history.features(2026,2,current=True,asof=manifest['captured_at'])
    indexed = injury_index(injuries)
    statuses = {p['id']:availability(p,injuries,indexed) for p in features['players']}
    for p in features['players']:
        p['unavailable'] = statuses[p['id']]['projection_adjusted']
    result = predict(features, model['parameters'])
    # Previously published display fields retain their identity; no old points,
    # workbook roles or expert reference rows are used by the component engine.
    display = {p['id']:p for p in json.loads(PREVIOUS.read_text())['players']}
    roster = {p['id']:p for p in features['players']}
    for p in result['players']:
        old = display.get(p['id'],{})
        if old and (old['team'],old['position']) != (p['team'],p['position']):
            old = {}
        p['name'] = old.get('name',p['name'])
        p.update(features['slate'][p['team']])
        p.update(slug=old.get('slug',slug(p['name'])), photo=roster[p['id']]['photo'],
                 team_logo=f"https://a.espncdn.com/i/teamlogos/nfl/500/{p['team'].lower()}.png",
                 adp=old.get('adp'), history=old.get('history',{}),history_season=old.get('history_season'),
                 availability=statuses[p['id']], forecast_updated_at=manifest['captured_at'])
        p['role'] = {'roster_status':'ACT','depth_rank':p['depth_rank'],'depth_position':p['position'],
                     'projected_qb_role':p['projected_qb_role'],'2026_average_offense_snap_pct':p['evidence']['current_snap_pct']}
        p['expected_opportunity'] = {label:round(p['stat_projection'][key],1) for label,key in
                                     (('pass_attempts','attempts'),('carries','carries'),('targets','targets'))}
        p['data_coverage'] = {'current_roster':True,'current_injury_report':True,
                              'depth_chart':p['depth_rank'] is not None,
                              'historical_weekly':p['evidence']['prior_appearances']>0,
                              'current_weekly_usage':p['evidence']['current_appearances']>0,
                              'snap_participation':p['evidence']['current_snap_pct'] is not None,
                              'team_volume':True,'opportunity':True,'betting_market':False,
                              'opponent_matchup':bool(model['parameters']['opponent_weight'])}
        p['market'] = {'state':'unavailable','quality':'UNAVAILABLE','updated_at':None,'player_components':[]}
        p['matchup'] = {'opponent':p['opponent'],'label':'Scheduled Week 2 opponent; no defensive adjustment selected in training','projection_factor':1.0}
        p['identity_resolution'] = {'method':'Current roster GSIS identity; exact team and position','stable_gsis_id':p['id'],'roster_record':True,'season_prior_stat_line':False}
    stamp = manifest['captured_at']
    payload = {'schema_version':'lineupbeat-weekly-projections-v2','mode':'weekly','season':2026,'week':2,
               'updated_at':stamp,'available_formats':list(FORMATS),'model_version':VERSION,
               **result,'excluded_players':[],'withheld_players':[],'unresolved_players':[],
               'editorial_opinions':[],'schedule_sos_available':False,
               'identity_method':'Stable GSIS roster ID; historical game and team identity; exact injury name/team/position, no fuzzy joins.',
               'sources':{'model':{'label':'LineupBeat current-usage model v2','updated_at':stamp},
                          'history':{'label':'nflverse player and team statistics through 2026 Week 1','updated_at':stamp},
                          'week1_usage':{'label':'2026 Week 1 player/team stats, offensive snaps and play-by-play','updated_at':stamp},
                          'depth':{'label':'Current nflverse depth chart','updated_at':features['depth_asof']},
                          'injuries':{'label':'ESPN current injury status','url':injuries['source_url'],'updated_at':injuries['fetched_at']},
                          'ranks':{'label':'Independent weekly 1QB and Superflex rankings','updated_at':stamp},
                          'market':{'label':'No qualified current TheRundown snapshot','updated_at':None},
                          'matchup':{'label':'Current schedule; defensive weighting not selected in training','updated_at':stamp}},
               'methodology':{'summary':'Current roster identities and strictly earlier team/player usage, with historical efficiency and scoring-area opportunities. Parameters chosen on 2024, then evaluated on 2025.',
                              'version':VERSION,'parameters':model['parameters'],
                              'injury_policy':'Questionable and Doubtful keep full projections. Confirmed Out, IR and suspended players receive zero; available teammates share the opportunity.',
                              'scoring':'4/pass TD, 0.04/pass yard, -2/interception, 0.1/rush or receiving yard, 6/rush or receiving TD, -2/fumble lost, 2/two-point conversion, 6/return TD, plus 1/0.5/0 per reception.',
                              'rankings':'Separate artifact; mean/typical-outcome score and reference lineup replacement value. Superflex changes ranks, not points.',
                              'season_total_divisor':None,'market_policy':'No betting adjustment or external expert projections.',
                              'allocation_policy':'Team attempts, carries, targets and TDs are conserved. Receiving yards, completions and TDs reconcile to the passing offense.'},
               'limitations':['Only one completed 2026 week; role changes are shrunk toward prior appearances.',
                              'Rookies and players without history use observed historical workload-slot priors and position efficiency.',
                              'No live route participation, weather or qualified betting snapshot is included.',
                              'Historical validation is against a recent-average baseline, not a head-to-head test against an expert site.']}
    if previous:
        payload = lock_started(payload, previous, now)
    n = len(payload['players'])
    payload['population'] = {'projection_source':n,'identity_resolved':n,'identity_unresolved':0,
                             'ranked_production':n,'ranked_active_projected':n,'ranked_excluded':0,'identity_resolved_not_ranked':0}
    validate_payload(payload)
    body = encoded(payload)
    ranks = ranking_model.build(payload,model,digest(body))
    ranking_model.validate(payload,ranks,digest(body))
    provenance = {'season':2026,'week':2,'updated_at':stamp,'assets':manifest['assets'],
                  'model_sha256':digest(MODEL.read_bytes()),'projection_sha256':digest(body),
                  'ranking_sha256':digest(encoded(ranks)),'validation_sha256':digest(MODEL.with_name('validation.json').read_bytes()),
                  'methodology':payload['methodology'],'license_review':manifest['license_review'],
                  'paid_provider_calls':0,'model_api_calls':0,'external_reference_rows_used':0}
    OUT.mkdir(parents=True,exist_ok=True)
    for name,value in (('projections.json',body),('rankings.json',encoded(ranks)),('provenance.json',encoded(provenance))):
        temporary = OUT/(name+'.tmp')
        temporary.write_bytes(value)
        temporary.replace(OUT/name)
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, required=True)
    payload = build(parser.parse_args().cache)
    print(f"Week 2: {len(payload['players'])} players, three scoring formats, 1QB and Superflex rankings; {payload['updated_at']}")
