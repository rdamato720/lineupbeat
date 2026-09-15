"""Pregame workload features and portable, fitted carry/target share models.

Only recorded usage and pregame roster/depth information enter these features.
Offensive snaps are participation observations, never a substitute for routes.
Inference uses exported regression trees and the Python standard library.
"""
from collections import defaultdict
import struct

KEYS = ('carries', 'targets')


def mean(values):
    return sum(values) / len(values) if values else 0.0


def observations(history, player, season, week):
    rows = [r for r in history.player.get(player['id'], [])
            if r['season'] >= season-2 and history.before(r, season, week)]
    club = player['team']
    games = [r for r in history.teams.get(club, []) if history.before(r, season, week)]
    latest = games[-1] if games else None
    same = [r for r in rows if r['team'] == club]
    result = {'changed_team': float(bool(rows) and rows[-1]['team'] != club),
              'same_team_games': min(len(same), 16),
              'recent_old_team_fraction': mean([float(r['team'] != club) for r in rows[-12:]]),
              'last_team_game_observed': 0.0, 'last_team_snap': 0.0,
              'last_appearance_snap': rows[-1].get('offense_pct', 0.0) if rows else 0.0,
              'last_four_snap': mean([r['offense_pct'] for r in rows[-4:] if 'offense_pct' in r]),
              'last_four_snap_n': sum('offense_pct' in r for r in rows[-4:])}
    matched = next((r for r in reversed(same) if latest and r['game_id'] == latest['game_id']), None)
    snap = getattr(history, 'snap', {}).get((player['id'], latest['game_id']), {}) if latest else {}
    if matched is not None or (snap and snap.get('team') == club):
        result['last_team_game_observed'] = 1.0
        result['last_team_snap'] = (matched or snap).get('offense_pct', 0.0)
    for key in KEYS:
        def share(r):
            den = history.team_games.get((r['game_id'], r['team']), {}).get(key, 0)
            return r.get(key, 0)/den if den else 0.0
        result['last_appearance_'+key] = share(rows[-1]) if rows else 0.0
        result['last_four_'+key] = mean([share(r) for r in rows[-4:]])
        result['last_team_'+key] = share(matched) if matched else 0.0
        snapped = [r for r in rows[-12:] if r.get('offense_snaps', 0) > 0]
        result['per_snap_'+key] = sum(r.get(key, 0) for r in snapped)/max(1, sum(r['offense_snaps'] for r in snapped))
    return result


def raw_share(player, key, features, config):
    rank = min(player['depth_rank'] or 7, 7)
    prior = player['shares']['prior'][key]
    if prior is None:
        prior = features['role_priors'].get((player['position'], key, rank), 0.0)
    recent = player['shares']['recent'][key]
    weight = player['recent_n']/(player['recent_n']+config['player_prior_games'])
    value = prior if recent is None else (1-weight)*prior+weight*recent
    if player['prior']['offense_pct'] > .05 and player['recent_snap_n']:
        ratio = max(.5, min(1.75, player['recent']['offense_pct']/player['prior']['offense_pct']))
        value *= ratio**config['snap_weight']
    return max(0.0, value)


def feature_rows(features, config):
    groups = defaultdict(list)
    raw = {}
    for p in features['players']:
        groups[p['team']].append(p)
        raw[p['id']] = {k:raw_share(p, k, features, config) for k in KEYS}
    result = {}
    for p in features['players']:
        mates = [m for m in groups[p['team']] if not m.get('unavailable')]
        same_pos = [m for m in mates if m['position'] == p['position'] and m['id'] != p['id']]
        row = {**p['workload_observations'], 'depth': min(p['depth_rank'] or 7, 7),
               'has_depth': float(bool(p['depth_rank'])), 'prior_n': min(p['prior_n'], 12),
               'current_n': min(p['recent_n'], 16), 'week': features['week'],
               'prior_snap': p['prior']['offense_pct'], 'current_snap': p['recent']['offense_pct'],
               'prior_snap_n': p['prior_snap_n'], 'current_snap_n': p['recent_snap_n'],
               'same_position_competitors': len(same_pos)}
        row.update({'position_'+pos:float(p['position'] == pos) for pos in ('QB','RB','WR','TE')})
        for key in KEYS:
            value = raw[p['id']][key]
            den = sum(raw[m['id']][key] for m in mates)
            row['base_'+key] = value
            row['normalized_'+key] = value/den if den else 0.0
            row['team_sum_'+key] = den
            row['position_rank_'+key] = 1+sum(raw[m['id']][key] > value for m in same_pos)
            row['competitor_max_'+key] = max((raw[m['id']][key] for m in same_pos), default=0.0)
            row['competitor_sum_'+key] = sum(raw[m['id']][key] for m in same_pos)
            for period in ('prior','recent'):
                share = p['shares'][period][key]
                row[period+'_'+key] = share if share is not None else 0.0
                row[period+'_'+key+'_observed'] = float(share is not None)
        result[p['id']] = row
    return result


def tree_predict(model, row):
    # sklearn trees compare float32 feature values. Preserve those boundaries
    # in portable inference instead of relying on double precision coincidence.
    values = [struct.unpack('f', struct.pack('f', row[k]))[0] for k in model['features']]
    prediction = model['intercept']
    for tree in model['trees']:
        index = 0
        while tree[index][0] >= 0:
            feature, threshold, left, right, _ = tree[index]
            index = left if values[feature] <= threshold else right
        prediction += model['learning_rate']*tree[index][4]
    return prediction


def estimate(features, config):
    fitted = config.get('workload_model')
    if not fitted:
        return {}
    rows = feature_rows(features, config)
    result = {}
    for p in features['players']:
        row = rows[p['id']]
        result[p['id']] = {}
        for key, model in fitted['heads'].items():
            weight = fitted['weights'][key]
            # The learned role requires an observed recent game at this club.
            # Absence of a usage row is not evidence of demotion. Quarterback
            # rushing/rare receiving roles retain their dedicated allocation.
            if not weight or p['position'] == 'QB' or not row['last_team_game_observed']:
                continue
            learned = max(0.0, min(1.0, tree_predict(model, row)))
            result[p['id']][key] = ((1-weight)*row['normalized_'+key]+weight*learned)*row['team_sum_'+key]
    return result
