#!/usr/bin/env python3
"""News links only. No models, article bodies, commentary or publication writes.

--refresh reads each enabled public RSS feed once. Normal builds read
only the last successful snapshot; --refresh failures preserve that snapshot.
"""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, html, json, os, re, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
import feedparser
import seo
from wire.capture import _matches, _entry_time
from news_wire_sources import sources
SNAPSHOT = ROOT/'data/news_wire.json'
PATH = '/nfl/wire/'

def check_health(path=SNAPSHOT, now=None, max_age_hours=6):
    """Report capture freshness separately from whether new headlines exist."""
    now = now or datetime.now(timezone.utc)
    problems = []
    warnings = []
    try:
        data = validated(json.loads(path.read_text()))
        stamp = datetime.fromisoformat(data['updated_at'])
        if stamp.tzinfo is None:
            raise ValueError('Missing capture timezone')
        age = (now - stamp).total_seconds() / 3600
        if age < -5/60 or age > max_age_hours:
            problems.append('News snapshot is stale or has a future capture time')
        health = data.get('sources', [])
        responding = sum(h.get('ok') is True for h in health)
        if not health or not responding:
            problems.append('No successful source checks recorded')
        elif responding < len(health):
            warnings.append(f'{len(health)-responding} of {len(health)} source checks failed')
        stale=sum(h.get('stale') is True for h in health)
        if stale:
            warnings.append(f'{stale} responding sources contain only old entries')
        if not data['items']:
            warnings.append('No eligible recent headlines; capture freshness is separate from coverage')
        summary = (f"Last successful capture: {stamp.isoformat()} ({age:.1f} hours ago). "
                   f"Sources: {responding}/{len(health)} responding. "
                   f"Headlines: {len(data['items'])}. Zero model calls.")
    except (OSError, ValueError, KeyError, TypeError):
        problems.append('News snapshot is missing or invalid')
        summary = 'No validated news snapshot available.'
    for message in warnings:
        print(f'::warning title=News coverage::{message}')
    for message in problems:
        print(f'::error title=News freshness::{message}')
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as report:
            report.write('\n## News freshness\n' + summary + '\n' +
                         ''.join(f'- {m}\n' for m in problems + warnings))
    return not problems

def normalized(value):
    value=re.sub(r"['’]s\b", '', value.lower())
    return ' '.join(re.sub(r'[^a-z0-9 ]', '', value).split())

def identities():
    return [p for p in json.loads((ROOT/'sources/wire_players.json').read_text())['players']
            if p['position'] in {'QB','RB','WR','TE'}]

def clean_url(value):
    u = urlsplit(value)
    if u.scheme != 'https' or not u.hostname or u.username or u.password:
        return ''
    query=urlencode([(k,v) for k,v in parse_qsl(u.query) if not k.lower().startswith('utm_') and k.lower() not in {'fbclid','gclid'}])
    return urlunsplit((u.scheme,u.netloc,u.path,query,''))

ROUNDUP=re.compile(r'\b(injury report|practice (report|notebook|updates?|notes|observations)|inactives)\b',re.I)
NON_NEWS=re.compile(r'\b(mock draft|predict\w*|rankings?|fantasy (advice|football|outlook)|betting|best bets|trade proposal|opinion|analysis|quiz|how to watch|tickets|sweepstakes|giveaway|radiothon|cheerleader|sponsored|mural|inspire change|hypothetical|foreshadowing|top 100|swing factors|reasons to be optimistic|ex-\w+|former)\b',re.I)
NEWS_EVENT=re.compile(r'\b(practice|injur\w*|inactives?|limited|questionable|doubtful|ruled out|full participant|contract|extension|signs?|signed|waiv\w*|releas\w*|activat\w*|promot\w*|elevat\w*|return\w*|starting|starter|depth chart|roster|workload|snaps?|carries|targets?|captains?|press conference|arrest|plea)\b',re.I)

def plain(value):
    return ' '.join(html.unescape(re.sub('<[^>]*>',' ',value or '')).split())

def row_teams(row):
    return row.get('teams') or sorted({p['team'] for p in row['players']})

