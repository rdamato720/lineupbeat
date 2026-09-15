"""Bounded free-data capture for the Week 2 daily refresh; no betting/API keys."""
import argparse, hashlib, json, urllib.request
from datetime import datetime,timezone
from pathlib import Path
from capture_week1_inputs import CATALOG,LICENSE,validate_current_asset
from espn_injury_inputs import normalize_payload

def capture(cache):
    cache.mkdir(parents=True,exist_ok=True);assets=[]
    catalog=[(tag,name) for tag,name,_ in CATALOG if tag!='pbp']
    catalog.append(('stats_player','stats_player_week_2026.csv.gz'))
    for tag,name in catalog:
        path=cache/name
        current='2026' in name or tag=='schedules'
        url=f'https://github.com/nflverse/nflverse-data/releases/download/{tag}/{name}'
        if current or not path.exists():
            with urllib.request.urlopen(url,timeout=45) as r:body=r.read()
            tmp=path.with_suffix('.tmp');tmp.write_bytes(body)
            # Validate before replacing any previously usable capture.
            import gzip,csv,io
            with gzip.open(io.BytesIO(body),'rt') as f:
                reader=csv.DictReader(f);rows=list(reader)
            if not rows:raise ValueError(f'Empty input {name}')
            tmp.replace(path)
        assets.append({'file':name,'source_url':url,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    for name in ('games.csv.gz','roster_2026.csv.gz','depth_charts_2026.csv.gz'):
        validate_current_asset(cache/name)
    url='https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries'
    with urllib.request.urlopen(url,timeout=45) as r:raw=json.load(r)
    injuries=normalize_payload(raw)
    age=datetime.now(timezone.utc)-datetime.fromisoformat(injuries['fetched_at'].replace('Z','+00:00'))
    if not 0<=age.total_seconds()<86400:raise ValueError('Injury snapshot is stale or future dated')
    (cache/'espn_injuries.json').write_text(json.dumps(injuries))
    assets.append({'file':'espn_injuries.json','source_url':url,'sha256':hashlib.sha256((cache/'espn_injuries.json').read_bytes()).hexdigest()})
    manifest={'captured_at':injuries['fetched_at'],'assets':assets,'license_review':LICENSE,'unavailable':{'markets':'Not included; no current qualified TheRundown capture'}}
    (cache/'capture_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--cache',type=Path,default=Path('.cache/nfl-week2'));capture(ap.parse_args().cache)
