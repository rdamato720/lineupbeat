"""Evaluate usage revisions on 2024 and replay the already-inspected 2025.

Expert projections are not read. A finite search uses the same fixed sample
as the published model. The 2025 replay is a regression check, not a new
untouched holdout. Outputs stay in the explicitly supplied research folder.
"""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from nfl_role_candidate import History, predict, FORMATS
import train_nfl_usage_model as evaluation
from train_nfl_usage_model import fit_dispersion, ranking_validation
from nfl_weekly_rankings import fit_ranking_weight

ROOT = Path(__file__).resolve().parents[1]
SEARCH = {
    'transfer_prior_factor': [1,.5,.25,0],
    'qb_volume_weight': [0,.25,.5,.75],
    'qb_td_weight': [0,.25,.5,.75],
    'snap_weight': [0,.2,.4],
}


def encoded(value):
    return (json.dumps(value,indent=2,sort_keys=True)+'\n').encode()


def replay(*args, **kwargs):
    # Reuse the exact published sample/scoring protocol. This substitution is
    # local to a synchronous research replay and is always restored afterward.
    with patch.object(evaluation,'predict',predict):
        return evaluation.replay(*args,**kwargs)


def run(cache, output):
    output.mkdir(parents=True,exist_ok=True)
    original = json.loads((ROOT/'data/nfl_weekly/model-v2/model.json').read_text())
    base = original['parameters']
    config = {**base,'transfer_prior_factor':1,'qb_volume_weight':0,'qb_td_weight':0}
    history = History(cache,years=(2023,2024,2025))
    training = [history.features(2024,w) for w in range(2,19)]
    best, baseline_training, _ = replay(history,training,base)
    trials = []
    print(f'Published 2024 objective {best:.6f}',flush=True)
    for cycle in range(2):
        changed = False
        for key, values in SEARCH.items():
            selected = config[key]
            for value in values:
                candidate = {**config,key:value}
                objective, _, _ = replay(history,training,candidate)
                trials.append({'cycle':cycle+1,'parameter':key,'value':value,
                               'objective':objective,'parameters':candidate})
                if objective < best-1e-9:
                    best = objective
                    selected = value
                    changed = True
            config[key] = selected
            print(f'2024 only: {key}={selected}; objective {best:.6f}',flush=True)
        if not changed:
            break
    _, train_report, train_records = replay(history,training,config,keep=True)
    model = {**original,'research_only':True,'parameters':config,'ranking_dispersion':fit_dispersion(train_records),
             'code_sha256':hashlib.sha256((ROOT/'scripts/nfl_role_candidate.py').read_bytes()).hexdigest()}
    selected, ranking_trials = fit_ranking_weight(train_records,model['ranking_dispersion'])
    model.update(ranking_typical_weight=selected['weight'],ranking_history_weight=selected['history_weight'])
    # Persist the candidate before inspecting 2025 regression results.
    (output/'candidate-model.json').write_bytes(encoded(model))
    print('Candidate frozen. Beginning previously-inspected 2025 regression replay.',flush=True)
    testing = [history.features(2025,w) for w in range(2,19)]
    _, baseline_report, _ = replay(history,testing,base)
    _, regression_report, regression_records = replay(history,testing,config,keep=True)
    # A predefined deployment gate prevents gains in one format masking losses
    # in another. Position MAE regressions larger than 0.15 are also rejected.
    gates = []
    for fmt in FORMATS:
        before = baseline_report['formats'][fmt]
        after = regression_report['formats'][fmt]
        for metric in ('mae','rmse'):
            gates.append({'format':fmt,'scope':'overall','metric':metric,
                          'passed':after['overall']['model'][metric] <= before['overall']['model'][metric]})
        for pos in before['positions']:
            gates.append({'format':fmt,'scope':pos,'metric':'mae','tolerance':.15,
                          'passed':after['positions'][pos]['model']['mae'] <= before['positions'][pos]['model']['mae']+.15})
    report = {
        'selection_season':2024,'regression_season':2025,
        'evaluation_note':'2025 was inspected during the prior release. This is a regression replay, not a fresh independent holdout.',
        'candidate_model_sha256':hashlib.sha256(encoded(model)).hexdigest(),
        'parameters':config,'training_trials':trials,'ranking_training_trials':ranking_trials,
        'published_training':baseline_training,'candidate_training':train_report,
        'published_regression':baseline_report,'candidate_regression':regression_report,
        'ranking_regression':ranking_validation(regression_records,model),
        'deployment_gates':gates,'gates_passed':all(g['passed'] for g in gates),
        'expert_rows_used':0,'paid_calls':0,
        'sources':[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                   for p in sorted(cache.glob('*.csv.gz')) if '2026' not in p.name],
    }
    (output/'evaluation.json').write_bytes(encoded(report))
    (output/'training-predictions.json').write_bytes(encoded(train_records))
    (output/'regression-predictions.json').write_bytes(encoded(regression_records))
    print(json.dumps({
        'parameters':config,'gates_passed':report['gates_passed'],
        'overall':{fmt:{'published':baseline_report['formats'][fmt]['overall']['model'],
                        'candidate':regression_report['formats'][fmt]['overall']['model']} for fmt in FORMATS},
        'failed_gates':[g for g in gates if not g['passed']],
    },indent=2),flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args = ap.parse_args()
    run(args.cache,args.output)