def normalize_entry(source, entry, players, now):
    url = clean_url(entry.get('link',''))
    if not url or not source.owns(url) or not _matches(source,entry): return None
    title = plain(entry.get('title',''))
    stamp = _entry_time(entry)
    if not title or len(title)>300 or not stamp or NON_NEWS.search(title): return None
    date = datetime.fromisoformat(stamp)
    if not now-timedelta(days=7) <= date <= now+timedelta(minutes=5): return None
    teams=source.teams_for(url)
    if not teams: return None
    # Only publisher-supplied RSS description, never content:encoded/body.
    # Metadata is used for tagging, never rendered as LineupBeat copy.
    description=plain(entry.get('summary',''))
    if len(description)>2000: description=''
    if not NEWS_EVENT.search(title+' '+description): return None
    match_text=' '+normalized(title+' '+description)+' '
    matched = [p for p in players if p['team'] in teams and
               ' '+normalized(p['full_name'])+' ' in match_text]
    if not matched and not ROUNDUP.search(title): return None
    return {'id':hashlib.sha256(url.encode()).hexdigest()[:20], 'headline':title,
            'url':url, 'source_id':source.source_id, 'source':source.source_name,
            'teams':teams, 'feed_description':description,
            'published_at':stamp, 'players':[{'id':p['player_id'],'name':p['full_name'],
            'team':p['team'],'position':p['position']} for p in matched]}

def refresh(path=SNAPSHOT):
    now=datetime.now(timezone.utc); players=identities(); registered=sources()
    # Cache a feed shared by multiple registered reporters. Never fetch a body.
    def fetch(url):
        def get(target):
            r=subprocess.run(['curl','--silent','--show-error','--fail','--location',
                              '--proto','=https','--proto-redir','=https',
                              '--user-agent','LineupBeatNews/1.0 (+https://lineupbeat.com/about/)',
                              '--max-time','20','--max-filesize','5000000',target],capture_output=True)
            return None if r.returncode else r.stdout
        u=urlsplit(url);robots=get(urlunsplit((u.scheme,u.netloc,'/robots.txt','','')))
        if robots is None: return None,'robots_unavailable'
        policy=RobotFileParser();policy.parse(robots.decode(errors='replace').splitlines())
        if not policy.can_fetch('LineupBeatNews',url): return None,'robots_disallowed'
        raw=get(url)
        if raw is None: return None,'feed_unavailable'
        parsed=feedparser.parse(raw)
        return (parsed,'') if parsed.get('version') else (None,'invalid_feed')
    urls=sorted({s.feed_url for s in registered})
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        feeds=dict(zip(urls,pool.map(fetch,urls)))
    rows={};health=[]
    for s in registered:
        feed,error=feeds[s.feed_url]; count=0
        if feed is not None:
            for entry in feed.entries[:200]:
                row=normalize_entry(s,entry,players,now)
                if row: rows[row['url']]=row;count+=1
        stamps=[_entry_time(e) for e in feed.entries[:200]] if feed is not None else []
        latest=max((t for t in stamps if t),default=None)
        health.append({'source_id':s.source_id,'ok':feed is not None,'eligible':count,'error':error,
                       'latest_entry_at':latest,'stale':bool(latest and datetime.fromisoformat(latest)<now-timedelta(days=7))})
    if not any(h['ok'] for h in health):
        raise RuntimeError('All news feeds failed; previous snapshot retained')
    old=json.loads(path.read_text()) if path.exists() else {'items':[]}
    # Retain recent prior headlines if one feed is temporarily unavailable.
    for row in old['items']:
        if datetime.fromisoformat(row['published_at'])>=now-timedelta(days=7):
            row['url']=clean_url(row['url'])
            row['id']=hashlib.sha256(row['url'].encode()).hexdigest()[:20]
            if NON_NEWS.search(row['headline']): continue
            rows.setdefault(row['url'],row)
    data={'updated_at':now.isoformat(),'sources':health,
          'items':sorted(rows.values(),key=lambda x:(x['published_at'],x['id']),reverse=True)[:150]}
    validated(data)
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
        if not row['headline'] or len(row['headline'])>300: raise ValueError('Incomplete news headline')
        scoped=s.teams_for(row['url'])
        if not scoped or not set(row_teams(row))<=set(scoped): raise ValueError('News team scope mismatch')
        if not row['players'] and (not row_teams(row) or not ROUNDUP.search(row['headline'])):
            raise ValueError('Not a team practice roundup')
        description=row.get('feed_description','')
        if not isinstance(description,str) or len(description)>2000 or plain(description)!=description:
            raise ValueError('Invalid feed description')
        match_text=' '+normalized(row['headline']+' '+description)+' '
        if row['source']!=s.source_name: raise ValueError('News source mismatch')
        for p in row['players']:
            r=registry.get(p['id'])
            if not r or (p['name'],p['team'],p['position'])!=(r['full_name'],r['team'],r['position']):
                raise ValueError('News identity mismatch')
            if p['team'] not in scoped or p['team'] not in row_teams(row) or ' '+normalized(p['name'])+' ' not in match_text:
                raise ValueError('News headline subject mismatch')
    return data

