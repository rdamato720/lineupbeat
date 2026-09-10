#!/usr/bin/env python3
"""Publish two weekly boards from the same validated source as Decision Room."""
from __future__ import annotations
import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import decision_data
import seo

ROOT = Path(__file__).resolve().parents[1]
FORMATS = {'half_ppr': 'Half-PPR', 'ppr': 'PPR', 'non_ppr': 'Non-PPR'}
STATS = [('passing_yards', 'Pass Yds'), ('passing_tds', 'Pass TD'),
         ('passing_interceptions', 'INT'), ('carries', 'Carries'),
         ('rushing_yards', 'Rush Yds'), ('rushing_tds', 'Rush TD'),
         ('receptions', 'Rec'), ('receiving_yards', 'Rec Yds'), ('receiving_tds', 'Rec TD')]
e = lambda value: html.escape(str(value), quote=True)
CSS = '''
body{margin:0;background:var(--paper);color:var(--ink);font-family:system-ui,sans-serif}
.wb{max-width:1240px;margin:auto;padding:1.5rem 1rem 3rem}.wb h1{font-family:var(--agate);font-size:clamp(2rem,4vw,3rem);line-height:1.1;margin:.4rem 0 .7rem}.wb-meta,.wb-note{font-size:.875rem;color:var(--quiet);line-height:1.6}.wb-meta{margin:0}.wb-links{display:flex;gap:.6rem 1.2rem;flex-wrap:wrap;margin:1rem 0}.wb a{color:var(--signal);text-underline-offset:3px}.wb-links a{font-size:1rem}.wb-links [aria-current]{color:var(--ink);font-weight:700}.wb-controls{display:grid;grid-template-columns:repeat(3,minmax(120px,1fr)) minmax(180px,2fr);gap:.75rem;padding:1rem;background:var(--card);border:1px solid var(--rule);border-top:3px solid var(--signal);border-radius:8px}.wb-controls label{display:grid;gap:.4rem;font-size:.875rem}.wb-controls select,.wb-controls input{width:100%;min-width:0;min-height:44px;background:var(--paper);color:var(--ink);border:1px solid var(--rule);border-radius:5px;padding:.5rem;font:inherit;font-size:1rem}.wb-controls :focus-visible,.wb-table:focus-visible{outline:2px solid var(--signal);outline-offset:3px}.wb-table{overflow:auto;border:1px solid var(--rule);border-radius:8px}.wb table{width:100%;border-collapse:separate;border-spacing:0;font-size:.9375rem;font-variant-numeric:tabular-nums}.wb caption{text-align:left;padding:1rem;font-weight:600}.wb th,.wb td{padding:.8rem;white-space:nowrap;border-bottom:1px solid var(--rule);text-align:right}.wb th{font-size:.875rem;color:var(--quiet);background:var(--card)}.wb .player,.wb th.player{text-align:left;position:sticky;left:0;background:var(--card);z-index:1}.wb .player a{color:var(--ink);font-weight:600;text-decoration:none}.wb .player a:hover{text-decoration:underline}.wb .points{color:var(--signal);font-weight:700}.wb .status{font-size:.875rem}.wb tr:last-child td{border-bottom:0}.wb tr[hidden],.wb [hidden]{display:none}.wb-empty{padding:1rem}.wb .player small{display:block;color:var(--quiet);font-size:.875rem;margin-top:.25rem}.wb-footer{margin-top:1.5rem;border-top:1px solid var(--rule);padding-top:1rem}@media(max-width:640px){.wb-controls{grid-template-columns:1fr 1fr}.wb{padding-top:1rem}.wb th,.wb td{padding:.7rem .55rem}.wb .player{max-width:175px;white-space:normal;min-width:145px}}
'''
# Shared homepage palette and condensed typography, with a compact weekly scorecard.
CSS += r"""
body{background:#080c0b;font-family:var(--agate)}.wb{max-width:1180px;padding:32px 16px 64px}.wb-hero{position:relative;padding:clamp(24px,5vw,48px);border:1px solid #354239;border-radius:12px;background:radial-gradient(ellipse at 90% 5%,#c6f53c12,transparent 65%),linear-gradient(#ffffff05 1px,transparent 1px),linear-gradient(90deg,#ffffff05 1px,transparent 1px),#0b120e;background-size:auto,64px 64px,64px 64px,auto;overflow:hidden}.wb-hero:before{content:"";position:absolute;top:0;left:0;width:90px;height:4px;background:#c6f53c}.wb .wb-eyebrow{color:#c6f53c;font:700 12px var(--agate);letter-spacing:.14em}.wb h1{font:700 clamp(40px,6vw,72px)/1 var(--agate);letter-spacing:-.03em;text-transform:uppercase;max-width:850px;margin:18px 0}.wb h1 span{color:#c6f53c}.wb-intro{font:18px/1.5 var(--agate);color:#c1cabf;max-width:620px;margin:0 0 18px}.wb-meta,.wb-note{font:15px/1.5 var(--agate);color:#aeb9ad}.wb-links{margin:22px 0;gap:10px}.wb-links a{padding:10px 16px;border:1px solid #354239;border-radius:7px;background:#101813;text-decoration:none;font:600 16px var(--agate)}.wb-links a[aria-current]{background:#c6f53c;color:#11170b;border-color:#c6f53c}.wb-links a:hover{border-color:#c6f53c}.wb a:focus-visible{outline:2px solid #c6f53c;outline-offset:4px}.wb-controls{background:#111813;border-color:#354239;border-top-width:1px;padding:20px;gap:16px}.wb-controls label{font:600 15px var(--agate);color:#c6cec1}.wb-controls input,.wb-controls select{font:16px var(--agate);background:#080e0a;border-color:#52614e;min-height:46px;padding:10px 12px}.wb #wb-count{margin:18px 0 12px}.wb-table{border-color:#354239;background:#0e150f}.wb table{font-family:var(--agate);font-size:18px}.wb caption{color:#c6f53c;font-size:16px;background:#111a13}.wb th{font:600 13px var(--agate);text-transform:uppercase;letter-spacing:.06em;background:#141e16;color:#b8c5b3}.wb td{border-color:#293629}.wb tbody tr:hover td{background:#182317}.wb td.player{background:#101912}.wb .wb-identity{display:flex;align-items:center;gap:12px;min-width:205px;white-space:normal}.wb .wb-photo{width:48px;height:54px;object-fit:contain;flex:0 0 48px;border-radius:8px;background:#080e0a}.wb .wb-player-name{font:600 20px/1.15 var(--agate);color:#f3f6ef}.wb .player small{display:flex;align-items:center;gap:6px;font:14px var(--agate);color:#acb8a5;margin-top:6px}.wb .wb-logo{width:18px;height:18px;object-fit:contain}.wb .points{font-size:23px;color:#c6f53c}.wb .status{font-size:16px}.wb-up-next{margin:0 0 22px;border:1px solid #3b4933;border-top:3px solid #c6f53c;border-radius:10px;background:radial-gradient(ellipse at top right,#c6f53c10,transparent 65%),#101611;padding:20px 22px;display:flex;align-items:center;justify-content:space-between;gap:20px}.wb-score-label{font:700 12px var(--agate);letter-spacing:.13em;color:#c6f53c;text-transform:uppercase}.wb-up-next h2{font:700 27px/1.1 var(--agate);color:#f3f6ef;margin:8px 0}.wb-up-next p{font:16px/1.5 var(--agate);color:#b9c3b3;margin:0;max-width:650px}.wb-pending{border:1px solid #526044;border-radius:999px;padding:9px 14px;font:700 12px var(--agate);letter-spacing:.08em;color:#c4cfb9;white-space:nowrap}.wb-footer{margin-top:24px;padding-top:20px}@media(max-width:640px){.wb{padding:20px 16px 48px}.wb-hero{padding:28px 22px}.wb h1{font-size:44px}.wb-intro{font-size:17px}.wb-up-next{padding:18px 16px;display:block}.wb-pending{display:inline-block;margin-top:14px;font-size:11px}.wb-up-next h2{font-size:25px}.wb .wb-identity{min-width:160px;gap:9px}.wb .wb-photo{width:36px;height:44px;flex-basis:36px}.wb .wb-player-name{font-size:18px}.wb .player small{font-size:13px}.wb .player{min-width:175px;max-width:205px}.wb-controls{padding:16px;gap:12px}.wb-links a{font-size:15px;padding:10px 12px}.wb .points{font-size:21px}}
"""
PENDING_RESULTS = '''<section class="wb-up-next" aria-label="Upcoming Week 1 projection results"><div><div class="wb-score-label">The weekly scorecard</div><h2>Week 1. Then we show our work.</h2><p>After Week 1, see how our projections performed: projected vs. actual points, average miss, and results by position.</p></div><span class="wb-pending">COMING AFTER WEEK 1</span></section>'''

