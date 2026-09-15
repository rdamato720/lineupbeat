"""Choose model settings on 2024 only; evaluate the frozen result on 2025.

Replay uses prior-week rosters, strictly earlier game statistics and pregame
depth snapshots (previous-week depth for the older weekly schema). Inactive
players without final stat lines remain ungraded. This is a reproducible test
of the component engine, not a substitute proxy forecast.
"""
from __future__ import annotations
import argparse, hashlib, json, math, statistics
from collections import defaultdict
from pathlib import Path
from nfl_usage_model import History, predict, score, FORMATS, POSITIONS, VERSION

ROOT=Path(__file__).resolve().parents[1]
DEFAULT={'player_prior_games':3,'team_prior_games':4,'efficiency_prior':60,
         'scoring_opportunity_weight':.5,'opponent_weight':.15,'neutral_weight':.15,'snap_weight':.2}
# This finite search is declared before inspecting 2025. Only the training
# season can select a parameter, and scoring formats share one stat model.
SEARCH={'player_prior_games':[1,3,6], 'efficiency_prior':[30,60,120],
        'scoring_opportunity_weight':[0,.25,.5], 'opponent_weight':[0,.15,.3],
        'snap_weight':[0,.2,.4], 'neutral_weight':[0,.15,.3], 'team_prior_games':[2,4,8]}


def metrics(values):
    if not values:return {'n':0,'mae':None,'rmse':None,'bias_actual_minus_projected':None}
    return {'n':len(values),'mae':round(statistics.fmean(abs(x) for x in values),4),
            'rmse':round(math.sqrt(statistics.fmean(x*x for x in values)),4),
            'bias_actual_minus_projected':round(statistics.fmean(values),4)}


def replay(history, feature_sets, config, keep=False):
    errors=defaultdict(list);baseline_errors=defaultdict(list);records=[]
    population=matched=0
    for features in feature_sets:
        result=predict(features,config)
        by_id={p['id']:p for p in result['players']}
        # The comparison sample is fixed by the naive pregame baseline, never
        # by actual top scorers or by each candidate's preferred sample.
        sample=set()
        for pos in POSITIONS:
            group=sorted((p for p in features['players'] if p['position']==pos),
                         key=lambda p:(-p['baseline']['half_ppr'],p['id']))
            sample.update(p['id'] for p in group[:30])
        for p in features['players']:
            if p['id'] not in sample:continue
            population+=1
            game=features['slate'][p['team']]
            actual=history.actual.get((p['id'],game['game_id']))
            if actual is None:continue
            if actual['team']!=p['team'] or actual['opponent']!=game['opponent']:
                continue
            matched+=1
            predicted=by_id[p['id']]
            row={'id':p['id'],'name':p['name'],'position':p['position'],'team':p['team'],
                 'week':features['week'],'game_id':game['game_id'],'formats':{}}
            for fmt,rv in FORMATS.items():
                observed=score(actual,rv)
                point=score(predicted['stat_projection'],rv)
                errors[(fmt,p['position'])].append(observed-point)
                baseline_errors[(fmt,p['position'])].append(observed-p['baseline'][fmt])
                row['formats'][fmt]={'projected':round(point,4),'actual':round(observed,4),
                                     'baseline':round(p['baseline'][fmt],4)}
                if keep:
                    non_td=point-4*predicted['stat_projection']['passing_tds']-6*(predicted['stat_projection']['rushing_tds']+predicted['stat_projection']['receiving_tds'])
                    actual_non_td=observed-4*actual['passing_tds']-6*(actual['rushing_tds']+actual['receiving_tds'])
                    row['formats'][fmt].update(non_td_mean=non_td,non_td_error=actual_non_td-non_td)
            if keep:
                row['stat_projection']=predicted['stat_projection']
                records.append(row)
    objective=statistics.fmean(statistics.fmean(e*e for e in errors[(fmt,pos)])
                              for fmt in FORMATS for pos in POSITIONS)
    summary={fmt:{'positions':{pos:{'model':metrics(errors[(fmt,pos)]),'baseline':metrics(baseline_errors[(fmt,pos)])}
                               for pos in POSITIONS},
                  'overall':{'model':metrics([x for pos in POSITIONS for x in errors[(fmt,pos)]]),
                             'baseline':metrics([x for pos in POSITIONS for x in baseline_errors[(fmt,pos)]])}}
             for fmt in FORMATS}
    return objective,{'population':population,'matched':matched,'ungraded':population-matched,'formats':summary},records


def fit_dispersion(records):
    """Estimate non-TD score uncertainty on training residuals only.

    A nonnegative affine variance curve supplies the normal component of a
    Poisson-touchdown mixture. It is used for a typical-outcome ranking score,
    not advertised as a calibrated player floor or ceiling.
    """
    result={}
    for fmt in FORMATS:
        result[fmt]={}
        for pos in POSITIONS:
            rows=[r['formats'][fmt] for r in records if r['position']==pos]
            xs=[max(0,r['non_td_mean']) for r in rows]
            ys=[r['non_td_error']**2 for r in rows]
            xbar=statistics.fmean(xs);ybar=statistics.fmean(ys)
            den=sum((x-xbar)**2 for x in xs)
            slope=max(0,sum((x-xbar)*(y-ybar) for x,y in zip(xs,ys))/den) if den else 0
            intercept=max(.01,ybar-slope*xbar)
            result[fmt][pos]={'variance_intercept':round(intercept,6),'variance_per_point':round(slope,6),'training_rows':len(rows)}
    return result


