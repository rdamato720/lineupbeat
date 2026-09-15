"""Bounded free-data capture. A verified manifest commits each complete capture."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from capture_week1_inputs import LICENSE
from espn_injury_inputs import normalize_payload

SEASON, WEEK = 2026, 2
CATALOG = [('schedules', 'games.csv.gz')]
for year in (2024, 2025, 2026):
    CATALOG.extend((tag, f'{prefix}_{year}.csv.gz') for tag, prefix in (
        ('stats_player', 'stats_player_week'), ('stats_team', 'stats_team_week'),
        ('snap_counts', 'snap_counts'), ('pbp', 'play_by_play'), ('depth_charts', 'depth_charts')))
    CATALOG.append(('rosters' if year == SEASON else 'weekly_rosters',
                    f'roster_{year}.csv.gz' if year == SEASON else f'roster_weekly_{year}.csv.gz'))


def rows(body):
    return list(csv.DictReader(io.StringIO(gzip.decompress(body).decode('utf-8-sig'))))


def validate_current(pending, now):
    schedule = rows(pending['games.csv.gz'])
    completed = {r['game_id'] for r in schedule if r['season'] == str(SEASON)
                 and r['game_type'] == 'REG' and int(r['week']) == WEEK-1
                 and r.get('home_score') not in ('', None) and r.get('away_score') not in ('', None)}
    if len(completed) != 16:
        raise ValueError('Previous NFL week is not complete; retain the published release')
    roster = rows(pending[f'roster_{SEASON}.csv.gz'])
    active = [r for r in roster if r.get('status') == 'ACT' and r.get('position') in ('QB','RB','WR','TE')]
    if len(active) < 400 or len({r['team'] for r in active}) != 32:
        raise ValueError('Incomplete current offensive roster')
    ids = [r.get('gsis_id') for r in active]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError('Missing or duplicated active roster identity')
    if any(int(r['season']) != SEASON or int(r['week']) < WEEK for r in active):
        raise ValueError('Stale current roster week')
    depth = rows(pending[f'depth_charts_{SEASON}.csv.gz'])
    stamp = max(r['dt'] for r in depth)
    age = (now - datetime.fromisoformat(stamp.replace('Z','+00:00'))).total_seconds()
    if not -300 <= age <= 72*3600 or len({r['team'] for r in depth if r['dt'] == stamp}) != 32:
        raise ValueError('Stale, future-dated or incomplete current depth chart')
    for prefix in ('stats_player_week','stats_team_week','snap_counts','play_by_play'):
        current = rows(pending[f'{prefix}_{SEASON}.csv.gz'])
        if any(int(r['season']) != SEASON for r in current):
            raise ValueError(f'Wrong season in {prefix}')
        observed = {r['game_id'] for r in current if int(r['week']) == WEEK-1}
        if not completed <= observed:
            raise ValueError(f'Incomplete previous-week {prefix}')
    return sorted(completed)


def capture(cache):
    cache.mkdir(parents=True, exist_ok=True)
    pending, assets = {}, []
    for tag, name in CATALOG:
        path = cache/name
        current = str(SEASON) in name or tag == 'schedules'
        url = f'https://github.com/nflverse/nflverse-data/releases/download/{tag}/{name}'
        if current or not path.exists():
            # Some older releases have CSV only. Use that documented asset,
            # with deterministic compression, rather than an alternate feed.
            source_url = url[:-3] if name in ('depth_charts_2024.csv.gz', 'roster_weekly_2024.csv.gz') else url
            with urllib.request.urlopen(source_url, timeout=45) as response:
                body = response.read(35_000_001)
            if len(body) > 35_000_000:
                raise ValueError(f'Oversize input {name}')
            if not source_url.endswith('.gz'):
                body = gzip.compress(body, mtime=0)
        else:
            body = path.read_bytes()
            source_url = url[:-3] if name in ('depth_charts_2024.csv.gz', 'roster_weekly_2024.csv.gz') else url
        if not rows(body):
            raise ValueError(f'Empty input {name}')
        pending[name] = body
        assets.append({'file': name, 'source_url': source_url,
                       'sha256': hashlib.sha256(body).hexdigest(), 'current_capture': current})
    injury_url = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries'
    with urllib.request.urlopen(injury_url, timeout=45) as response:
        injuries = normalize_payload(json.load(response))
    now = datetime.now(timezone.utc)
    completed = validate_current(pending, now)
    age = (now - datetime.fromisoformat(injuries['fetched_at'].replace('Z','+00:00'))).total_seconds()
    if not 0 <= age < 86400:
        raise ValueError('Stale or future-dated injury snapshot')
    pending['espn_injuries.json'] = (json.dumps(injuries, sort_keys=True)+'\n').encode()
    assets.append({'file':'espn_injuries.json', 'source_url':injury_url,
                   'sha256':hashlib.sha256(pending['espn_injuries.json']).hexdigest(), 'current_capture':True})
    # Validate everything before replacing inputs. The manifest is written last;
    # readers reject digest mismatches if an interrupted write mixes captures.
    for name, body in pending.items():
        temporary = cache/(name+'.tmp')
        temporary.write_bytes(body)
        temporary.replace(cache/name)
    manifest = {'captured_at':now.isoformat(), 'season':SEASON, 'forecast_week':WEEK,
                'stats_through_week':WEEK-1, 'completed_games':completed, 'assets':assets,
                'license_review':LICENSE, 'paid_provider_calls':0,
                'unavailable':{'markets':'No current qualified TheRundown capture; no market adjustment'}}
    temporary = cache/'capture_manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
    temporary.replace(cache/'capture_manifest.json')
    print(f'Captured NFL Week {WEEK} inputs; {len(completed)} completed games; no paid calls')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, default=Path('.cache/nfl-week2'))
    capture(parser.parse_args().cache)