JS = '''
(()=>{
const data=JSON.parse(document.getElementById('week1-data').textContent);
const form=document.getElementById('wb-format'),pos=document.getElementById('wb-position'),team=document.getElementById('wb-team'),search=document.getElementById('wb-search');
const body=document.getElementById('wb-rows'),rows=new Map(Array.from(body.rows,r=>[r.dataset.id,r]));
const labels={half_ppr:'Half-PPR',ppr:'PPR',non_ppr:'Non-PPR'};
function render(){
 const fmt=form.value,q=search.value.trim().toLowerCase();let count=0;
 const sorted=[...data.players].sort((a,b)=>a.formats[fmt].overall_rank-b.formats[fmt].overall_rank);
 for(const p of sorted){const row=rows.get(p.id),f=p.formats[fmt];
 row.hidden=!!((pos.value&&p.position!==pos.value)||(team.value&&p.team!==team.value)||(q&&!p.name.toLowerCase().includes(q)));
 if(!row.hidden)count++;
 row.querySelector('[data-col=rank]').textContent=pos.value?f.position_rank:f.overall_rank;
 row.querySelector('[data-col=position-rank]').textContent=p.position+f.position_rank;
 row.querySelector('[data-col=points]').textContent=f.projected_points.toFixed(1);
 body.appendChild(row);
 }
 document.getElementById('wb-count').textContent=count+(count===1?' player · ':' players · ')+labels[fmt];
 document.getElementById('wb-caption').textContent=labels[fmt]+' · '+(pos.value||'All positions');
 document.getElementById('wb-empty').hidden=count!==0;
}
[form,pos,team].forEach(el=>el.addEventListener('change',render));search.addEventListener('input',render);
render();
})();
'''