def display_date(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo('America/New_York')).strftime('%b %-d, %Y · %-I:%M %p ET')

CSS='''
body{background:#080c0b}.nw,.nw *{box-sizing:border-box}.nw{color:var(--ink);font-family:var(--agate);padding-bottom:64px}.nw-inner{width:min(calc(100% - 32px),1180px);margin:auto}.nw-hero{position:relative;padding:clamp(32px,5vw,64px) 0;background:radial-gradient(ellipse at 76% 15%,#c6f53c0e,transparent 55%),linear-gradient(#ffffff06 1px,transparent 1px),linear-gradient(90deg,#ffffff06 1px,transparent 1px),#050708;background-size:auto,72px 72px,72px 72px,auto;border-bottom:1px solid #2b3730}.nw-eyebrow{color:var(--signal);font:800 12px var(--agate);letter-spacing:.14em;text-transform:uppercase}.nw h1{font:900 clamp(48px,7vw,84px)/1 var(--agate);letter-spacing:-.045em;text-transform:uppercase;margin:12px 0 16px;color:#f7f9f5}.nw h1 span{color:var(--signal)}.nw-intro{color:#b8c2ba;font:18px/1.5 var(--agate);max-width:600px;margin:0 0 20px}.nw-feed{padding-top:28px}.nw-filters{display:flex;flex-wrap:wrap;gap:16px;padding:20px;background:#111715;border:1px solid #354239;border-radius:10px}.nw-filters label{display:grid;gap:8px;font:700 14px var(--agate);flex:1;min-width:min(220px,100%)}.nw-filters input,.nw-filters select{width:100%;min-height:46px;max-width:100%;padding:10px 12px;color:var(--ink);background:#080c0b;border:1px solid #536159;border-radius:6px;font:16px var(--agate)}.nw :is(input,select,a):focus-visible{outline:2px solid var(--signal);outline-offset:4px}.nw-list{list-style:none;padding:0;margin:0;display:grid;gap:12px}.nw-story{display:flex;gap:18px;padding:22px;background:linear-gradient(135deg,#151d18,#101612);border:1px solid #354239;border-radius:10px;align-items:flex-start}.nw-photo{width:64px;height:64px;object-fit:contain;flex:0 0 64px;background:#080c0b;border-radius:8px}.nw-story>div{min-width:0}.nw-story h2{font:800 clamp(21px,3vw,27px)/1.2 var(--agate);letter-spacing:-.015em;margin:9px 0 12px;overflow-wrap:anywhere}.nw-story h2 a{color:#f3f6f2;text-decoration:none}.nw-story h2 a:hover{color:var(--signal);text-decoration:underline;text-underline-offset:4px}.nw-meta,.nw-count{font:14px/1.5 var(--agate);color:#aeb8b0}.nw-count{margin:20px 0 14px}.nw-player{font:700 14px var(--agate);color:var(--signal)}.nw [hidden]{display:none!important}.nw-empty{padding:24px;background:#111715;border:1px solid #354239;border-radius:10px}@media(max-width:480px){.nw-story{gap:12px;padding:16px}.nw-photo{width:48px;height:48px;flex-basis:48px}.nw-filters{padding:16px}.nw-intro{font-size:16px}.nw-story h2{font-size:22px}.nw-meta{font-size:13px}}
'''
JS='''(()=>{const stamp=document.querySelector('[data-checked]');if(stamp&&Date.now()-Date.parse(stamp.dataset.checked)>48*60*60*1000){document.getElementById('news-delayed').hidden=false;}const q=document.getElementById('news-player'),t=document.getElementById('news-team'),rows=[...document.querySelectorAll('.nw-story')],count=document.getElementById('news-count'),empty=document.getElementById('news-empty');function draw(){let n=0;const query=q.value.trim().toLowerCase();for(const r of rows){r.hidden=!(r.dataset.players.includes(query)&&(!t.value||r.dataset.teams.split(' ').includes(t.value)));if(!r.hidden)n++;}count.textContent=n+' '+(n===1?'headline':'headlines');empty.hidden=n>0;}q.addEventListener('input',draw);t.addEventListener('change',draw);draw();})();'''

