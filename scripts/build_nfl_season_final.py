#!/usr/bin/env python3
"""Reproduce final v1.5 from frozen local inputs; no benchmark or network reads."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import math
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'data/nfl_season/2026/v1.5-final'
FORMATS={'ppr':1,'half_ppr':0.5,'non_ppr':0}
PASS=('attempts','completions','passing_yards','passing_tds','passing_interceptions')
RECEIVE=('targets','receptions','receiving_yards','receiving_tds')
LIMITATIONS=['Current injuries unavailable','Refreshed ADP unavailable','Season sportsbook props and futures unavailable in this version','Week 1 markets excluded from season inputs','Missing evidence creates uncertainty; rankings are not guarantees']

def dump(value):return json.dumps(value,indent=2,sort_keys=True)+'\n'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(dump(value));temp.replace(path)
def norm(values):
    positive={k:max(0,float(v)) for k,v in values.items()};total=sum(positive.values())
    return {k:v/total if total else 1/len(positive) for k,v in positive.items()}
def allocate(total,weights):
    shares=norm(weights);keys=sorted(shares)
    result={k:round(total*shares[k],6) for k in keys}
    if keys:result[max(keys,key=lambda k:shares[k])]=round(result[max(keys,key=lambda k:shares[k])]+total-sum(result.values()),6)
    return result
def bounded(total,weights,caps):
    if total>sum(caps.values())+0.00001:raise ValueError('insufficient capacity')
    result={k:0.0 for k in caps};free=set(caps);remaining=total
    while free and remaining>1e-9:
        share=norm({k:weights[k] for k in sorted(free)})
        capped={k for k in free if remaining*share[k]>caps[k]-result[k]}
        if not capped:
            for k in free:result[k]+=remaining*share[k]
            break
        for k in sorted(capped):
            value=caps[k]-result[k];result[k]+=value;remaining-=value;free.remove(k)
    return {k:round(v,6) for k,v in result.items()}
def score(stat,reception):
    weights={'passing_yards':'.04','passing_tds':'4','passing_interceptions':'-2','rushing_yards':'.1','rushing_tds':'6','receiving_yards':'.1','receiving_tds':'6','receptions':str(reception),'fumbles_lost_total':'-2'}
    return float(sum(Decimal(str(stat[k]))*Decimal(w) for k,w in weights.items()))
def ranks(players):
    formats={}
    for fmt in FORMATS:
        ordered=sorted(players,key=lambda p:(-p['formats'][fmt],p['gsis_id']))
        seen=Counter();rows=[]
        for rank,p in enumerate(ordered,1):
            seen[p['position']]+=1
            rows.append({'gsis_id':p['gsis_id'],'player_id':p['player_id'],'name':p['name'],'team':p['team'],'position':p['position'],'url':p['url'],'fantasy_points':p['formats'][fmt],'overall_rank':rank,'position_rank':seen[p['position']]})
        formats[fmt]={'rows':rows}
    return {'method':'descending corresponding projected fantasy points; stable GSIS id breaks ties','manual_adjustments':0,'formats':formats}

def qb_distribution(room,features,budget):
    ordered=sorted(room,key=lambda p:(features[p['gsis_id']]['features']['current_depth_rank'] or 999,-features[p['gsis_id']]['features']['production_prior']['attempts'],-features[p['gsis_id']]['features']['historical_per_game']['attempts'],p['gsis_id']))
    starter=ordered[0];sid=starter['gsis_id'];backups=ordered[1:]
    if not backups:return {sid:1.0},{'primary_id':sid,'sole_qb_fallback':True,'conditional_primary_share':1.0,'absence_coverage_share':0.0}
    # Small same-game relief is observed history, not an invented passing package.
    relief={p['gsis_id']:features[p['gsis_id']]['historical_limited_relief_attempts_per_team_game'] for p in backups}
    package=min(0.02,sum(relief.values())/(budget['attempts']/17))
    active=starter['projected_games_active']/17
    primary_share=active*(1-package)
    # Coverage weights are conditional on needing a backup; availability is not
    # applied to expected-season prior totals a second time.
    depth=norm({p['gsis_id']:math.exp(-2.3*i) for i,p in enumerate(backups)})
    prior=norm({p['gsis_id']:features[p['gsis_id']]['features']['production_prior']['attempts'] for p in backups})
    has_prior=any(features[p['gsis_id']]['features']['production_prior']['attempts']>0 for p in backups)
    coverage=norm({p['gsis_id']:(0.65*prior[p['gsis_id']]+0.35*depth[p['gsis_id']]) if has_prior else depth[p['gsis_id']] for p in backups})
    package_dist=norm(relief)
    shares={sid:primary_share,**{p['gsis_id']:(1-active)*coverage[p['gsis_id']]+active*package*package_dist[p['gsis_id']] for p in backups}}
    return shares,{'primary_id':sid,'primary_expected_active_games':starter['projected_games_active'],'conditional_primary_share':1-package,'absence_coverage_share':1-active,'conditional_limited_relief_share':package,'backup_absence_distribution':coverage,'backup_relief_distribution':package_dist,'season_shares':shares,'sole_qb_fallback':False}

def te_distribution(room,features,pool):
    depth=norm({p['gsis_id']:math.exp(-0.9*((features[p['gsis_id']]['features']['current_depth_rank'] or 6)-1)) for p in room})
    primary={};hierarchy={}
    for p in room:
        pid=p['gsis_id'];f=features[pid]['features']
        if f['production_prior_present'] and f['production_prior']['targets']>0:
            primary[pid]=f['production_prior']['targets'];hierarchy[pid]='established expected-season target prior'
        elif f['historical_per_game']['targets']>0:
            primary[pid]=f['historical_per_game']['targets']*p['projected_games_active'];hierarchy[pid]='historical target rate times expected active games (once)'
        else:
            primary[pid]=pool*depth[pid];hierarchy[pid]='current-depth room share fallback'
    prior=norm(primary)
    result=norm({pid:0.8*prior[pid]+0.2*depth[pid] for pid in depth})
    return result,{'weights':{'established_or_historical_opportunity':0.8,'current_depth':0.2,'snap_participation':0},'input_hierarchy':hierarchy,'raw_opportunity_inputs':primary,'depth_shares':depth,'final_target_shares':result}

def build(inputs):
    features={p['base']['gsis_id']:p for p in inputs['players']}
    players=[copy.deepcopy(p['base']) for p in inputs['players']]
    if len(players)!=505 or len(features)!=505:raise ValueError('active identity population must be 505 unique GSIS ids')
    qb_audit=[];te_audit=[]
    for team,budget in sorted(inputs['team_budgets'].items()):
        qbs=[p for p in players if p['team']==team and p['position']=='QB']
        shares,qa=qb_distribution(qbs,features,budget);qb_audit.append({'team':team,**qa})
        attempts=allocate(budget['attempts'],shares)
        for metric in PASS:
            if metric=='attempts':values=attempts
            else:
                weights={p['gsis_id']:attempts[p['gsis_id']]*(p['stat_projection'][metric]/p['stat_projection']['attempts'] if p['stat_projection']['attempts'] else budget[metric]/budget['attempts']) for p in qbs}
                values=bounded(budget[metric],weights,attempts) if metric=='completions' else allocate(budget[metric],weights)
            for p in qbs:p.setdefault('_new_pass',{})[metric]=values[p['gsis_id']]
        for p in qbs:p['stat_projection'].update(p.pop('_new_pass'))
        tes=[p for p in players if p['team']==team and p['position']=='TE']
        pools={m:round(sum(p['stat_projection'][m] for p in tes),6) for m in RECEIVE}
        dist,ta=te_distribution(tes,features,pools['targets']);te_audit.append({'team':team,'preserved_room_budgets':pools,**ta})
        targets=allocate(pools['targets'],dist)
        for metric in RECEIVE:
            if metric=='targets':values=targets
            else:
                weights={p['gsis_id']:targets[p['gsis_id']]*(p['stat_projection'][metric]/p['stat_projection']['targets'] if p['stat_projection']['targets'] else pools[metric]/pools['targets']) for p in tes}
                values=bounded(pools[metric],weights,targets) if metric=='receptions' else allocate(pools[metric],weights)
            for p in tes:p.setdefault('_new_receive',{})[metric]=values[p['gsis_id']]
        for p in tes:p['stat_projection'].update(p.pop('_new_receive'))
    for p in players:
        pid=p['gsis_id'];f=features[pid]['features'];stat=p['stat_projection'];b=inputs['team_budgets'][p['team']]
        p['player_id']='nfl-'+p['sleeper_id'] if p.get('sleeper_id') else 'nfl-gsis-'+pid
        p['canonical_slug']=re.sub(r'[^a-z0-9]+','-',p['name'].lower()).strip('-')
        p['url']='/nfl/'+p['canonical_slug']+'/'
        p['methodology_version']='v1.5-final';p['data_cutoff']=inputs['cutoff_utc']
        p['formats']={fmt:score(stat,weight) for fmt,weight in FORMATS.items()}
        p['expected_season_opportunity']={m:stat[m] for m in ('attempts','carries','targets','receptions')}
        p['conditional_per_active_game']={m:stat[m]/p['projected_games_active'] for m in stat}
        p['evidence_limitation_flags']=LIMITATIONS.copy()
        if not f['production_prior_present']:p['evidence_limitation_flags'].append('No established player prior; general fallback applied')
        if f['current_depth_rank'] is None:p['evidence_limitation_flags'].append('Current exact depth unavailable; general fallback applied')
        p['allocation']={m+'_team_share':stat[m]/b[m] if b[m] else 0.0 for m in ('attempts','carries','targets','receptions','receiving_yards','receiving_tds')}
        p['backup_workload_explanation']='QB absence coverage and observed limited relief are separated from the primary conditional role; TE expected opportunity uses one primary prior with depth adjustment. RB/WR allocations remain approved v1.4.'
        if p['position']=='QB':
            rates=f['pbp'];den=rates['scramble_rate']+rates['designed_rush_rate']
            scramble=stat['carries']*rates['scramble_rate']/den if den else 0
            p['rushing_usage']={'scramble_carries':scramble,'designed_carries':stat['carries']-scramble if den else 0,'unclassified_carries':0 if den else stat['carries'],'pass_allocation_independent':True,'source':'historical PBP rate decomposition; QB carry/rush totals unchanged'}
    if len({p['canonical_slug'] for p in players})!=505:raise ValueError('canonical player slug collision')
    metadata={'schema_version':'lineupbeat-nfl-season-final-v1','methodology_version':'v1.5-final','season':2026,'cutoff_utc':inputs['cutoff_utc'],'status':'FINAL_DEVELOPMENT_ONLY','active_population':505,'position_counts':dict(Counter(p['position'] for p in players)),'base_v14_sha256':inputs['base_sha256'],'recommendations_enabled':False,'production_deployment_authorized':False,'private_benchmark_or_adp_tuning':False,'external_provider_requests':0,'model_api_calls':0,'model_api_cost_usd':0,'limitations':LIMITATIONS}
    return {'metadata':metadata,'players':players},qb_audit,te_audit

def validate(candidate,inputs):
    failures=[];teams=[];players=candidate['players']
    for p in players:
        if p['status']!='ACT' or not p['active_for_projection']:failures.append('inactive player '+p['gsis_id'])
        if any(not math.isfinite(n) or n<0 for n in p['stat_projection'].values()):failures.append('negative/nonfinite component '+p['gsis_id'])
        if any(s>1+1e-6 or s<0 for s in p['allocation'].values()):failures.append('invalid share '+p['gsis_id'])
        if not 0<p['projected_games_active']<17:failures.append('invalid availability '+p['gsis_id'])
        for fmt,weight in FORMATS.items():
            if p['formats'][fmt]!=score(p['stat_projection'],weight):failures.append('scoring '+p['gsis_id'])
    for team,b in sorted(inputs['team_budgets'].items()):
        room=[p for p in players if p['team']==team]
        sums={m:sum(p['stat_projection'][m] for p in room) for m in room[0]['stat_projection']}
        sums['targetable_attempts']=sums['targets'];sums['non_targeted_attempts']=sums['attempts']-sums['targets']
        delta={m:sums[m]-b[m] for m in b}
        if any(abs(n)>.01 for n in delta.values()):failures.append('team reconciliation '+team)
        if abs(sums['receptions']-sums['completions'])>.01 or abs(sums['receiving_yards']-sums['passing_yards'])>.01 or abs(sums['receiving_tds']-sums['passing_tds'])>.01:failures.append('team receiving reconciliation '+team)
        rooms={pos:{m:sum(p['stat_projection'][m] for p in room if p['position']==pos)/b[m] if b[m] else 0 for m in ('attempts','targets','carries','receptions')} for pos in ('QB','RB','WR','TE')}
        teams.append({'team':team,'budgets':b,'player_sums':sums,'differences':delta,'position_room_shares':rooms,'pass':all(abs(n)<=.01 for n in delta.values())})
    return {'status':'PASS' if not failures else 'FAIL','failures':failures,'teams':teams,'tolerance':.01,'active_count':len(players),'identity_count':len({p['gsis_id'] for p in players})}

def review(candidate,inputs,ranking):
    old={x['base']['gsis_id']:x for x in inputs['players']}
    oldplayers=[]
    for x in old.values():
        p=copy.deepcopy(x['base']);p.update(player_id=p['gsis_id'],url='');oldplayers.append(p)
    oldrank=inputs.get('base_rankings') or ranks(oldplayers)
    before={fmt:{p['gsis_id']:p for p in oldrank['formats'][fmt]['rows']} for fmt in FORMATS}
    after={fmt:{p['gsis_id']:p for p in ranking['formats'][fmt]['rows']} for fmt in FORMATS}
    output=[]
    for p in candidate['players']:
        pid=p['gsis_id'];o=old[pid]['base'];f=old[pid]['features'];reasons=[]
        differences={fmt:p['formats'][fmt]-o['formats'][fmt] for fmt in FORMATS}
        changed=any(abs(p['stat_projection'][m]-o['stat_projection'][m])>1e-5 for m in p['stat_projection'])
        if pid in inputs['tier1_ids']:reasons.append('remaining Tier 1')
        if p['position'] in ('QB','TE') and changed:reasons.append('QB/TE general correction')
        if max(abs(v) for v in differences.values())>=30:reasons.append('at least 30 points changed')
        if any(abs(before[fmt][pid][k]-after[fmt][pid][k])>=24 for fmt in FORMATS for k in ('overall_rank','position_rank')):reasons.append('at least 24 ranks changed')
        if any((before[fmt][pid]['position_rank']<=t)!=(after[fmt][pid]['position_rank']<=t) for fmt in FORMATS for t in (12,24,36,48)):reasons.append('positional threshold crossed')
        if p['name'] in ('James Cook','Tony Pollard','Rico Dowdle','Bhayshul Tuten'):reasons.append('explicitly requested case')
        if not f['production_prior_present']:reasons.append('no established prior')
        if f['current_depth_rank'] is None:reasons.append('no current exact depth')
        disposition='Explicitly disclosed low-evidence projection' if not f['production_prior_present'] or f['current_depth_rank'] is None else 'Corrected by the general formula' if changed else 'Approved'
        output.append({'gsis_id':pid,'name':p['name'],'position':p['position'],'team':p['team'],'queue_required':bool(reasons),'triggers':reasons,'disposition':disposition,'disposition_basis':'deterministic integrity review under Ralph authorization; no claim of separate per-player human signoff','component_changes':{m:p['stat_projection'][m]-o['stat_projection'][m] for m in p['stat_projection']},'format_changes':differences,'prior_position_ranks':{fmt:before[fmt][pid]['position_rank'] for fmt in FORMATS},'final_position_ranks':{fmt:after[fmt][pid]['position_rank'] for fmt in FORMATS}})
    return {'status':'COMPLETE_NO_UNRESOLVED_HOLDS','authority':'Ralph authorized the general final model and approved existing RB/identity conclusions in the task attachment; Codex performed computational review.','players':output,'queue_count':sum(p['queue_required'] for p in output),'excluded':[dict(p,disposition='Excluded because not projection-active') for p in inputs['excluded']]}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=OUTPUT);args=parser.parse_args()
    inputs=json.loads((OUTPUT/'inputs.json').read_text())
    candidate,qb,te=build(inputs);validation=validate(candidate,inputs)
    if validation['status']!='PASS':raise ValueError(validation['failures'])
    ranking=ranks(candidate['players']);queue=review(candidate,inputs,ranking)
    path=args.output/'season_projections.json'
    # Frozen numerical output cannot be tuned after the private QA has run.
    freeze=OUTPUT/'benchmark_freeze.json'
    if freeze.exists() and hashlib.sha256(dump(candidate).encode()).hexdigest()!=json.loads(freeze.read_text())['season_sha256']:
        raise ValueError('post-benchmark numerical mutation prohibited')
    for name,value in [('season_projections.json',candidate),('season_rankings.json',ranking),('qb_allocation.json',{'teams':qb}),('te_allocation.json',{'teams':te}),('validation.json',validation),('final_review_queue.json',queue)]:write(args.output/name,value)
    print(dump({'version':'v1.5-final','active':len(candidate['players']),'validation':validation['status'],'queue':queue['queue_count'],'season_sha256':sha(path),'provider_requests':0}))
    return 0

if __name__=='__main__':raise SystemExit(main())