def ranking_validation(records, model):
    from nfl_weekly_rankings import typical_points
    grouped=defaultdict(list)
    for row in records:
        for fmt,rv in FORMATS.items():
            f=row['formats'][fmt]
            typical=typical_points(row['stat_projection'],rv,model['ranking_dispersion'][fmt][row['position']])
            weight=model['ranking_typical_weight']
            hw=model.get('ranking_history_weight',0)
            ranking=(1-hw)*((1-weight)*f['projected']+weight*typical)+hw*f['baseline']
            grouped[(fmt,row['week'],row['position'])].append((f['projected'],ranking,f['actual']))
    result={}
    for fmt in FORMATS:
        totals=[0,0,0]
        for (scoring,_,_),group in grouped.items():
            if scoring!=fmt:continue
            for i,a in enumerate(group):
                for b in group[i+1:]:
                    if abs(a[2]-b[2])<.05:continue
                    totals[2]+=1
                    for k in (0,1):
                        totals[k]+=1 if (a[k]-b[k])*(a[2]-b[2])>0 else (.5 if abs(a[k]-b[k])<1e-9 else 0)
        result[fmt]={'unequal_actual_pairs':totals[2], 'projection_order_accuracy':totals[0]/totals[2], 'ranking_order_accuracy':totals[1]/totals[2]}
    return result


def run(cache, output, frozen=False):
    history=History(cache,years=(2023,2024,2025))
    training=[]
    for week in range(2,19):
        training.append(history.features(2024,week))
    config=json.loads((output/'model.json').read_text())['parameters'] if frozen else dict(DEFAULT)
    trials=json.loads((output/'validation.json').read_text())['training_trials'] if frozen else []
    best,_,_=replay(history,training,config)
    print(f'Training starting MSE: {best:.4f}',flush=True)
    for cycle in range(0 if frozen else 2):
        for key,values in SEARCH.items():
            chosen=config[key]
            for value in values:
                candidate={**config,key:value}
                objective,_,_=replay(history,training,candidate)
                trials.append({'cycle':cycle+1,'parameter':key,'value':value,'mse':round(objective,6),'config':candidate})
                if objective<best-1e-9:
                    best=objective;chosen=value
            config[key]=chosen
            print(f'Training cycle {cycle+1}: {key}={chosen}; MSE {best:.4f}',flush=True)
    _,train_report,train_records=replay(history,training,config,keep=True)
    model={'version':VERSION,'training_season':2024,'training_weeks':list(range(2,19)),
           'parameters':config,'ranking_dispersion':fit_dispersion(train_records),
           'code_sha256':hashlib.sha256((ROOT/'scripts/nfl_usage_model.py').read_bytes()).hexdigest()}
    from nfl_weekly_rankings import fit_ranking_weight
    selected,ranking_trials=fit_ranking_weight(train_records,model['ranking_dispersion'])
    model.update(ranking_typical_weight=selected['weight'],ranking_history_weight=selected['history_weight'],ranking_code_sha256=hashlib.sha256((ROOT/'scripts/nfl_weekly_rankings.py').read_bytes()).hexdigest())
    # Freeze the chosen model before building or inspecting any test result.
    output.mkdir(parents=True,exist_ok=True)
    (output/'model.json').write_text(json.dumps(model,indent=2,sort_keys=True)+'\n')
    print('Parameters frozen; beginning 2025 holdout.',flush=True)
    testing=[history.features(2025,week) for week in range(2,19)]
    _,test_report,test_records=replay(history,testing,config,keep=True)
    report={'version':VERSION,'production_component_engine_reproduced':True,
            'model_sha256':hashlib.sha256((output/'model.json').read_bytes()).hexdigest(),
            'training':train_report,'holdout_season':2025,'holdout_weeks':list(range(2,19)),
            'holdout':test_report,'future_game_rows_used':0,
            'ranking_holdout':ranking_validation(test_records,model),
            'evaluation_note':('Parameters retained after the first holdout review; replay repeated after correcting fumble counts to use reconciled opportunities. No parameter was chosen from 2025 results.' if frozen else 'Parameters selected on 2024 before holdout evaluation.'),
            'population':'Top 30 per position by strictly prior last-eight-appearance Half-PPR baseline; same players for all candidate comparisons and scoring formats.',
            'baseline':'Mean scoring-format points over up to eight prior recorded appearances.',
            'availability_limitation':'Historical evaluation uses prior-week active roster status and final matched stat lines. It does not reconstruct every pregame injury report. Missing actual rows remain ungraded.',
            'depth_limitation':'2024 depth uses the preceding weekly snapshot; 2025 uses the latest timestamp strictly before the first kickoff of that week.',
            'training_trials':trials,'ranking_training_trials':ranking_trials,
            'sources':[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(Path(cache).glob('*.csv.gz')) if '2026' not in p.name],
            'provider_calls':{'paid_api':0,'model_api':0,'cost_usd':0}}
    (output/'validation.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    (Path(cache)/'training_predictions.json').write_text(json.dumps(train_records,separators=(',',':'))+'\n')
    (Path(cache)/'holdout_predictions.json').write_text(json.dumps(test_records,separators=(',',':'))+'\n')
    print(json.dumps({'model':model,'holdout':test_report},indent=2),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--cache',type=Path,required=True)
    ap.add_argument('--output',type=Path,default=ROOT/'data/nfl_weekly/model-v2')
    ap.add_argument('--frozen-parameters',action='store_true')
    args=ap.parse_args();run(args.cache,args.output,args.frozen_parameters)
