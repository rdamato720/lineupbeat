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
 document.getElementById('wb-count').textContent=count+' players · '+labels[fmt];
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
        matchup = ('vs ' if p['home'] else '@ ') + p['opponent']
        cells = ''.join(f'<td>{p["stat_projection"][key]:.1f}</td>' for key, _ in STATS) if kind == 'projections' else ''
        rows.append(f'<tr data-id="{e(p["id"])}"><td data-col="rank">{f["overall_rank"]}</td><td class="player">{name}<small>{e(p["team"])} · {e(p["position"])}</small></td><td data-col="position-rank">{p["position"]}{f["position_rank"]}</td><td>{e(matchup)}</td><td class="points" data-col="points">{f["projected_points"]:.1f}</td><td class="status">{e(p["availability"]["status"])}</td>{cells}</tr>')
    stats_head = ''.join(f'<th scope="col">{label}</th>' for _, label in STATS) if kind == 'projections' else ''
    data = {'updated_at': payload['updated_at'], 'players': [{k:p[k] for k in ('id','name','team','position','formats')} for p in players]}
    encoded = json.dumps(data, separators=(',', ':')).replace('<', '\\u003c')
    links = ''.join(f'<a href="/nfl/week-1/{k}/"'+(' aria-current="page"' if k==kind else '')+f'>Week 1 {k.title()}</a>' for k in ('rankings','projections'))
    options = ''.join(f'<option value="{k}">{label}</option>' for k,label in FORMATS.items())
    teams = ''.join(f'<option>{e(t)}</option>' for t in sorted({p['team'] for p in players}))
    description = f'2026 NFL Week 1 fantasy {kind} for QB, RB, WR and TE, with PPR, Half-PPR and Non-PPR scoring, matchups and injury status.'
    return seo.check_page(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} | LineupBeat</title><meta name="description" content="{e(description)}"><link rel="canonical" href="https://lineupbeat.com{path}"><style>{CSS}</style></head><body>
{seo.site_nav('week1_'+kind, 'nfl')}
<main class="wb"><header><p class="wb-meta">2026 FANTASY FOOTBALL · WEEK 1</p><h1>NFL Week 1 {kind.title()}</h1><p class="wb-meta">Updated <time datetime="{e(payload['updated_at'])}">{stamp}</time></p></header>
<nav class="wb-links" aria-label="Week 1 boards">{links}<a href="/decision-room/nfl/">Compare players</a></nav>
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
