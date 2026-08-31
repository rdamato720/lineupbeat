#!/usr/bin/env python3
"""Inject the development-only 2026 Preseason Decision Room homepage."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

import build_comparison_tool
import decision_data
from decision_engine import (FORMAT_LABELS, DecisionContext, closest_calls,
                             convictions, scoring_movers)

START = "<!-- LB DECISION ROOM START -->"
END = "<!-- LB DECISION ROOM END -->"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def player_label(player: dict) -> str:
    return f"{player['name']} · {player['team']} {player['position']}"


def call_card(result: dict) -> str:
    w, r = result["winner"], result["runner_up"]
    wf, rf = result["winner_format"], result["runner_up_format"]
    return f'''<article class="dr-mini">
      <div class="dr-mini-pair"><span>{esc(w['name'])}</span><i>over</i><span>{esc(r['name'])}</span></div>
      <p>{esc(w['position'])}{wf['position_rank']} vs {esc(r['position'])}{rf['position_rank']}
        · {result['gap']:.1f}-point edge · {esc(result['confidence'])}</p>
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
    signals = convictions(players, "half_ppr")
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
<main id="decision-room" class="dr-shell" data-mode="season" data-season="2026">
  <section class="dr-hero">
    <div class="dr-kicker">2026 Preseason Decision Room</div>
    <div class="dr-mode">Draft Mode — based on full-season projections</div>
    <h1>Make the decision—not just the projection.</h1>
    <p class="dr-lede">Compare outcomes, see the stronger full-season projection, and understand exactly what would change the pick.</p>
    <p class="dr-week-note">Weekly lineup decisions will become available after validated weekly projections are added. Season values are never presented as weekly forecasts.</p>
    <section class="dr-compare" aria-labelledby="dr-compare-title">
      <div class="dr-compare-head"><div><small>Decision 01</small><h2 id="dr-compare-title">Player vs. player</h2></div>
        <label>Scoring format<select id="dr-format"><option value="ppr">PPR</option><option value="half_ppr" selected>Half-PPR</option><option value="non_ppr">Non-PPR</option></select></label></div>
      <div class="dr-selectors"><label>Player one<select id="dr-a">{options}</select></label><b>VS</b><label>Player two<select id="dr-b">{options}</select></label></div>
      <div id="dr-result" aria-live="polite"></div>
    </section>
  </section>

  <section class="dr-section" id="closest"><div class="dr-section-head"><div><small>Decision pressure</small><h2>Closest Calls</h2></div><p>Same-position preseason decisions separated by the fewest validated half-PPR season points.</p></div>
    <div class="dr-card-grid">{''.join(call_card(c) for c in calls)}</div></section>

  <section class="dr-section dr-convictions"><div class="dr-section-head"><div><small>Market contrast</small><h2>Lineup Beat Convictions</h2></div><p>The largest meaningful gaps between projection rank and validated market ADP—not labels, just measurable disagreement.</p></div>
    <div class="dr-signal-grid">{''.join(conviction_card(c) for c in signals)}</div></section>

  <section class="dr-section"><div class="dr-section-head"><div><small>Format sensitivity</small><h2>Scoring-format movers</h2></div><p>Players whose position rank changes most when receptions change value.</p></div>
    <div class="dr-mover-grid">{''.join(mover_card(m) for m in movers)}</div></section>

  <section class="dr-future-grid">
    <article class="dr-empty"><small>Coming next</small><h2>Decision Inbox</h2><p>Connect your league to see the decisions that matter on your roster.</p><button disabled>League sync not yet available</button></article>
    <article class="dr-empty"><small>Accountability layer</small><h2>Decision Record</h2><p>No decisions have been recorded. Future snapshots will preserve the recommendation, inputs, timestamp, and eventual outcome instead of silently rewriting the call.</p><div class="dr-empty-line">No saved decisions yet</div></article>
  </section>

  <nav class="dr-tools" aria-label="More Lineup Beat tools"><span>Keep exploring</span><a href="/nfl/rankings/">Rankings</a><a href="/nfl/projections/">Projections</a><a href="/nfl/who-should-i-draft/">Draft comparison</a><a href="#wire">The Beat</a></nav>
</main>
<script id="dr-data" type="application/json">{data}</script>
<script>{javascript(first['winner']['id'], first['runner_up']['id'], updated)}</script>
{END}'''
    return block


def javascript(default_a: str, default_b: str, updated: str) -> str:
    return r'''(()=>{const D=JSON.parse(document.getElementById("dr-data").textContent),P=Object.fromEntries(D.players.map(p=>[p.id,p])),A=document.getElementById("dr-a"),B=document.getElementById("dr-b"),F=document.getElementById("dr-format"),O=document.getElementById("dr-result"),L={ppr:"PPR",half_ppr:"Half-PPR",non_ppr:"Non-PPR"},FM=["ppr","half_ppr","non_ppr"];
const num=v=>Number(v).toFixed(1),adp=p=>p.adp==null?"Not available":Number(p.adp).toFixed(1),conf=g=>g<=2?"Toss-Up":g<12?"Lean":"Clear Edge",fmt=(p,k)=>p.formats[k],winner=(a,b,k)=>{let x=fmt(a,k),y=fmt(b,k);return x.projected_points===y.projected_points?(x.overall_rank<=y.overall_rank?a:b):(x.projected_points>y.projected_points?a:b)};
function portrait(p){return `<div class="dr-person" style="--team:${p.team_color}"><img class="dr-logo" src="${p.team_logo}" alt=""><img class="dr-photo" src="${p.photo||p.team_logo}" alt="${p.name}" onerror="this.src='${p.team_logo}'"><div><small>${p.team} · ${p.position}</small><h3>${p.name}</h3></div></div>`}
function draw(){let a=P[A.value],b=P[B.value],k=F.value;if(!a||!b||a.id===b.id){O.innerHTML='<p class="dr-error">Choose two different players.</p>';return}let w=winner(a,b,k),r=w.id===a.id?b:a,wf=fmt(w,k),rf=fmt(r,k),gap=+(wf.projected_points-rf.projected_points).toFixed(1),flip=+(gap+.1).toFixed(1),flips=FM.filter(x=>x!==k&&winner(a,b,x).id!==w.id).map(x=>L[x]),market=w.adp!=null&&r.adp!=null?(w.adp>r.adp?'Market ADP prefers '+r.name+'.':'Market ADP agrees with the pick.'):'ADP comparison is unavailable for this pair.';
O.innerHTML=`<section class="dr-verdict"><div><small>${conf(gap)} · ${L[k]}</small><h2>Recommend ${w.name}</h2><p>${w.name} projects for ${num(wf.projected_points)} full-season ${L[k]} points, ${num(gap)} more than ${r.name}. The recommendation follows the higher validated season projection.</p></div><div class="dr-adv"><b>+${num(gap)}</b><span>season-point advantage</span></div></section><div class="dr-player-grid"><article>${portrait(a)}<dl><div><dt>Projected points</dt><dd>${num(fmt(a,k).projected_points)}</dd></div><div><dt>Projection rank</dt><dd>#${fmt(a,k).overall_rank} · ${a.position}${fmt(a,k).position_rank}</dd></div><div><dt>ADP</dt><dd>${adp(a)}</dd></div></dl></article><article>${portrait(b)}<dl><div><dt>Projected points</dt><dd>${num(fmt(b,k).projected_points)}</dd></div><div><dt>Projection rank</dt><dd>#${fmt(b,k).overall_rank} · ${b.position}${fmt(b,k).position_rank}</dd></div><div><dt>ADP</dt><dd>${adp(b)}</dd></div></dl></article></div><section class="dr-boundary"><div class="dr-boundary-title"><small>Signature analysis</small><h2>What changes the pick?</h2></div><div class="dr-boundary-grid"><article><b>+${num(flip)}</b><span>${r.name} needs this many additional projected season points to move ahead.</span></article><article><b>−${num(flip)}</b><span>${w.name} could lose this many projected season points before the recommendation flips.</span></article><article><b>${flips.length?flips.join(' / '):'No flip'}</b><span>${flips.length?'These available scoring formats reverse the recommendation.':'The recommendation holds in every available scoring format.'}</span></article><article><b>${market.startsWith('Market ADP prefers')?'Disagreement':market.startsWith('Market')?'Agreement':'No ADP'}</b><span>${market}</span></article></div></section><p class="dr-stamp">Projection data updated ''' + esc(updated) + r''' · 2026 full season · ${L[k]}</p>`}
A.value="''' + esc(default_a) + r'''";B.value="''' + esc(default_b) + r'''";[A,B,F].forEach(x=>x.addEventListener('change',draw));document.querySelectorAll('.dr-open').forEach(x=>x.addEventListener('click',()=>{A.value=x.dataset.a;B.value=x.dataset.b;draw();document.getElementById('dr-compare-title').scrollIntoView({behavior:'smooth'})}));draw()})();'''


CSS = r'''
#decision-room{--dr-bg:#080c0c;--dr-panel:#101615;--dr-line:#29312d;--dr-lime:#c6f53c;--dr-ink:#f3f5ef;--dr-muted:#aab2ac;color:var(--dr-ink);background:var(--dr-bg)}
.dr-shell{font-family:var(--text);padding-bottom:5rem}.dr-hero{padding:clamp(3.5rem,7vw,7rem) max(1rem,calc((100% - 1180px)/2));background:radial-gradient(circle at 82% 8%,rgba(198,245,60,.13),transparent 31%),linear-gradient(145deg,#111817,#080b0b);border-bottom:1px solid var(--dr-line)}
.dr-kicker,.dr-mode,.dr-section small,.dr-compare small,.dr-empty small{font:800 .72rem/1.2 var(--agate);letter-spacing:.13em;text-transform:uppercase}.dr-kicker{color:var(--dr-lime)}.dr-mode{display:inline-block;margin:.8rem 0 1.2rem;padding:.55rem .75rem;border:1px solid #52641f;background:#17200d}.dr-hero>h1{max-width:850px;margin:.4rem 0 1rem;font:700 clamp(3rem,7vw,6.4rem)/.9 var(--display);letter-spacing:-.04em}.dr-lede{max-width:720px;font-size:clamp(1.05rem,2vw,1.3rem);color:#d7ddd7}.dr-week-note{max-width:760px;color:var(--dr-muted);border-left:3px solid var(--dr-lime);padding-left:1rem}
.dr-compare{margin-top:2.4rem;border:1px solid var(--dr-line);border-top:4px solid var(--dr-lime);background:rgba(8,12,12,.92);padding:clamp(1rem,3vw,2rem)}.dr-compare-head,.dr-section-head{display:flex;justify-content:space-between;gap:2rem;align-items:end}.dr-compare h2,.dr-section h2,.dr-empty h2{font:700 clamp(1.8rem,4vw,3rem)/1 var(--display);margin:.25rem 0}.dr-compare label{font:700 .72rem var(--agate);letter-spacing:.08em;text-transform:uppercase;color:var(--dr-muted)}.dr-compare select{display:block;width:100%;margin-top:.45rem;padding:.85rem;background:#0b100f;color:var(--dr-ink);border:1px solid #46504b;font:600 1rem var(--text)}.dr-selectors{display:grid;grid-template-columns:1fr auto 1fr;gap:1rem;align-items:end;margin:1.4rem 0}.dr-selectors>b{color:var(--dr-lime);padding-bottom:1rem}
.dr-verdict{display:flex;justify-content:space-between;gap:2rem;align-items:center;padding:1.3rem;border:1px solid #52641f;background:#131b0e}.dr-verdict small{color:var(--dr-lime)}.dr-verdict h2{font-size:clamp(2rem,5vw,4rem)}.dr-verdict p{max-width:720px;margin:.5rem 0;color:#d8ddd8}.dr-adv{text-align:center;min-width:150px}.dr-adv b{display:block;font:700 3rem var(--display);color:var(--dr-lime)}.dr-adv span{font:700 .67rem var(--agate);text-transform:uppercase;color:var(--dr-muted)}
.dr-player-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:1rem}.dr-player-grid>article{border:1px solid var(--dr-line);background:var(--dr-panel);padding:1rem}.dr-person{height:130px;position:relative;display:flex;align-items:end;overflow:hidden;border-bottom:3px solid var(--team)}.dr-person>div{position:relative;z-index:2;padding:.8rem}.dr-person h3{font:700 clamp(1.5rem,3vw,2.4rem)/1 var(--display);margin:.2rem 0}.dr-photo{position:absolute;right:0;bottom:0;height:125px;max-width:48%;object-fit:contain;z-index:1}.dr-logo{position:absolute;right:32%;top:10px;width:100px;opacity:.1}.dr-player-grid dl{display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem;margin:1rem 0 0}.dr-player-grid dl div{background:#171d1b;padding:.7rem}.dr-player-grid dt{font:700 .64rem var(--agate);color:var(--dr-muted);text-transform:uppercase}.dr-player-grid dd{margin:.3rem 0 0;font:700 1.2rem var(--display)}
.dr-boundary{margin-top:1rem;padding:clamp(1rem,3vw,2rem);background:#e9efe6;color:#101410}.dr-boundary-title small{color:#52630f}.dr-boundary-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:#aeb8aa;border:1px solid #aeb8aa;margin-top:1rem}.dr-boundary-grid article{background:#f7faf5;padding:1rem}.dr-boundary-grid b{display:block;font:700 1.7rem var(--display)}.dr-boundary-grid span{display:block;margin-top:.5rem;font-size:.9rem}.dr-stamp{font:700 .68rem var(--agate);color:var(--dr-muted);text-transform:uppercase}.dr-error{padding:1rem;background:#301515;color:#ffd7d7}
.dr-section{max-width:1180px;margin:0 auto;padding:clamp(3.5rem,7vw,6rem) 1rem;border-bottom:1px solid var(--dr-line)}.dr-section-head>p{max-width:500px;color:var(--dr-muted)}.dr-card-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:1.5rem}.dr-mini,.dr-signal,.dr-mover,.dr-empty{border:1px solid var(--dr-line);background:var(--dr-panel);padding:1.1rem}.dr-mini-pair{display:flex;flex-direction:column;font:700 1.35rem var(--display)}.dr-mini-pair i{font:700 .65rem var(--agate);color:var(--dr-lime);text-transform:uppercase}.dr-mini p,.dr-signal p,.dr-mover p{color:var(--dr-muted);font-size:.9rem}.dr-market{min-height:1.2em}.dr-open{border:0;background:var(--dr-lime);color:#101410;padding:.65rem 1rem;font:800 .72rem var(--agate);text-transform:uppercase;cursor:pointer}.dr-convictions{max-width:none;padding-left:max(1rem,calc((100% - 1180px)/2));padding-right:max(1rem,calc((100% - 1180px)/2));background:#0d1211}.dr-signal-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:1.5rem}.dr-signal{display:grid;grid-template-columns:70px 1fr;gap:1rem}.dr-signal img{width:70px;height:70px;object-fit:contain}.dr-signal h3,.dr-mover h3{font:700 1.5rem var(--display);margin:.2rem 0}.dr-mover-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:1.5rem}.dr-mover>div{display:flex;gap:.5rem;flex-wrap:wrap}.dr-mover>div span{background:#1a211e;padding:.4rem;font-size:.78rem}.dr-mover b{color:var(--dr-lime)}
.dr-future-grid{max-width:1180px;margin:0 auto;padding:clamp(3.5rem,7vw,6rem) 1rem;display:grid;grid-template-columns:1fr 1fr;gap:1rem}.dr-empty{min-height:220px}.dr-empty p{color:var(--dr-muted);max-width:520px}.dr-empty button,.dr-empty-line{margin-top:1.2rem;padding:.8rem;border:1px dashed #56605b;background:transparent;color:var(--dr-muted)}.dr-tools{max-width:1180px;margin:auto;padding:1.2rem 1rem;border-top:1px solid var(--dr-line);display:flex;gap:1.2rem;flex-wrap:wrap}.dr-tools span{color:var(--dr-muted)}.dr-tools a{color:var(--dr-ink)}
@media(max-width:780px){.dr-hero{padding-top:4rem}.dr-compare-head,.dr-section-head,.dr-verdict{align-items:stretch;flex-direction:column}.dr-selectors{grid-template-columns:1fr}.dr-selectors>b{text-align:center;padding:0}.dr-player-grid,.dr-future-grid{grid-template-columns:1fr}.dr-boundary-grid{grid-template-columns:1fr 1fr}.dr-card-grid,.dr-signal-grid,.dr-mover-grid{grid-template-columns:1fr}.dr-player-grid dl{grid-template-columns:1fr 1fr}.dr-adv{text-align:left}.dr-photo{max-width:44%}}
@media(max-width:430px){.dr-boundary-grid,.dr-player-grid dl{grid-template-columns:1fr}.dr-hero>h1{font-size:3.35rem}.dr-person{height:115px}.dr-photo{height:110px}}
'''


def inject(path: Path) -> None:
    payload = decision_data.load_season(2026)
    page = path.read_text()
    if "<body" not in page or "</head>" not in page:
        raise SystemExit("refusing to modify malformed homepage")
    block = render(payload)
    if START in page and END in page:
        page = page.split(START, 1)[0] + block + page.split(END, 1)[1]
    else:
        hero = re.search(r'<section class="lb-hero" id="hero">.*?</section>\s*(?=<section class="hero medhero")', page, re.S)
        if hero is None:
            raise SystemExit("development homepage hero boundary not found")
        page = page[:hero.start()] + block + "\n" + page[hero.end():]
    style = f'<style id="decision-room-css">{CSS}</style>'
    if 'id="decision-room-css"' in page:
        page = re.sub(r'<style id="decision-room-css">.*?</style>', style, page, count=1, flags=re.S)
    else:
        page = page.replace("</head>", style + "\n</head>", 1)
    path.write_text(page)
    print(f"built 2026 season Decision Room with {len(payload['players'])} players in {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--homepage", type=Path, default=Path("site/index.html"))
    args = parser.parse_args()
    inject(args.homepage)


if __name__ == "__main__":
    main()
