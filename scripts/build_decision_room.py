#!/usr/bin/env python3
"""Inject the development-only 2026 Preseason Decision Room homepage."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from urllib.parse import urlencode

import build_comparison_tool
import college_decision_data
import college_decision_room
import decision_data
import seo
from decision_engine import (FORMAT_LABELS, DecisionContext, closest_calls,
                             confidence, scoring_movers,
                             strongest_projection_edges, value_signals)

START = "<!-- LB DECISION ROOM START -->"
END = "<!-- LB DECISION ROOM END -->"
WIRE_START = "<!-- LB WIRE REPLACEMENT START -->"
WIRE_END = "<!-- LB WIRE REPLACEMENT END -->"
WIRE_PATH = "/decision-room/reviewed-wire/"
NFL_ROOM_PATH = "/decision-room/nfl/"
COLLEGE_ROOM_PATH = "/decision-room/college/"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def player_label(player: dict) -> str:
    return f"{player['name']} · {player['team']} {player['position']}"


def call_card(result: dict) -> str:
    if result["no_clear_edge"]:
        w, r = result["player_a"], result["player_b"]
        wf, rf = result["player_a_format"], result["player_b_format"]
        relation, recommendation = "vs.", "No clear edge"
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


def room_url(a: dict | None = None, b: dict | None = None,
             scoring_format: str = "half_ppr") -> str:
    query = {"format": scoring_format}
    if a:
        query["a"] = a["id"]
    if b:
        query["b"] = b["id"]
    return f"{NFL_ROOM_PATH}?{urlencode(query)}"


def featured_decision(players: list[dict], context: DecisionContext | None = None) -> dict:
    """Choose a close, credible same-position call with complete visual identity."""
    candidates = strongest_projection_edges(players, "half_ppr", limit=60,
                                             context=context)
    for result in candidates:
        a = result["winner"] or result["player_a"]
        b = result["runner_up"] or result["player_b"]
        af = result["winner_format"] if result["winner"] else result["player_a_format"]
        bf = result["runner_up_format"] if result["runner_up"] else result["player_b_format"]
        if (result["confidence"] in {"Lean", "Edge", "Strong Edge"}
                and a.get("photo") and b.get("photo")
                and a.get("team_logo") and b.get("team_logo")
                and abs(af["position_rank"] - bf["position_rank"]) <= 2):
            return result
    raise ValueError("no eligible featured projection edge with complete player art")


def board_call(result: dict) -> str:
    a = result["winner"] or result["player_a"]
    b = result["runner_up"] or result["player_b"]
    recommendation = f"{a['name']} over {b['name']}" if result["winner"] else "No clear edge"
    return f'''<a class="hp-board-card" href="{esc(room_url(a, b))}"><small>Closest call</small>
      <h3>{esc(a['name'])} <i>vs.</i> {esc(b['name'])}</h3>
      <p>{esc(recommendation)} · {result['gap']:.1f}-point edge · {esc(result['confidence'])}</p></a>'''


def board_signal(row: dict, stance: str) -> str:
    p, f, delta = row["player"], row["format"], row["rank_adp_delta"]
    word = "earlier" if stance == "Value" else "later"
    return f'''<a class="hp-board-card" href="{esc(room_url(p))}#{stance.lower()}s"><small>Our {esc(stance)}</small>
      <h3>{esc(p['name'])}</h3><p>Projection rank {f['overall_rank']} · ADP {float(p['adp']):.1f} · ranked {abs(delta):.1f} spots {word}</p></a>'''


def board_mover(row: dict) -> str:
    p, ranks = row["player"], row["ranks"]
    return f'''<a class="hp-board-card" href="{esc(room_url(p))}#movers"><small>Scoring-format mover</small>
      <h3>{esc(p['name'])}</h3><p>{esc(p['position'])}{ranks['ppr']} PPR · {esc(p['position'])}{ranks['half_ppr']} Half-PPR · {esc(p['position'])}{ranks['non_ppr']} Non-PPR</p></a>'''


def decision_language(result: dict) -> str:
    return {"Toss-Up": "No clear edge", "Lean": "Prefer",
            "Edge": "Recommend", "Strong Edge": "Strongly prefer"}[result["confidence"]]


def sport_header(sport: str, activity: str, players: list[dict] | None = None) -> str:
    """Adapter to the one repository-wide shell, with NFL search data."""
    search = ""
    if sport == "nfl":
        options = "".join(f'<option value="{esc(player_label(p))}" data-id="{esc(p["id"])}"></option>'
                          for p in (players or []))
        search = (f'<input id="site-player-search" type="search" list="site-player-list" '
                  f'placeholder="Search NFL players" aria-label="Search NFL players">'
                  f'<datalist id="site-player-list">{options}</datalist>')
    return seo.site_nav(activity if activity != "home" else None, sport, search)


def render_home(payload: dict, college_payload: dict) -> str:
    """Render two complete sport bodies controlled only by the global header."""
    players = payload["players"]
    for p in players:
        p["team_color"] = build_comparison_tool.TEAM_COLORS.get(
            p["team"], ("#263238", "#c6f53c"))[0]
    nfl_context = DecisionContext(payload["mode"], payload["season"],
                                  "half_ppr", payload.get("week"))
    feature = featured_decision(players, nfl_context)
    winner, runner = feature["winner"], feature["runner_up"]
    wf, rf = feature["winner_format"], feature["runner_up_format"]
    calls = [r for r in closest_calls(players, "half_ppr", limit=len(players),
                                      context=nfl_context)
             if not r["is_tie"]][:3]
    values, fades = value_signals(players, "half_ppr")
    if payload.get("mode") == "weekly":
        weekly = sorted(players, key=lambda p: (
            -p["formats"]["half_ppr"]["projected_points"], p["name"]))[:4]
        values = [{"player": p, "format": p["formats"]["half_ppr"],
                   "rank_adp_delta": 0} for p in weekly[:2]]
        fades = [{"player": p, "format": p["formats"]["half_ppr"],
                  "rank_adp_delta": 0} for p in weekly[2:]]
    movers = scoring_movers(players)[:3]

    cp = {p["id"]: p for p in college_payload["players"]}
    college_edges = college_payload["strongest_edges"][:4]
    college_call_pool = []
    for position in ("QB", "RB", "WR", "TE"):
        group = sorted((p for p in cp.values() if p["position"] == position),
                       key=lambda p: (-p["formats"]["yahoo"]["projected_points"], p["name"]))
        for a, b in zip(group, group[1:]):
            gap = round(a["formats"]["yahoo"]["projected_points"]
                        - b["formats"]["yahoo"]["projected_points"], 1)
            if gap > 0:
                classification = confidence(
                    gap, a["formats"]["yahoo"]["projected_points"], "weekly")
                college_call_pool.append({
                    "a": a["id"], "b": b["id"],
                    "winner": None if classification == "Toss-Up" else a["id"],
                    "gap": gap, "confidence": classification})
    college_calls = sorted(college_call_pool, key=lambda r: (r["gap"], r["a"], r["b"]))[:3]
    college_feature = next(r for r in college_edges if r["confidence"] == "Lean")
    cw = cp[college_feature["winner"]]
    cr_id = college_feature["b"] if college_feature["winner"] == college_feature["a"] else college_feature["a"]
    cr = cp[cr_id]
    cf = cw["formats"]["yahoo"]
    crf = cr["formats"]["yahoo"]

    def identity(p: dict, college: bool = False) -> str:
        art = p["team_logo"] if college else (p.get("photo") or p["team_logo"])
        logo = ("" if college else
                f'<img class="hp-id-logo" src="{esc(p["team_logo"])}" alt="" loading="lazy">')
        identity_class = "hp-identity hp-college-identity" if college else "hp-identity"
        return (f'<div class="{identity_class}"><img class="hp-id-art" src="{esc(art)}" '
                f'alt="{esc(p["name"])}" loading="lazy">{logo}<div><b>{esc(p["name"])}</b>'
                f'<span>{esc(p["position"])} · {esc(p["team"])}</span></div></div>')

    def action(href: str, icon: str, title: str, copy: str) -> str:
        return (f'<a class="hp-action" href="{href}"><span class="hp-action-icon" aria-hidden="true">{icon}</span>'
                f'<div><h3>{title}</h3><p>{copy}</p><b>Open →</b></div></a>')

    def nfl_signal(row: dict, stance: str) -> str:
        p, f, delta = row["player"], row["format"], row["rank_adp_delta"]
        if payload.get("mode") == "weekly":
            venue = "vs." if p.get("home") else "at"
            opp = p.get("expected_opportunity", {})
            opportunity = (f'{opp.get("pass_attempts", 0):.1f} attempts'
                           if p["position"] == "QB" else
                           f'{opp.get("carries", 0):.1f} carries · {opp.get("targets", 0):.1f} targets')
            return (f'<a class="hp-signal-card" href="{esc(room_url(p))}">{identity(p)}'
                    f'<small>WEEK 1 MODEL SIGNAL</small><p><b>{f["projected_points"]:.1f}</b> Half-PPR · '
                    f'{venue} {esc(p["opponent"])} · {esc(opportunity)}</p>'
                    f'<strong>2025 matchup context</strong></a>')
        return (f'<a class="hp-signal-card" href="{esc(room_url(p))}#{stance.lower()}s">{identity(p)}'
                f'<small>OUR {stance.upper()}</small><p>Projection rank <b>#{f["overall_rank"]}</b> · '
                f'ADP <b>{float(p["adp"]):.1f}</b></p><strong>{abs(delta):.1f}-spot disagreement</strong></a>')

    def college_signal(row: dict) -> str:
        a, b = cp[row["a"]], cp[row["b"]]
        w = cp[row["winner"]] if row.get("winner") else a
        f = w["formats"]["yahoo"]
        opponent = f' · vs. {esc(w["opponent"])}' if w.get("opponent") else ""
        return (f'<a class="hp-signal-card" href="{COLLEGE_ROOM_PATH}?a={esc(a["id"])}&amp;b={esc(b["id"])}">'
                f'{identity(w, True)}<small>WEEK 1 PROJECTION EDGE</small>'
                f'<p><b>{f["projected_points"]:.1f}</b> Yahoo points{opponent} · rank #{f["overall_rank"]}</p>'
                f'<strong>+{row["gap"]:.1f}-point edge</strong></a>')

    def closest_card(row: dict, college: bool = False) -> str:
        if college:
            a, b = cp[row["a"]], cp[row["b"]]
            href = f'{COLLEGE_ROOM_PATH}?a={a["id"]}&amp;b={b["id"]}'
            scoring = "Yahoo · Week 1"
        else:
            a = row["winner"] or row["player_a"]
            b = row["runner_up"] or row["player_b"]
            href, scoring = room_url(a, b), ("Half-PPR · Week 1"
                                           if payload.get("mode") == "weekly"
                                           else "Half-PPR · 2026 season")
        return (f'<a class="hp-call-card" href="{esc(href)}"><div class="hp-call-art">'
                f'{identity(a, college)}<span>VS</span>{identity(b, college)}</div>'
                f'<div class="hp-call-data"><small>{scoring}</small><b>{row["gap"]:.1f}-point gap</b>'
                f'<span>{esc(row["confidence"])} · Compare →</span></div></a>')

    nfl_header = sport_header("nfl", "home", players)
    nfl_feature = f'''<article class="hp-feature lb-feature-card" style="--c:{esc(winner['team_color'])}"><small>Featured evidence · Week 1 · Half-PPR</small><div class="hp-feature-players"><div><img src="{esc(winner['photo'])}" alt="{esc(winner['name'])}"><img class="hp-team-mark" src="{esc(winner['team_logo'])}" alt=""><span>{esc(winner['position'])} · {esc(winner['team'])} · {"vs." if winner.get("home") else "at"} {esc(winner.get("opponent"))}</span><b>{esc(winner['name'])}</b></div><i>VS</i><div><img src="{esc(runner['photo'])}" alt="{esc(runner['name'])}"><img class="hp-team-mark" src="{esc(runner['team_logo'])}" alt=""><span>{esc(runner['position'])} · {esc(runner['team'])} · {"vs." if runner.get("home") else "at"} {esc(runner.get("opponent"))}</span><b>{esc(runner['name'])}</b></div></div><h2>Projection edge: {esc(winner['name'])}</h2><p><b>+{feature['gap']:.1f}</b> projected Week 1 points · {esc(feature['confidence'])}</p><div class="hp-boundary"><strong>What changes the call?</strong> Projection difference alone is not an unqualified recommendation. Check matchup, opportunity, availability, and missing market evidence.</div><a class="hp-card-cta" href="{esc(room_url(winner, runner))}">Open the full evidence case →</a></article>'''
    college_feature_html = f'''<article class="hp-feature hp-college-feature lb-feature-card" style="--c:{esc(cw['team_color'])}"><small>Featured Decision · Week 1 · Yahoo scoring</small><div class="hp-feature-players"><div><img src="{esc(cw['team_logo'])}" alt="{esc(cw['team'])}"><span>{esc(cw['position'])} · {esc(cw['team'])}</span><b>{esc(cw['name'])}</b><em>{cf['projected_points']:.1f} pts</em></div><i>VS</i><div><img src="{esc(cr['team_logo'])}" alt="{esc(cr['team'])}"><span>{esc(cr['position'])} · {esc(cr['team'])}</span><b>{esc(cr['name'])}</b><em>{crf['projected_points']:.1f} pts</em></div></div><h2>Prefer {esc(cw['name'])}</h2><p><b>+{college_feature['gap']:.1f}</b> Week 1 projected points · {esc(college_feature['confidence'])}</p><div class="hp-boundary"><strong>What changes the pick?</strong> {esc(cr['name'])} needs +{college_feature['gap'] + .1:.1f} projected points to move ahead.</div><a class="hp-card-cta" href="{COLLEGE_ROOM_PATH}?a={esc(cw['id'])}&amp;b={esc(cr['id'])}">Open College Decision Room →</a></article>'''

    nfl_body = f'''<div class="hp-experience" data-home-sport="nfl"><section class="lb-hero lb-decision-hero"><div class="lb-hero-inner"><div class="lb-hero-copy"><div class="lb-eyebrow">NFL WEEK 1 INTELLIGENCE</div><h1 class="lb-hero-title">Make the Week 1 call <span>before kickoff.</span></h1><p class="lb-hero-description">Compare Lineup Beat-owned weekly projections with opportunity, opponent, availability, and clearly labeled missing market evidence.</p><div class="lb-hero-actions"><a class="lb-btn lb-btn-primary" href="{NFL_ROOM_PATH}">Compare Week 1 Players <b>→</b></a><a class="lb-btn lb-btn-secondary" href="/nfl/rankings/">Explore Season Rankings</a></div></div>{nfl_feature}</div></section>
    <section class="hp-section"><div class="hp-section-head"><small>MAKE YOUR NEXT MOVE</small><h2>Go straight to the decision.</h2></div><div class="hp-action-grid">{action('/my-team/','MY','My Team','Connect an ESPN roster locally for Week 1 starter and bench decisions.')}{action(NFL_ROOM_PATH,'±','Decision Room','Compare two players and see the flip boundary.')}{action('/nfl/rankings/','#','Rankings','See the projection order by format and position.')}{action('/nfl/projections/','Σ','Projections','Inspect the validated full-season numbers.')}{action('/nfl/data/','⌁','NFL Fantasy Data','Open ADP, draft value, context, and advanced tools.')}</div></section>
    <section class="hp-section hp-difference"><div class="hp-section-head"><small>WEEK 1 MODEL</small><h2>START WITH THE EVIDENCE</h2><p>Weekly points, expected opportunity, opponent and venue. Odds and current injury reports are explicitly unavailable.</p></div><div class="hp-signal-grid">{''.join(nfl_signal(x,'Value') for x in values[:2])}{''.join(nfl_signal(x,'Fade') for x in fades[:2])}</div></section>
    <section class="hp-section"><div class="hp-section-head"><small>DECISION PRESSURE</small><h2>CLOSEST CALLS</h2><p>Three Week 1 decisions where the projection edge remains narrow and cannot stand alone.</p></div><div class="hp-call-grid">{''.join(closest_card(x) for x in calls)}</div></section>
    <section class="hp-section hp-movers"><div class="hp-section-head"><small>FORMAT SENSITIVITY</small><h2>SCORING FORMAT MOVERS</h2><p>Position rank changes across PPR, Half-PPR, and Non-PPR.</p></div><div class="hp-mover-grid">{''.join(mover_card(x).replace('class="dr-mover"','class="hp-mover-card"') for x in movers)}</div></section></div>'''
    college_body = f'''<div class="hp-experience" data-home-sport="college" hidden><section class="lb-hero lb-decision-hero hp-college-hero"><div class="lb-hero-inner"><div class="lb-hero-copy"><div class="lb-eyebrow">COLLEGE WEEK 1 DECISIONS</div><div class="lb-hero-title" data-home-title>Find the Week 1 edge <span>before kickoff.</span></div><p class="lb-hero-description">Compare 2,205 players across 64 teams using validated Yahoo Week 1 projections.</p><div class="lb-hero-actions"><a class="lb-btn lb-btn-primary" href="{COLLEGE_ROOM_PATH}">Compare College Players <b>→</b></a><a class="lb-btn lb-btn-secondary" href="/college-fantasy-football/week-1/">Explore Week 1 Rankings</a></div></div>{college_feature_html}</div></section>
    <section class="hp-section"><div class="hp-section-head"><small>MAKE YOUR NEXT MOVE</small><h2>Start with Week 1.</h2></div><div class="hp-action-grid">{action(COLLEGE_ROOM_PATH,'±','Decision Room','Compare validated Yahoo Week 1 projections.')}{action('/college-fantasy-football/week-1/','#','Week 1 Rankings','Browse 2,205 players across 64 modeled teams.')}{action('/college-fantasy-football/projections/','Σ','Season Projections','Explore the separate 2,351-player season dataset.')}{action(COLLEGE_ROOM_PATH,'⌕','Player Search','Find College players inside the comparison tool.')}</div></section>
    <section class="hp-section hp-difference"><div class="hp-section-head"><small>WEEK 1 SEPARATION</small><h2>WHERE WE SEE IT DIFFERENTLY</h2><p>Strongest validated Yahoo projection edges. College ADP is not available.</p></div><div class="hp-signal-grid">{''.join(college_signal(x) for x in college_edges)}</div></section>
    <section class="hp-section"><div class="hp-section-head"><small>DECISION PRESSURE</small><h2>CLOSEST CALLS</h2><p>Three same-position Week 1 comparisons with narrow Yahoo projection gaps.</p></div><div class="hp-call-grid">{''.join(closest_card(x, True) for x in college_calls)}</div></section></div>'''
    switch_js = '''<script>(function(){
var nav={nfl:[['My Team','/my-team/'],['Decision','/decision-room/nfl/'],['Rankings','/nfl/rankings/'],['Projections','/nfl/projections/'],['Fantasy Data','/nfl/data/']],college:[['Decision','/decision-room/college/'],['Week 1 Rankings','/college-fantasy-football/week-1/'],['Season Projections','/college-fantasy-football/projections/']]};
function retag(el,tag){if(!el||el.tagName.toLowerCase()===tag)return;var n=document.createElement(tag);Array.from(el.attributes).forEach(function(a){n.setAttribute(a.name,a.value)});n.innerHTML=el.innerHTML;el.replaceWith(n)}
function setSport(s,push){if(s!=="college")s="nfl";document.querySelectorAll("[data-home-sport]").forEach(function(x){x.hidden=x.dataset.homeSport!==s;retag(x.querySelector(".lb-hero-title"),x.dataset.homeSport===s?"h1":"div")});var bar=document.querySelector(".topbar");if(bar){bar.querySelectorAll(".sport-pill").forEach(function(a){var hit=a.textContent.trim().toLowerCase()===s;a.setAttribute("aria-pressed",hit?"true":"false");a.href=hit?(s==="college"?"/?sport=college":"/"):(s==="college"?"/":"/?sport=college")});var views=bar.querySelector(".views");if(views)views.innerHTML=nav[s].map(function(x){return '<a class="vbtn" href="'+x[1]+'">'+x[0]+'</a>'}).join('');var finder=bar.querySelector(".finder");if(finder)finder.innerHTML=s==="college"?'<a class="college-search-entry" href="/decision-room/college/">Search 2,205 College players</a>':'<a class="college-search-entry" href="/decision-room/nfl/">Search NFL players</a>';var mobile=bar.querySelector(".navlinks");if(mobile)mobile.innerHTML='<a class="navlink" href="/">NFL</a><a class="navlink" href="/?sport=college">COLLEGE</a>'+nav[s].map(function(x){return '<a class="navlink" href="'+x[1]+'">'+x[0]+'</a>'}).join('')+'<a class="navlink" href="'+(s==="college"?'/decision-room/college/':'/decision-room/nfl/')+'">Search '+(s==="college"?'College':'NFL')+' players</a>'}document.body.dataset.defaultSport=s;if(push)history.pushState({sport:s},"",s==="college"?"/?sport=college":"/")}
document.addEventListener("click",function(e){var a=e.target.closest&&e.target.closest(".sport-pill");if(!a)return;e.preventDefault();setSport(a.textContent.trim().toLowerCase(),true)});window.addEventListener("popstate",function(){setSport(new URLSearchParams(location.search).get("sport")||"nfl",false)});setSport(new URLSearchParams(location.search).get("sport")||"nfl",false)})();</script>'''
    return f'''{START}{nfl_header}<main id="lineup-beat-home" class="hp-shell">{nfl_body}{college_body}</main>{switch_js}{END}'''


def render(payload: dict) -> str:
    players = payload["players"]
    calls = closest_calls(players, "half_ppr", context=DecisionContext(
        payload["mode"], payload["season"], "half_ppr", payload.get("week")))
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
    context_players = sorted(players, key=lambda p: (
        -p["formats"]["half_ppr"]["projected_points"], p["name"]))[:6]

    def weekly_context_card(p: dict) -> str:
        opportunity = p.get("expected_opportunity", {})
        role = (f'{opportunity.get("pass_attempts", 0):.1f} pass attempts'
                if p["position"] == "QB" else
                f'{opportunity.get("carries", 0):.1f} carries · {opportunity.get("targets", 0):.1f} targets')
        venue = "vs." if p.get("home") else "at"
        return (f'<article class="dr-signal"><img src="{esc(p.get("photo") or p["team_logo"])}" alt="">'
                f'<div><small>{esc(p["team"])} · {esc(p["position"])} · {venue} {esc(p.get("opponent"))}</small>'
                f'<h3>{esc(p["name"])}</h3><p>{p["formats"]["half_ppr"]["projected_points"]:.1f} Half-PPR · '
                f'{esc(role)} · 2025 matchup factor {p.get("matchup", {}).get("projection_factor", 1):.2f}.</p></div></article>')
    block = f'''{START}
<main id="decision-room" class="dr-shell" data-mode="weekly" data-season="2026" data-week="1">
  <section class="dr-hero">
    <div class="dr-kicker">2026 NFL Week 1 Decision Room</div>
    <div class="dr-mode">Week 1 — Lineup Beat-owned weekly projections</div>
    <h1>Make the decision—not just the projection.</h1>
    <p class="dr-lede">Compare Week 1 projections, opportunity, opponent context and availability—and see which evidence is still missing.</p>
    <p class="dr-week-note">Odds and current injury reports are unavailable. Prior-season matchup metrics are labeled 2025 context. The model covers QB, RB, WR and TE only; no D/ST projection is included. A point difference alone cannot create an unqualified recommendation.</p>
    <section class="dr-compare" aria-labelledby="dr-compare-title">
      <div class="dr-compare-head"><div><small>Decision 01</small><h2 id="dr-compare-title">Player vs. player</h2></div>
        <label>Scoring format<select id="dr-format"><option value="ppr">PPR</option><option value="half_ppr" selected>Half-PPR</option><option value="non_ppr">Non-PPR</option></select></label></div>
      <div class="dr-selectors"><div class="dr-picker"><label for="dr-a-search">Player one</label><input id="dr-a-search" type="search" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="dr-a-list" autocomplete="off"><ul id="dr-a-list" role="listbox" hidden></ul><select id="dr-a" class="dr-native" aria-label="Player one fallback">{options}</select></div><b>VS</b><div class="dr-picker"><label for="dr-b-search">Player two</label><input id="dr-b-search" type="search" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="dr-b-list" autocomplete="off"><ul id="dr-b-list" role="listbox" hidden></ul><select id="dr-b" class="dr-native" aria-label="Player two fallback">{options}</select></div></div>
      <label class="dr-cross"><input type="checkbox" id="dr-cross-position"> Compare across positions</label>
      <div id="dr-result" aria-live="polite"></div>
    </section>
  </section>

  <section class="dr-section" id="closest"><div class="dr-section-head"><div><small>Decision pressure</small><h2>Closest Calls</h2></div><p>Same-position Week 1 decisions separated by the fewest modeled half-PPR points.</p></div>
    <div class="dr-card-grid">{''.join(call_card(c) for c in calls)}</div></section>

  <section class="dr-section dr-convictions" id="week1-context"><div class="dr-section-head"><div><small>Validated weekly inputs</small><h2>Opportunity and opponent context</h2></div><p>Modeled opportunity with clearly labeled 2025 prior-season defense. Odds and current injury reports remain unavailable.</p></div>
    <div class="dr-signal-grid">{''.join(weekly_context_card(p) for p in context_players)}</div></section>

  <section class="dr-section" id="movers"><div class="dr-section-head"><div><small>Format sensitivity</small><h2>Scoring-format movers</h2></div><p>Players whose position rank changes most when receptions change value.</p></div>
    <div class="dr-mover-grid">{''.join(mover_card(m) for m in movers)}</div></section>

  <section class="dr-future-grid">
    <article class="dr-empty"><small>Available in development</small><h2>My Team</h2><p>Connect an ESPN roster locally to see supported Week 1 starter and bench decisions.</p><a class="dr-empty-link" href="/my-team/">Open My Team</a></article>
    <article class="dr-empty"><small>Accountability layer</small><h2>Decision Record</h2><p>No decisions have been recorded. Future snapshots will preserve the recommendation, inputs, timestamp, and eventual outcome instead of silently rewriting the call.</p><div class="dr-empty-line">No saved decisions yet</div></article>
  </section>

  <nav class="dr-tools" aria-label="More Lineup Beat tools"><span>Keep exploring</span><a href="/nfl/rankings/">Rankings</a><a href="/nfl/projections/">Projections</a><a href="/nfl/who-should-i-draft/">Draft comparison</a></nav>
</main>
<script id="dr-data" type="application/json">{data}</script>
<script>{comparison_v2_javascript((first['winner'] or first['player_a'])['id'], (first['runner_up'] or first['player_b'])['id'], updated)}</script>
{END}'''
    return block


def _legacy_javascript(default_a: str, default_b: str, updated: str) -> str:
    return r'''(()=>{document.getElementById("decision-room").classList.add("dr-enhanced");const D=JSON.parse(document.getElementById("dr-data").textContent),P=Object.fromEntries(D.players.map(p=>[p.id,p])),A=document.getElementById("dr-a"),B=document.getElementById("dr-b"),F=document.getElementById("dr-format"),X=document.getElementById("dr-cross-position"),O=document.getElementById("dr-result"),L={ppr:"PPR",half_ppr:"Half-PPR",non_ppr:"Non-PPR"},FM=["ppr","half_ppr","non_ppr"];
const num=v=>Number(v).toFixed(1),shown=v=>Number(Number(v).toFixed(1)),adp=p=>p.adp==null?"Not available":Number(p.adp).toFixed(1),conf=g=>g===0?"True Toss-Up":g<=2?"Toss-Up":g<12?"Lean":"Clear Edge",fmt=(p,k)=>p.formats[k],winner=(a,b,k)=>{let x=shown(fmt(a,k).projected_points),y=shown(fmt(b,k).projected_points);return x===y?null:(x>y?a:b)};
function portrait(p){return `<div class="dr-person" style="--team:${p.team_color}"><img class="dr-logo" src="${p.team_logo}" alt=""><img class="dr-photo" src="${p.photo||p.team_logo}" alt="${p.name}" onerror="this.src='${p.team_logo}'"><div><small>${p.team} · ${p.position}</small><h3>${p.name}</h3></div></div>`}
function playerCards(a,b,k){return `<div class="dr-player-grid"><article>${portrait(a)}<dl><div><dt>Projected points</dt><dd>${num(fmt(a,k).projected_points)}</dd></div><div><dt>Projection rank</dt><dd>#${fmt(a,k).overall_rank} · ${a.position}${fmt(a,k).position_rank}</dd></div><div><dt>ADP</dt><dd>${adp(a)}</dd></div></dl></article><article>${portrait(b)}<dl><div><dt>Projected points</dt><dd>${num(fmt(b,k).projected_points)}</dd></div><div><dt>Projection rank</dt><dd>#${fmt(b,k).overall_rank} · ${b.position}${fmt(b,k).position_rank}</dd></div><div><dt>ADP</dt><dd>${adp(b)}</dd></div></dl></article></div>`}
function draw(){let a=P[A.value],b=P[B.value],k=F.value;if(!a||!b||a.id===b.id){O.innerHTML='<p class="dr-error">Choose two different players.</p>';return}let w=winner(a,b,k),gap=Math.abs(shown(fmt(a,k).projected_points)-shown(fmt(b,k).projected_points));if(!w){let edges=FM.filter(x=>x!==k&&winner(a,b,x)).map(x=>L[x]);O.innerHTML=`<section class="dr-verdict"><div><small>True Toss-Up · ${L[k]}</small><h2>No clear edge</h2><p>Both players display at ${num(fmt(a,k).projected_points)} full-season ${L[k]} points. Lineup Beat does not recommend either player when the displayed projections are equal.</p></div><div class="dr-adv"><b>0.0</b><span>displayed point gap</span></div></section>${playerCards(a,b,k)}<section class="dr-boundary"><div class="dr-boundary-title"><small>Signature analysis</small><h2>What changes the pick?</h2></div><div class="dr-boundary-grid"><article><b>+0.1</b><span>Either player needs one tenth of a displayed season point to move ahead.</span></article><article><b>${edges.length?edges.join(' / '):'No edge'}</b><span>${edges.length?'These scoring formats produce a leader.':'Every available scoring format remains tied.'}</span></article></div></section><p class="dr-stamp">Projection data updated ''' + esc(updated) + r''' · Page build: current development deployment · 2026 full season · ${L[k]}</p>`;return}let r=w.id===a.id?b:a,wf=fmt(w,k),rf=fmt(r,k);gap=+(shown(wf.projected_points)-shown(rf.projected_points)).toFixed(1);let flip=+(gap+.1).toFixed(1),flips=FM.filter(x=>x!==k&&(!winner(a,b,x)||winner(a,b,x).id!==w.id)).map(x=>L[x]),market=w.adp!=null&&r.adp!=null?(w.adp>r.adp?'Market ADP prefers '+r.name+'.':'Market ADP agrees with the pick.'):'ADP comparison is unavailable for this pair.';
O.innerHTML=`<section class="dr-verdict"><div><small>${conf(gap)} · ${L[k]}</small><h2>Recommend ${w.name}</h2><p>${w.name} projects for ${num(wf.projected_points)} full-season ${L[k]} points, ${num(gap)} more than ${r.name}. The recommendation follows the higher displayed validated season projection.</p></div><div class="dr-adv"><b>+${num(gap)}</b><span>season-point advantage</span></div></section>${playerCards(a,b,k)}<section class="dr-boundary"><div class="dr-boundary-title"><small>Signature analysis</small><h2>What changes the pick?</h2></div><div class="dr-boundary-grid"><article><b>+${num(flip)}</b><span>${r.name} needs this many additional projected season points to move ahead.</span></article><article><b>−${num(flip)}</b><span>${w.name} could lose this many projected season points before the recommendation flips.</span></article><article><b>${flips.length?flips.join(' / '):'No flip'}</b><span>${flips.length?'These available scoring formats remove or reverse the recommendation.':'The recommendation holds in every available scoring format.'}</span></article><article><b>${market.startsWith('Market ADP prefers')?'Disagreement':market.startsWith('Market')?'Agreement':'No ADP'}</b><span>${market}</span></article></div></section><p class="dr-stamp">Projection data updated ''' + esc(updated) + r''' · Page build: current development deployment · 2026 full season · ${L[k]}</p>`}
function candidates(which){let other=which===A?B:A,base=D.players.filter(p=>p.id!==other.value);if(which===B&&!X.checked&&P[A.value])base=base.filter(p=>p.position===P[A.value].position);return base}
function setup(select,input,list){let active=-1;function close(){list.hidden=true;input.setAttribute('aria-expanded','false');active=-1}function show(){let q=input.value.toLowerCase(),rows=candidates(select).filter(p=>!q||(`${p.name} ${p.team} ${p.position}`).toLowerCase().includes(q)).slice(0,40);list.innerHTML=rows.length?rows.map((p,i)=>`<li role="option" data-id="${p.id}" id="${list.id}-${i}">${p.name}<small>${p.team} · ${p.position}</small></li>`).join(''):'<li class="dr-no-result">No matching players</li>';list.hidden=false;input.setAttribute('aria-expanded','true')}function choose(id){let p=P[id];if(!p)return;select.value=id;input.value=`${p.name} · ${p.team} ${p.position}`;close();select.dispatchEvent(new Event('change'))}input.addEventListener('focus',()=>{input.select();show()});input.addEventListener('input',show);input.addEventListener('keydown',e=>{let rows=[...list.querySelectorAll('[role=option]')];if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();active=Math.max(0,Math.min(rows.length-1,active+(e.key==='ArrowDown'?1:-1)));rows.forEach((x,i)=>x.setAttribute('aria-selected',i===active?'true':'false'));if(rows[active])rows[active].scrollIntoView({block:'nearest'})}else if(e.key==='Enter'&&rows[active]){e.preventDefault();choose(rows[active].dataset.id)}else if(e.key==='Escape')close()});list.addEventListener('mousedown',e=>{let row=e.target.closest('[role=option]');if(row){e.preventDefault();choose(row.dataset.id)}});select.addEventListener('change',()=>{let p=P[select.value];if(p)input.value=`${p.name} · ${p.team} ${p.position}`});document.addEventListener('click',e=>{if(!e.target.closest('.dr-picker'))close()});return{refresh:show}}
let Q=new URLSearchParams(location.search);A.value=P[Q.get('a')]?Q.get('a'):"''' + esc(default_a) + r'''";B.value=P[Q.get('b')]&&Q.get('b')!==A.value?Q.get('b'):"''' + esc(default_b) + r'''";if(F.querySelector(`option[value="${Q.get('format')}"]`))F.value=Q.get('format');let PA=setup(A,document.getElementById('dr-a-search'),document.getElementById('dr-a-list')),PB=setup(B,document.getElementById('dr-b-search'),document.getElementById('dr-b-list'));A.dispatchEvent(new Event('change'));B.dispatchEvent(new Event('change'));A.addEventListener('change',()=>{if(!X.checked&&P[A.value]&&(!P[B.value]||P[B.value].position!==P[A.value].position||A.value===B.value)){let next=D.players.find(p=>p.id!==A.value&&p.position===P[A.value].position);if(next){B.value=next.id;B.dispatchEvent(new Event('change'))}}draw()});[B,F].forEach(x=>x.addEventListener('change',draw));X.addEventListener('change',()=>{A.dispatchEvent(new Event('change'));PB.refresh()});document.querySelectorAll('.dr-open').forEach(x=>x.addEventListener('click',()=>{A.value=x.dataset.a;B.value=x.dataset.b;A.dispatchEvent(new Event('change'));B.dispatchEvent(new Event('change'));draw();document.getElementById('dr-compare-title').scrollIntoView({behavior:'smooth'})}));draw()})();'''


def comparison_v2_javascript(default_a: str, default_b: str, updated: str) -> str:
    """Render Comparison Engine v2 from validated, identity-keyed evidence."""
    script = r'''(()=>{
const root=document.getElementById("decision-room");root.classList.add("dr-enhanced");
const D=JSON.parse(document.getElementById("dr-data").textContent),P=Object.fromEntries(D.players.map(p=>[p.id,p])),A=document.getElementById("dr-a"),B=document.getElementById("dr-b"),F=document.getElementById("dr-format"),X=document.getElementById("dr-cross-position"),O=document.getElementById("dr-result"),L={ppr:"PPR",half_ppr:"Half-PPR",non_ppr:"Non-PPR"},FM=["ppr","half_ppr","non_ppr"],weekly=D.mode==="weekly";
const safe=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])),shown=v=>+Number(v).toFixed(1),num=v=>shown(v).toFixed(1),fmt=(p,k)=>p.formats[k],points=(p,k)=>shown(fmt(p,k).projected_points),pct=(g,r)=>+(g/Math.max(Math.abs(r),.1)*100).toFixed(1),terminalText=v=>/[.!?]$/.test(String(v??'').trim())?String(v).trim():`${String(v??'').trim()}.`,terminalName=p=>terminalText(safe(p.name));
function classification(g,r){let q=pct(g,r);if(weekly){if(g<=.5||q<=3)return"Toss-Up";if(q<=7)return"Lean";if(q<=15)return"Edge";return"Strong Edge"}if(g<=2||q<=1)return"Toss-Up";if(q<=3)return"Lean";if(q<=7)return"Edge";return"Strong Edge"}
function projectedLeader(a,b,k){let x=points(a,k),y=points(b,k);return x===y?null:(x>y?a:b)}
function opinion(a,b){return (D.editorial_opinions||[]).find(x=>[x.subject_id,x.preferred_over_id].includes(a.id)&&[x.subject_id,x.preferred_over_id].includes(b.id))||null}
function history(p,k){let h=(p.history||{})[k];return h&&Number(h.games)>=8?h:null}
function portrait(p){return `<div class="dr-person" style="--team:${safe(p.team_color)}"><img class="dr-logo" src="${safe(p.team_logo)}" alt=""><img class="dr-photo" src="${safe(p.photo||p.team_logo)}" alt="${safe(p.name)}" onerror="this.src='${safe(p.team_logo)}'"><div><small>${safe(p.team)} · ${safe(p.position)}</small><h3>${safe(p.name)}</h3></div></div>`}
function opportunity(p){let o=p.expected_opportunity||{};return p.position==='QB'?`${num(o.pass_attempts||0)} attempts`:`${num(o.carries||0)} carries · ${num(o.targets||0)} targets`}
function playerCards(a,b,k){return `<div class="dr-player-grid">${[a,b].map(p=>`<article>${portrait(p)}<dl><div><dt>Week 1 projection</dt><dd>${num(points(p,k))}</dd></div><div><dt>Opponent</dt><dd>${p.home?'vs.':'at'} ${safe(p.opponent||'—')}</dd></div><div><dt>Opportunity</dt><dd>${opportunity(p)}</dd></div></dl></article>`).join('')}</div>`}
function spotWord(n){return Number(n)===1?'spot':'spots'}
function rankText(a,b,k){let af=fmt(a,k),bf=fmt(b,k),og=Math.abs(af.overall_rank-bf.overall_rank),pg=Math.abs(af.position_rank-bf.position_rank),overall=af.overall_rank===bf.overall_rank?'Overall ranks are tied.':`${af.overall_rank<bf.overall_rank?safe(a.name):safe(b.name)} ranks ${og} overall ${spotWord(og)} higher.`,position=af.position_rank===bf.position_rank?'Position ranks are tied.':`${af.position_rank<bf.position_rank?safe(a.name):safe(b.name)} ranks ${pg} ${safe(a.position===b.position?a.position:'position')} ${spotWord(pg)} higher.`;return overall+' '+position}
function adpState(a,b){let missing=[a,b].filter(p=>p.adp==null);return{state:missing.length===0?'present':missing.length===1?'one-missing':'both-missing',missing}}
function adpMissingText(a,b){let x=adpState(a,b);if(x.state==='present')return'';if(x.state==='one-missing')return `Unavailable — validated ADP is unavailable for ${terminalName(x.missing[0])}`;return `Unavailable — validated ADP is unavailable for both ${safe(a.name)} and ${terminalName(b)}`}
function marketText(a,b,k){if(a.adp==null||b.adp==null)return adpMissingText(a,b);let early=Number(a.adp)<Number(b.adp)?a:b,diff=Math.abs(Number(a.adp)-Number(b.adp)).toFixed(1),av=Number(a.adp)-fmt(a,k).overall_rank,bv=Number(b.adp)-fmt(b,k).overall_rank;return `Market ADP selects ${safe(early.name)} ${diff} picks earlier. Projection-rank value margin: ${safe(a.name)} ${av>=0?'+':''}${av.toFixed(1)}; ${safe(b.name)} ${bv>=0?'+':''}${bv.toFixed(1)}.`}
function formatRows(a,b){return FM.map(k=>{let g=Math.abs(points(a,k)-points(b,k)),c=classification(g,Math.max(points(a,k),points(b,k))),raw=projectedLeader(a,b,k);return{k,label:L[k],classification:c,raw,winner:c==='Toss-Up'?null:raw}})}
function sensitivity(a,b){let rows=formatRows(a,b),meaningful=rows.filter(x=>x.winner),winnerIds=new Set(meaningful.map(x=>x.winner.id)),rawIds=new Set(rows.filter(x=>x.raw).map(x=>x.raw.id)),toss=rows.filter(x=>x.classification==='Toss-Up').map(x=>x.label);if(!meaningful.length)return{label:'No reversal',text:rawIds.size>1?'No meaningful scoring-format reversal. The raw projection leader changes, but every format remains a Toss-Up.':'No meaningful scoring-format reversal. Every validated format remains a Toss-Up.'};if(winnerIds.size>1)return{label:'Meaningful reversal',text:'A meaningful scoring-format reversal produces opposing non–Toss-Up leaders across the validated formats.'};if(toss.length)return{label:'No reversal',text:`No meaningful scoring-format reversal. ${toss.join(' / ')} ${toss.length===1?'becomes a Toss-Up':'become Toss-Ups'}; no validated format produces a meaningful edge for the other player.`};let classes=new Set(rows.map(x=>x.classification));return{label:'No reversal',text:classes.size>1?'No meaningful scoring-format reversal. Edge strength changes, but the same player holds every validated call.':'No meaningful scoring-format reversal. The same meaningful call holds across PPR, Half-PPR, and Non-PPR.'}}
function formatText(a,b){return sensitivity(a,b).text}
function historyText(a,b,k){let ah=history(a,k),bh=history(b,k);if(!ah||!bh)return'Unavailable — each player needs at least eight games in the validated 2025 history.';let steady=Number(ah.consistency_score)>=Number(bh.consistency_score)?a:b,h=steady.id===a.id?ah:bh;return `${safe(steady.name)} had the higher 2025 consistency score (${h.consistency_score}) across ${h.games} games. ${safe(a.name)} averaged ${num(ah.average)}; ${safe(b.name)} averaged ${num(bh.average)} ${L[k]} points per game.`}
function editorialText(a,b){let e=opinion(a,b);if(!e)return'No documented Lineup Beat comparison opinion for this exact pair.';return `Historical opinion (${safe(e.evidence_date)}): ${safe(e.source_text)} This is dated evidence, not the current ranking source.`}
function caseFor(p,o,k,e){let facts=[],pf=fmt(p,k),of=fmt(o,k);if(points(p,k)>points(o,k))facts.push(`Projects ${num(points(p,k)-points(o,k))} Week 1 points higher.`);let po=p.expected_opportunity||{},oo=o.expected_opportunity||{},pv=p.position==='QB'?Number(po.pass_attempts||0):Number(po.carries||0)+Number(po.targets||0),ov=o.position==='QB'?Number(oo.pass_attempts||0):Number(oo.carries||0)+Number(oo.targets||0);if(pv>ov)facts.push(`Carries the stronger validated opportunity estimate (${opportunity(p)}).`);if(Number(p.matchup?.projection_factor||1)>Number(o.matchup?.projection_factor||1))facts.push(`Draws the more favorable 2025 opponent-context factor.`);let ph=history(p,k),oh=history(o,k);if(ph&&oh&&Number(ph.consistency_score)>Number(oh.consistency_score))facts.push(`Higher validated 2025 consistency score (${ph.consistency_score} vs. ${oh.consistency_score}).`);if(e&&e.subject_id===p.id&&e.preferred_over_id===o.id)facts.push(`The dated August 18 editorial opinion preferred ${safe(p.name)} in this exact pair.`);return facts.length?facts.slice(0,3).map(x=>`<li>${x}</li>`).join(''):'<li>No additional validated counter-case is available.</li>'}
function coverage(a,b,k,e){let cats=[['Week 1 projection','Present'],['Opportunity','Present'],['2025 matchup','Present'],['Scoring formats','Present'],['2025 consistency',history(a,k)&&history(b,k)?'Present':'Unavailable'],['Current injury report','Unavailable'],['Betting market','Unavailable']],present=cats.filter(x=>x[1]==='Present').length;return {cats,present}}
function agreement(a,b,k,e,cls,lead){let signals=[],add=(category,p)=>signals.push({category,player:p});if(cls!=='Toss-Up'&&lead)add('projection edge',lead);let ao=a.expected_opportunity||{},bo=b.expected_opportunity||{},av=a.position==='QB'?Number(ao.pass_attempts||0):Number(ao.carries||0)+Number(ao.targets||0),bv=b.position==='QB'?Number(bo.pass_attempts||0):Number(bo.carries||0)+Number(bo.targets||0);if(a.position===b.position&&Math.abs(av-bv)>=2)add('expected opportunity',av>bv?a:b);let am=Number(a.matchup?.projection_factor||1),bm=Number(b.matchup?.projection_factor||1);if(Math.abs(am-bm)>=.03)add('2025 opponent context',am>bm?a:b);let ah=history(a,k),bh=history(b,k);if(ah&&bh){let cg=Number(ah.consistency_score)-Number(bh.consistency_score),ag=Number(ah.average)-Number(bh.average);if(Math.abs(cg)>=5)add('prior-year consistency',cg>0?a:b);else if(Math.abs(ag)>=3)add('prior-year consistency',ag>0?a:b)}if(e)add('dated Lineup Beat opinion',e.subject_id===a.id?a:b);let groups={[a.id]:signals.filter(x=>x.player.id===a.id),[b.id]:signals.filter(x=>x.player.id===b.id)},represented=[a,b].filter(p=>groups[p.id].length),state=represented.length===0?'Mixed':represented.length===1&&signals.length>1?'Aligned':represented.length===1?'Mixed':represented.every(p=>groups[p.id].length>=2)?'Split':'Mixed';return{state,groups,signals}}
function joinWords(xs){return xs.length<2?xs[0]||'validated evidence':xs.length===2?`${xs[0]} and ${xs[1]}`:`${xs.slice(0,-1).join(', ')}, and ${xs.at(-1)}`}
function sentenceStart(v){return v?v.charAt(0).toUpperCase()+v.slice(1):v}
function agreementVerb(xs){return xs.length===1&&xs[0].category!=='current ranks'?'favors':'favor'}
function agreementText(a,b,x){let clauses=[a,b].filter(p=>x.groups[p.id].length).map(p=>`${joinWords(x.groups[p.id].map(s=>s.category))} ${agreementVerb(x.groups[p.id])} ${safe(p.name)}`);return `${terminalText(sentenceStart(clauses.join('; ')))} Evidence agreement is ${x.state}.`}
function changeCards(a,b,k,lead,cls){let gap=Math.abs(points(a,k)-points(b,k)),reference=Math.max(points(a,k),points(b,k)),boundary=weekly?Math.max(.5,reference*.03):Math.max(2,reference*.01),need=cls==='Toss-Up'?+(boundary+.1).toFixed(1):+(gap+.1).toFixed(1),runner=lead?(lead.id===a.id?b:a):null,s=sensitivity(a,b);return `<div class="dr-boundary-grid"><article><b>+${num(need)}</b><span>${cls==='Toss-Up'?`A displayed gap above the ${num(need-.1)}-point no-call boundary is required for a Lean.`:`${safe(runner.name)} needs this projection gain to move ahead.`}</span></article><article><b>${safe(s.label)}</b><span>${s.text}</span></article><article><b>Unavailable</b><span>Odds were not requested because the isolated credential is unavailable.</span></article><article><b>Check status</b><span>Current Week 1 injury reports are unavailable; a validated availability change can reverse the call.</span></article></div>`}
function draw(){let a=P[A.value],b=P[B.value],k=F.value;if(!a||!b||a.id===b.id){O.innerHTML='<p class="dr-error">Choose two different players.</p>';return}let ap=points(a,k),bp=points(b,k),gap=Math.abs(ap-bp),gp=pct(gap,Math.max(ap,bp)),lead=projectedLeader(a,b,k),cls=classification(gap,Math.max(ap,bp)),w=cls==='Toss-Up'?null:lead,r=w?(w.id===a.id?b:a):null,e=opinion(a,b),q=coverage(a,b,k,e),agree=agreement(a,b,k,e,cls,lead),call=cls==='Toss-Up'?'No clear edge':agree.state==='Split'?'Split case':agree.state==='Mixed'?`Mixed case — projection favors ${safe(w.name)}`:`${cls==='Lean'?'Prefer':cls==='Edge'?'Recommend':'Strongly prefer'} ${safe(w.name)}`,projection=cls==='Toss-Up'?`Projection edge: Toss-Up. The ${num(gap)}-point difference (${gp.toFixed(1)}%) is inside the deterministic no-call band.`:`Projection edge: ${cls} — ${safe(w.name)}. ${safe(w.name)} projects ${num(gap)} points (${gp.toFixed(1)}%) ahead of ${terminalName(r)}`,reconcile=agree.signals.length?agreementText(a,b,agree):'No additional directional evidence is available.';
O.innerHTML=`<section class="dr-verdict"><div><small>Lineup Beat call · ${agree.state} · ${L[k]}</small><h2>${call}</h2><p><strong>${projection}</strong> ${reconcile}</p></div><div class="dr-adv"><b>${gap?'+':''}${num(gap)}</b><span>Week 1 point difference</span></div></section>${playerCards(a,b,k)}<section class="dr-evidence"><div class="dr-evidence-title"><small>Evidence stack 02</small><h2>Why</h2></div><div class="dr-why-grid"><article><h3>Our Week 1 projection</h3><p>${cls} · ${num(gap)} points · ${gp.toFixed(1)}% difference.</p></article><article><h3>What the market says</h3><p>Unavailable — zero odds requests were made because the isolated credential is unavailable.</p></article><article><h3>Opponent matchup</h3><p>${safe(a.name)}: ${safe(a.matchup?.label)} factor ${Number(a.matchup?.projection_factor||1).toFixed(2)}. ${safe(b.name)}: ${safe(b.matchup?.label)} factor ${Number(b.matchup?.projection_factor||1).toFixed(2)}.</p></article><article><h3>Expected opportunity</h3><p>${safe(a.name)}: ${opportunity(a)}. ${safe(b.name)}: ${opportunity(b)}.</p></article><article><h3>Availability</h3><p>Both are active on the captured roster. Current Week 1 injury reports are unavailable.</p></article><article><h3>Prior-year consistency</h3><p>${historyText(a,b,k)}</p></article></div></section><section class="dr-cases"><div class="dr-evidence-title"><small>Balanced evidence 03</small><h2>Case for each player</h2></div><div class="dr-case-grid"><article><h3>${safe(a.name)}</h3><ul>${caseFor(a,b,k,e)}</ul></article><article><h3>${safe(b.name)}</h3><ul>${caseFor(b,a,k,e)}</ul></article></div></section><section class="dr-boundary"><div class="dr-boundary-title"><small>Decision boundaries 04</small><h2>What changes the call</h2></div>${changeCards(a,b,k,lead,cls)}</section><section class="dr-quality"><div class="dr-evidence-title"><small>Transparency 05</small><h2>Data coverage and evidence agreement</h2><p><b>Data coverage</b> · ${q.present} of ${q.cats.length} evidence categories available for this pair.</p><p><b>Evidence agreement</b> · ${agree.state}. Classification describes projection-edge size, not probability or confidence.</p></div><div class="dr-quality-grid">${q.cats.map(x=>`<span class="${x[1].toLowerCase().replace(' ','-')}"><b>${x[0]}</b>${x[1]}</span>`).join('')}</div><p class="dr-stamp">Lineup Beat model ${safe(D.sources.model.updated_at)} · History: ${safe(D.sources.history.updated_at)} · Matchup: 2025 context · Market: unavailable${e?` · Editorial ${safe(e.evidence_date)} (historical/stale)`:''}</p></section>`}
function candidates(which){let other=which===A?B:A,base=D.players.filter(p=>p.id!==other.value);if(which===B&&!X.checked&&P[A.value])base=base.filter(p=>p.position===P[A.value].position);return base}
function setup(select,input,list){let active=-1;function close(){list.hidden=true;input.setAttribute('aria-expanded','false');active=-1}function show(){let q=input.value.toLowerCase(),rows=candidates(select).filter(p=>!q||(`${p.name} ${p.team} ${p.position}`).toLowerCase().includes(q)).slice(0,40);list.innerHTML=rows.length?rows.map((p,i)=>`<li role="option" data-id="${safe(p.id)}" id="${list.id}-${i}">${safe(p.name)}<small>${safe(p.team)} · ${safe(p.position)}</small></li>`).join(''):'<li class="dr-no-result">No matching players</li>';list.hidden=false;input.setAttribute('aria-expanded','true')}function choose(id){let p=P[id];if(!p)return;select.value=id;input.value=`${p.name} · ${p.team} ${p.position}`;close();select.dispatchEvent(new Event('change'))}input.addEventListener('focus',()=>{input.select();show()});input.addEventListener('input',show);input.addEventListener('keydown',e=>{let rows=[...list.querySelectorAll('[role=option]')];if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();active=Math.max(0,Math.min(rows.length-1,active+(e.key==='ArrowDown'?1:-1)));rows.forEach((x,i)=>x.setAttribute('aria-selected',i===active?'true':'false'));if(rows[active])rows[active].scrollIntoView({block:'nearest'})}else if(e.key==='Enter'&&rows[active]){e.preventDefault();choose(rows[active].dataset.id)}else if(e.key==='Escape')close()});list.addEventListener('mousedown',e=>{let row=e.target.closest('[role=option]');if(row){e.preventDefault();choose(row.dataset.id)}});select.addEventListener('change',()=>{let p=P[select.value];if(p)input.value=`${p.name} · ${p.team} ${p.position}`});document.addEventListener('click',e=>{if(!e.target.closest('.dr-picker'))close()});return{refresh:show}}
let Q=new URLSearchParams(location.search);A.value=P[Q.get('a')]?Q.get('a'):__DEFAULT_A__;B.value=P[Q.get('b')]&&Q.get('b')!==A.value?Q.get('b'):__DEFAULT_B__;if(F.querySelector(`option[value="${Q.get('format')}"]`))F.value=Q.get('format');let PA=setup(A,document.getElementById('dr-a-search'),document.getElementById('dr-a-list')),PB=setup(B,document.getElementById('dr-b-search'),document.getElementById('dr-b-list'));A.dispatchEvent(new Event('change'));B.dispatchEvent(new Event('change'));A.addEventListener('change',()=>{if(!X.checked&&P[A.value]&&(!P[B.value]||P[B.value].position!==P[A.value].position||A.value===B.value)){let next=D.players.find(p=>p.id!==A.value&&p.position===P[A.value].position);if(next){B.value=next.id;B.dispatchEvent(new Event('change'))}}draw()});[B,F].forEach(x=>x.addEventListener('change',draw));X.addEventListener('change',()=>{A.dispatchEvent(new Event('change'));PB.refresh()});document.querySelectorAll('.dr-open').forEach(x=>x.addEventListener('click',()=>{A.value=x.dataset.a;B.value=x.dataset.b;A.dispatchEvent(new Event('change'));B.dispatchEvent(new Event('change'));draw();document.getElementById('dr-compare-title').scrollIntoView({behavior:'smooth'})}));draw()})();'''
    return (script.replace("__DEFAULT_A__", json.dumps(default_a))
            .replace("__DEFAULT_B__", json.dumps(default_b)))


CSS = r'''
body{margin:0;--display:"Source Serif 4",Georgia,serif}.room-home-nav{display:flex;justify-content:space-between;gap:1rem;align-items:center;padding:.8rem 1rem;background:#050807;color:#fff;border-bottom:1px solid #29312d;font:700 .8rem var(--agate)}.room-home-nav a{color:#e8ece8}.room-home-nav nav{display:flex;gap:1rem;flex-wrap:wrap}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.sport-header{position:relative;z-index:50;background:#080b0b;color:#fff;border-bottom:1px solid #2d3532}.sport-header .tbrow{min-height:66px;display:flex;align-items:center;gap:1rem;padding:0 max(1rem,calc((100% - 1180px)/2))}.sport-header .logo{border:0;background:none;color:#fff;font:700 1.05rem var(--agate);text-decoration:none;white-space:nowrap}.sport-header .logo em{font-style:normal;color:#c6f53c}.sport-switch,.sport-activities{display:flex;gap:.3rem;align-items:center}.sport-header .vbtn{padding:.55rem .7rem;border:1px solid transparent;border-radius:999px;color:#d7ddd8;text-decoration:none;font:700 .7rem var(--agate);text-transform:uppercase;letter-spacing:.05em}.sport-header .sport-pill[aria-pressed=true],.sport-header .vbtn[aria-current=page]{background:#c6f53c;color:#0b100d}.sport-activities{margin-left:auto}.context-search{margin-left:.5rem}.context-search input{width:190px;padding:.65rem .75rem;border:1px solid #46504b;background:#101514;color:#fff}.college-search{color:#c6f53c;font:700 .72rem var(--agate);text-decoration:none}.sport-header .navbtn{display:none;background:#c6f53c;border:0;padding:.55rem .7rem;font-weight:800}.sport-header .navdrawer{background:#0b100f;border-top:1px solid #29312d}.sport-header .navlinks{display:flex;flex-direction:column;padding:1rem}.sport-header .navlinks>.context-search{margin:.5rem 0 0}.sport-header .navlinks .finder{display:none}
.lb-decision-hero{position:relative;overflow:hidden;min-height:720px;background:radial-gradient(circle at 37% 37%,rgba(35,43,45,.33),rgba(5,7,8,0) 55%),#050708}.lb-decision-hero:before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);background-size:72px 72px}.lb-decision-hero .lb-hero-inner{position:relative;z-index:5;width:min(1130px,calc(100% - 64px));margin:auto;display:grid;grid-template-columns:1.04fr .9fr;gap:72px;padding:70px 0 110px;align-items:center}.lb-decision-hero .lb-eyebrow{margin-bottom:20px;font:700 18px var(--agate);color:#c6f53c;letter-spacing:.045em}.lb-decision-hero .lb-hero-title{font:400 clamp(56px,4.5vw,72px)/.99 Georgia,serif;letter-spacing:-.033em;margin:0;color:#f3f5ef}.lb-decision-hero .lb-hero-title span{color:#c6f53c}.lb-decision-hero .lb-hero-description{max-width:590px;margin:28px 0 0;font:19px/1.7 Georgia,serif;color:#aeb7b0}.lb-decision-hero .lb-hero-actions{display:flex;gap:18px;margin-top:32px}.lb-decision-hero .lb-btn{min-height:66px;display:inline-flex;align-items:center;justify-content:center;padding:0 28px;border-radius:8px;text-decoration:none;font:700 16px var(--agate)}.lb-decision-hero .lb-btn-primary{gap:2rem;background:#c6f53c;color:#060806}.lb-decision-hero .lb-btn-secondary{border:1px solid #727b76;color:#fff}.lb-decision-hero .lb-proof-row{display:grid;grid-template-columns:repeat(4,1fr);margin-top:38px}.lb-decision-hero .lb-proof{padding:0 12px;min-width:0}.lb-decision-hero .lb-proof:first-child{padding-left:0}.lb-decision-hero .lb-proof+.lb-proof{border-left:1px solid #ffffff2e}.lb-decision-hero .lb-proof strong{display:block;font:700 25px var(--agate)}.lb-decision-hero .lb-proof span{display:block;margin-top:6px;font:600 9px var(--agate);letter-spacing:.06em;text-transform:uppercase;color:#8e9991}.lb-feature-card{position:relative;z-index:8;border-radius:18px!important;border-top:1px solid #ffffff38!important;box-shadow:0 32px 90px #0009!important;background:linear-gradient(180deg,#111516fa,#0d1112fa)!important}.lb-edge{position:absolute;top:35px;bottom:0;width:225px;z-index:2;pointer-events:none;opacity:.32}.lb-edge-left{left:0}.lb-edge-right{right:0}.lb-mini-panel{position:relative;margin:18px 8px;padding:13px;border:1px solid #ffffff29;border-radius:5px;background:#0d111285;color:#9ba69f;font:12px var(--agate)}.lb-mini-title{margin-bottom:8px;letter-spacing:.08em}.lb-data-number{font:700 28px var(--agate);color:#c6f53c}.lb-playbook{padding:30px;color:#c6f53c;font-size:30px;word-spacing:25px}.lb-decision-hero .hp-sport-choice{display:flex;gap:.4rem;margin-bottom:1.4rem}.lb-decision-hero .hp-sport-choice a{padding:.5rem 1rem;border:1px solid #4d5952;color:#fff;text-decoration:none;font:800 .72rem var(--agate)}.lb-decision-hero .hp-sport-choice a:first-child{background:#c6f53c;color:#101410}
.hp-shell{--lime:#c6f53c;--ink:#f3f5ef;--muted:#aeb7b0;--panel:#111715;background:#080c0b;color:var(--ink);font-family:var(--text)}.hp-nav{position:relative;z-index:10;display:flex;align-items:center;justify-content:space-between;padding:.8rem max(1rem,calc((100% - 1180px)/2));background:#050807;color:#fff;border-bottom:1px solid #2b332f}.hp-mark{font:900 1rem var(--agate);letter-spacing:.08em;color:#fff}.hp-mark b{color:#c6f53c}.hp-nav nav{display:flex;gap:1.15rem;align-items:center}.hp-nav nav a{color:#e4e9e4;font:800 .72rem var(--agate);letter-spacing:.06em;text-transform:uppercase}.hp-menu{display:none;background:#c6f53c;border:0;padding:.55rem .8rem;font-weight:800}.hp-hero{display:grid;grid-template-columns:1.05fr .95fr;gap:clamp(2rem,5vw,5rem);align-items:center;padding:clamp(3rem,7vw,7rem) max(1rem,calc((100% - 1180px)/2));background:radial-gradient(circle at 76% 18%,rgba(198,245,60,.13),transparent 28%),linear-gradient(145deg,#111817,#080b0b)}.hp-copy>small,.hp-section-head>small,.hp-beat-intro>small,.hp-feature>small,.hp-board-card>small,.hp-sport-card>small{font:800 .7rem var(--agate);letter-spacing:.12em;text-transform:uppercase;color:var(--lime)}.hp-copy h1{font:800 clamp(3.5rem,7vw,6.7rem)/.87 var(--display);letter-spacing:-.055em;margin:.7rem 0 1.2rem}.hp-copy>p{max-width:620px;color:#ced5cf;font-size:clamp(1.05rem,2vw,1.28rem);line-height:1.55}.hp-sport-choice{display:flex;gap:.4rem;margin-bottom:1.5rem}.hp-sport-choice a{padding:.55rem 1rem;border:1px solid #4d5952;color:#fff;font-weight:800}.hp-sport-choice a:first-child{background:#c6f53c;color:#101410;border-color:#c6f53c}.hp-ctas{display:flex;gap:.7rem;flex-wrap:wrap;margin-top:2rem}.hp-ctas a{padding:.9rem 1.1rem;border:1px solid #79827c;color:#fff;font:800 .75rem var(--agate);text-transform:uppercase}.hp-ctas .hp-primary{background:#c6f53c;color:#101410;border-color:#c6f53c}.hp-feature{border:1px solid #3c4741;border-top:5px solid var(--c);background:#111715;padding:clamp(1rem,3vw,1.7rem);box-shadow:0 24px 70px #0008}.hp-feature-players{display:grid;grid-template-columns:1fr auto 1fr;align-items:end;gap:.5rem;margin:1rem 0}.hp-feature-players>div{min-width:0}.hp-feature-players img{display:block;width:100%;height:150px;object-fit:contain;background:linear-gradient(#1b231f,#101513)}.hp-feature-players span,.hp-feature-players b{display:block}.hp-feature-players span{font-size:.68rem;color:var(--muted);margin-top:.6rem}.hp-feature-players b{font:700 1.2rem var(--display)}.hp-feature-players i{padding-bottom:3rem;color:var(--lime);font:800 .7rem var(--agate)}.hp-feature h2{font:750 clamp(1.8rem,4vw,3rem) var(--display);margin:.8rem 0}.hp-feature>p b{color:var(--lime);font-size:1.5rem}.hp-boundary{padding:1rem;background:#e9efe6;color:#101410;margin:1rem 0;line-height:1.45}.hp-boundary strong{display:block;font:800 .7rem var(--agate);text-transform:uppercase}.hp-feature>a{color:var(--lime);font-weight:800}.hp-section{max-width:1180px;margin:auto;padding:clamp(3.2rem,6vw,5rem) 1rem;border-bottom:1px solid #29312d}.hp-section-head{display:flex;align-items:end;justify-content:space-between;gap:2rem;flex-wrap:wrap}.hp-section h2{font:750 clamp(2.2rem,5vw,4rem)/.95 var(--display);margin:.35rem 0}.hp-section-head p{color:var(--muted)}.hp-actions{display:grid;grid-template-columns:repeat(3,1fr);gap:.75rem;margin-top:1.5rem}.hp-actions a{border:1px solid #303a35;background:var(--panel);padding:1rem;color:#fff}.hp-actions b,.hp-actions span{display:block}.hp-actions span{color:var(--muted);margin-top:.35rem;font-size:.85rem}.hp-board{max-width:none;padding-left:max(1rem,calc((100% - 1180px)/2));padding-right:max(1rem,calc((100% - 1180px)/2));background:#0d1211}.hp-board-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:.7rem;margin-top:1.5rem}.hp-board-card{padding:1rem;background:#151c19;border:1px solid #303b35;color:#fff;min-height:125px}.hp-board-card h3{font:700 1.15rem var(--display);margin:.55rem 0}.hp-board-card i{color:var(--muted);font-size:.75rem}.hp-board-card p{color:var(--muted);font-size:.82rem;line-height:1.4}.hp-sport-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:1.5rem}.hp-sport-card{display:block;min-height:230px;padding:clamp(1.3rem,3vw,2rem);border:1px solid #39433e;color:#fff;background:#121816}.hp-sport-card h3{font:750 clamp(1.8rem,4vw,3.1rem)/1 var(--display);margin:.7rem 0}.hp-sport-card p{font-size:1.08rem}.hp-sport-card span{display:block;color:var(--muted);line-height:1.5}.hp-sport-card b{display:block;margin-top:1.5rem;color:var(--lime)}.hp-college{background:linear-gradient(145deg,#13221e,#0d1412);border-top:5px solid #6de0bd}.hp-beat-intro{padding-bottom:1.5rem}.hp-beat-intro p{color:var(--muted);max-width:700px}.hp-beat-intro>a{color:var(--lime);font-weight:800}
#decision-room{--dr-bg:#080c0c;--dr-panel:#101615;--dr-line:#29312d;--dr-lime:#c6f53c;--dr-ink:#f3f5ef;--dr-muted:#aab2ac;color:var(--dr-ink);background:var(--dr-bg)}
.dr-sports{position:relative;z-index:5;display:flex;justify-content:center;gap:.35rem;padding:.7rem;background:#050807;border-bottom:1px solid #29312d}.dr-sports a{min-width:110px;padding:.7rem 1rem;text-align:center;color:#d8ddd8;border:1px solid #46504b;font:800 .75rem var(--agate);letter-spacing:.1em;text-transform:uppercase}.dr-sports a[aria-pressed=true]{background:#c6f53c;color:#101410;border-color:#c6f53c}.cdr{--dr-bg:#080c0c;--dr-panel:#101615;--dr-line:#29312d;--dr-lime:#c6f53c;--dr-ink:#f3f5ef;--dr-muted:#aab2ac;color:var(--dr-ink);background:var(--dr-bg)}.cdr-filters{display:grid;grid-template-columns:1fr 2fr;gap:1rem;margin:1.25rem 0}.cdr input[type=search]{display:block;width:100%;box-sizing:border-box;margin-top:.45rem;padding:.85rem;background:#0b100f;color:var(--dr-ink);border:1px solid #46504b;font:600 1rem var(--text)}.cdr-crest{position:absolute;right:1rem;top:1rem;display:grid;place-items:center;width:76px;height:76px;padding:.4rem;border:1px solid var(--team-accent,#c6f53c);border-radius:12px;background:#0d1211}.cdr-crest .cdr-logo-wrap,.cdr-crest img{display:grid;place-items:center;width:100%;height:100%;object-fit:contain}.cdr-logo-wrap b{font:800 .75rem var(--agate);color:var(--dr-lime)}.cdr .dr-person{border-color:var(--team-accent,var(--dr-lime))}.cdr-mini-team,.cdr-selector-team{display:flex;align-items:center;gap:.45rem}.cdr-selector-team{min-height:2rem;margin-top:.35rem;color:var(--dr-ink);font:600 .85rem var(--text);text-transform:none;letter-spacing:0}.cdr-mini-logo{width:1.6rem;height:1.6rem;object-fit:contain;flex:none}.hp-nfl-actions{grid-template-columns:repeat(4,1fr)}
.dr-shell{font-family:var(--text);padding-bottom:5rem}.dr-hero{padding:clamp(2rem,4vw,3.75rem) max(1rem,calc((100% - 1180px)/2));background:radial-gradient(circle at 82% 8%,rgba(198,245,60,.13),transparent 31%),linear-gradient(145deg,#111817,#080b0b);border-bottom:1px solid var(--dr-line)}
.dr-kicker,.dr-mode,.dr-section small,.dr-compare small,.dr-empty small{font:800 .72rem/1.2 var(--agate);letter-spacing:.13em;text-transform:uppercase}.dr-kicker{color:var(--dr-lime)}.dr-mode{display:inline-block;margin:.65rem 0 .8rem;padding:.45rem .7rem;border:1px solid #52641f;background:#17200d}.dr-hero>h1{max-width:850px;margin:.3rem 0 .65rem;font:700 clamp(2.35rem,5vw,4.5rem)/.94 var(--display);letter-spacing:-.04em}.dr-lede{max-width:720px;font-size:clamp(1rem,1.6vw,1.18rem);color:#d7ddd7}.dr-week-note{max-width:760px;color:var(--dr-muted);border-left:3px solid var(--dr-lime);padding-left:1rem}.dr-beat-link{display:inline-block;margin-top:.4rem;color:var(--dr-ink);font:800 .72rem var(--agate);letter-spacing:.08em;text-transform:uppercase}
.dr-compare{margin-top:2.4rem;border:1px solid var(--dr-line);border-top:4px solid var(--dr-lime);background:rgba(8,12,12,.92);padding:clamp(1rem,3vw,2rem)}.dr-compare-head,.dr-section-head{display:flex;justify-content:space-between;gap:2rem;align-items:end}.dr-compare h2,.dr-section h2,.dr-empty h2{font:700 clamp(1.8rem,4vw,3rem)/1 var(--display);margin:.25rem 0}.dr-compare label{font:700 .72rem var(--agate);letter-spacing:.08em;text-transform:uppercase;color:var(--dr-muted)}.dr-compare select{display:block;width:100%;margin-top:.45rem;padding:.85rem;background:#0b100f;color:var(--dr-ink);border:1px solid #46504b;font:600 1rem var(--text)}.dr-selectors{display:grid;grid-template-columns:1fr auto 1fr;gap:1rem;align-items:end;margin:1.4rem 0}.dr-selectors>b{color:var(--dr-lime);padding-bottom:1rem}
.dr-picker{position:relative}.dr-picker input[type=search],.dr-picker ul{display:none}.dr-enhanced .dr-picker input[type=search]{display:block;width:100%;margin-top:.45rem;padding:.85rem;background:#0b100f;color:var(--dr-ink);border:1px solid #46504b;font:600 1rem var(--text)}.dr-picker input:focus{outline:3px solid var(--dr-lime);outline-offset:2px}.dr-enhanced .dr-picker ul:not([hidden]){display:block;position:absolute;z-index:20;left:0;right:0;max-height:280px;overflow:auto;margin:2px 0 0;padding:0;list-style:none;background:#101615;border:1px solid #67716c;box-shadow:0 12px 30px #000}.dr-picker li{display:flex;justify-content:space-between;gap:1rem;padding:.75rem;cursor:pointer;text-transform:none;letter-spacing:0;color:var(--dr-ink)}.dr-picker li[aria-selected=true],.dr-picker li:hover{background:#26331d}.dr-picker li small{color:var(--dr-muted)}.dr-enhanced .dr-native{position:absolute!important;width:1px!important;height:1px!important;overflow:hidden!important;clip:rect(0 0 0 0)!important;white-space:nowrap!important}.dr-cross{display:flex!important;align-items:center;gap:.55rem;margin:-.5rem 0 1.25rem}.dr-cross input{width:1.15rem;height:1.15rem;accent-color:var(--dr-lime)}
.dr-verdict{display:flex;justify-content:space-between;gap:2rem;align-items:center;padding:1.3rem;border:1px solid #52641f;background:#131b0e}.dr-verdict small{color:var(--dr-lime)}.dr-verdict h2{font-size:clamp(2rem,5vw,4rem)}.dr-verdict p{max-width:720px;margin:.5rem 0;color:#d8ddd8}.dr-adv{text-align:center;min-width:150px}.dr-adv b{display:block;font:700 3rem var(--display);color:var(--dr-lime)}.dr-adv span{font:700 .67rem var(--agate);text-transform:uppercase;color:var(--dr-muted)}
.dr-player-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:1rem}.dr-player-grid>article{border:1px solid var(--dr-line);background:var(--dr-panel);padding:1rem}.dr-person{height:130px;position:relative;display:flex;align-items:end;overflow:hidden;border-bottom:3px solid var(--team)}.dr-person>div{position:relative;z-index:2;padding:.8rem}.dr-person h3{font:700 clamp(1.5rem,3vw,2.4rem)/1 var(--display);margin:.2rem 0}.dr-photo{position:absolute;right:0;bottom:0;height:125px;max-width:48%;object-fit:contain;z-index:1}.dr-logo{position:absolute;right:32%;top:10px;width:100px;opacity:.1}.dr-player-grid dl{display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem;margin:1rem 0 0}.dr-player-grid dl div{background:#171d1b;padding:.7rem}.dr-player-grid dt{font:700 .64rem var(--agate);color:var(--dr-muted);text-transform:uppercase}.dr-player-grid dd{margin:.3rem 0 0;font:700 1.2rem var(--display)}
.dr-boundary{margin-top:1rem;padding:clamp(1rem,3vw,2rem);background:#e9efe6;color:#101410}.dr-boundary-title small{color:#52630f}.dr-boundary-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:#aeb8aa;border:1px solid #aeb8aa;margin-top:1rem}.dr-boundary-grid article{background:#f7faf5;padding:1rem}.dr-boundary-grid b{display:block;font:700 1.7rem var(--display)}.dr-boundary-grid span{display:block;margin-top:.5rem;font-size:.9rem}.dr-stamp{font:700 .68rem var(--agate);color:var(--dr-muted);text-transform:uppercase}.dr-error{padding:1rem;background:#301515;color:#ffd7d7}
.dr-evidence,.dr-cases,.dr-quality{margin-top:1rem;padding:clamp(1rem,3vw,2rem);border:1px solid var(--dr-line);background:#0e1412}.dr-evidence-title{display:flex;align-items:end;justify-content:space-between;gap:1.5rem}.dr-evidence-title small{color:var(--dr-lime)}.dr-evidence-title h2{margin:.25rem 0;font:700 clamp(1.6rem,3vw,2.5rem)/1 var(--display)}.dr-evidence-title p{max-width:590px;color:var(--dr-muted)}.dr-why-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;margin-top:1rem;background:var(--dr-line);border:1px solid var(--dr-line)}.dr-why-grid article,.dr-case-grid article{min-width:0;padding:1rem;background:#151c19}.dr-why-grid h3,.dr-case-grid h3{margin:0 0 .5rem;font:700 1rem var(--agate);color:var(--dr-lime)}.dr-why-grid p,.dr-case-grid li{color:#d4dad5;font-size:.9rem;line-height:1.5}.dr-case-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:1rem}.dr-case-grid ul{margin:.5rem 0 0;padding-left:1.1rem}.dr-quality-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:.5rem;margin-top:1rem}.dr-quality-grid span{min-width:0;padding:.7rem;border:1px solid var(--dr-line);color:var(--dr-muted);font:700 .66rem var(--agate);text-transform:uppercase}.dr-quality-grid b{display:block;margin-bottom:.35rem;color:var(--dr-ink);font-size:.7rem}.dr-quality-grid .present{border-color:#536925}.dr-quality-grid .unavailable{border-color:#63453d}.dr-quality-grid .historical{border-color:#6b6335}
.dr-section{max-width:1180px;margin:0 auto;padding:clamp(3.5rem,7vw,6rem) 1rem;border-bottom:1px solid var(--dr-line)}.dr-section-head>p{max-width:500px;color:var(--dr-muted)}.dr-card-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:1.5rem}.dr-mini,.dr-signal,.dr-mover,.dr-empty{border:1px solid var(--dr-line);background:var(--dr-panel);padding:1.1rem}.dr-mini-pair{display:flex;flex-direction:column;font:700 1.35rem var(--display)}.dr-mini-pair i{font:700 .65rem var(--agate);color:var(--dr-lime);text-transform:uppercase}.dr-mini p,.dr-signal p,.dr-mover p{color:var(--dr-muted);font-size:.9rem}.dr-market{min-height:1.2em}.dr-open{border:0;background:var(--dr-lime);color:#101410;padding:.65rem 1rem;font:800 .72rem var(--agate);text-transform:uppercase;cursor:pointer}.dr-convictions{max-width:none;padding-left:max(1rem,calc((100% - 1180px)/2));padding-right:max(1rem,calc((100% - 1180px)/2));background:#0d1211}.dr-signal-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:1.5rem}.dr-signal{display:grid;grid-template-columns:70px 1fr;gap:1rem}.dr-signal img{width:70px;height:70px;object-fit:contain}.dr-signal h3,.dr-mover h3{font:700 1.5rem var(--display);margin:.2rem 0}.dr-mover-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:1.5rem}.dr-mover>div{display:flex;gap:.5rem;flex-wrap:wrap}.dr-mover>div span{background:#1a211e;padding:.4rem;font-size:.78rem}.dr-mover b{color:var(--dr-lime)}
.dr-fades-head{margin-top:3rem}
.dr-future-grid{max-width:1180px;margin:0 auto;padding:clamp(3.5rem,7vw,6rem) 1rem;display:grid;grid-template-columns:1fr 1fr;gap:1rem}.dr-empty{min-height:220px}.dr-empty p{color:var(--dr-muted);max-width:520px}.dr-empty button,.dr-empty-line{margin-top:1.2rem;padding:.8rem;border:1px dashed #56605b;background:transparent;color:var(--dr-muted)}.dr-tools{max-width:1180px;margin:auto;padding:1.2rem 1rem;border-top:1px solid var(--dr-line);display:flex;gap:1.2rem;flex-wrap:wrap}.dr-tools span{color:var(--dr-muted)}.dr-tools a{color:var(--dr-ink)}
.dr-news .lb-wire-card{display:block;max-width:760px;margin:1.5rem 0 0}.dr-news .lb-wire-feed{min-height:180px}
.hp-section-head p{color:#c5ccc6;font-size:1rem;line-height:1.6}.hp-actions a{border-color:#3b4741;padding:1.05rem;line-height:1.45}.hp-actions span{color:#c5ccc6;font-size:.92rem;line-height:1.5}.hp-board-card{border-color:#39463f;line-height:1.45}.hp-board-card small{font-size:.72rem}.hp-board-card i{color:#c0c8c1;font-size:.8rem}.hp-board-card p{color:#cbd2cc;font-size:.9rem;line-height:1.5}
.hp-experience[hidden]{display:none}.hp-experience{background:#080c0b}.hp-experience .lb-decision-hero{min-height:650px}.hp-experience .lb-hero-inner{padding:64px 0 78px}.hp-experience .lb-hero-title{display:block;max-width:650px;font-size:clamp(50px,5vw,76px);line-height:.98}.hp-experience .lb-hero-description{font-family:var(--agate);font-size:1.15rem;line-height:1.55;color:#cbd2cc}.hp-college-hero{background:radial-gradient(circle at 72% 20%,rgba(198,245,60,.1),transparent 32%),#050708}.hp-college-feature .hp-feature-players>div>img{height:128px;padding:12px}.hp-college-feature .hp-feature-players em{display:block;margin-top:.35rem;color:var(--lime);font:700 .8rem var(--agate);font-style:normal}.hp-feature-players>div{position:relative}.hp-feature-players .hp-team-mark{position:absolute;right:6px;top:6px;width:38px;height:38px;padding:4px;background:#0b100fd9;object-fit:contain;border-radius:7px}.hp-card-cta{display:inline-flex;padding:.7rem 0;text-decoration:none}.hp-section-head{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,420px);align-items:end}.hp-section-head small,.hp-section-head h2{grid-column:1}.hp-section-head p{grid-column:2;grid-row:1/3;margin:0}.hp-action-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-top:2rem}.hp-action{display:flex;gap:1rem;min-height:180px;padding:1.4rem;border:1px solid #354039;background:linear-gradient(145deg,#151b18,#0d1210);color:#fff;text-decoration:none;box-shadow:0 12px 30px #0003;transition:transform .18s,border-color .18s}.hp-action:hover,.hp-action:focus-visible{transform:translateY(-4px);border-color:var(--lime);outline:none}.hp-action-icon{display:grid;place-items:center;width:42px;height:42px;flex:none;border:1px solid #56654b;color:var(--lime);font:800 1.15rem var(--data)}.hp-action h3{margin:.1rem 0 .65rem;font:700 1.35rem var(--agate);text-transform:uppercase;letter-spacing:.02em}.hp-action p{margin:0;color:#bac4bc;font:1rem/1.5 var(--agate)}.hp-action b{display:block;margin-top:1rem;color:var(--lime);font:700 .78rem var(--agate);text-transform:uppercase}.hp-difference{max-width:none;padding-left:max(1rem,calc((100% - 1180px)/2));padding-right:max(1rem,calc((100% - 1180px)/2));background:#0d1211}.hp-signal-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-top:2rem}.hp-signal-card{position:relative;display:block;min-width:0;padding:1.2rem;border:1px solid #354039;background:linear-gradient(180deg,#171e1a,#101411);color:#fff;text-decoration:none;box-shadow:0 18px 35px #0004}.hp-signal-card:hover,.hp-signal-card:focus-visible{border-color:var(--lime);outline:none}.hp-signal-card>small{display:block;margin:1rem 0 .45rem;color:var(--lime);font:800 .68rem var(--agate);letter-spacing:.1em}.hp-signal-card>p{color:#c4ccc6;font:1rem/1.45 var(--agate)}.hp-signal-card>strong{font:700 1rem var(--data)}.hp-identity{display:grid;grid-template-columns:64px 24px 1fr;align-items:end;gap:.45rem;min-width:0}.hp-id-art{width:64px;height:72px;object-fit:contain;background:#0b100f}.hp-id-logo{width:24px;height:24px;object-fit:contain}.hp-identity b,.hp-identity span{display:block}.hp-identity b{font:700 1.05rem var(--agate);line-height:1.05}.hp-identity span{margin-top:.25rem;color:#aeb8b0;font:600 .75rem var(--agate)}.hp-call-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:2rem}.hp-call-card{display:block;padding:1.1rem;border:1px solid #354039;background:#111715;color:#fff;text-decoration:none}.hp-call-card:hover,.hp-call-card:focus-visible{border-color:var(--lime);outline:none}.hp-call-art{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:.55rem}.hp-call-art>.hp-identity{grid-template-columns:48px 1fr}.hp-call-art>.hp-identity .hp-id-art{width:48px;height:58px}.hp-call-art>.hp-identity .hp-id-logo{display:none}.hp-call-art>span{color:var(--lime);font:800 .67rem var(--agate)}.hp-call-data{display:grid;grid-template-columns:1fr auto;margin-top:1rem;padding-top:1rem;border-top:1px solid #303a35;gap:.45rem}.hp-call-data small{grid-column:1/3;color:#9eaaa1;font:700 .7rem var(--agate);text-transform:uppercase}.hp-call-data b{font:700 1.05rem var(--data)}.hp-call-data span{color:var(--lime);font:700 .75rem var(--agate)}.hp-mover-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:2rem}.hp-mover-card{padding:1.35rem;border:1px solid #354039;background:linear-gradient(145deg,#151b18,#0e1311)}.hp-mover-card small{color:var(--lime);font:700 .7rem var(--agate);text-transform:uppercase}.hp-mover-card h3{font:700 1.45rem var(--agate);margin:.5rem 0 1rem}.hp-mover-card>div{display:grid;grid-template-columns:repeat(3,1fr);gap:.4rem}.hp-mover-card>div span{padding:.7rem .35rem;background:#0a0f0d;color:#aeb8b0;text-align:center;font:600 .72rem var(--agate)}.hp-mover-card>div b{display:block;margin-top:.25rem;color:#fff;font:700 .95rem var(--data)}.hp-mover-card p{color:#bac3bc;font:1rem/1.45 var(--agate)}
.hp-college-identity{grid-template-columns:64px 1fr}
@media(max-width:900px){.hp-board-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:1000px){.lb-edge{display:none}.lb-decision-hero .lb-hero-inner{width:min(920px,calc(100% - 32px));gap:32px}.sport-activities{display:none}.sport-header .navtoggle{display:block;margin-left:auto}}
@media(max-width:900px){.hp-action-grid,.hp-signal-grid{grid-template-columns:1fr 1fr}.hp-call-grid{grid-template-columns:1fr}.hp-call-art>.hp-identity{grid-template-columns:60px 1fr}.hp-call-art>.hp-identity .hp-id-art{width:60px}}
@media(max-width:780px){.sport-header .tbrow{min-height:60px}.sport-header>.tbrow>.context-search{display:none}.sport-header .navdrawer:not([hidden]){display:block}.sport-switch .vbtn{padding:.45rem .55rem}.lb-decision-hero{min-height:0}.lb-decision-hero .lb-hero-inner{grid-template-columns:1fr;padding:42px 0 54px;width:min(100% - 32px,580px);gap:34px}.hp-experience .lb-hero-title{font-size:clamp(42px,12vw,58px)}.lb-decision-hero .lb-hero-description{font-size:1.05rem;margin-top:20px}.lb-decision-hero .lb-hero-actions{flex-direction:column;align-items:stretch;margin-top:24px}.lb-decision-hero .lb-btn{min-height:54px}.lb-feature-card{margin:0!important}.hp-section{padding:3.2rem 1rem}.hp-section-head{display:block}.hp-section-head p{margin-top:1rem}.hp-sport-grid{grid-template-columns:1fr}.hp-actions{grid-template-columns:1fr 1fr}.dr-hero{padding-top:4rem}.dr-compare-head,.dr-section-head,.dr-verdict,.dr-evidence-title{align-items:stretch;flex-direction:column}.dr-selectors,.cdr-filters{grid-template-columns:1fr}.dr-selectors>b{text-align:center;padding:0}.dr-player-grid,.dr-future-grid,.dr-case-grid{grid-template-columns:1fr}.dr-boundary-grid,.dr-why-grid{grid-template-columns:1fr 1fr}.dr-quality-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.dr-card-grid,.dr-signal-grid,.dr-mover-grid{grid-template-columns:1fr}.dr-player-grid dl{grid-template-columns:1fr 1fr}.dr-adv{text-align:left}.dr-photo{max-width:44%}}
@media(max-width:520px){.hp-actions,.hp-board-grid,.hp-action-grid,.hp-signal-grid,.hp-mover-grid{grid-template-columns:1fr}.hp-feature-players img{height:105px}.hp-feature-players .hp-team-mark{width:32px;height:32px}.hp-action{min-height:0}.hp-call-art{grid-template-columns:1fr}.hp-call-art>span{text-align:center}.hp-call-data{grid-template-columns:1fr}.hp-call-data small{grid-column:auto}.hp-mover-card>div{grid-template-columns:1fr 1fr 1fr}}
@media(max-width:430px){.dr-boundary-grid,.dr-player-grid dl,.dr-why-grid{grid-template-columns:1fr}.dr-hero>h1{font-size:clamp(2.65rem,15vw,3.35rem)}.dr-person{height:115px}.dr-photo{height:110px}}
.hp-action-grid{grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
.dr-empty-link{display:inline-block;margin-top:1.2rem;padding:.8rem;border:1px solid var(--dr-lime);color:var(--dr-lime);font:800 .72rem var(--agate);text-transform:uppercase}
'''

WIRE_PAGE_CSS = r'''
body{margin:0;background:#f4f5f0;color:#151916;font-family:Arial,sans-serif}.rw-head{padding:1.25rem max(1rem,calc((100% - 1100px)/2));background:#0b100f;color:#fff;border-bottom:4px solid #c6f53c}.rw-head a{color:#c6f53c;font-weight:800}.rw-head h1{font-size:clamp(2.2rem,7vw,4.8rem);margin:.8rem 0 .35rem}.rw-head p{max-width:700px;color:#d7ddd8}.rw-main{max-width:1100px;margin:auto;padding:1rem}.rw-back{display:inline-block;margin-bottom:1rem}
#wire .shead h2,#wire .sub{color:#151916}#wire .wfilters button,#wire .wfilters select{background:#fff;color:#151916;border-color:#68716b}#wire .tile{background:#101513;color:#f2f4ef;border:1px solid #303833;border-left:5px solid var(--c1,#68716b);padding:1rem 7rem 1rem 1rem;border-radius:8px}#wire .tile:hover{background:#151b18}#wire .wplayer,#wire .wpos{color:#fff}#wire .wdate{color:#d9ded9}#wire .wrep,#wire .wimp{color:#f0f3ee}#wire .wsrc,#wire .wsrc a{color:#b9c1ba}#wire .wsrc a:hover{color:#c6f53c}
@media(max-width:600px){.rw-head{padding-top:2rem}.rw-main{padding:.65rem}#wire .tile{padding-right:5.8rem}}
'''


def split_wire(page: str) -> tuple[str, str]:
    """Preserve the complete reviewed Wire in its unlisted archive only."""
    if WIRE_START not in page or WIRE_END not in page:
        raise SystemExit("reviewed Wire replacement is missing")
    start = page.index(WIRE_START)
    end = page.index(WIRE_END, start) + len(WIRE_END)
    complete = page[start:end]
    if not re.search(r'<article class="tile wire"', complete):
        raise SystemExit("reviewed Wire archive has no cards")
    return page[:start] + page[end:], complete


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
{seo.site_nav(None, "nfl")}<header class="rw-head"><p>Unlisted internal archive</p><h1>Reviewed Wire Archive</h1><p>Preserved human-approved fantasy-relevant updates from trusted sources. Filter by team or position.</p></header>
<main class="rw-main">{complete}</main>{seo.site_footer()}</body></html>'''
    target.write_text(document)
    return target


def write_decision_pages(homepage: Path, payload: dict, source_page: str) -> tuple[Path, Path]:
    head_style = re.search(r'<style id="decision-room-css">.*?</style>', source_page, re.S)
    if head_style is None:
        head_style_text = f'<style id="decision-room-css">{CSS}</style>'
    else:
        head_style_text = head_style.group(0)
    block = render(payload)
    nfl_nav = sport_header("nfl", "decision", payload["players"])
    base = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><meta name="description" content="Compare validated fantasy football projections and see what changes the pick.">''' + head_style_text + '''</head><body>''' + nfl_nav + block + seo.site_footer() + '''</body></html>'''
    nfl = homepage.parent / "decision-room" / "nfl" / "index.html"
    college = homepage.parent / "decision-room" / "college" / "index.html"
    nfl.parent.mkdir(parents=True, exist_ok=True)
    college.parent.mkdir(parents=True, exist_ok=True)
    nfl.write_text(base.replace("</head>", "<title>2026 NFL Week 1 Decision Room | Lineup Beat</title></head>", 1))
    college_block = (college_decision_room.SHELL
                     + f'<script>{college_decision_room.JS}</script>')
    college_doc = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><meta name="description" content="Compare validated 2026 College Week 1 fantasy projections and see what changes the pick."><title>College Week 1 Decision Room | Lineup Beat</title>''' + head_style_text + '''</head><body data-default-sport="college">''' + sport_header("college", "decision") + college_block + seo.site_footer() + '''</body></html>'''
    college.write_text(college_doc)
    return nfl, college


def update_metadata(page: str) -> str:
    title = "Fantasy Football Decisions for NFL &amp; College | Lineup Beat"
    description = ("Compare NFL and college fantasy players, find projection-versus-market "
                   "disagreements, and see the boundaries that change each decision.")
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


def remove_original_header(page: str) -> str:
    for opening in ('<header class="topbar">',
                    '<header class="topbar sport-header"'):
        while opening in page:
            start = page.find(opening)
            end = page.find('</header>', start)
            if end < 0:
                raise SystemExit("production header boundary is malformed")
            page = page[:start] + page[end + len('</header>'):]
    return page


def clean_home_document(page: str, homepage: str) -> str:
    """Keep the production visual head, but ship only the decision homepage.

    The original homepage application used to remain after the replacement
    block.  Its hidden roster views and old navigation were no longer visible,
    but they still shipped as executable DOM-building code.  A product-shell
    replacement must remove that application rather than merely cover it.
    """
    head_end = page.find("</head>")
    if head_end < 0:
        raise SystemExit("homepage head boundary is missing")
    head = page[:head_end + len("</head>")]
    banner = re.search(r'<div id="lb-dev-banner".*?</div>', page, re.S)
    development_banner = "\n" + banner.group(0) if banner else ""
    return (head + '<body data-default-sport="nfl">' + development_banner
            + homepage + seo.site_footer() + "</body></html>")


def inject(path: Path) -> None:
    payload = decision_data.load_weekly(2026, 1)
    college_payload = college_decision_data.load_weekly()
    page = path.read_text()
    if "<body" not in page or "</head>" not in page:
        raise SystemExit("refusing to modify malformed homepage")
    archive = path.parent / "decision-room" / "reviewed-wire" / "index.html"
    if WIRE_START in page and WIRE_END in page:
        page, homepage_wire = split_wire(page)
        complete_wire = homepage_wire
        if archive.exists():
            archived = archive.read_text()
            begin = archived.find(WIRE_START)
            finish = archived.find(WIRE_END, begin)
            if begin >= 0 and finish >= 0:
                archived_wire = archived[begin:finish + len(WIRE_END)]
                if archived_wire.count('class="tile wire"') > complete_wire.count('class="tile wire"'):
                    complete_wire = archived_wire
    else:
        if not archive.exists():
            raise SystemExit("reviewed Wire is absent from both homepage and archive")
        archived = archive.read_text()
        begin = archived.find(WIRE_START)
        finish = archived.find(WIRE_END, begin)
        if begin < 0 or finish < 0:
            raise SystemExit("reviewed Wire archive boundary is missing")
        complete_wire = archived[begin:finish + len(WIRE_END)]
    page = remove_original_header(page)
    wire_page = write_wire_page(path, complete_wire, page)
    college_path = path.parent / "data" / "decision-room-college.json"
    college_path.parent.mkdir(parents=True, exist_ok=True)
    college_path.write_text(json.dumps(college_payload, separators=(",", ":")) + "\n")
    decision_style = f'<style id="decision-room-css">{CSS}</style>'
    if 'id="decision-room-css"' not in page:
        page = page.replace("</head>", decision_style + "\n</head>", 1)
    nfl_page, college_page = write_decision_pages(path, payload, page)
    block = render_home(payload, college_payload)
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
    page = clean_home_document(page, block)
    path.write_text(page)
    print(f"built 2026 Week 1 Decision Room with {len(payload['players'])} active players in {path}")
    print(f"built complete reviewed Wire in {wire_page}")
    print(f"built NFL Decision Room in {nfl_page}")
    print(f"built College Decision Room in {college_page}")
    print(f"built isolated College Decision Room payload with {len(college_payload['players'])} players in {college_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--homepage", type=Path, default=Path("site/index.html"))
    args = parser.parse_args()
    inject(args.homepage)


if __name__ == "__main__":
    main()
