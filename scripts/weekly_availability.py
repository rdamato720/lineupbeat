"""Apply reviewed, game-specific availability reports ahead of lagging feeds."""
import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'data/weekly_availability_reports.json'


def apply_reports(payload, reports=None):
    reports=reports if reports is not None else json.loads(CONFIG.read_text())['reports']
    applied=[]
    for report in reports:
        if (report['season'],report['week'])!=(payload['season'],payload['week']):continue
        matches=[p for p in payload['players'] if p['id']==report['player_id']]
        if len(matches)!=1:raise ValueError('Reported unavailable player identity is missing or ambiguous')
        player=matches[0]
        if (player['name'],player['team'],player['position'],player['game_id'])!=(report['name'],report['team'],report['position'],report['game_id']):
            raise ValueError('Availability report identity or game mismatch')
        if report['status']!='Out' or not report['source_url'].startswith('https://') or not report['reviewed_at']:
            raise ValueError('Invalid reviewed availability report')
        prior=player['availability'].get('feed_status_before_report',player['availability']['status'])
        player['availability']={**player['availability'],'state':'out','status':'Out','tag':'O',
            'source':report['source'],'source_url':report['source_url'],'updated_at':report['reviewed_at'],
            'projection_adjusted':True,'projection_factor':0.0,'feed_status_before_report':prior,
            'policy':'Reported unavailable for this game; projection set to zero'}
        player['stat_projection']={k:0.0 for k in player['stat_projection']}
        player['expected_opportunity']={k:0.0 for k in player['expected_opportunity']}
        for fmt in player['formats']:player['formats'][fmt]['projected_points']=0.0
        applied.append(report)
    if not applied:return payload
    for fmt in ('ppr','half_ppr','non_ppr'):
        counts=defaultdict(int)
        for overall,p in enumerate(sorted(payload['players'],key=lambda p:(-p['formats'][fmt]['projected_points'],p['name'])),1):
            counts[p['position']]+=1
            p['formats'][fmt].update(overall_rank=overall,position_rank=counts[p['position']])
    payload['players'].sort(key=lambda p:(p['position'],p['formats']['half_ppr']['position_rank'],p['name']))
    payload['reviewed_availability_reports']=applied
    return payload


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--snapshot',type=Path,required=True);args=parser.parse_args()
    old=json.loads(args.snapshot.read_text());payload=apply_reports(json.loads(args.snapshot.read_text()))
    if payload==old:
        print('Reviewed availability already current; zero provider calls');return
    from validate_daily_fantasy_refresh import validate
    validate(payload,old)
    temporary=args.snapshot.with_suffix('.tmp');temporary.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n');temporary.replace(args.snapshot)
    print('Applied reviewed game availability and reconciled ranks; zero provider calls')

if __name__=='__main__':main()
