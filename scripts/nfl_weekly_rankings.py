"""Independent ranking output for three reception rules and two lineup types.

The projection remains an expected stat line. Rankings can use a trained
mixture of mean and typical outcome, then express overall value relative to
the next player outside a declared reference starting lineup. Superflex changes
eligible starting slots, never scoring or the statistical forecast.
"""
from __future__ import annotations
import hashlib, json, math
from collections import defaultdict
from nfl_usage_model import FORMATS, POSITIONS, score

LINEUPS={
    'one_qb':{'teams':12,'QB':1,'RB':2,'WR':2,'TE':1,'flex':1,'superflex':0},
    'superflex':{'teams':12,'QB':1,'RB':2,'WR':2,'TE':1,'flex':1,'superflex':1},
}


def poisson(mean):
    if mean<0 or not math.isfinite(mean):raise ValueError('Invalid TD expectation')
    weights=[math.exp(-mean)]
    while sum(weights)<1-1e-10:
        if len(weights)>100:raise ValueError('Unbounded TD distribution')
        weights.append(weights[-1]*mean/len(weights))
    return weights


def typical_points(stat, rv, dispersion):
    expected=score(stat,rv)
    passing=stat['passing_tds'];other=stat['rushing_tds']+stat['receiving_tds']
    non_td=expected-4*passing-6*other
    if not passing and not other:return expected
    variance=dispersion['variance_intercept']+dispersion['variance_per_point']*max(0,non_td)
    sigma=math.sqrt(max(.01,variance))
    shifts=defaultdict(float)
    for a,pa in enumerate(poisson(passing)):
        for b,pb in enumerate(poisson(other)):
            shifts[4*a+6*b]+=pa*pb
    low=non_td-10*sigma;high=non_td+max(shifts)+10*sigma
    for _ in range(40):
        x=(low+high)/2
        cdf=sum(prob*(1+math.erf((x-non_td-shift)/(sigma*math.sqrt(2))))/2 for shift,prob in shifts.items())
        if cdf<.5:low=x
        else:high=x
    return (low+high)/2


def replacement_levels(players, points, lineup):
    """Allocate mandatory, FLEX and Superflex slots without double counting."""
    ordered={pos:sorted([p for p in players if p['position']==pos],key=lambda p:(-points[p['id']],p['id'])) for pos in POSITIONS}
    used={pos:min(len(ordered[pos]),lineup['teams']*lineup[pos]) for pos in POSITIONS}
    for key,positions in (('flex',('RB','WR','TE')),('superflex',POSITIONS)):
        for _ in range(lineup['teams']*lineup[key]):
            available=[pos for pos in positions if used[pos]<len(ordered[pos])]
            if not available:break
            chosen=max(available,key=lambda pos:(points[ordered[pos][used[pos]]['id']],-POSITIONS.index(pos)))
            used[chosen]+=1
    levels={pos:points[ordered[pos][used[pos]]['id']] if used[pos]<len(ordered[pos]) else 0 for pos in POSITIONS}
    return levels,used


def build(payload, model, source_sha256):
    eligible=[p for p in payload['players'] if not p['availability']['projection_adjusted']]
    weight=model.get('ranking_typical_weight',0)
    history_weight=model.get('ranking_history_weight',0)
    rows={p['id']:{'id':p['id'],'name':p['name'],'team':p['team'],'position':p['position'],
                  'eligible':not p['availability']['projection_adjusted'],
                  'eligibility_reason':'Confirmed unavailable' if p['availability']['projection_adjusted'] else 'Available roster identity',
                  'formats':{}} for p in payload['players']}
    baselines={}
    for fmt,rv in FORMATS.items():
        points={}
        for p in eligible:
            expected=score(p['stat_projection'],rv)
            typical=typical_points(p['stat_projection'],rv,model['ranking_dispersion'][fmt][p['position']])
            evidence=p.get('evidence',{})
            baseline=evidence.get('baseline',{}).get(fmt,expected)
            hw=history_weight if evidence.get('prior_appearances',0)+evidence.get('current_appearances',0)>0 else 0
            points[p['id']]=(1-hw)*((1-weight)*expected+weight*typical)+hw*baseline
            rows[p['id']]['formats'][fmt]={'expected_points':round(expected,4),'typical_points':round(typical,4),
                                         'history_points':round(baseline,4),'ranking_points':round(points[p['id']],4),'leagues':{}}
        positional={}
        for pos in POSITIONS:
            group=sorted((p for p in eligible if p['position']==pos),key=lambda p:(-points[p['id']],p['id']))
            positional.update({p['id']:rank for rank,p in enumerate(group,1)})
        baselines[fmt]={}
        for league,lineup in LINEUPS.items():
            levels,slots=replacement_levels(eligible,points,lineup)
            baselines[fmt][league]={'replacement_points':{k:round(v,4) for k,v in levels.items()},'filled_slots':slots}
            values={p['id']:points[p['id']]-levels[p['position']] for p in eligible}
            ordered=sorted(eligible,key=lambda p:(-values[p['id']],-points[p['id']],p['id']))
            for rank,p in enumerate(ordered,1):
                rows[p['id']]['formats'][fmt]['leagues'][league]={
                    'overall_rank':rank,'position_rank':positional[p['id']],
                    'lineup_value':round(values[p['id']],4)}
    return {'schema_version':'lineupbeat-weekly-rankings-v2','season':payload['season'],'week':payload['week'],
            'updated_at':payload['updated_at'],'projection_sha256':source_sha256,
            'model_version':model['version'],'scoring_formats':list(FORMATS),'lineups':LINEUPS,
            'methodology':{'projection':'Expected fantasy points calculated from the projected stat line.',
                           'ranking':'Positional ranks use weights selected for player ordering on 2024: expected points, typical outcome and recent recorded scoring history. Overall ranks subtract the next available positional replacement after filling the reference lineup.',
                           'typical_weight':weight,'history_weight':history_weight,'eligibility':'Confirmed unavailable players remain in projections at zero and have no ranking. Questionable and Doubtful are not discounted.',
                           'superflex':'Adds one QB/RB/WR/TE-eligible slot per team; changes overall positional demand without changing scoring, projections or within-position order.'},
            'replacement_levels':baselines,'players':list(rows.values())}