def build(base='https://lineupbeat.com', out=None):
    data=validated(json.loads(SNAPSHOT.read_text())) if SNAPSHOT.exists() else {'items':[],'updated_at':None}
    e=lambda v:html.escape(str(v),quote=True)
    # Image-only join after headline selection. Fantasy numbers are never used.
    display=json.loads((ROOT/'data/wire_display_fantasy.json').read_text()).get('players',{})
    cards=[]
    for row in data['items']:
        p=row['players'][0] if row['players'] else {'id':'','name':'Team report'};d=display.get(p['id'],{});espn=d.get('espn');ref=str(d.get('player_ref','')).removeprefix('nfl-')
        photo=f'https://a.espncdn.com/i/headshots/nfl/players/full/{espn}.png' if espn else f'https://sleepercdn.com/content/nfl/players/thumb/{ref}.jpg' if ref.isdigit() else '/assets/player-placeholder.svg'
        names=' · '.join(f"{x['name']} · {x['team']} {x['position']}" for x in row['players']) or ' · '.join(row_teams(row))+' · Team report'
        picture=f'''<img class="nw-photo" src="{e(photo)}" width="64" height="64" alt="{e(p['name'])}" loading="lazy" onerror="this.onerror=null;this.src='/assets/player-placeholder.svg'">''' if row['players'] else ''
        cards.append(f'''<li class="nw-story" data-players="{e(' '.join(x['name'].lower() for x in row['players']))}" data-teams="{e(' '.join(row_teams(row)))}">{picture}<div><div class="nw-player">{e(names)}</div><h2><a href="{e(row['url'])}" target="_blank" rel="noopener noreferrer">{e(row['headline'])}</a></h2><div class="nw-meta">{e(row['source'])} · <time datetime="{e(row['published_at'])}">{e(display_date(row['published_at']))}</time></div></div></li>''')
    teams=sorted({t for row in data['items'] for t in row_teams(row)})
    options=''.join(f'<option value="{e(t)}">{e(t)}</option>' for t in teams)
    updated=f'<p class="nw-meta" data-checked="{e(data["updated_at"])}">Last checked {e(display_date(data["updated_at"]))}</p>' if data['updated_at'] else ''
    body=f'''<main class="nw"><section class="nw-hero"><div class="nw-inner"><div class="nw-eyebrow">NFL PLAYER NEWS</div><h1>The <span>Wire</span></h1><p class="nw-intro">NFL player news from trusted sources. Read the latest headlines and follow each link to the original report.</p>{updated}</div></section><div class="nw-inner nw-feed"><p id="news-delayed" class="nw-empty" hidden>Updates are delayed. These are the latest available headlines.</p><div class="nw-filters"><label for="news-player">Player<input id="news-player" type="search" placeholder="Search player names" autocomplete="off"></label><label for="news-team">Team<select id="news-team"><option value="">All teams</option>{options}</select></label></div><p id="news-count" class="nw-count" role="status">{len(cards)} headlines</p><ul class="nw-list">{''.join(cards)}</ul><p id="news-empty" class="nw-empty" {'hidden' if cards else ''}>No headlines match. Try another player or team.</p></div></main>'''
    doc=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>The Wire: NFL Player News | Lineup Beat</title><meta name="description" content="NFL player news, headlines and original reports from trusted sources. Filter The Wire by player and team."><link rel="canonical" href="{e(base.rstrip('/')+PATH)}"><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600&amp;family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&amp;display=swap" rel="stylesheet"><style>{CSS}</style></head><body>{seo.site_nav('wire','nfl')}{body}{seo.site_footer()}<script>{JS}</script></body></html>'''
    target=out or ROOT/'site/nfl/wire/index.html';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(doc)
    return target

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--refresh',action='store_true');ap.add_argument('--refresh-only',action='store_true');ap.add_argument('--check-health',action='store_true');ap.add_argument('--base',default='https://lineupbeat.com');args=ap.parse_args()
    if args.check_health:
        raise SystemExit(0 if check_health() else 1)
    if args.refresh or args.refresh_only:refresh()
    if not args.refresh_only:build(args.base)
if __name__=='__main__':main()
