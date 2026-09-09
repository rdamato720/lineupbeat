#!/usr/bin/env python3
"""News links only. No models, article bodies, commentary or publication writes.

--refresh reads each existing AUTO_READY RSS feed once. Normal builds read
only the last successful snapshot; --refresh failures preserve that snapshot.
"""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, html, json, re, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
import feedparser
import seo
from wire.capture import _matches, _entry_time
from wire.registry import load as load_sources
SNAPSHOT = ROOT/'data/news_wire.json'
PATH = '/nfl/wire/'

def normalized(value):
    return ' '.join(re.sub(r'[^a-z0-9 ]', '', value.lower()).split())

def identities():
    return [p for p in json.loads((ROOT/'sources/wire_players.json').read_text())['players']
            if p['position'] in {'QB','RB','WR','TE'}]

def sources():
    return [s for s in load_sources() if s.pollable and s.feed_url and not s.paid]

def clean_url(value):
    u = urlsplit(value)
    if u.scheme != 'https' or not u.hostname or u.username or u.password:
        return ''
    return urlunsplit((u.scheme,u.netloc,u.path,u.query,''))

def normalize_entry(source, entry, players, now):
    url = clean_url(entry.get('link',''))
    if not url or not source.owns(url) or not _matches(source,entry): return None
    title = html.unescape(re.sub('<[^>]*>','',entry.get('title',''))).strip()
    stamp = _entry_time(entry)
    if not title or len(title)>300 or not stamp: return None
    date = datetime.fromisoformat(stamp)
    if not now-timedelta(days=7) <= date <= now+timedelta(minutes=5): return None
    title_words = ' '+normalized(title)+' '
    matched = [p for p in players if p['team'] in source.teams and
               ' '+normalized(p['full_name'])+' ' in title_words]
    if not matched: return None
    return {'id':hashlib.sha256(url.encode()).hexdigest()[:20], 'headline':title,
            'url':url, 'source_id':source.source_id, 'source':source.source_name,
            'published_at':stamp, 'players':[{'id':p['player_id'],'name':p['full_name'],
            'team':p['team'],'position':p['position']} for p in matched]}

def refresh(path=SNAPSHOT):
    now=datetime.now(timezone.utc); players=identities(); registered=sources()
    # Cache a feed shared by multiple registered reporters. Never fetch a body.
    def fetch(url):
        r=subprocess.run(['curl','--silent','--show-error','--fail','--location',
                          '--max-time','20','--max-filesize','5000000',url],capture_output=True)
        if r.returncode: return None
        parsed=feedparser.parse(r.stdout)
        return parsed if parsed.get('version') else None
    urls=sorted({s.feed_url for s in registered})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        feeds=dict(zip(urls,pool.map(fetch,urls)))
    rows={};health=[]
    for s in registered:
        feed=feeds[s.feed_url]; count=0
        if feed is not None:
            for entry in feed.entries[:200]:
                row=normalize_entry(s,entry,players,now)
                if row: rows[row['url']]=row;count+=1
        health.append({'source_id':s.source_id,'ok':feed is not None,'eligible':count})
    if not any(h['ok'] for h in health):
        raise RuntimeError('All news feeds failed; previous snapshot retained')
    old=json.loads(path.read_text()) if path.exists() else {'items':[]}
    # Retain recent prior headlines if one feed is temporarily unavailable.
    for row in old['items']:
        if datetime.fromisoformat(row['published_at'])>=now-timedelta(days=7):
            rows.setdefault(row['url'],row)
    data={'updated_at':now.isoformat(),'sources':health,
          'items':sorted(rows.values(),key=lambda x:(x['published_at'],x['id']),reverse=True)[:150]}
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(path)
    print(f"News feeds: {sum(h['ok'] for h in health)}/{len(health)} responding; {len(data['items'])} headlines; zero model calls")
    return data

def validated(data):
    registry={p['player_id']:p for p in identities()}; registered={s.source_id:s for s in sources()}
    seen=set()
    datetime.fromisoformat(data['updated_at'])
    for row in data['items']:
        s=registered.get(row['source_id'])
        if not s or not s.owns(row['url']) or not clean_url(row['url']): raise ValueError('Unregistered news link')
        if row['url'] in seen: raise ValueError('Duplicate news URL')
        seen.add(row['url']);datetime.fromisoformat(row['published_at'])
        if not row['headline'] or not row['players']: raise ValueError('Incomplete news headline')
        if row['source']!=s.source_name: raise ValueError('News source mismatch')
        for p in row['players']:
            r=registry.get(p['id'])
            if not r or (p['name'],p['team'],p['position'])!=(r['full_name'],r['team'],r['position']):
                raise ValueError('News identity mismatch')
            if p['team'] not in s.teams or ' '+normalized(p['name'])+' ' not in ' '+normalized(row['headline'])+' ':
                raise ValueError('News headline subject mismatch')
    return data

def display_date(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo('America/New_York')).strftime('%b %-d, %Y · %-I:%M %p ET')

