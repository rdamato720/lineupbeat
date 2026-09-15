"""Fit usage models on earlier games; choose complexity on later 2024 games.

The previously inspected 2025 data are used only for a frozen regression replay.
No uploaded forecasts, rankings, external predictions or player-name features.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path

from nfl_usage_model import History, FORMATS
from nfl_workload_model import KEYS, feature_rows, tree_predict
from train_nfl_usage_model import replay, fit_dispersion, ranking_validation
from nfl_weekly_rankings import fit_ranking_weight

ROOT = Path(__file__).resolve().parents[1]


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()


def training_data(history, sets, base):
    x, y = [], {key:[] for key in KEYS}
    identity = []
    names = None
    for features in sets:
        vectors = feature_rows(features, base)
        if names is None:
            names = sorted(next(iter(vectors.values())))
        for p in features['players']:
            if p['position'] == 'QB' or not vectors[p['id']]['last_team_game_observed']:
                continue
            game = features['slate'][p['team']]
            actual = history.actual.get((p['id'], game['game_id']))
            snap = history.snap.get((p['id'], game['game_id']))
            if actual and (actual['team'] != p['team'] or actual['opponent'] != game['opponent']):
                continue
            if not actual and (not snap or snap.get('team') != p['team']):
                continue
            totals = history.team_games.get((game['game_id'], p['team']))
            if not totals or any(totals.get(k,0) <= 0 for k in KEYS):
                continue
            # A recorded snap row establishes a game appearance; a player
            # without a stat row or a snap row is unknown, never a zero label.
            x.append([vectors[p['id']][name] for name in names])
            for key in KEYS:
                y[key].append((actual or {}).get(key, 0.0)/totals[key])
            identity.append({'season':features['season'],'week':features['week'],'id':p['id'],
                             'game_id':game['game_id'],'recorded_stats':bool(actual)})
    return names, x, y, identity


def export(estimator, names):
    trees = []
    for stage in estimator.estimators_:
        t = stage[0].tree_
        trees.append([[int(t.feature[i]),float(t.threshold[i]),int(t.children_left[i]),
                       int(t.children_right[i]),float(t.value[i,0,0])] for i in range(t.node_count)])
    return {'features':names,'intercept':float(estimator.init_.constant_[0,0]),
            'learning_rate':estimator.learning_rate,'trees':trees}


def fit(dataset, depth):
    from sklearn.ensemble import GradientBoostingRegressor
    import numpy as np
    names, x, targets, _ = dataset
    heads = {}
    for key in KEYS:
        estimator = GradientBoostingRegressor(loss='squared_error',n_estimators=100,
            learning_rate=.05,max_depth=depth,min_samples_leaf=40,random_state=720)
        estimator.fit(x, targets[key])
        heads[key] = export(estimator,names)
        observed = estimator.predict(x)
        portable = [tree_predict(heads[key], dict(zip(names, row))) for row in x]
        error = float(np.max(np.abs(observed-portable)))
        if error > 1e-10:
            raise ValueError(f'Portable model differs from training inference: {error}')
    return heads


def gates(before, after):
    result = []
    for fmt in FORMATS:
        b, a = before['formats'][fmt], after['formats'][fmt]
        for metric in ('mae','rmse'):
            result.append({'format':fmt,'scope':'overall','metric':metric,
                           'before':b['overall']['model'][metric],'after':a['overall']['model'][metric],
                           'passed':a['overall']['model'][metric] <= b['overall']['model'][metric]})
        for pos in b['positions']:
            result.append({'format':fmt,'scope':pos,'metric':'mae','tolerance':.15,
                           'before':b['positions'][pos]['model']['mae'],'after':a['positions'][pos]['model']['mae'],
                           'passed':a['positions'][pos]['model']['mae'] <= b['positions'][pos]['model']['mae']+.15})
    return result


def run(cache, output):
    output.mkdir(parents=True, exist_ok=True)
    original = json.loads((ROOT/'data/nfl_weekly/model-v2/baseline-v2.0.json').read_text())
    base = original['parameters']
    history = History(cache,years=(2023,2024,2025))
    earlier = [history.features(2023,w) for w in range(2,19)]
    training = [history.features(2024,w) for w in range(2,19)]
    fit_sets, select_sets = earlier+training[:8], training[8:]
    dataset = training_data(history,fit_sets,base)
    print(f'Fitting {len(dataset[1])} player-games; selecting on 2024 Weeks 10–18.',flush=True)
    best, published_selection, _ = replay(history,select_sets,base)
    selection = {'depth':None,'weights':dict.fromkeys(KEYS,0)}
    trials = []
    # Finite choices declared before the regression replay. No search on 2025.
    for depth in (2,3):
        heads = fit(dataset,depth)
        for carry_weight, target_weight in itertools.product((0,.5,1),repeat=2):
            if carry_weight == target_weight == 0:
                continue
            weights = dict(zip(KEYS,(carry_weight,target_weight)))
            params = {**base,'workload_model':{'heads':heads,'weights':weights}}
            objective, summary, _ = replay(history,select_sets,params)
            trials.append({'depth':depth,'weights':weights,'objective':objective,'report':summary})
            if objective < best:
                best = objective
                selection = {'depth':depth,'weights':weights}
        print(f'Depth {depth} complete; selected {selection}, MSE {best:.6f}',flush=True)
    if selection['depth'] is None:
        (output/'selection.json').write_bytes(encoded({'selected':selection,'trials':trials}))
        raise ValueError('No later-2024 improvement; no 2025 replay or publication')
    all_data = training_data(history,earlier+training,base)
    heads = fit(all_data,selection['depth'])
    heads = {key:value for key,value in heads.items() if selection['weights'][key] > 0}
    config = {**base,'workload_model':{'heads':heads,'weights':selection['weights']}}
    _, train_report, train_records = replay(history,training,config,keep=True)
    model = {**original,'version':'nfl-usage-v2.1','parameters':config,
             'workload_training':{'seasons':[2023,2024],'selection_fit':'2023 Weeks 2–18 and 2024 Weeks 2–9',
                                  'selection_validation':'2024 Weeks 10–18','rows':len(all_data[1]),
                                  'depth':selection['depth'],'trees':100,'min_samples_leaf':40},
             'ranking_dispersion':fit_dispersion(train_records),
             'code_sha256':hashlib.sha256((ROOT/'scripts/nfl_usage_model.py').read_bytes()).hexdigest(),
             'workload_code_sha256':hashlib.sha256((ROOT/'scripts/nfl_workload_model.py').read_bytes()).hexdigest()}
    ranking, ranking_trials = fit_ranking_weight(train_records,model['ranking_dispersion'])
    model.update(ranking_typical_weight=ranking['weight'],ranking_history_weight=ranking['history_weight'])
    (output/'model.json').write_bytes(encoded(model))
    print('Model frozen. Replaying previously inspected 2025.',flush=True)
    testing = [history.features(2025,w) for w in range(2,19)]
    _, before, _ = replay(history,testing,base)
    _, after, records = replay(history,testing,config,keep=True)
    gate_results = gates(before,after)
    early_before = replay(history,testing[:3],base)[1]
    early_after = replay(history,testing[:3],config)[1]
    report = {'version':model['version'],'model_sha256':hashlib.sha256(encoded(model)).hexdigest(),
              'production_component_engine_reproduced':True,'training':train_report,
              'selection':selection,'training_trials':trials,'published_selection':published_selection,
              'holdout_season':2025,'holdout_weeks':list(range(2,19)),'holdout':after,
              'evaluation_note':'2025 is a previously inspected regression replay, not a new independent holdout. An initial candidate was inspected, then a missing-input fallback and a QB role boundary were added after reviewing current forecasts; settings were again selected on 2024 only before replaying 2025.',
              'published_regression':before,'deployment_gates':gate_results,
              'gates_passed':all(g['passed'] for g in gate_results),
              'early_season_regression':{'published':early_before,'candidate':early_after},
              'ranking_holdout':ranking_validation(records,model),'ranking_training_trials':ranking_trials,
              'future_game_rows_used':0,'external_reference_rows_used':0,
              'population':'Fixed top 30 per position by prior eight-appearance Half-PPR average; missing actual stat rows ungraded.',
              'workload_fit_population':'RB/WR/TE with a recorded previous team-game appearance, prior active roster and matched target-game stats or snaps, including recorded zero offensive involvement. No absent player is assigned an assumed zero. Other players retain the prior allocation.',
              'availability_limitation':'Historical pregame injury designations are not completely reconstructed.',
              'sources':[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                         for p in sorted(cache.glob('*.csv.gz')) if '2026' not in p.name],
              'provider_calls':{'paid_api':0,'model_api':0,'cost_usd':0}}
    (output/'validation.json').write_bytes(encoded(report))
    (output/'training-predictions.json').write_bytes(encoded(train_records))
    (output/'regression-predictions.json').write_bytes(encoded(records))
    print(json.dumps({'selection':selection,'passed':report['gates_passed'],
        'metrics':{fmt:{'before':before['formats'][fmt]['overall']['model'],
                        'after':after['formats'][fmt]['overall']['model']} for fmt in FORMATS},
        'failed':[g for g in gate_results if not g['passed']]},indent=2),flush=True)


def verify_frozen(cache, output):
    """Replay after integration edits without refitting or choosing settings."""
    model = json.loads((output/'model.json').read_text())
    report = json.loads((output/'validation.json').read_text())
    model['parameters']['workload_model']['heads'] = {
        key:value for key,value in model['parameters']['workload_model']['heads'].items()
        if model['parameters']['workload_model']['weights'][key] > 0}
    model['code_sha256'] = hashlib.sha256((ROOT/'scripts/nfl_usage_model.py').read_bytes()).hexdigest()
    model['workload_code_sha256'] = hashlib.sha256((ROOT/'scripts/nfl_workload_model.py').read_bytes()).hexdigest()
    # Freeze the identical fitted weights before the final regression replay.
    (output/'model.json').write_bytes(encoded(model))
    h = History(cache,years=(2023,2024,2025))
    for season, field, filename in ((2024,'training','training-predictions.json'),
                                   (2025,'holdout','regression-predictions.json')):
        _, summary, records = replay(h,[h.features(season,w) for w in range(2,19)],model['parameters'],keep=True)
        if summary != report[field]:
            raise ValueError(f'Integration changed the frozen {season} predictions')
        if records != json.loads((output/filename).read_text()):
            raise ValueError(f'Integration changed an individual {season} forecast')
    report['model_sha256'] = hashlib.sha256(encoded(model)).hexdigest()
    report['frozen_integration_replay_identical'] = True
    report['training_code_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (output/'validation.json').write_bytes(encoded(report))
    print('Frozen 2024 and 2025 replays match; engine and workload hashes verified.',flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--verify-frozen',action='store_true')
    args = ap.parse_args()
    (verify_frozen if args.verify_frozen else run)(args.cache,args.output)
