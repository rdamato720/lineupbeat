"""Capture free historical inputs for a reproducible 2024/2025 replay."""
import argparse
import gzip
import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def capture(cache):
    cache.mkdir(parents=True,exist_ok=True)
    catalog=[('schedules','games.csv.gz')]
    for year in (2023,2024,2025):
        catalog.extend((tag,f'{prefix}_{year}.csv.gz') for tag,prefix in (
            ('stats_player','stats_player_week'),('stats_team','stats_team_week'),
            ('pbp','play_by_play'),('snap_counts','snap_counts'),
            ('weekly_rosters','roster_weekly'),('depth_charts','depth_charts')))
    assets=[]
    for tag,name in catalog:
        path=cache/name
        url=f'https://github.com/nflverse/nflverse-data/releases/download/{tag}/{name}'
        if not path.exists():
            try:
                response=urllib.request.urlopen(url,timeout=45)
            except urllib.error.HTTPError as exc:
                if exc.code!=404:raise
                url=url[:-3]
                response=urllib.request.urlopen(url,timeout=45)
            with response:
                body=response.read(35_000_001)
            if len(body)>35_000_000:raise ValueError(f'Oversize {name}')
            if not url.endswith('.gz'):body=gzip.compress(body,mtime=0)
            if not gzip.decompress(body):raise ValueError(f'Empty {name}')
            temporary=cache/(name+'.tmp');temporary.write_bytes(body);temporary.replace(path)
        assets.append({'file':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                       'release_url':f'https://github.com/nflverse/nflverse-data/releases/tag/{tag}'})
    manifest={'captured_at':datetime.now(timezone.utc).isoformat(),'assets':assets,'paid_calls':0}
    (cache/'training_capture_manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    print(f'{len(assets)} historical inputs available; no paid providers')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--cache',type=Path,required=True)
    capture(parser.parse_args().cache)