CSS='''
.nw{max-width:1000px;margin:auto;padding:clamp(20px,4vw,44px) 16px 60px}.nw h1{font-size:clamp(36px,7vw,66px);margin:0}.nw-intro{color:var(--quiet);max-width:60ch}.nw-filters{display:flex;flex-wrap:wrap;gap:16px;margin:28px 0 16px}.nw-filters label{display:grid;gap:6px;font:600 14px var(--agate);flex:1;min-width:min(220px,100%)}.nw-filters input,.nw-filters select{min-height:44px;max-width:100%;padding:10px;color:var(--ink);background:var(--card);border:1px solid var(--rule);border-radius:8px;font:inherit}.nw-list{list-style:none;padding:0}.nw-story{display:flex;gap:18px;padding:22px 0;border-top:1px solid var(--rule)}.nw-photo{width:64px;height:64px;object-fit:contain;flex:0 0 64px}.nw-story>div{min-width:0}.nw-story h2{font-size:clamp(19px,3vw,25px);line-height:1.3;margin:8px 0;overflow-wrap:anywhere}.nw-meta,.nw-count{font:14px/1.5 var(--agate);color:var(--quiet)}.nw-player{font:600 14px var(--agate);color:var(--signal)}.nw [hidden]{display:none!important}.nw-empty{padding:24px;border:1px solid var(--rule);border-radius:8px}@media(max-width:480px){.nw-story{gap:12px}.nw-photo{width:48px;height:48px;flex-basis:48px}}
'''
JS='''(()=>{const stamp=document.querySelector('[data-checked]');if(stamp&&Date.now()-Date.parse(stamp.dataset.checked)>48*60*60*1000){document.getElementById('news-delayed').hidden=false;}const q=document.getElementById('news-player'),t=document.getElementById('news-team'),rows=[...document.querySelectorAll('.nw-story')],count=document.getElementById('news-count'),empty=document.getElementById('news-empty');function draw(){let n=0;const query=q.value.trim().toLowerCase();for(const r of rows){r.hidden=!(r.dataset.players.includes(query)&&(!t.value||r.dataset.teams.split(' ').includes(t.value)));if(!r.hidden)n++;}count.textContent=n+' '+(n===1?'headline':'headlines');empty.hidden=n>0;}q.addEventListener('input',draw);t.addEventListener('change',draw);draw();})();'''

def build(base='https://lineupbeat.com', out=None):
    data=validated(json.loads(SNAPSHOT.read_text())) if SNAPSHOT.exists() else {'items':[],'updated_at':None}
    e=lambda v:html.escape(str(v),quote=True)
    # Image-only join after headline selection. Fantasy numbers are never used.
    display=json.loads((ROOT/'data/wire_display_fantasy.json').read_text()).get('players',{})
    cards=[]
    for row in data['items']:
        p=row['players'][0];d=display.get(p['id'],{});espn=d.get('espn');ref=str(d.get('player_ref','')).removeprefix('nfl-')
        photo=f'https://a.espncdn.com/i/headshots/nfl/players/full/{espn}.png' if espn else f'https://sleepercdn.com/content/nfl/players/thumb/{ref}.jpg' if ref.isdigit() else '/assets/player-placeholder.svg'
        names=' · '.join(f"{x['name']} · {x['team']} {x['position']}" for x in row['players'])
        cards.append(f'''<li class="nw-story" data-players="{e(' '.join(x['name'].lower() for x in row['players']))}" data-teams="{e(' '.join(sorted({x['team'] for x in row['players']})))}"><img class="nw-photo" src="{e(photo)}" width="64" height="64" alt="{e(p['name'])}" loading="lazy" onerror="this.onerror=null;this.src='/assets/player-placeholder.svg'"><div><div class="nw-player">{e(names)}</div><h2><a href="{e(row['url'])}" target="_blank" rel="noopener noreferrer">{e(row['headline'])}</a></h2><div class="nw-meta">{e(row['source'])} · <time datetime="{e(row['published_at'])}">{e(display_date(row['published_at']))}</time></div></div></li>''')
    teams=sorted({p['team'] for row in data['items'] for p in row['players']})
    options=''.join(f'<option value="{e(t)}">{e(t)}</option>' for t in teams)
    updated=f'<p class="nw-meta" data-checked="{e(data["updated_at"])}">Last checked {e(display_date(data["updated_at"]))}</p>' if data['updated_at'] else ''
    body=f'''<main class="nw"><h1>The Wire</h1><p class="nw-intro">NFL player news from trusted sources. Read the latest headlines and follow each link to the original report.</p>{updated}<p id="news-delayed" class="nw-empty" hidden>Updates are delayed. These are the latest available headlines.</p><div class="nw-filters"><label for="news-player">Player<input id="news-player" type="search" placeholder="Search player names" autocomplete="off"></label><label for="news-team">Team<select id="news-team"><option value="">All teams</option>{options}</select></label></div><p id="news-count" class="nw-count" role="status">{len(cards)} headlines</p><ul class="nw-list">{''.join(cards)}</ul><p id="news-empty" class="nw-empty" {'hidden' if cards else ''}>No headlines match. Try another player or team.</p></main>'''
    doc=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>The Wire: NFL Player News | Lineup Beat</title><meta name="description" content="NFL player news, headlines and original reports from trusted sources. Filter The Wire by player and team."><link rel="canonical" href="{e(base.rstrip('/')+PATH)}"><style>{CSS}</style></head><body>{seo.site_nav('wire','nfl')}{body}{seo.site_footer()}<script>{JS}</script></body></html>'''
    target=out or ROOT/'site/nfl/wire/index.html';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(doc)
    return target

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--refresh',action='store_true');ap.add_argument('--refresh-only',action='store_true');ap.add_argument('--base',default='https://lineupbeat.com');args=ap.parse_args()
    if args.refresh or args.refresh_only:refresh()
    if not args.refresh_only:build(args.base)
if __name__=='__main__':main()
