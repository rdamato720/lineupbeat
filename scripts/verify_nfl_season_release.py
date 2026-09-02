#!/usr/bin/env python3
"""Verify exact final season data, active identity links and dev-only surfaces."""
import argparse
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit,unquote
import build_nfl_season_release as release

class Document(HTMLParser):
    def __init__(self,text):
        super().__init__();self.rows=[];self.links=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='tr' and 'data-player-id' in a:self.rows.append(a)
        if tag=='a' and a.get('href'):self.links.append(a['href'])

def manifest(root):return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file()}

def verify(root):
    model,ranking=release.load();ids={p['gsis_id'] for p in model['players']};errors=[]
    published=json.loads((root/'data/nfl-season-v15.json').read_text())
    if len(published['players'])!=505 or {p['gsis_id'] for p in published['players']}!=ids:errors.append('public population')
    source={p['gsis_id']:p for p in model['players']}
    for p in published['players']:
        if p['formats']!=source[p['gsis_id']]['formats'] or p['stat_projection']!=source[p['gsis_id']]['stat_projection']:errors.append('public numerical mismatch '+p['name'])
        text=(root/p['url'].strip('/')/'index.html').read_text()
        if f'data-season-player-id="{p["gsis_id"]}"' not in text or p['name'] not in text or 'v1.5 final' not in text:errors.append('player page '+p['name'])
        for n in p['formats'].values():
            if f'{n:.1f}' not in text:errors.append('player scoring display '+p['name'])
    checked=0
    for fmt,(_,slug) in release.FORMATS.items():
        for pos in [None,'QB','RB','WR','TE']:
            path=root/'nfl/rankings'/slug/(pos.lower() if pos else '')/'index.html'
            rows=Document(path.read_text()).rows
            expected=[r for r in ranking['formats'][fmt]['rows'] if not pos or r['position']==pos]
            if [r['data-player-id'] for r in rows]!=[r['gsis_id'] for r in expected]:errors.append(str(path)+' order/population')
            for r,e in zip(rows,expected):
                if float(r[f'data-{fmt}-points'])!=e['fantasy_points'] or int(r[f'data-{fmt}-rank'])!=e['overall_rank'] or int(r[f'data-position_{fmt}-rank'])!=e['position_rank']:errors.append('rank display payload '+e['name'])
            checked+=len(rows)
    missing={}
    for path in sorted(root.rglob('*.html')):
        for href in Document(path.read_text()).links:
            url=urlsplit(href)
            if url.scheme and url.netloc not in ('lineupbeat.com','www.lineupbeat.com','lineupbeat-dev.pages.dev'):continue
            rel=unquote(url.path)
            if not rel or rel.startswith(('mailto:','tel:')):continue
            target=root/rel.lstrip('/') if rel.startswith('/') else path.parent/rel
            if target.is_file() or (target/'index.html').is_file() or Path(str(target)+'.html').is_file():continue
            if rel.startswith(('/nfl/wire','/wire')):continue
            missing.setdefault(rel,[]).append(str(path.relative_to(root)))
    result={'active_players':505,'rank_rows_checked':checked,'errors':errors,'missing_internal_links':missing,'model_sha256':hashlib.sha256((release.SOURCE/'season_projections.json').read_bytes()).hexdigest()}
    print(json.dumps(result,indent=2))
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('site',type=Path);ap.add_argument('--manifest',type=Path);ap.add_argument('--compare',type=Path);args=ap.parse_args()
    if args.manifest:args.manifest.write_text(json.dumps(manifest(args.site),sort_keys=True,indent=2)+'\n');return
    if args.compare:
        before=json.loads(args.compare.read_text());after=manifest(args.site)
        changes=[p for p in sorted(before.keys()|after.keys()) if before.get(p)!=after.get(p)]
        print(json.dumps({'files':len(after),'changed':changes},indent=2));raise SystemExit(bool(changes))
    result=verify(args.site);raise SystemExit(bool(result['errors'] or result['missing_internal_links']))
if __name__=='__main__':main()
