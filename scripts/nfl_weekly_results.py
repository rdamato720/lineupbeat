"""Grade immutable per-game pre-kickoff NFL forecasts against nflverse final stats."""
from __future__ import annotations
import csv, gzip, hashlib, json, statistics, subprocess
from datetime import datetime
from pathlib import Path
from build_week1_intelligence import score, team
ROOT=Path(__file__).resolve().parents[1]
FORECAST='data/week1/2026/v1.2/nfl_week1_projections.json'
OUT=ROOT/'data/nfl_weekly/2026/week-1/results.json'
FORMATS={'half_ppr':.5,'ppr':1.,'non_ppr':0.}

def dt(s):return datetime.fromisoformat(s.replace('Z','+00:00'))
def read_csv(p):
    with gzip.open(p,'rt') as f:return list(csv.DictReader(f))
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True)
def summarize(rows,fmt):
    values=[r['formats'][fmt]['error'] for r in rows if r['matched']]
    return {'matched':len(values),'total':len(rows),'mae':statistics.fmean(abs(v) for v in values) if values else None,
            'bias':statistics.fmean(values) if values else None,'within_5':sum(abs(v)<=5 for v in values)}
def capture(cache):
    scoreboard=json.loads((cache/'week1-scoreboard.json').read_text())
    if scoreboard['week']['number']!=1 or len(scoreboard['events'])!=16:raise ValueError('Wrong Week 1 slate')
    events={}
    for event in scoreboard['events']:
        if not event['status']['type']['completed']:raise ValueError('Week 1 not final')
        for club in event['competitions'][0]['competitors']:
            events[team(club['team']['abbreviation'])]=event
    versions=[]
    for line in git('log','--format=%H %cI','--',FORECAST).splitlines():
        commit,stamp=line.split(' ',1)
        raw=git('show',f'{commit}:{FORECAST}')
        versions.append((dt(stamp),commit,raw,json.loads(raw)))
    stats=read_csv(cache/'stats_player_week_2026.csv.gz')
    selected=[r for r in stats if r['season']=='2026' and r['week']=='1' and r['season_type']=='REG']
    by_id={r['player_id']:r for r in selected}
    if len(by_id)!=len(selected):raise ValueError('Duplicate actual identity')
    rows=[];sources={}
    for club,event in sorted(events.items()):
        candidates=[v for v in versions if v[0]<dt(event['date'])]
        if not candidates:raise ValueError('Missing pregame forecast')
        stamp,commit,raw,payload=max(candidates,key=lambda v:v[0])
        sha=hashlib.sha256(raw.encode()).hexdigest()
        sources[commit]={'committed_at':stamp.isoformat(),'sha256':sha,'path':FORECAST}
        opponents={team(c['team']['abbreviation']) for c in event['competitions'][0]['competitors']} - {club}
        for p in payload['players']:
            if p['team']!=club:continue
            if team(p['opponent']) not in opponents:raise ValueError('Forecast matchup mismatch')
            r=by_id.get(p['id'])
            if r and (team(r['team'])!=club or team(r['opponent_team']) not in opponents):
                raise ValueError(f'Actual identity or matchup mismatch: {p["name"]} {club} {p["position"]} / {r["team"]} {r["position"]} {r["opponent_team"]}')
            row={k:p[k] for k in ['id','name','team','position','photo']}
            row.update(actual_position=r['position'] if r else None,opponent=p['opponent'],forecast_commit=commit,event_id=event['id'],kickoff=event['date'],matched=r is not None,
                       source_url=f'https://www.espn.com/nfl/boxscore/_/gameId/{event["id"]}',formats={})
            for fmt,rec in FORMATS.items():
                projected=p['formats'][fmt]['projected_points']; actual=round(score(r,rec),2) if r else None
                # Cross-check published nflverse PPR and non-PPR totals, including fumbles/2PT/return TDs.
                if r and fmt in ('ppr','non_ppr'):
                    key='fantasy_points_ppr' if fmt=='ppr' else 'fantasy_points'
                    return_lost=float(r['fumbles_lost_total'])-sum(float(r[k]) for k in ('sack_fumbles_lost','rushing_fumbles_lost','receiving_fumbles_lost'))
                    if abs(actual-(float(r[key])-2*return_lost))>.011:raise ValueError(f'Scoring disagreement {p["name"]}: {actual} vs {r[key]}')
                row['formats'][fmt]={'projected':projected,'actual':actual,'error':round(actual-projected,2) if r else None}
            rows.append(row)
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('Duplicate forecasts')
    summary={}
    for fmt in FORMATS:
        top=[]
        for pos in ('QB','RB','WR','TE'):
            group=sorted([r for r in rows if r['position']==pos],key=lambda r:(-r['formats'][fmt]['projected'],r['name']))
            for rank,r in enumerate(group,1):r['formats'][fmt]['rank']=rank
            top+=group[:30]
        summary[fmt]={'top30':summarize(top,fmt),'all':summarize(rows,fmt),
                      'positions':{pos:summarize([r for r in top if r['position']==pos],fmt) for pos in ('QB','RB','WR','TE')}}
    result={'season':2026,'week':1,'source':'nflverse weekly player statistics, CC BY 4.0',
            'source_url':'https://github.com/nflverse/nflverse-data/releases/tag/stats_player',
            'actuals_sha256':hashlib.sha256((cache/'stats_player_week_2026.csv.gz').read_bytes()).hexdigest(),
            'forecasts':sources,'games':16,'methodology':'Last committed forecast before each game kickoff. Top 30 per position selected by those frozen projected points in each scoring format. Missing stat lines remain ungraded; they are not assumed zero. Full NFL scoring includes lost fumbles, two-point conversions and special-teams touchdowns.',
            'rows':rows,'summary':summary}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    return result
if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--cache',type=Path,required=True)
    r=capture(ap.parse_args().cache);print(json.dumps(r['summary'],indent=2))
