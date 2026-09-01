#!/usr/bin/env python3
"""Inject the development-only 2026 Preseason Decision Room homepage."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

import build_comparison_tool
import college_decision_data
import college_decision_room
import decision_data
from decision_engine import (FORMAT_LABELS, DecisionContext, closest_calls,
                             scoring_movers, value_signals)

START = "<!-- LB DECISION ROOM START -->"
END = "<!-- LB DECISION ROOM END -->"
WIRE_START = "<!-- LB WIRE REPLACEMENT START -->"
WIRE_END = "<!-- LB WIRE REPLACEMENT END -->"
WIRE_PATH = "/decision-room/reviewed-wire/"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def player_label(player: dict) -> str:
    return f"{player['name']} · {player['team']} {player['position']}"


def call_card(result: dict) -> str:
    if result["is_tie"]:
        w, r = result["player_a"], result["player_b"]
        wf, rf = result["player_a_format"], result["player_b_format"]
        relation, recommendation = "tied with", "No clear edge"
    else:
        w, r = result["winner"], result["runner_up"]
        wf, rf = result["winner_format"], result["runner_up_format"]
        relation, recommendation = "over", f"Recommend {w['name']}"
    return f'''<article class="dr-mini">
      <div class="dr-mini-pair"><span>{esc(w['name'])}</span><i>{relation}</i><span>{esc(r['name'])}</span></div>
      <p>{esc(w['position'])}{wf['position_rank']} vs {esc(r['position'])}{rf['position_rank']}
        · {result['gap']:.1f}-point gap · {esc(result['confidence'])}</p>
      <p class="dr-recommendation">{esc(recommendation)}</p>
      <p class="dr-market">ADP {esc(w['adp'] if w['adp'] is not None else '—')} / {esc(r['adp'] if r['adp'] is not None else '—')}</p>
      <button type="button" class="dr-open" data-a="{esc(w['id'])}" data-b="{esc(r['id'])}">Compare</button>
    </article>'''


def conviction_card(row: dict) -> str:
    p, f, delta = row["player"], row["format"], row["rank_adp_delta"]
    direction = "earlier" if delta > 0 else "later"
    return f'''<article class="dr-signal">
      <img src="{esc(p['photo'] or p['team_logo'])}" alt="" onerror="this.src='{esc(p['team_logo'])}'">
      <div><small>{esc(p['team'])} · {esc(p['position'])}</small><h3>{esc(p['name'])}</h3>
      <p>Lineup Beat ranks {esc(p['name'])} {abs(delta):.1f} spots {direction} than market ADP:
      projection rank {f['overall_rank']}, ADP {float(p['adp']):.1f}.</p></div>
    </article>'''


def mover_card(row: dict) -> str:
    p, ranks = row["player"], row["ranks"]
    return f'''<article class="dr-mover"><small>{esc(p['team'])} · {esc(p['position'])}</small>
      <h3>{esc(p['name'])}</h3><div><span>PPR <b>{esc(p['position'])}{ranks['ppr']}</b></span>
      <span>Half-PPR <b>{esc(p['position'])}{ranks['half_ppr']}</b></span>
      <span>Non-PPR <b>{esc(p['position'])}{ranks['non_ppr']}</b></span></div>
      <p>Moves {row['spread']} position-rank spots across scoring formats.</p></article>'''


def render(payload: dict) -> str:
    players = payload["players"]
    calls = closest_calls(players, "half_ppr")
    values, fades = value_signals(players, "half_ppr")
    movers = scoring_movers(players)
    if not calls:
        raise ValueError("validated projections produced no closest calls")
    first = calls[0]
    for p in players:
        p["team_color"] = build_comparison_tool.TEAM_COLORS.get(
            p["team"], ("#263238", "#c6f53c"))[0]
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    options = "".join(f'<option value="{esc(p["id"])}">{esc(player_label(p))}</option>'
                      for p in players)
    updated = payload["updated_at"]
    block = f'''{START}
<nav class="dr-sports" aria-label="Decision Room sport"><a data-sport="nfl" href="/?sport=nfl" aria-pressed="true">NFL</a><a data-sport="college" href="/?sport=college" aria-pressed="false">College</a></nav>
<main id="decision-room" class="dr-shell" data-mode="season" data-season="2026">
  <section class="dr-hero">
    <div class="dr-kicker">2026 Preseason Decision Room</div>
    <div class="dr-mode">Draft Mode — based on full-season projections</div>
    <h1>Make the decision—not just the projection.</h1>
    <p class="dr-lede">Compare outcomes, see the stronger full-season projection, and understand exactly what would change the pick.</p>
    <p class="dr-week-note">Weekly lineup decisions will become available after validated weekly projections are added. Season values are never presented as weekly forecasts.</p>
    <a class="dr-beat-link" href="{WIRE_PATH}">Browse the complete reviewed Wire →</a>
    <section class="dr-compare" aria-labelledby="dr-compare-title">
      <div class="dr-compare-head"><div><small>Decision 01</small><h2 id="dr-compare-title">Player vs. player</h2></div>
        <label>Scoring format<select id="dr-format"><option value="ppr">PPR</option><option value="half_ppr" selected>Half-PPR</option><option value="non_ppr">Non-PPR</option></select></label></div>
      <div class="dr-selectors"><div class="dr-picker"><label for="dr-a-search">Player one</label><input id="dr-a-search" type="search" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="dr-a-list" autocomplete="off"><ul id="dr-a-list" role="listbox" hidden></ul><select id="dr-a" class="dr-native" aria-label="Player one fallback">{options}</select></div><b>VS</b><div class="dr-picker"><label for="dr-b-search">Player two</label><input id="dr-b-search" type="search" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="dr-b-list" autocomplete="off"><ul id="dr-b-list" role="listbox" hidden></ul><select id="dr-b" class="dr-native" aria-label="Player two fallback">{options}</select></div></div>
      <label class="dr-cross"><input type="checkbox" id="dr-cross-position"> Compare across positions</label>
      <div id="dr-result" aria-live="polite"></div>
    </section>
  </section>

  <section class="dr-section" id="closest"><div class="dr-section-head"><div><small>Decision pressure</small><h2>Closest Calls</h2></div><p>Same-position preseason decisions separated by the fewest validated half-PPR season points.</p></div>
    <div class="dr-card-grid">{''.join(call_card(c) for c in calls)}</div></section>

  <section class="dr-section dr-convictions"><div class="dr-section-head"><div><small>Projection rank vs. ADP</small><h2>Our Values</h2></div><p>Players Lineup Beat ranks meaningfully earlier than validated market ADP.</p></div>
    <div class="dr-signal-grid">{''.join(conviction_card(c) for c in values)}</div>
    <div class="dr-section-head dr-fades-head"><div><small>Projection rank vs. ADP</small><h2>Our Fades</h2></div><p>Players Lineup Beat ranks meaningfully later than validated market ADP.</p></div>
    <div class="dr-signal-grid">{''.join(conviction_card(c) for c in fades)}</div></section>

  <section class="dr-section"><div class="dr-section-head"><div><small>Format sensitivity</small><h2>Scoring-format movers</h2></div><p>Players whose position rank changes most when receptions change value.</p></div>
    <div class="dr-mover-grid">{''.join(mover_card(m) for m in movers)}</div></section>

  <section class="dr-section dr-news" aria-labelledby="dr-news-title"><div class="dr-section-head"><div><small>The Beat</small><h2 id="dr-news-title">Latest from trusted sources</h2></div><p>The existing news experience remains available as supporting context, separate from projection-driven decisions.</p></div>
    <aside class="lb-wire-card"><div class="lb-wire-header"><div class="lb-wire-live"><span class="lb-live-dot"></span>RECENT NEWS</div><div class="lb-wire-time" id="liveago">NEWS UPDATED NOW</div></div><div class="lb-wire-feed" id="livelist"></div><a href="{WIRE_PATH}" class="lb-wire-footer"><span>VIEW THE COMPLETE REVIEWED WIRE</span></a></aside></section>

  <section class="dr-future-grid">
    <article class="dr-empty"><small>Coming next</small><h2>Decision Inbox</h2><p>Connect your league to see the decisions that matter on your roster.</p><button disabled>League sync not yet available</button></article>
    <article class="dr-empty"><small>Accountability layer</small><h2>Decision Record</h2><p>No decisions have been recorded. Future snapshots will preserve the recommendation, inputs, timestamp, and eventual outcome instead of silently rewriting the call.</p><div class="dr-empty-line">No saved decisions yet</div></article>
  </section>

  <nav class="dr-tools" aria-label="More Lineup Beat tools"><span>Keep exploring</span><a href="/nfl/rankings/">Rankings</a><a href="/nfl/projections/">Projections</a><a href="/nfl/who-should-i-draft/">Draft comparison</a><a href="{WIRE_PATH}">The reviewed Wire</a></nav>
</main>
{college_decision_room.SHELL}
<script id="dr-data" type="application/json">{data}</script>
<script>{javascript((first['winner'] or first['player_a'])['id'], (first['runner_up'] or first['player_b'])['id'], updated)}</script>
<script>{college_decision_room.JS}</script>
{END}'''
    return block


def javascript(default_a: str, default_b: str, updated: str) -> str:
    return r'''(()=>{document.getElementById("decision-room").classList.add("dr-enhanced");const D=JSON.parse(document.getElementById("dr-data").textContent),P=Object.fromEntries(D.players.map(p=>[p.id,p])),A=document.getElementById("dr-a"),B=document.getElementById("dr-b"),F=document.getElementById("dr-format"),X=document.getElementById("dr-cross-position"),O=document.getElementById("dr-result"),L={ppr:"PPR",half_ppr:"Half-PPR",non_ppr:"Non-PPR"},FM=["ppr","half_ppr","non_ppr"];
const num=v=>Number(v).toFixed(1),shown=v=>Number(Number(v).toFixed(1)),adp=p=>p.adp==null?"Not available":Number(p.adp).toFixed(1),conf=g=>g===0?"True Toss-Up":g<=2?"Toss-Up":g<12?"Lean":"Clear Edge",fmt=(p,k)=>p.formats[k],winner=(a,b,k)=>{let x=shown(fmt(a,k).projected_points),y=shown(fmt(b,k).projected_points);return x===y?null:(x>y?a:b)};
function portrait(p){return `<div class="dr-person" style="--team:${p.team_color}"><img class="dr-logo" src="${p.team_logo}" alt=""><img class="dr-photo" src="${p.photo||p.team_logo}" alt="${p.name}" onerror="this.src='${p.team_logo}'"><div><small>${p.team} · ${p.position}</small><h3>${p.name}</h3></div></div>`}
function playerCards(a,b,k){return `<div class="dr-player-grid"><article>${portrait(a)}<dl><div><dt>Projected points</dt><dd>${num(fmt(a,k).projected_points)}</dd></div><div><dt>Projection rank</dt><dd>#${fmt(a,k).overall_rank} · ${a.position}${fmt(a,k).position_rank}</dd></div><div><dt>ADP</dt><dd>${adp(a)}</dd></div></dl></article><article>${portrait(b)}<dl><div><dt>Projected points</dt><dd>${num(fmt(b,k).projected_points)}</dd></div><div><dt>Projection rank</dt><dd>#${fmt(b,k).overall_rank} · ${b.position}${fmt(b,k).position_rank}</dd></div><div><dt>ADP</dt><dd>${adp(b)}</dd></div></dl></article></div>`}
function draw(){let a=P[A.value],b=P[B.value],k=F.value;if(!a||!b||a.id===b.id){O.innerHTML='<p class="dr-error">Choose two different players.</p>';return}let w=winner(a,b,k),gap=Math.abs(shown(fmt(a,k).projected_points)-shown(fmt(b,k).projected_points));if(!w){let edges=FM.filter(x=>x!==k&&winner(a,b,x)).map(x=>L[x]);O.innerHTML=`<section class="dr-verdict"><div><small>True Toss-Up · ${L[k]}</small><h2>No clear edge</h2><p>Both players display at ${num(fmt(a,k).projected_points)} full-season ${L[k]} points. Lineup Beat does not recommend either player when the displayed projections are equal.</p></div><div class="dr-adv"><b>0.0</b><span>displayed point gap</span></div></section>${playerCards(a,b,k)}<section class="dr-boundary"><div class="dr-boundary-title"><small>Signature analysis</small><h2>What changes the pick?</h2></div><div class="dr-boundary-grid"><article><b>+0.1</b><span>Either player needs one tenth of a displayed season point to move ahead.</span></article><article><b>${edges.length?edges.join(' / '):'No edge'}</b><span>${edges.length?'These scoring formats produce a leader.':'Every available scoring format remains tied.'}</span></article></div></section><p class="dr-stamp">Projection data updated ''' + esc(updated) + r''' · Page build: current development deployment · 2026 full season · ${L[k]}</p>`;return}let r=w.id===a.id?b:a,wf=fmt(w,k),rf=fmt(r,k);gap=+(shown(wf.projected_points)-shown(rf.projected_points)).toFixed(1);let flip=+(gap+.1).toFixed(1),flips=FM.filter(x=>x!==k&&(!winner(a,b,x)||winner(a,b,x).id!==w.id)).map(x=>L[x]),market=w.adp!=null&&r.adp!=null?(w.adp>r.adp?'Market ADP prefers '+r.name+'.':'Market ADP agrees with the pick.'):'ADP comparison is unavailable for this pair.';
O.innerHTML=`<section class="dr-verdict"><div><small>${conf(gap)} · ${L[k]}</small><h2>Recommend ${w.name}</h2><p>${w.name} projects for ${num(wf.projected_points)} full-season ${L[k]} points, ${num(gap)} more than ${r.name}. The recommendation follows the higher displayed validated season projection.</p></div><div class="dr-adv"><b>+${num(gap)}</b><span>season-point advantage</span></div></section>${playerCards(a,b,k)}<section class="dr-boundary"><div class="dr-boundary-title"><small>Signature analysis</small><h2>What changes the pick?</h2></div><div class="dr-boundary-grid"><article><b>+${num(flip)}</b><span>${r.name} needs this many additional projected season points to move ahead.</span></article><article><b>−${num(flip)}</b><span>${w.name} could lose this many projected season points before the recommendation flips.</span></article><article><b>${flips.length?flips.join(' / '):'No flip'}</b><span>${flips.length?'These available scoring formats remove or reverse the recommendation.':'The recommendation holds in every available scoring format.'}</span></article><article><b>${market.startsWith('Market ADP prefers')?'Disagreement':market.startsWith('Market')?'Agreement':'No ADP'}</b><span>${market}</span></article></div></section><p class="dr-stamp">Projection data updated ''' + esc(updated) + r''' · Page build: current development deployment · 2026 full season · ${L[k]}</p>`}
function candidates(which){let other=which===A?B:A,base=D.players.filter(p=>p.id!==other.value);if(which===B&&!X.checked&&P[A.value])base=base.filter(p=>p.position===P[A.value].position);return base}
function setup(select,input,list){let active=-1;function close(){list.hidden=true;input.setAttribute('aria-expanded','false');active=-1}function show(){let q=input.value.toLowerCase(),rows=candidates(select).filter(p=>!q||(`${p.name} ${p.team} ${p.position}`).toLowerCase().includes(q)).slice(0,40);list.innerHTML=rows.length?rows.map((p,i)=>`<li role="option" data-id="${p.id}" id="${list.id}-${i}">${p.name}<small>${p.team} · ${p.position}</small></li>`).join(''):'<li class="dr-no-result">No matching players</li>';list.hidden=false;input.setAttribute('aria-expanded','true')}function choose(id){let p=P[id];if(!p)return;select.value=id;input.value=`${p.name} · ${p.team} ${p.position}`;close();select.dispatchEvent(new Event('change'))}input.addEventListener('focus',()=>{input.select();show()});input.addEventListener('input',show);input.addEventListener('keydown',e=>{let rows=[...list.querySelectorAll('[role=option]')];if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();active=Math.max(0,Math.min(rows.length-1,active+(e.key==='ArrowDown'?1:-1)));rows.forEach((x,i)=>x.setAttribute('aria-selected',i===active?'true':'false'));if(rows[active])rows[active].scrollIntoView({block:'nearest'})}else if(e.key==='Enter'&&rows[active]){e.preventDefault();choose(rows[active].dataset.id)}else if(e.key==='Escape')close()});list.addEventListener('mousedown',e=>{let row=e.target.closest('[role=option]');if(row){e.preventDefault();choose(row.dataset.id)}});select.addEventListener('change',()=>{let p=P[select.value];if(p)input.value=`${p.name} · ${p.team} ${p.position}`});document.addEventListener('click',e=>{if(!e.target.closest('.dr-picker'))close()});return{refresh:show}}
A.value="''' + esc(default_a) + r'''";B.value="''' + esc(default_b) + r'''";let PA=setup(A,document.getElementById('dr-a-search'),document.getElementById('dr-a-list')),PB=setup(B,document.getElementById('dr-b-search'),document.getElementById('dr-b-list'));A.dispatchEvent(new Event('change'));B.dispatchEvent(new Event('change'));A.addEventListener('change',()=>{if(!X.checked&&P[A.value]&&(!P[B.value]||P[B.value].position!==P[A.value].position||A.value===B.value)){let next=D.players.find(p=>p.id!==A.value&&p.position===P[A.value].position);if(next){B.value=next.id;B.dispatchEvent(new Event('change'))}}draw()});[B,F].forEach(x=>x.addEventListener('change',draw));X.addEventListener('change',()=>{A.dispatchEvent(new Event('change'));PB.refresh()});document.querySelectorAll('.dr-open').forEach(x=>x.addEventListener('click',()=>{A.value=x.dataset.a;B.value=x.dataset.b;A.dispatchEvent(new Event('change'));B.dispatchEvent(new Event('change'));draw();document.getElementById('dr-compare-title').scrollIntoView({behavior:'smooth'})}));draw()})();'''


CSS = r'''
#decision-room{--dr-bg:#080c0c;--dr-panel:#101615;--dr-line:#29312d;--dr-lime:#c6f53c;--dr-ink:#f3f5ef;--dr-muted:#aab2ac;color:var(--dr-ink);background:var(--dr-bg)}
.dr-sports{position:relative;z-index:5;display:flex;justify-content:center;gap:.35rem;padding:.7rem;background:#050807;border-bottom:1px solid #29312d}.dr-sports a{min-width:110px;padding:.7rem 1rem;text-align:center;color:#d8ddd8;border:1px solid #46504b;font:800 .75rem var(--agate);letter-spacing:.1em;text-transform:uppercase}.dr-sports a[aria-pressed=true]{background:#c6f53c;color:#101410;border-color:#c6f53c}.cdr{--dr-bg:#09100f;--dr-panel:#111b19;--dr-line:#29413b;--dr-lime:#6de0bd;--dr-ink:#f3f5ef;--dr-muted:#aabbb6;color:var(--dr-ink);background:var(--dr-bg)}.cdr-filters{display:grid;grid-template-columns:1fr 2fr;gap:1rem;margin:1.25rem 0}.cdr input[type=search]{display:block;width:100%;box-sizing:border-box;margin-top:.45rem;padding:.85rem;background:#0b100f;color:var(--dr-ink);border:1px solid #46504b;font:600 1rem var(--text)}.cdr-crest{position:absolute;right:1rem;top:1rem;display:grid;place-items:center;width:72px;height:72px;border:2px solid var(--dr-lime);border-radius:50%;color:var(--dr-lime);font:800 1.1rem var(--agate);opacity:.75}.cdr .dr-person{border-color:var(--dr-lime)}
.dr-shell{font-family:var(--text);padding-bottom:5rem}.dr-hero{padding:clamp(3.5rem,7vw,7rem) max(1rem,calc((100% - 1180px)/2));background:radial-gradient(circle at 82% 8%,rgba(198,245,60,.13),transparent 31%),linear-gradient(145deg,#111817,#080b0b);border-bottom:1px solid var(--dr-line)}
.dr-kicker,.dr-mode,.dr-section small,.dr-compare small,.dr-empty small{font:800 .72rem/1.2 var(--agate);letter-spacing:.13em;text-transform:uppercase}.dr-kicker{color:var(--dr-lime)}.dr-mode{display:inline-block;margin:.8rem 0 1.2rem;padding:.55rem .75rem;border:1px solid #52641f;background:#17200d}.dr-hero>h1{max-width:850px;margin:.4rem 0 1rem;font:700 clamp(3rem,7vw,6.4rem)/.9 var(--display);letter-spacing:-.04em}.dr-lede{max-width:720px;font-size:clamp(1.05rem,2vw,1.3rem);color:#d7ddd7}.dr-week-note{max-width:760px;color:var(--dr-muted);border-left:3px solid var(--dr-lime);padding-left:1rem}.dr-beat-link{display:inline-block;margin-top:.4rem;color:var(--dr-ink);font:800 .72rem var(--agate);letter-spacing:.08em;text-transform:uppercase}
.dr-compare{margin-top:2.4rem;border:1px solid var(--dr-line);border-top:4px solid var(--dr-lime);background:rgba(8,12,12,.92);padding:clamp(1rem,3vw,2rem)}.dr-compare-head,.dr-section-head{display:flex;justify-content:space-between;gap:2rem;align-items:end}.dr-compare h2,.dr-section h2,.dr-empty h2{font:700 clamp(1.8rem,4vw,3rem)/1 var(--display);margin:.25rem 0}.dr-compare label{font:700 .72rem var(--agate);letter-spacing:.08em;text-transform:uppercase;color:var(--dr-muted)}.dr-compare select{display:block;width:100%;margin-top:.45rem;padding:.85rem;background:#0b100f;color:var(--dr-ink);border:1px solid #46504b;font:600 1rem var(--text)}.dr-selectors{display:grid;grid-template-columns:1fr auto 1fr;gap:1rem;align-items:end;margin:1.4rem 0}.dr-selectors>b{color:var(--dr-lime);padding-bottom:1rem}
.dr-picker{position:relative}.dr-picker input[type=search],.dr-picker ul{display:none}.dr-enhanced .dr-picker input[type=search]{display:block;width:100%;margin-top:.45rem;padding:.85rem;background:#0b100f;color:var(--dr-ink);border:1px solid #46504b;font:600 1rem var(--text)}.dr-picker input:focus{outline:3px solid var(--dr-lime);outline-offset:2px}.dr-enhanced .dr-picker ul:not([hidden]){display:block;position:absolute;z-index:20;left:0;right:0;max-height:280px;overflow:auto;margin:2px 0 0;padding:0;list-style:none;background:#101615;border:1px solid #67716c;box-shadow:0 12px 30px #000}.dr-picker li{display:flex;justify-content:space-between;gap:1rem;padding:.75rem;cursor:pointer;text-transform:none;letter-spacing:0;color:var(--dr-ink)}.dr-picker li[aria-selected=true],.dr-picker li:hover{background:#26331d}.dr-picker li small{color:var(--dr-muted)}.dr-enhanced .dr-native{position:absolute!important;width:1px!important;height:1px!important;overflow:hidden!important;clip:rect(0 0 0 0)!important;white-space:nowrap!important}.dr-cross{display:flex!important;align-items:center;gap:.55rem;margin:-.5rem 0 1.25rem}.dr-cross input{width:1.15rem;height:1.15rem;accent-color:var(--dr-lime)}
.dr-verdict{display:flex;justify-content:space-between;gap:2rem;align-items:center;padding:1.3rem;border:1px solid #52641f;background:#131b0e}.dr-verdict small{color:var(--dr-lime)}.dr-verdict h2{font-size:clamp(2rem,5vw,4rem)}.dr-verdict p{max-width:720px;margin:.5rem 0;color:#d8ddd8}.dr-adv{text-align:center;min-width:150px}.dr-adv b{display:block;font:700 3rem var(--display);color:var(--dr-lime)}.dr-adv span{font:700 .67rem var(--agate);text-transform:uppercase;color:var(--dr-muted)}
.dr-player-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:1rem}.dr-player-grid>article{border:1px solid var(--dr-line);background:var(--dr-panel);padding:1rem}.dr-person{height:130px;position:relative;display:flex;align-items:end;overflow:hidden;border-bottom:3px solid var(--team)}.dr-person>div{position:relative;z-index:2;padding:.8rem}.dr-person h3{font:700 clamp(1.5rem,3vw,2.4rem)/1 var(--display);margin:.2rem 0}.dr-photo{position:absolute;right:0;bottom:0;height:125px;max-width:48%;object-fit:contain;z-index:1}.dr-logo{position:absolute;right:32%;top:10px;width:100px;opacity:.1}.dr-player-grid dl{display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem;margin:1rem 0 0}.dr-player-grid dl div{background:#171d1b;padding:.7rem}.dr-player-grid dt{font:700 .64rem var(--agate);color:var(--dr-muted);text-transform:uppercase}.dr-player-grid dd{margin:.3rem 0 0;font:700 1.2rem var(--display)}
.dr-boundary{margin-top:1rem;padding:clamp(1rem,3vw,2rem);background:#e9efe6;color:#101410}.dr-boundary-title small{color:#52630f}.dr-boundary-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:#aeb8aa;border:1px solid #aeb8aa;margin-top:1rem}.dr-boundary-grid article{background:#f7faf5;padding:1rem}.dr-boundary-grid b{display:block;font:700 1.7rem var(--display)}.dr-boundary-grid span{display:block;margin-top:.5rem;font-size:.9rem}.dr-stamp{font:700 .68rem var(--agate);color:var(--dr-muted);text-transform:uppercase}.dr-error{padding:1rem;background:#301515;color:#ffd7d7}
.dr-section{max-width:1180px;margin:0 auto;padding:clamp(3.5rem,7vw,6rem) 1rem;border-bottom:1px solid var(--dr-line)}.dr-section-head>p{max-width:500px;color:var(--dr-muted)}.dr-card-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:1.5rem}.dr-mini,.dr-signal,.dr-mover,.dr-empty{border:1px solid var(--dr-line);background:var(--dr-panel);padding:1.1rem}.dr-mini-pair{display:flex;flex-direction:column;font:700 1.35rem var(--display)}.dr-mini-pair i{font:700 .65rem var(--agate);color:var(--dr-lime);text-transform:uppercase}.dr-mini p,.dr-signal p,.dr-mover p{color:var(--dr-muted);font-size:.9rem}.dr-market{min-height:1.2em}.dr-open{border:0;background:var(--dr-lime);color:#101410;padding:.65rem 1rem;font:800 .72rem var(--agate);text-transform:uppercase;cursor:pointer}.dr-convictions{max-width:none;padding-left:max(1rem,calc((100% - 1180px)/2));padding-right:max(1rem,calc((100% - 1180px)/2));background:#0d1211}.dr-signal-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:1.5rem}.dr-signal{display:grid;grid-template-columns:70px 1fr;gap:1rem}.dr-signal img{width:70px;height:70px;object-fit:contain}.dr-signal h3,.dr-mover h3{font:700 1.5rem var(--display);margin:.2rem 0}.dr-mover-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:1.5rem}.dr-mover>div{display:flex;gap:.5rem;flex-wrap:wrap}.dr-mover>div span{background:#1a211e;padding:.4rem;font-size:.78rem}.dr-mover b{color:var(--dr-lime)}
.dr-fades-head{margin-top:3rem}
.dr-future-grid{max-width:1180px;margin:0 auto;padding:clamp(3.5rem,7vw,6rem) 1rem;display:grid;grid-template-columns:1fr 1fr;gap:1rem}.dr-empty{min-height:220px}.dr-empty p{color:var(--dr-muted);max-width:520px}.dr-empty button,.dr-empty-line{margin-top:1.2rem;padding:.8rem;border:1px dashed #56605b;background:transparent;color:var(--dr-muted)}.dr-tools{max-width:1180px;margin:auto;padding:1.2rem 1rem;border-top:1px solid var(--dr-line);display:flex;gap:1.2rem;flex-wrap:wrap}.dr-tools span{color:var(--dr-muted)}.dr-tools a{color:var(--dr-ink)}
.dr-news .lb-wire-card{display:block;max-width:760px;margin:1.5rem 0 0}.dr-news .lb-wire-feed{min-height:180px}
@media(max-width:780px){.dr-hero{padding-top:4rem}.dr-compare-head,.dr-section-head,.dr-verdict{align-items:stretch;flex-direction:column}.dr-selectors,.cdr-filters{grid-template-columns:1fr}.dr-selectors>b{text-align:center;padding:0}.dr-player-grid,.dr-future-grid{grid-template-columns:1fr}.dr-boundary-grid{grid-template-columns:1fr 1fr}.dr-card-grid,.dr-signal-grid,.dr-mover-grid{grid-template-columns:1fr}.dr-player-grid dl{grid-template-columns:1fr 1fr}.dr-adv{text-align:left}.dr-photo{max-width:44%}}
@media(max-width:430px){.dr-boundary-grid,.dr-player-grid dl{grid-template-columns:1fr}.dr-hero>h1{font-size:3.35rem}.dr-person{height:115px}.dr-photo{height:110px}}
'''

WIRE_PAGE_CSS = r'''
body{margin:0;background:#f4f5f0;color:#101410;font-family:Arial,sans-serif}.rw-head{padding:1.25rem max(1rem,calc((100% - 1100px)/2));background:#0b100f;color:#fff;border-bottom:4px solid #c6f53c}.rw-head a{color:#c6f53c;font-weight:800}.rw-head h1{font-size:clamp(2.2rem,7vw,4.8rem);margin:.8rem 0 .35rem}.rw-head p{max-width:700px;color:#cbd2cc}.rw-main{max-width:1100px;margin:auto;padding:1rem}.rw-back{display:inline-block;margin-bottom:1rem}@media(max-width:600px){.rw-head{padding-top:4rem}.rw-main{padding:.65rem}}
'''


def split_wire(page: str, homepage_count: int = 4) -> tuple[str, str]:
    """Move the complete reviewed Wire to a development-only destination."""
    if WIRE_START not in page or WIRE_END not in page:
        raise SystemExit("reviewed Wire replacement is missing")
    start = page.index(WIRE_START)
    end = page.index(WIRE_END, start) + len(WIRE_END)
    complete = page[start:end]
    cards = list(re.finditer(r'<article class="tile wire".*?</article>', complete, re.S))
    if len(cards) < homepage_count:
        raise SystemExit("reviewed Wire has too few cards for homepage context")
    reduced = complete
    for match in reversed(cards[homepage_count:]):
        reduced = reduced[:match.start()] + reduced[match.end():]
    reduced = re.sub(r'\b\d+ reviewed reports\b',
                     f'{homepage_count} newest reviewed updates', reduced, count=1)
    link = (f'<p class="dr-wire-more"><a href="{WIRE_PATH}">'
            'View all reviewed Wire updates →</a></p>')
    reduced = reduced.replace(WIRE_END, link + "\n" + WIRE_END, 1)
    return page[:start] + reduced + page[end:], complete


def write_wire_page(homepage: Path, complete: str, source_page: str) -> Path:
    style_match = re.search(r'<style id="wire-css">(.*?)</style>', source_page, re.S)
    if style_match is None:
        raise SystemExit("reviewed Wire styles are missing")
    target = homepage.parent / "decision-room" / "reviewed-wire" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reviewed Fantasy Football Wire | Lineup Beat</title>
<meta name="description" content="The complete filterable collection of human-reviewed Lineup Beat fantasy football updates.">
<meta name="robots" content="noindex,nofollow"><style>{WIRE_PAGE_CSS}</style>
<style id="wire-css">{style_match.group(1)}</style></head><body>
<header class="rw-head"><a href="/">← Decision Room</a><h1>Reviewed Wire</h1><p>All human-approved fantasy-relevant updates from trusted sources. Filter by team or position.</p></header>
<main class="rw-main"><a class="rw-back" href="/">Back to the Decision Room</a>{complete}</main></body></html>'''
    target.write_text(document)
    return target


def update_metadata(page: str) -> str:
    title = "Fantasy Football Decision Room | Lineup Beat"
    description = ("Compare fantasy football players across scoring formats and see "
                   "the season-projection decision boundaries that would change each pick.")
    page = re.sub(r'<title>.*?</title>', f'<title>{title}</title>', page, count=1, flags=re.S)
    if re.search(r'<meta\s+name="description"[^>]*>', page, re.I):
        page = re.sub(r'<meta\s+name="description"[^>]*>',
                      f'<meta name="description" content="{description}">',
                      page, count=1, flags=re.I)
    else:
        page = page.replace("</head>", f'<meta name="description" content="{description}">\n</head>', 1)
    # The compact header timestamp comes from DATA.generated_at (the news
    # feed), not projection freshness or the deployment build.
    page = page.replace('"Updated " + ago(DATA.generated_at)',
                        '"News updated " + ago(DATA.generated_at)')
    return page


def inject(path: Path) -> None:
    payload = decision_data.load_season(2026)
    college_payload = college_decision_data.load_weekly()
    page = path.read_text()
    if "<body" not in page or "</head>" not in page:
        raise SystemExit("refusing to modify malformed homepage")
    page, complete_wire = split_wire(page)
    wire_page = write_wire_page(path, complete_wire, page)
    college_path = path.parent / "data" / "decision-room-college.json"
    college_path.parent.mkdir(parents=True, exist_ok=True)
    college_path.write_text(json.dumps(college_payload, separators=(",", ":")) + "\n")
    block = render(payload)
    if START in page and END in page:
        page = page.split(START, 1)[0] + block + page.split(END, 1)[1]
    else:
        hero = re.search(r'<section class="lb-hero" id="hero">.*?</section>\s*(?=<section class="hero medhero")', page, re.S)
        if hero is None:
            raise SystemExit("development homepage hero boundary not found")
        page = page[:hero.start()] + block + "\n" + page[hero.end():]
    page = update_metadata(page)
    style = f'<style id="decision-room-css">{CSS}</style>'
    if 'id="decision-room-css"' in page:
        page = re.sub(r'<style id="decision-room-css">.*?</style>', style, page, count=1, flags=re.S)
    else:
        page = page.replace("</head>", style + "\n</head>", 1)
    path.write_text(page)
    print(f"built 2026 season Decision Room with {len(payload['players'])} players in {path}")
    print(f"built complete reviewed Wire in {wire_page}")
    print(f"built isolated College Decision Room payload with {len(college_payload['players'])} players in {college_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--homepage", type=Path, default=Path("site/index.html"))
    args = parser.parse_args()
    inject(args.homepage)


if __name__ == "__main__":
    main()
