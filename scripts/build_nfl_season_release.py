#!/usr/bin/env python3
"""Render the trusted-current season release in the isolated development build.

This module never changes the model, production workbook, or recommendation
inputs. It consumes the freeze manifest and writes public season display files.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import html
import json
import os
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/nfl_season/2026/v1.6-trusted-current'
SITE = ROOT / 'site'
FORMATS = {'ppr': ('PPR', 'ppr'), 'half_ppr': ('Half-PPR', 'half-ppr'), 'non_ppr': ('Non-PPR', 'non-ppr')}
FIELDS = [('attempts','Pass attempts'),('completions','Completions'),('passing_yards','Passing yards'),('passing_tds','Passing TD'),('passing_interceptions','Interceptions'),('carries','Carries'),('rushing_yards','Rushing yards'),('rushing_tds','Rushing TD'),('targets','Targets'),('receptions','Receptions'),('receiving_yards','Receiving yards'),('receiving_tds','Receiving TD'),('fumbles_lost_total','Fumbles lost')]
DISCLOSURE = '''<section class="v15method" id="methodology"><h2>Why this is the trusted set</h2><p>These projections retain Lineup Beat's reviewed August 30 season stat lines only where a player also has one exact, current active roster match by normalized name, NFL team and position. There is no fuzzy matching. Eighty-one current players without that level of support are withheld instead of receiving a lower-confidence estimate.</p><p>The experimental v1.5 values are not used. FantasyGuru remains a private quality-control benchmark: its values are not copied, blended or used to tune individual players. Rankings are calculated directly from the retained stat lines for PPR, Half-PPR and Non-PPR scoring.</p><p>Current injuries and season sportsbook props are not incorporated. Available 60-second-delayed Week 1 markets are not season-projection inputs. These are projections, not guarantees; Week 1 and My Team recommendations remain disabled.</p></section>'''
CSS = '''
.v15{max-width:1120px;margin:auto;padding:32px 16px 60px;color:#e9ece7;font:16px/1.55 system-ui,sans-serif}.v15 h1{font:800 clamp(30px,5vw,56px)/1.1 system-ui;margin:8px 0 16px}.v15 h2{font-size:23px}.v15 a{color:#c6f24e}.v15 .eyebrow{font-size:12px;letter-spacing:.12em;color:#c6f24e;text-transform:uppercase}.v15 .meta{color:#bac2ba}.v15nav,.v15filters{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0}.v15nav a{padding:7px 12px;border:1px solid #3a4437;border-radius:4px;text-decoration:none}.v15nav a[aria-current=page]{background:#c6f24e;color:#142008}.v15 label{display:block;font-size:13px}.v15 input,.v15 select{max-width:100%;min-height:44px;background:#111a12;color:#f1f5ed;border:1px solid #536047;border-radius:4px;padding:9px;font:inherit}.v15 input{width:270px}.v15 table{width:100%;border-collapse:collapse;table-layout:fixed}.v15 th{font-size:12px;text-align:left;color:#bdc9b1;padding:10px 5px}.v15 td{padding:12px 5px;border-top:1px solid #30392e;vertical-align:top}.v15 .rank{width:52px}.v15 .points{width:88px;text-align:right;font-variant-numeric:tabular-nums}.v15 .identity{display:flex;gap:10px;align-items:center;min-width:0}.v15 .identity img{width:42px;height:42px;object-fit:contain;flex-shrink:0}.v15 .identity a{font-weight:700;overflow-wrap:anywhere}.v15 .identity small{display:block;color:#b6c0b0;font-size:12px}.v15 .identity .logo{width:20px;height:20px;vertical-align:middle}.v15 details{margin:8px 0;color:#b8c3b1;font-size:13px}.v15 summary{cursor:pointer}.v15 .statgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:14px 0}.v15 .statgrid div{padding:10px;background:#162016}.v15 .statgrid b{display:block;font-size:20px;color:#edf4e7}.v15 .statgrid span{font-size:12px;color:#bac9ad}.v15method{margin-top:32px;border-top:2px solid #829d4b;padding-top:14px}.v15 [hidden]{display:none!important}.v15 .notice{border-left:3px solid #cfab56;background:#252315;padding:10px 14px}.v15 .points span{display:block;font-size:11px;color:#bcc9b1}.v15 .caption{font-size:12px;color:#bdc9b1;margin:8px 0}.v15 .empty{padding:16px}.v15 .formatpts{display:flex;gap:18px;flex-wrap:wrap}.v15 .formatpts strong{font-size:28px;display:block}.v15 .formatpts span{font-size:13px}@media(max-width:480px){.v15{padding:24px 12px}.v15 .rank{width:35px}.v15 .points{width:62px}.v15 .identity{gap:5px}.v15 .identity img{width:30px;height:30px}.v15 .identity a{font-size:14px}.v15 .identity small{font-size:11px}.v15 th{font-size:11px}.v15 td{padding:10px 3px}.v15nav{gap:6px}.v15nav a{font-size:13px;padding:7px}.v15 details{font-size:12px}}
'''
JS = '''
const search=document.querySelector('#season-search'),position=document.querySelector('#season-position'),team=document.querySelector('#season-team'),format=document.querySelector('#season-format');
const rows=[...document.querySelectorAll('tr[data-player-id]')];
function update(){const q=search.value.trim().toLowerCase(),p=position.value,t=team.value,f=format.value;let count=0;
rows.sort((a,b)=>Number(a.dataset[f+'Rank'])-Number(b.dataset[f+'Rank'])).forEach(r=>{r.parentElement.append(r);r.hidden=!(r.dataset.name.includes(q)&&(!p||r.dataset.position===p)&&(!t||r.dataset.team===t)&&(!document.body.dataset.top||Number(r.dataset[f+'Rank'])<=200));if(!r.hidden)count++;r.querySelector('.rank').textContent=r.dataset[(document.body.dataset.position?'position_':'')+f+'Rank'];r.querySelector('.pointvalue').textContent=Number(r.dataset[f+'Points']).toFixed(1);r.querySelector('.pointlabel').textContent=format.selectedOptions[0].text;});
document.querySelector('#season-count').textContent=count+(count===1?' player':' players');document.querySelector('#season-empty').hidden=count!==0;
const h=document.querySelector('h1');h.textContent=h.textContent.replace(/(?:Half-PPR|Non-PPR|PPR) /,format.selectedOptions[0].text+' ');document.title=h.textContent+' | Lineup Beat';}
[search,position,team,format].forEach(e=>e.addEventListener('input',update));update();
'''

def esc(value): return html.escape(str(value), quote=True)
def enabled():
    if os.environ.get('LINEUPBEAT_NFL_SEASON') != 'v1.6-trusted-current':return False
    if os.environ.get('DEV_PROJECT') != 'lineupbeat-dev':raise ValueError('trusted-current release requires isolated development project')
    return True

@lru_cache(maxsize=1)
def load():
    freeze=json.loads((SOURCE/'release_freeze.json').read_text())
    for name,key in [('season_projections.json','season_sha256'),('season_rankings.json','rankings_sha256'),('withheld_players.json','withheld_sha256')]:
        if hashlib.sha256((SOURCE/name).read_bytes()).hexdigest()!=freeze[key]:raise ValueError('frozen release integrity failure: '+name)
    for rel,key in [(freeze['source_workbook'],'source_workbook_sha256'),(freeze['current_active_source'],'current_active_source_sha256')]:
        if hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()!=freeze[key]:raise ValueError('trusted source integrity failure: '+rel)
    model=json.loads((SOURCE/'season_projections.json').read_text())
    rank=json.loads((SOURCE/'season_rankings.json').read_text())
    return model,rank

def overlay_roster(roster):
    for p in load()[0]['players']:
        prior=roster.get(p['player_id'],{})
        roster[p['player_id']]={**prior,'id':p['player_id'],'name':p['name'],'team':p['team'],'position':p['position'],'depth_pos':p['position'],'depth_order':p['role_confidence']['current_depth_rank'] or '', 'years_exp':p['years_exp'],'adp':'','injury_status':'','season_photo':photo(p)}
    return roster

def projection_map():
    fieldmap={'targets':'targets','rec':'receptions','recyd':'receiving_yards','rectd':'receiving_tds','ruatt':'carries','ruyd':'rushing_yards','rutd':'rushing_tds','patt':'attempts','cmp':'completions','payd':'passing_yards','patd':'passing_tds','int':'passing_interceptions','fl':'fumbles_lost_total'}
    model,ranking=load();ranks={r['gsis_id']:r['position_rank'] for r in ranking['formats']['ppr']['rows']}
    return {p['canonical_slug']:{'ppr':p['formats']['ppr'],'half':p['formats']['half_ppr'],'std':p['formats']['non_ppr'],'rank':ranks[p['gsis_id']],'pos':p['position'],'line':{k:p['stat_projection'][m] for k,m in fieldmap.items()}} for p in model['players']}

def legacy_rank_sets():
    return {fmt:[{**r,'player_name':r['name'],'projected_points':r['fantasy_points'],'vorp':None} for r in spec['rows']] for fmt,spec in load()[1]['formats'].items()}

def photo(p):
    if p.get('espn_id'):return 'https://a.espncdn.com/i/headshots/nfl/players/full/'+p['espn_id']+'.png'
    return p.get('headshot_url') or '/assets/player-placeholder.svg'

def wrapper(title,path,body,position='',script=''):
    import build_projections as bp
    import seo
    css,header,footer=bp.site_chrome()
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} | Lineup Beat</title><meta name="description" content="Trusted 2026 NFL season projections and scoring rankings for 424 current players, reviewed for the redesigned development site."><link rel="canonical" href="https://lineupbeat-dev.pages.dev{path}"><style>{css}{seo.UI_CSS}{CSS}</style></head><body data-position="{position}">{header}<main class="v15">{body}</main>{footer}<script>{script}</script></body></html>'''

def identity(p):
    return f'''<div class="identity"><img src="{esc(photo(p))}" alt="" loading="lazy" onerror="this.onerror=null;this.src='/assets/player-placeholder.svg'"><div><a href="{p['url']}">{esc(p['name'])}</a><small><img class="logo" src="https://a.espncdn.com/i/teamlogos/nfl/500/{p['team'].lower()}.png" alt=""> {p['team']} · {p['position']} · {esc(p['offensive_role'] or 'Depth unavailable')}</small></div></div>'''

def table(model,ranking,fmt='ppr',pos=None,kind='projections',top=False):
    players={p['gsis_id']:p for p in model['players']}
    lookup={f:{r['gsis_id']:r for r in ranking['formats'][f]['rows']} for f in FORMATS}
    chosen=[r for r in ranking['formats'][fmt]['rows'] if not pos or r['position']==pos]
    parts=[]
    for r in chosen:
        p=players[r['gsis_id']];pid=p['gsis_id'];attrs=['hidden'] if top and r['overall_rank']>200 else []
        for f in FORMATS:
            attrs.extend([f'data-{f}-rank="{lookup[f][pid]["overall_rank"]}"',f'data-position_{f}-rank="{lookup[f][pid]["position_rank"]}"',f'data-{f}-points="{p["formats"][f]}"'])
        details=''
        if kind=='projections':
            stats=' · '.join(f'{label}: {p["stat_projection"][m]:.1f}' for m,label in FIELDS if p['stat_projection'][m])
            details=f'<details><summary>Projected season stat line</summary><p>{stats}</p><p>Reviewed August 30 stat line; current injury adjustments are unavailable.</p></details>'
        parts.append(f'''<tr data-player-id="{pid}" data-name="{esc(p['name'].lower())}" data-team="{p['team']}" data-position="{p['position']}" {' '.join(attrs)}><td class="rank">{r['position_rank'] if pos else r['overall_rank']}</td><td>{identity(p)}{details}</td><td class="points"><b class="pointvalue">{p['formats'][fmt]:.1f}</b><span class="pointlabel">{FORMATS[fmt][0]}</span></td></tr>''')
    return '<table><caption class="caption">Descending projected points; stable player ID breaks ties.</caption><thead><tr><th class="rank">Rank</th><th>Player / team / role</th><th class="points">Points</th></tr></thead><tbody>'+''.join(parts)+'</tbody></table>'

def board_page(model,ranking,path,fmt='ppr',pos=None,kind='projections',top=False):
    label=FORMATS[fmt][0];title=f'2026 NFL {pos+" " if pos else ""}{label+" " if kind=="rankings" else ""}{"Top 200 " if top else ""}{kind.title()}'
    base='/nfl/'+kind+'/'
    fmtlinks=''.join(f'<a href="/nfl/rankings/{slug}/"'+(' aria-current="page"' if f==fmt and kind=='rankings' else '')+f'>{lab}</a>' for f,(lab,slug) in FORMATS.items())
    nav=f'<nav class="v15nav" aria-label="Season tools"><a href="/nfl/projections/">Projections</a>{fmtlinks}</nav><nav class="v15nav" aria-label="Positions"><a href="{base+(FORMATS[fmt][1]+"/" if kind=="rankings" else "")}">Overall</a>'+''.join(f'<a href="{base+(FORMATS[fmt][1]+"/" if kind=="rankings" else "")}{p.lower()}/">{p}</a>' for p in ('QB','RB','WR','TE'))+'</nav>'
    if kind=='rankings':
        import build_ranking_formats as more
        nav+='<details><summary>More ranking views</summary><nav class="v15nav">'+''.join(f'<a href="{url}">{label}</a>' for label,url,live in more.FORMAT_NAV if live)+'</nav></details>'
    controls='<div class="v15filters"><label>Search player<input id="season-search" type="search" placeholder="Name"></label><label>Scoring<select id="season-format">'+''.join(f'<option value="{f}"'+(' selected' if f==fmt else '')+f'>{lab}</option>' for f,(lab,_) in FORMATS.items())+'</select></label><label>Position<select id="season-position"><option value="">All positions</option>'+''.join(f'<option value="{p}"'+(' selected' if pos==p else '')+f'>{p}</option>' for p in ('QB','RB','WR','TE'))+'</select></label><label>Team<select id="season-team"><option value="">All teams</option>'+''.join(f'<option>{t}</option>' for t in sorted({p['team'] for p in model['players']}))+'</select></label></div>'
    counts=model['metadata']['position_counts']
    body=f'<p class="eyebrow">NFL · 2026 season · trusted current set</p><h1>{title}</h1><p class="meta">{model["metadata"]["trusted_population"]} trusted current players · QB {counts["QB"]} / RB {counts["RB"]} / WR {counts["WR"]} / TE {counts["TE"]}<br>Projection values reviewed August 30 · current roster matched September 2</p>{nav}{controls}<p id="season-count" aria-live="polite"></p>'+table(model,ranking,fmt,pos,kind,top)+'<p class="empty" id="season-empty" hidden>No matching players.</p>'+DISCLOSURE
    page=wrapper(title,path,body,pos or '',JS)
    return page.replace('<body data-position=', '<body data-top="200" data-position=',1) if top else page

def write(path,text):
    dest=SITE/path.strip('/')/'index.html';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(text)

def context_pages():
    for slug,title in [('strength-of-schedule','Strength of schedule'),('offensive-line-rb-performance','Offensive line and running back performance'),('durability','Durability')]:
        path='/nfl/'+slug+'/'
        write(path,wrapper(title,path,f'<p class="eyebrow">Development preview</p><h1>{title}</h1><p>The historical database needed for this page is not included in this offline development release.</p><p><a href="/nfl/projections/">View the validated 2026 season projections</a>.</p>'))

def recommendation_gates():
    """Keep the authorized disabled state at the dev presentation boundary.

    The existing weekly models, engines, adapter sources and extension remain
    unchanged. Forecast inspection and roster connections still work.
    """
    gates={'season_version':'v1.6-trusted-current','week1_recommendations_enabled':False,'my_team_recommendations_enabled':False,'reason':'Trusted season projections do not authorize weekly lineup recommendations'}
    (SITE/'data/nfl-trusted-release-gates.json').write_text(json.dumps(gates,sort_keys=True)+'\n')
    notice='<aside class="v15 notice" id="weekly-release-gate"><strong>Week 1 recommendations are disabled.</strong> Forecasts are available for inspection. This season release does not authorize lineup recommendations.</aside>'
    page=SITE/'decision-room/nfl/index.html';text=page.read_text()
    marker='function draw(){'
    if text.count(marker)!=1:raise ValueError('weekly comparison gate insertion is ambiguous')
    readonly="""function draw(){if(true){let a=P[A.value],b=P[B.value],k=F.value;O.innerHTML='<section class="dr-empty" data-weekly-recommendations="disabled"><h3>No reliable call</h3><p>Week 1 recommendations are disabled for this release.</p>'+(a&&b?'<p>'+safe(a.name)+': '+num(points(a,k))+' projected points · '+safe(b.name)+': '+num(points(b,k))+' projected points</p>':'')+'</section>';return;}"""
    text=text.replace(marker,readonly,1).replace('<section class="dr-compare"',notice+'<section class="dr-compare"',1)
    page.write_text(text)
    page=SITE/'my-team/index.html';text=page.read_text()
    marker='<script src="/my-team/league-adapter.js"></script>'
    if text.count(marker)!=1:raise ValueError('My Team recommendation gate insertion is ambiguous')
    gate='''<script id="my-team-release-gate">Object.defineProperty(LineupBeatLeagueAdapter,'actionableDecisions',{value:()=>[],writable:false,configurable:false});Object.defineProperty(LineupBeatLeagueAdapter,'lineupDecisions',{value:()=>[],writable:false,configurable:false});</script>'''
    text=text.replace(marker,marker+gate,1)
    text=text.replace('</head>','<style>#mt-outlook,#mt-decisions,#mt-team .mt-team-block:has(#mt-decisions){display:none!important}</style></head>',1)
    text=text.replace('<section class="mt-team-card"',notice.replace('id="weekly-release-gate"','id="my-team-recommendations-disabled"')+'<section class="mt-team-card"',1)
    page.write_text(text)

def player_pages(model,ranking):
    """Reuse approved player-page news; replace only the season panel and identity."""
    import build_pages as bp
    def original_slug(name):return re.sub(r'[\s_]+','-',re.sub(r'[^\w\s-]','',name.lower())).strip('-')
    rank={r['gsis_id']:r for r in ranking['formats']['ppr']['rows']}
    for p in model['players']:
        oldpath=SITE/'nfl'/original_slug(p['name'])/'index.html'
        path=SITE/p['url'].strip('/')/'index.html'
        # If the established route uses a suffix variant, match by stable page id.
        source=path if path.exists() else oldpath
        legacy=source.read_text() if source.exists() else ''
        # Only approved publications enter a newly created page.
        if not legacy:
            prior=bp.PROJECTIONS;bp.PROJECTIONS={}
            row={'id':p['player_id'],'name':p['name'],'team':p['team'],'pos':p['position'],'meta':{'years_exp':p['years_exp']}}
            legacy=bp.player_page(row,[],'https://lineupbeat-dev.pages.dev',bp.load_wire_impacts().get(p['gsis_id'],[]))
            bp.PROJECTIONS=prior
        stats=''.join(f'<div><span>{label}</span><b>{p["stat_projection"][m]:.1f}</b></div>' for m,label in FIELDS if p['stat_projection'][m] or (p['position']=='QB' and m in ('attempts','completions')))
        pts=''.join(f'<div><span>{label} · {p["position"]}{next(r["position_rank"] for r in ranking["formats"][fmt]["rows"] if r["gsis_id"]==p["gsis_id"])}</span><strong>{p["formats"][fmt]:.1f}</strong></div>' for fmt,(label,_) in FORMATS.items())
        flags=' '.join(p['evidence_limitation_flags'])
        panel=f'<section class="proj v15" data-season-player-id="{p["gsis_id"]}"><p class="eyebrow">2026 season · trusted current set</p><h2>Season projection</h2><div class="formatpts">{pts}</div><p>{p["team"]} · {p["position"]} · {esc(p["offensive_role"] or "Depth unavailable")} · current active exact match</p><p class="meta">Projection values reviewed August 30 · current roster matched September 2</p>'+ (f'<p class="notice">{esc(flags)}</p>' if flags else '')+f'<div class="statgrid">{stats}</div><a href="/nfl/projections/{p["position"].lower()}/">All {p["position"]} projections</a> · <a href="/nfl/rankings/ppr/{p["position"].lower()}/">PPR rankings</a>{DISCLOSURE}</section>'
        if re.search(r'<section class="proj[" ]',legacy):legacy=re.sub(r'<section class="proj[" ].*?</section>',lambda _:panel,legacy,count=1,flags=re.S)
        else:legacy=legacy.replace('</main>',panel+'</main>',1)
        # Remove outdated current injury/ADP assertions from this season view.
        legacy=re.sub(r'<span class="chip">(?:Current ADP|Status).*?</span>','',legacy,flags=re.S)
        legacy=legacy.replace('</head>','<style>'+CSS+'</style></head>',1)
        legacy=re.sub(r'<link rel="canonical" href="[^"]+">',f'<link rel="canonical" href="https://lineupbeat-dev.pages.dev{p["url"]}">',legacy)
        # The canonical heading retains approved prose below, with current identity.
        legacy=re.sub(r'(class="shot"[^>]*src=")[^"]+',lambda m:m[1]+esc(photo(p)),legacy)
        legacy=re.sub(r'(<img class="shot"[^>]*)(>)',r'\1 onerror="this.onerror=null;this.src=\'/assets/player-placeholder.svg\'"\2',legacy,count=1)
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(legacy)
        if oldpath!=path and oldpath.exists():
            oldpath.write_text(legacy)
    by_id={p['player_id']:p for p in model['players']}
    # Preserve existing suffix/apostrophe aliases through the stable roster ID.
    for old in csv.DictReader((ROOT/'rosters/nfl.csv').open()):
        p=by_id.get(old['id'])
        if p and original_slug(old['name'])!=p['canonical_slug']:
            alias=SITE/'nfl'/original_slug(old['name'])/'index.html'
            alias.parent.mkdir(parents=True,exist_ok=True)
            alias.write_bytes((SITE/p['url'].strip('/')/'index.html').read_bytes())

def withheld_player_pages():
    """Remove stale season numbers from current players outside the trusted set."""
    withheld=json.loads((SOURCE/'withheld_players.json').read_text())['players']
    for p in withheld:
        path=SITE/'nfl'/re.sub(r'[\s_]+','-',re.sub(r'[^\w\s-]','',p['name'].lower())).strip('-')/'index.html'
        if not path.exists():continue
        text=path.read_text()
        panel=f'''<section class="proj v15" data-season-projection="withheld"><p class="eyebrow">2026 season · evidence hold</p><h2>Season projection withheld</h2><p>{esc(p['name'])} is on the current {p['team']} roster, but does not have one exact current name, team and position match in the reviewed projection baseline. Lineup Beat will not fill that gap with a lower-confidence estimate.</p><p><a href="/nfl/projections/coverage/">See trusted-set coverage</a></p></section>'''
        if re.search(r'<section class="proj[" ]',text):
            text=re.sub(r'<section class="proj[" ].*?</section>',lambda _:panel,text,count=1,flags=re.S)
        elif '</main>' in text:text=text.replace('</main>',panel+'</main>',1)
        elif '<footer' in text:text=text.replace('<footer',panel+'<footer',1)
        else:text=text.replace('</body>',panel+'</body>',1)
        path.write_text(text.replace('</head>','<style>'+CSS+'</style></head>',1))

def build():
    model,ranking=load()
    asset=SITE/'assets/player-placeholder.svg';asset.parent.mkdir(parents=True,exist_ok=True)
    asset.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><rect width="100" height="100" fill="#203020"/><circle cx="50" cy="33" r="18" fill="#809477"/><path d="M16 95v-15a34 34 0 0168 0v15" fill="#809477"/></svg>')
    for pos in [None,'QB','RB','WR','TE']:
        path='/nfl/projections/'+(pos.lower()+'/' if pos else '')
        write(path,board_page(model,ranking,path,pos=pos))
    # Rankings are deliberately left to build_rankings/build_ranking_formats.
    # Those builders provide the established tiered board, filters, position
    # ranks and mobile treatment. Reusing the projection table here caused a
    # release-only visual regression on every rankings URL.
    public={'metadata':model['metadata'],'players':[{k:p[k] for k in ('gsis_id','player_id','name','team','position','status','url','offensive_role','stat_projection','formats','methodology_version','data_cutoff','evidence_limitation_flags')} for p in model['players']]}
    (SITE/'data').mkdir(exist_ok=True)
    (SITE/'data/nfl-season-trusted.json').write_text(json.dumps(public,sort_keys=True,separators=(',',':'))+'\n')
    (SITE/'data/nfl-season-rankings-trusted.json').write_text(json.dumps(ranking,sort_keys=True,separators=(',',':'))+'\n')
    player_pages(model,ranking)
    withheld_player_pages()
    # These legacy destinations need database evidence absent from the offline
    # checkout. Keep the links honest and usable without fetching new inputs.
    context_pages()
    withheld=json.loads((SOURCE/'withheld_players.json').read_text())
    rows=''.join(f'<tr><td>{esc(p["name"])}</td><td>{p["team"]}</td><td>{p["position"]}</td><td>{esc(p["offensive_role"] or "Role unavailable")}</td></tr>' for p in withheld['players'])
    path='/nfl/projections/coverage/'
    write(path,wrapper('Trusted projection coverage',path,f'<p class="eyebrow">2026 season · evidence policy</p><h1>Trusted projection coverage</h1><p>{model["metadata"]["trusted_population"]} current players have a reviewed projection and one exact current identity, team and position match. The {withheld["withheld_population"]} players below are deliberately withheld; no v1.5 fallback or fabricated estimate is shown.</p><table><thead><tr><th>Player</th><th>Team</th><th>Position</th><th>Current role</th></tr></thead><tbody>'+rows+'</tbody></table>'+DISCLOSURE))
    for page in list((SITE/'nfl/who-should-i-draft').rglob('index.html'))+[SITE/'nfl/draft-value/index.html']:
        if page.exists():
            text=page.read_text().replace('</main>','<div class="v15"><p class="eyebrow">Season source: trusted current set · September 2, 2026</p>'+DISCLOSURE+'</div></main>')
            page.write_text(text.replace('</head>','<style>'+CSS+'</style></head>'))
    hub=SITE/'nfl/data/index.html'
    hub.write_text(hub.read_text().replace('615-player','424-player').replace('505-player','424-player').replace('216-player','424-player'))
    about=SITE/'about/index.html'
    if about.exists():
        text=about.read_text().replace('Advanced Draft Comparison contains 216. NFL projection pages cover 615.','Advanced Draft Comparison and NFL projection boards use the 424-player trusted current set.')
        about.write_text(text.replace('Advanced Draft Comparison contains 505. NFL projection pages cover 505.','Advanced Draft Comparison and NFL projection boards use the 424-player trusted current set.'))
    sitemap=SITE/'sitemap.xml';text=sitemap.read_text()
    existing=set(re.findall(r'<loc>https?://[^/]+(/[^<]*)</loc>',text))
    required={p['url'] for p in model['players']}
    required.update('/'+str(p.parent.relative_to(SITE)).rstrip('/')+'/' for folder in ('nfl/projections','nfl/rankings') for p in (SITE/folder).rglob('index.html'))
    urls=sorted(existing|required)
    sitemap.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'+''.join(f'<url><loc>https://lineupbeat-dev.pages.dev{url}</loc></url>\n' for url in urls)+'</urlset>\n')
    recommendation_gates()
    print('v1.6 trusted-current: 424 projection pages; 81 evidence holds; 3 scoring formats verified')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--development',action='store_true');args=ap.parse_args()
    if not args.development:raise SystemExit('explicit --development required')
    build()

if __name__=='__main__':main()
