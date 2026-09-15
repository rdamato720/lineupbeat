"""Build the independent Week 2 release from explicit captured inputs."""
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
import build_week1_intelligence as model
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/nfl_weekly/2026/week-2/v1.0'

def build(cache):
    manifest=json.loads((cache/'capture_manifest.json').read_text())
    model.CACHE=cache
    payload,_,_,provenance=model.build(injury_path=cache/'espn_injuries.json',week=2,independent=True)
    now=datetime.fromisoformat(manifest['captured_at'].replace('Z','+00:00'))
    previous=OUT/'projections.json'
    if previous.exists():
        old=json.loads(previous.read_text())
        closed={p['team'] for p in old['players'] if datetime.fromisoformat(p['kickoff'])<=now}
        if closed:
            payload['players']=[p for p in payload['players'] if p['team'] not in closed]+[p for p in old['players'] if p['team'] in closed]
            for club in closed:
                for key in ('team_workload_audit','team_workload_budgets'):
                    payload[key][club]=old[key][club]
            from collections import defaultdict
            for fmt in payload['available_formats']:
                counts=defaultdict(int)
                for rank,p in enumerate(sorted(payload['players'],key=lambda p:(-p['formats'][fmt]['projected_points'],p['name'])),1):
                    counts[p['position']]+=1
                    p['formats'][fmt].update(overall_rank=rank,position_rank=counts[p['position']])
            pop=payload['population'];pop['ranked_production']=len(payload['players'])+len(payload['excluded_players'])
            pop['ranked_active_projected']=len(payload['players'])
            pop['identity_resolved']=pop['ranked_production']+pop['identity_resolved_not_ranked']
            pop['projection_source']=pop['identity_resolved']+pop['identity_unresolved']
            payload['locked_teams']=sorted(closed)
    payload['sources']['week1_usage']={'label':'nflverse 2026 Week 1 opportunity counts','updated_at':manifest['captured_at']}
    # Public lineage includes digests and source locations, not raw provider responses.
    provenance['assets']=manifest['assets']
    provenance['methodology']=payload['methodology']
    OUT.mkdir(parents=True,exist_ok=True)
    for name,value in [('projections.json',payload),('provenance.json',provenance)]:
        (OUT/name).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
    return payload
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--cache',type=Path,required=True)
    p=build(ap.parse_args().cache)
    print(f"Week 2: {len(p['players'])} players, {len({r['team'] for r in p['players']})} teams, updated {p['updated_at']}")