def render(payload, kind, site):
    title = f'2026 NFL Week 1 {kind.title()}'
    path = f'/nfl/week-1/{kind}/'
    updated = datetime.fromisoformat(payload['updated_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York'))
    stamp = updated.strftime('%B %d, %Y · %I:%M %p ET')
    players = sorted(payload['players'], key=lambda p: p['formats']['half_ppr']['overall_rank'])
    rows = []
    for p in players:
        f = p['formats']['half_ppr']
        url = '/nfl/' + p['slug'] + '/'
        name = f'<a href="{e(url)}">{e(p["name"])}</a>' if (site / url.lstrip('/') / 'index.html').is_file() else e(p['name'])
        photo = p.get('photo') or '/assets/player-placeholder.svg'
        portrait = f'<img class="wb-photo" src="{e(photo)}" alt="" width="48" height="54" loading="lazy" decoding="async" onerror="this.onerror=null;this.src=\'/assets/player-placeholder.svg\'">'
        logo = f'<img class="wb-logo" src="{e(p["team_logo"])}" alt="" width="18" height="18" loading="lazy" onerror="this.hidden=true">' if p.get('team_logo') else ''
        identity = f'<div class="wb-identity">{portrait}<div><span class="wb-player-name">{name}</span><small>{logo}{e(p["team"])} · {e(p["position"])}</small></div></div>'
        matchup = ('vs ' if p['home'] else '@ ') + p['opponent']
        cells = ''.join(f'<td>{p["stat_projection"][key]:.1f}</td>' for key, _ in STATS) if kind == 'projections' else ''
        rows.append(f'<tr data-id="{e(p["id"])}"><td data-col="rank">{f["overall_rank"]}</td><td class="player">{identity}</td><td data-col="position-rank">{p["position"]}{f["position_rank"]}</td><td>{e(matchup)}</td><td class="points" data-col="points">{f["projected_points"]:.1f}</td><td class="status">{e(p["availability"]["status"])}</td>{cells}</tr>')
    stats_head = ''.join(f'<th scope="col">{label}</th>' for _, label in STATS) if kind == 'projections' else ''
    data = {'updated_at': payload['updated_at'], 'players': [{k:p[k] for k in ('id','name','team','position','formats')} for p in players]}
    encoded = json.dumps(data, separators=(',', ':')).replace('<', '\\u003c')
    links = ''.join(f'<a href="/nfl/week-1/{k}/"'+(' aria-current="page"' if k==kind else '')+f'>Week 1 {k.title()}</a>' for k in ('rankings','projections'))
    options = ''.join(f'<option value="{k}">{label}</option>' for k,label in FORMATS.items())
    teams = ''.join(f'<option>{e(t)}</option>' for t in sorted({p['team'] for p in players}))
    description = f'2026 NFL Week 1 fantasy {kind} for QB, RB, WR and TE, with PPR, Half-PPR and Non-PPR scoring, matchups and injury status.'
    return seo.check_page(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} | LineupBeat</title><meta name="description" content="{e(description)}"><link rel="canonical" href="https://lineupbeat.com{path}"><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600&amp;family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&amp;display=swap" rel="stylesheet"><style>{CSS}</style></head><body>
{seo.site_nav('week1_'+kind, 'nfl')}
<main class="wb"><header class="wb-hero"><p class="wb-meta wb-eyebrow">2026 FANTASY FOOTBALL · WEEK 1</p><h1>NFL Week 1 <span>{kind.title()}</span></h1><p class="wb-intro">Your opening-week board. Find your players, choose your scoring, and get ready for kickoff.</p><p class="wb-meta">Updated <time datetime="{e(payload['updated_at'])}">{stamp}</time></p></header>
<nav class="wb-links" aria-label="Week 1 boards">{links}<a href="/decision-room/nfl/">Compare players</a></nav>
{PENDING_RESULTS}
<div class="wb-controls"><label>Scoring<select id="wb-format">{options}</select></label><label>Position<select id="wb-position"><option value="">All positions</option><option>QB</option><option>RB</option><option>WR</option><option>TE</option></select></label><label>Team<select id="wb-team"><option value="">All teams</option>{teams}</select></label><label>Player<input id="wb-search" type="search" placeholder="Search players" autocomplete="off"></label></div>
<p id="wb-count" class="wb-meta" role="status">{len(players)} players · Half-PPR</p>
<div class="wb-table" tabindex="0" role="region" aria-label="Week 1 {kind} table"><table><caption id="wb-caption">Half-PPR · All positions</caption><thead><tr><th scope="col">Rank</th><th scope="col" class="player">Player</th><th scope="col">Pos rank</th><th scope="col">Matchup</th><th scope="col">Proj. pts</th><th scope="col">Status</th>{stats_head}</tr></thead><tbody id="wb-rows">{''.join(rows)}</tbody></table><p class="wb-empty" id="wb-empty" hidden>No players match these filters.</p></div>
<p class="wb-note">Ranked by projected Week 1 points. QB, RB, WR and TE only. Confirmed unavailable players project for zero; Questionable and Doubtful tags do not reduce projections.</p>
<nav class="wb-links wb-footer" aria-label="Season-long boards"><a href="/nfl/rankings/">Season rankings</a><a href="/nfl/projections/">Season projections</a></nav></main>
{seo.site_footer()}<script id="week1-data" type="application/json">{encoded}</script><script>{JS}</script></body></html>''', path)


def build(site=ROOT/'site'):
    payload = decision_data.load_weekly()
    for kind in ('rankings','projections'):
        out = site/'nfl/week-1'/kind/'index.html'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(payload, kind, site))
    print(f'Built NFL Week 1 rankings and projections: {len(payload["players"])} players; {payload["updated_at"]}')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--site', type=Path, default=ROOT/'site')
    build(parser.parse_args().site)

if __name__ == '__main__':
    main()