def validate(payload, rankings, projection_sha256):
    if rankings.get('projection_sha256')!=projection_sha256:raise ValueError('Rankings refer to a different forecast')
    if (rankings['season'],rankings['week'],rankings['updated_at'])!=(payload['season'],payload['week'],payload['updated_at']):
        raise ValueError('Ranking release does not match projections')
    original={p['id']:p for p in payload['players']}
    if len(rankings['players'])!=len(original) or {r['id'] for r in rankings['players']}!=set(original):
        raise ValueError('Ranking identity coverage mismatch')
    for r in rankings['players']:
        p=original[r['id']]
        if (r['name'],r['team'],r['position'])!=(p['name'],p['team'],p['position']):raise ValueError('Ranking identity mismatch')
        if r['eligible']==p['availability']['projection_adjusted']:raise ValueError('Invalid ranking eligibility')
        if not r['eligible']:
            if r['formats']:raise ValueError('Unavailable player is ranked')
            continue
        for fmt,rv in FORMATS.items():
            f=r['formats'][fmt]
            if abs(f['expected_points']-score(p['stat_projection'],rv))>.0002:raise ValueError('Rankings changed expected points')
            if f['leagues']['one_qb']['position_rank']!=f['leagues']['superflex']['position_rank']:
                raise ValueError('Superflex changed position order')
    n=sum(r['eligible'] for r in rankings['players'])
    for fmt in FORMATS:
        for league in LINEUPS:
            rs=[r['formats'][fmt]['leagues'][league]['overall_rank'] for r in rankings['players'] if r['eligible']]
            if sorted(rs)!=list(range(1,n+1)):raise ValueError('Overall ranks are not consecutive')
            for pos in POSITIONS:
                rs=[r['formats'][fmt]['leagues'][league]['position_rank'] for r in rankings['players'] if r['eligible'] and r['position']==pos]
                if sorted(rs)!=list(range(1,len(rs)+1)):raise ValueError('Position ranks are not consecutive')


def fit_ranking_weight(records, dispersion):
    """Choose mean/typical weighting on 2024 pairwise ordering accuracy only."""
    groups=defaultdict(list)
    for r in records:
        for fmt,rv in FORMATS.items():
            f=r['formats'][fmt]
            typical=typical_points(r['stat_projection'],rv,dispersion[fmt][r['position']])
            groups[(r['week'],r['position'],fmt)].append((f['projected'],typical,f['baseline'],f['actual']))
    trials=[]
    for weight in (0,.5,1):
        for hw in (0,.15,.3,.5):
            correct=total=0
            for group in groups.values():
                for i,(mean,typical,baseline,actual) in enumerate(group):
                    pred=(1-hw)*((1-weight)*mean+weight*typical)+hw*baseline
                    for mean_b,typical_b,baseline_b,actual_b in group[i+1:]:
                        if abs(actual-actual_b)<.05:continue
                        pred_b=(1-hw)*((1-weight)*mean_b+weight*typical_b)+hw*baseline_b
                        total+=1
                        correct+=1 if (pred-pred_b)*(actual-actual_b)>0 else (.5 if abs(pred-pred_b)<1e-9 else 0)
            trials.append({'weight':weight,'history_weight':hw,'correct_pairs':correct,'total_pairs':total,'accuracy':correct/total})
    chosen=max(trials,key=lambda r:(r['accuracy'],-r['history_weight'],-r['weight']))
    return chosen,trials
