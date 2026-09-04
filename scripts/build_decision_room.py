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
                             compare, confidence, scoring_movers,
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


def gap_label(value: float) -> str:
    """Keep sub-tenth closest calls from looking like exact zeroes."""
    return "&lt;0.1" if abs(float(value)) < 0.1 else f"{float(value):.1f}"


def opportunity_value(player: dict, reception_key: str) -> float:
    opportunity = player.get("expected_opportunity", {})
    if player.get("position") == "QB":
        return float(opportunity.get("pass_attempts") or 0)
    return float(opportunity.get("carries") or 0) + float(
        opportunity.get(reception_key) or 0)


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
        · {gap_label(result['gap'])}-point gap · {esc(result['confidence'])}</p>
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
      <p>{esc(recommendation)} · {gap_label(result['gap'])}-point edge · {esc(result['confidence'])}</p></a>'''


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
                  f'data-player-search '
                  f'placeholder="Search NFL players" aria-label="Search NFL players">'
                  f'<datalist id="site-player-list">{options}</datalist>')
    return seo.site_nav(activity if activity != "home" else None, sport, search)


def home_header() -> str:
    """A neutral root header: the homepage belongs to the brand, not a sport."""
    desktop = "".join((
        '<a class="vbtn" href="/nfl/data/">NFL</a>',
        '<a class="vbtn" href="/decision-room/college/">College</a>',
        '<a class="vbtn" href="/my-team/">My Team</a>',
        '<a class="vbtn" href="/my-league/">My League</a>',
        '<a class="vbtn" href="/about/">About</a>',
    ))
    mobile = "".join((
        '<a class="navlink" href="/nfl/data/">NFL</a>',
        '<a class="navlink" href="/decision-room/college/">College</a>',
        '<a class="navlink" href="/decision-room/nfl/">NFL Decision Room</a>',
        '<a class="navlink" href="/decision-room/college/">College Decision Room</a>',
        '<a class="navlink" href="/my-team/">My Team</a>',
        '<a class="navlink" href="/my-league/">My League</a>',
        '<a class="navlink" href="/about/">About</a>',
    ))
    return (
        f'<style id="shared-shell-css">{seo.SHELL_CSS}{seo.TEAMS_CSS}{seo.NAV_CSS}</style>\n'
        '<header class="topbar home-topbar">\n'
        '  <div class="wrap tbrow">\n'
        '    <a class="logo" href="/" aria-current="page">Lineup<em>Beat</em></a>\n'
        f'    <nav class="views" aria-label="Explore LineupBeat">{desktop}</nav>\n'
        '    <a class="home-nav-cta" href="#featured-decisions">Today\'s decisions</a>\n'
        '    <button class="navbtn navtoggle" type="button" aria-expanded="false" '
        'aria-controls="navdrawer"><svg viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
        '<path class="bar bar1" d="M3 7h18"></path><path class="bar bar2" d="M3 12h18"></path>'
        '<path class="bar bar3" d="M3 17h18"></path></svg>Menu</button>\n'
        '  </div>\n'
        '  <div class="navdrawer" id="navdrawer" hidden>\n'
        f'    <nav class="navlinks" aria-label="All sections">{mobile}</nav>\n'
        '  </div>\n'
        '</header>' + seo.NAV_JS + '<script defer src="/feedback.js"></script>'
    )


def render_home(payload: dict, college_payload: dict) -> str:
    """Render the sport-neutral Lineup Beat homepage."""
    players = payload["players"]
    for p in players:
        p["team_color"] = build_comparison_tool.TEAM_COLORS.get(
            p["team"], ("#263238", "#c6f53c"))[0]
    by_name = {p["name"]: p for p in players}
    try:
        featured_a = by_name["Tony Pollard"]
        featured_b = by_name["Rico Dowdle"]
    except KeyError as exc:
        raise ValueError(f"homepage feature identity is unavailable: {exc}") from exc
    if not all(p.get("photo") and p.get("team_logo")
               for p in (featured_a, featured_b)):
        raise ValueError("homepage feature art is incomplete")
    featured_a_format = featured_a["formats"]["half_ppr"]
    featured_b_format = featured_b["formats"]["half_ppr"]
    nfl_result = compare(featured_a, featured_b, DecisionContext(
        "weekly", payload["season"], "half_ppr", payload["week"]))
    recommendation_state = payload.get("recommendation_state", {})
    cp = {p["id"]: p for p in college_payload["players"]}
    college_feature = next(
        r for r in college_payload["strongest_edges"]
        if r["confidence"] in {"Lean", "Edge"}
        and cp[r["a"]].get("player_market", {}).get("components")
        and cp[r["b"]].get("player_market", {}).get("components"))
    cw = cp[college_feature["winner"]]
    cr_id = college_feature["b"] if college_feature["winner"] == college_feature["a"] else college_feature["a"]
    cr = cp[cr_id]
    cf = cw["formats"]["yahoo"]
    crf = cr["formats"]["yahoo"]

    def action(href: str, icon: str, title: str, copy: str) -> str:
        return (f'<a class="hp-action" href="{href}"><span class="hp-action-icon" aria-hidden="true">{icon}</span>'
                f'<div><h3>{title}</h3><p>{copy}</p><b>Open →</b></div></a>')
    def matchup_tone(player: dict) -> str:
        factor = player["matchup"]["projection_factor"]
        if factor >= 1.05:
            return "favorable"
        if factor <= .95:
            return "difficult"
        return "near neutral"

    a_volume = opportunity_value(featured_a, "targets")
    b_volume = opportunity_value(featured_b, "targets")
    college_a_volume = opportunity_value(cw, "receptions")
    college_b_volume = opportunity_value(cr, "receptions")
    college_a_market = college_payload["market_context_by_team"][cw["team_id"]]
    college_b_market = college_payload["market_context_by_team"][cr["team_id"]]

    def spread_label(value: float) -> str:
        return f"+{value:.1f}" if value > 0 else f"{value:.1f}"
    def component_label(value: str) -> str:
        return value.replace("_", " ")
    if recommendation_state.get("enabled") is False:
        nfl_badge = "PROJECTION COMPARISON"
        nfl_call = ("No clear projection edge" if nfl_result["no_clear_edge"] else
                    f"Projection favors {nfl_result['winner']['name']}")
        nfl_summary = recommendation_state.get("reason", "Week 1 recommendations are disabled.")
        nfl_boundary = ("A lineup recommendation requires a qualifying validation result "
                        "and current injury evidence. Until then, use the complete "
                        "comparison as research—not an automatic start/sit instruction.")
    elif nfl_result["no_clear_edge"]:
        nfl_badge = "NO CLEAR EDGE"
        nfl_call = "No clear edge"
        nfl_summary = (f"The {nfl_result['gap']:.1f}-point projection difference is "
                       "inside the deterministic weekly no-call band.")
        nfl_boundary = (f"A displayed gap above {nfl_result['meaningful_gap_to_call'] - .1:.1f} "
                        "points is required before this becomes a Lean.")
    else:
        nfl_badge = f"EVIDENCE {nfl_result['confidence'].upper()}"
        nfl_call = nfl_result["winner"]["name"]
        nfl_summary = (f"The validated projection is {nfl_result['gap']:.1f} points "
                       f"higher for {nfl_call}; supporting context remains visible below.")
        nfl_boundary = (f"A {nfl_result['runner_up_gain_to_flip']:.1f}-point projection swing "
                        f"moves {nfl_result['runner_up']['name']} ahead.")
    nfl_feature = f'''<article class="hp-feature lb-feature-card hp-nfl-feature" style="--c:{esc(featured_a['team_color'])}"><small>NFL evidence case · Week 1 · Half-PPR</small><div class="hp-feature-players"><div><img src="{esc(featured_a['photo'])}" alt="{esc(featured_a['name'])}"><img class="hp-team-mark" src="{esc(featured_a['team_logo'])}" alt=""><span>{esc(featured_a['position'])} · {esc(featured_a['team'])} · {"vs." if featured_a.get("home") else "at"} {esc(featured_a.get("opponent"))}</span><b>{esc(featured_a['name'])}</b><em>{featured_a_format['projected_points']:.1f} pts</em></div><i>VS</i><div><img src="{esc(featured_b['photo'])}" alt="{esc(featured_b['name'])}"><img class="hp-team-mark" src="{esc(featured_b['team_logo'])}" alt=""><span>{esc(featured_b['position'])} · {esc(featured_b['team'])} · {"vs." if featured_b.get("home") else "at"} {esc(featured_b.get("opponent"))}</span><b>{esc(featured_b['name'])}</b><em>{featured_b_format['projected_points']:.1f} pts</em></div></div><h2>Tony Pollard vs. Rico Dowdle</h2><div class="hp-decision-summary"><small>{esc(nfl_badge)}</small><strong>{esc(nfl_call)}</strong><p>{esc(nfl_summary)}</p></div><div class="hp-evidence-grid"><div><span>Projection</span><b>{featured_a_format['projected_points']:.1f} vs. {featured_b_format['projected_points']:.1f}</b></div><div><span>Modeled opportunity</span><b>{a_volume:.1f} vs. {b_volume:.1f}</b></div><div><span>Opponent context</span><b>{esc(featured_a['opponent'])} {matchup_tone(featured_a)}</b></div><div><span>Missing evidence</span><b>Injury + sportsbook</b></div></div><div class="hp-boundary"><strong>What changes the call?</strong> {esc(nfl_boundary)}</div><a class="hp-card-cta" href="{esc(room_url(featured_a, featured_b))}">Compare the NFL evidence →</a></article>'''
    college_feature_html = f'''<article class="hp-feature hp-college-feature lb-feature-card" style="--c:{esc(cw['team_color'])}"><small>College evidence case · Week 1 · Yahoo scoring</small><div class="hp-feature-players"><div><img src="{esc(cw['team_logo'])}" alt="{esc(cw['team'])}"><span>{esc(cw['position'])} · {esc(cw['team'])}</span><b>{esc(cw['name'])}</b><em>{cf['projected_points']:.1f} pts</em></div><i>VS</i><div><img src="{esc(cr['team_logo'])}" alt="{esc(cr['team'])}"><span>{esc(cr['position'])} · {esc(cr['team'])}</span><b>{esc(cr['name'])}</b><em>{crf['projected_points']:.1f} pts</em></div></div><h2>{esc(cw['name'])} vs. {esc(cr['name'])}</h2><div class="hp-decision-summary"><small>EVIDENCE LEAN</small><strong>{esc(cw['name'])}</strong><p>Reconciled projection, modeled workload, game environment, and exact player-component markets form the current case.</p></div><div class="hp-evidence-grid"><div><span>Projection</span><b>{cf['projected_points']:.1f} vs. {crf['projected_points']:.1f}</b></div><div><span>Modeled opportunity</span><b>{college_a_volume:.1f} vs. {college_b_volume:.1f}</b></div><div><span>Player market evidence</span><b>{esc(', '.join(map(component_label, cw['player_market']['components'])))} vs. {esc(', '.join(map(component_label, cr['player_market']['components'])))}</b></div><div><span>Sportsbook team total</span><b>{college_a_market['team_implied_total']:.1f} vs. {college_b_market['team_implied_total']:.1f}</b></div></div><div class="hp-boundary"><strong>What changes the call?</strong> A material role or availability change, a player-market move, or a +{college_feature['gap'] + .1:.1f}-point projection swing moves {esc(cr['name'])} ahead. Market inputs are evidence, not outcomes or guarantees.</div><a class="hp-card-cta" href="{COLLEGE_ROOM_PATH}?a={esc(cw['id'])}&amp;b={esc(cr['id'])}">Compare the College evidence →</a></article>'''

    ambient = f'''<div class="hp-ambient-data" aria-hidden="true"><div class="hp-ambient-card hp-ambient-trend"><span>PROJECTION GAP</span><svg viewBox="0 0 210 82" focusable="false"><polyline points="5,67 28,49 49,56 70,31 91,43 115,18 139,36 162,14 184,25 205,9"/></svg></div><div class="hp-ambient-card hp-ambient-formats"><span>SCORING FORMATS</span><ol><li>PPR <b>01</b></li><li>HALF-PPR <b>02</b></li><li>NON-PPR <b>03</b></li></ol></div><div class="hp-ambient-card hp-ambient-share"><span>OPPORTUNITY SHARE</span><div class="hp-ambient-ring"><b>63%</b></div></div><div class="hp-ambient-card hp-ambient-status"><span>AVAILABILITY</span><p><b>Q</b> QUESTIONABLE</p><p><b>D</b> DOUBTFUL</p><p><b>O</b> OUT</p></div><div class="hp-ambient-card hp-ambient-volume"><span>MODELED VOLUME</span><i style="--w:82%"></i><i style="--w:66%"></i><i style="--w:54%"></i><i style="--w:38%"></i></div><div class="hp-ambient-card hp-ambient-market"><span>COLLEGE MARKET</span><ol><li>SPREAD <b>{spread_label(college_a_market['team_spread'])}</b></li><li>TOTAL <b>{college_a_market['game_total']:.1f}</b></li><li>PROPS <b>LIVE</b></li></ol></div></div>'''
    hero = f'''<section class="hp-home-hero">{ambient}<div class="hp-home-copy"><div class="lb-eyebrow">NFL + COLLEGE FANTASY FOOTBALL</div><h1>Make the call with the <span>evidence in front of you.</span></h1><p>LineupBeat is an independent fantasy football research site for NFL and College players—bringing projections, expected opportunity, opponent context, scoring format, and clearly labeled evidence limits into one place.</p><div class="lb-hero-actions"><a class="lb-btn lb-btn-primary" href="{NFL_ROOM_PATH}">NFL Decision Room <b>→</b></a><a class="lb-btn lb-btn-secondary" href="{COLLEGE_ROOM_PATH}">College Decision Room <b>→</b></a></div><div class="lb-proof-row" aria-label="LineupBeat product standards"><div class="lb-proof"><strong>NFL + College</strong><span>Equal experiences</span></div><div class="lb-proof"><strong>Clear</strong><span>Scoring context</span></div><div class="lb-proof"><strong>Local</strong><span>Roster privacy</span></div><div class="lb-proof"><strong>Visible</strong><span>Evidence limits</span></div></div></div><div class="hp-feature-intro" id="featured-decisions"><small>TWO GAMES · ONE STANDARD</small><h2>Today’s featured decisions.</h2><p>Real lineup questions from both sides of LineupBeat, evaluated with the same transparent approach.</p></div><div class="hp-dual-feature">{nfl_feature}{college_feature_html}</div></section>'''
    sport_body = f'''<section class="hp-section hp-sports"><div class="hp-section-head"><small>CHOOSE YOUR GAME</small><h2>NFL or College?</h2></div><div class="hp-sport-grid"><a class="hp-sport-card" href="{NFL_ROOM_PATH}"><span class="hp-sport-code" aria-hidden="true">NFL</span><div class="hp-sport-copy"><small>NFL FANTASY</small><h3>Rankings, projections &amp; decisions</h3><b>Enter NFL →</b></div></a><a class="hp-sport-card hp-college" href="{COLLEGE_ROOM_PATH}"><span class="hp-sport-code" aria-hidden="true">CFB</span><div class="hp-sport-copy"><small>COLLEGE FANTASY</small><h3>Week 1 rankings &amp; projections</h3><b>Enter College →</b></div></a></div></section>'''
    tools = f'''<section class="hp-section" id="tools"><div class="hp-section-head"><small>WHAT LINEUPBEAT OFFERS</small><h2>Fantasy football tools for the decisions that matter.</h2><p>Move from a lineup question to the projection, context, and uncertainty behind it—without hiding the limits of the evidence.</p></div><div class="hp-action-grid">{action('/my-team/','MY','My Team','Connect an ESPN, Yahoo, or CBS roster locally for private NFL starter and bench analysis.')}{action('/my-league/','LH','My League','Turn an ESPN or Yahoo league archive into a complete, shareable fantasy football record book.')}{action('/nfl/rankings/','#','NFL Rankings','Browse overall and positional ranks in three scoring formats.')}{action('/nfl/projections/','Σ','NFL Projections','Inspect every modeled component behind the full stat line.')}{action('/college-fantasy-football/week-1/','C#','College Rankings','Browse Week 1 rankings across 64 modeled teams.')}{action('/college-fantasy-football/projections/','CΣ','College Projections','Explore the separate 2,351-player season dataset.')}{action('/nfl/data/','⌁','NFL Fantasy Data','Explore ADP, draft value, team context, and advanced tools.')}</div></section>'''
    brand_body = '''<section class="hp-section hp-brand" id="how-lineupbeat-works"><div class="hp-section-head"><small>WHY LINEUPBEAT</small><h2>Fantasy football decisions, explained.</h2><p>LineupBeat is an independent fantasy football research site built to show the work behind rankings, projections, player comparisons, and roster decisions.</p></div><div class="hp-method-grid"><article><span>01 · MODEL</span><h3>Build the projection</h3><p>Translate team opportunity and player efficiency into complete, scoring-specific projections.</p></article><article><span>02 · CONTEXT</span><h3>Test the environment</h3><p>Review role, expected volume, opponent, and venue. Market evidence appears only when it is validated.</p></article><article><span>03 · DECISION</span><h3>Show what changes the call</h3><p>State the gap, the missing evidence, and the boundary where the preferred option would change.</p></article></div><div class="hp-trust"><div><small>OUR STANDARD</small><h3>Useful enough to act on. Clear enough to audit.</h3></div><p>Every view identifies its scoring format, timing, and evidence limits. Sportsbook context is never presented as player certainty, and unavailable injury or market evidence is labeled instead of guessed.</p><a href="/about/">Who we are and how we work →</a></div></section>'''
    return f'''{START}{home_header()}<main id="lineup-beat-home" class="hp-shell">{hero}{sport_body}{tools}{brand_body}</main>{END}'''


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
    <h1>Compare the evidence—not just the projection.</h1>
    <p class="dr-lede">Compare Week 1 projections, opportunity, opponent context and availability—and see exactly what is still missing.</p>
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
    <article class="dr-empty"><small>Available now</small><h2>My Team</h2><p>Connect an ESPN roster locally to see supported Week 1 starter and bench decisions.</p><a class="dr-empty-link" href="/my-team/">Open My Team</a></article>
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
function draw(){let a=P[A.value],b=P[B.value],k=F.value;if(!a||!b||a.id===b.id){O.innerHTML='<p class="dr-error">Choose two different players.</p>';return}let w=winner(a,b,k),gap=Math.abs(shown(fmt(a,k).projected_points)-shown(fmt(b,k).projected_points));if(!w){let edges=FM.filter(x=>x!==k&&winner(a,b,x)).map(x=>L[x]);O.innerHTML=`<section class="dr-verdict"><div><small>True Toss-Up · ${L[k]}</small><h2>No clear edge</h2><p>Both players display at ${num(fmt(a,k).projected_points)} full-season ${L[k]} points. Lineup Beat does not recommend either player when the displayed projections are equal.</p></div><div class="dr-adv"><b>0.0</b><span>displayed point gap</span></div></section>${playerCards(a,b,k)}<section class="dr-boundary"><div class="dr-boundary-title"><small>Signature analysis</small><h2>What changes the pick?</h2></div><div class="dr-boundary-grid"><article><b>+0.1</b><span>Either player needs one tenth of a displayed season point to move ahead.</span></article><article><b>${edges.length?edges.join(' / '):'No edge'}</b><span>${edges.length?'These scoring formats produce a leader.':'Every available scoring format remains tied.'}</span></article></div></section><p class="dr-stamp">Projection data updated ''' + esc(updated) + r''' · Page build: current release · 2026 full season · ${L[k]}</p>`;return}let r=w.id===a.id?b:a,wf=fmt(w,k),rf=fmt(r,k);gap=+(shown(wf.projected_points)-shown(rf.projected_points)).toFixed(1);let flip=+(gap+.1).toFixed(1),flips=FM.filter(x=>x!==k&&(!winner(a,b,x)||winner(a,b,x).id!==w.id)).map(x=>L[x]),market=w.adp!=null&&r.adp!=null?(w.adp>r.adp?'Market ADP prefers '+r.name+'.':'Market ADP agrees with the pick.'):'ADP comparison is unavailable for this pair.';
O.innerHTML=`<section class="dr-verdict"><div><small>${conf(gap)} · ${L[k]}</small><h2>Recommend ${w.name}</h2><p>${w.name} projects for ${num(wf.projected_points)} full-season ${L[k]} points, ${num(gap)} more than ${r.name}. The recommendation follows the higher displayed validated season projection.</p></div><div class="dr-adv"><b>+${num(gap)}</b><span>season-point advantage</span></div></section>${playerCards(a,b,k)}<section class="dr-boundary"><div class="dr-boundary-title"><small>Signature analysis</small><h2>What changes the pick?</h2></div><div class="dr-boundary-grid"><article><b>+${num(flip)}</b><span>${r.name} needs this many additional projected season points to move ahead.</span></article><article><b>−${num(flip)}</b><span>${w.name} could lose this many projected season points before the recommendation flips.</span></article><article><b>${flips.length?flips.join(' / '):'No flip'}</b><span>${flips.length?'These available scoring formats remove or reverse the recommendation.':'The recommendation holds in every available scoring format.'}</span></article><article><b>${market.startsWith('Market ADP prefers')?'Disagreement':market.startsWith('Market')?'Agreement':'No ADP'}</b><span>${market}</span></article></div></section><p class="dr-stamp">Projection data updated ''' + esc(updated) + r''' · Page build: current release · 2026 full season · ${L[k]}</p>`}
function candidates(which){let other=which===A?B:A,base=D.players.filter(p=>p.id!==other.value);if(which===B&&!X.checked&&P[A.value])base=base.filter(p=>p.position===P[A.value].position);return base}
function setup(select,input,list){let active=-1;function close(){list.hidden=true;input.setAttribute('aria-expanded','false');active=-1}function show(){let q=input.value.toLowerCase(),rows=candidates(select).filter(p=>!q||(`${p.name} ${p.team} ${p.position}`).toLowerCase().includes(q)).slice(0,40);list.innerHTML=rows.length?rows.map((p,i)=>`<li role="option" data-id="${p.id}" id="${list.id}-${i}">${p.name}<small>${p.team} · ${p.position}</small></li>`).join(''):'<li class="dr-no-result">No matching players</li>';list.hidden=false;input.setAttribute('aria-expanded','true')}function choose(id){let p=P[id];if(!p)return;select.value=id;input.value=`${p.name} · ${p.team} ${p.position}`;close();select.dispatchEvent(new Event('change'))}input.addEventListener('focus',()=>{input.select();show()});input.addEventListener('input',show);input.addEventListener('keydown',e=>{let rows=[...list.querySelectorAll('[role=option]')];if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();active=Math.max(0,Math.min(rows.length-1,active+(e.key==='ArrowDown'?1:-1)));rows.forEach((x,i)=>x.setAttribute('aria-selected',i===active?'true':'false'));if(rows[active])rows[active].scrollIntoView({block:'nearest'})}else if(e.key==='Enter'&&rows[active]){e.preventDefault();choose(rows[active].dataset.id)}else if(e.key==='Escape')close()});list.addEventListener('mousedown',e=>{let row=e.target.closest('[role=option]');if(row){e.preventDefault();choose(row.dataset.id)}});select.addEventListener('change',()=>{let p=P[select.value];if(p)input.value=`${p.name} · ${p.team} ${p.position}`});document.addEventListener('click',e=>{if(!e.target.closest('.dr-picker'))close()});return{refresh:show}}
let Q=new URLSearchParams(location.search);A.value=P[Q.get('a')]?Q.get('a'):"''' + esc(default_a) + r'''";B.value=P[Q.get('b')]&&Q.get('b')!==A.value?Q.get('b'):"''' + esc(default_b) + r'''";if(F.querySelector(`option[value="${Q.get('format')}"]`))F.value=Q.get('format');let PA=setup(A,document.getElementById('dr-a-search'),document.getElementById('dr-a-list')),PB=setup(B,document.getElementById('dr-b-search'),document.getElementById('dr-b-list'));A.dispatchEvent(new Event('change'));B.dispatchEvent(new Event('change'));A.addEventListener('change',()=>{if(!X.checked&&P[A.value]&&(!P[B.value]||P[B.value].position!==P[A.value].position||A.value===B.value)){let next=D.players.find(p=>p.id!==A.value&&p.position===P[A.value].position);if(next){B.value=next.id;B.dispatchEvent(new Event('change'))}}draw()});[B,F].forEach(x=>x.addEventListener('change',draw));X.addEventListener('change',()=>{A.dispatchEvent(new Event('change'));PB.refresh()});document.querySelectorAll('.dr-open').forEach(x=>x.addEventListener('click',()=>{A.value=x.dataset.a;B.value=x.dataset.b;A.dispatchEvent(new Event('change'));B.dispatchEvent(new Event('change'));draw();document.getElementById('dr-compare-title').scrollIntoView({behavior:'smooth'})}));draw()})();'''


def comparison_v2_javascript(default_a: str, default_b: str, updated: str) -> str:
    """Render Comparison Engine v2 from validated, identity-keyed evidence."""
    script = r'''(()=>{
const root=document.getElementById("decision-room");root.classList.add("dr-enhanced");
const D=JSON.parse(document.getElementById("dr-data").textContent),P=Object.fromEntries(D.players.map(p=>[p.id,p])),A=document.getElementById("dr-a"),B=document.getElementById("dr-b"),F=document.getElementById("dr-format"),X=document.getElementById("dr-cross-position"),O=document.getElementById("dr-result"),L={ppr:"PPR",half_ppr:"Half-PPR",non_ppr:"Non-PPR"},FM=["ppr","half_ppr","non_ppr"],weekly=D.mode==="weekly",recommendationsAuthorized=D.recommendation_state?.enabled===true;
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
function draw(){let a=P[A.value],b=P[B.value],k=F.value;if(!a||!b||a.id===b.id){O.innerHTML='<p class="dr-error">Choose two different players.</p>';return}let ap=points(a,k),bp=points(b,k),gap=Math.abs(ap-bp),gp=pct(gap,Math.max(ap,bp)),lead=projectedLeader(a,b,k),cls=classification(gap,Math.max(ap,bp)),w=cls==='Toss-Up'?null:lead,r=w?(w.id===a.id?b:a):null,e=opinion(a,b),q=coverage(a,b,k,e),agree=agreement(a,b,k,e,cls,lead),qualified=!weekly||recommendationsAuthorized,call=cls==='Toss-Up'?'No clear projection edge':!qualified?(agree.state==='Split'?`Split evidence — projection favors ${safe(w.name)}`:`Projection favors ${safe(w.name)}`):agree.state==='Split'?'Split case':agree.state==='Mixed'?`Mixed case — projection favors ${safe(w.name)}`:`${cls==='Lean'?'Prefer':cls==='Edge'?'Recommend':'Strongly prefer'} ${safe(w.name)}`,projection=cls==='Toss-Up'?`Projection edge: Toss-Up. The ${num(gap)}-point difference (${gp.toFixed(1)}%) is inside the deterministic no-call band.`:`Projection edge: ${cls} — ${safe(w.name)}. ${safe(w.name)} projects ${num(gap)} points (${gp.toFixed(1)}%) ahead of ${terminalName(r)}`,reconcile=agree.signals.length?agreementText(a,b,agree):'No additional directional evidence is available.';
O.innerHTML=`<section class="dr-verdict"><div><small>${qualified?'Lineup Beat call':'Projection comparison'} · ${agree.state} · ${L[k]}</small><h2>${call}</h2><p><strong>${projection}</strong> ${reconcile}</p>${qualified?'':'<p><strong>No lineup recommendation is issued.</strong> Check current availability before making a roster move.</p>'}</div><div class="dr-adv"><b>${gap?'+':''}${num(gap)}</b><span>Week 1 point difference</span></div></section>${playerCards(a,b,k)}<section class="dr-evidence"><div class="dr-evidence-title"><small>Evidence stack 02</small><h2>Why</h2></div><div class="dr-why-grid"><article><h3>Our Week 1 projection</h3><p>${cls} · ${num(gap)} points · ${gp.toFixed(1)}% difference.</p></article><article><h3>What the market says</h3><p>Unavailable — zero odds requests were made because the isolated credential is unavailable.</p></article><article><h3>Opponent matchup</h3><p>${safe(a.name)}: ${safe(a.matchup?.label)} factor ${Number(a.matchup?.projection_factor||1).toFixed(2)}. ${safe(b.name)}: ${safe(b.matchup?.label)} factor ${Number(b.matchup?.projection_factor||1).toFixed(2)}.</p></article><article><h3>Expected opportunity</h3><p>${safe(a.name)}: ${opportunity(a)}. ${safe(b.name)}: ${opportunity(b)}.</p></article><article><h3>Availability</h3><p>Both are active on the captured roster. Current Week 1 injury reports are unavailable.</p></article><article><h3>Prior-year consistency</h3><p>${historyText(a,b,k)}</p></article></div></section><section class="dr-cases"><div class="dr-evidence-title"><small>Balanced evidence 03</small><h2>Case for each player</h2></div><div class="dr-case-grid"><article><h3>${safe(a.name)}</h3><ul>${caseFor(a,b,k,e)}</ul></article><article><h3>${safe(b.name)}</h3><ul>${caseFor(b,a,k,e)}</ul></article></div></section><section class="dr-boundary"><div class="dr-boundary-title"><small>Decision boundaries 04</small><h2>What changes the call</h2></div>${changeCards(a,b,k,lead,cls)}</section><section class="dr-quality"><div class="dr-evidence-title"><small>Transparency 05</small><h2>Data coverage and evidence agreement</h2><p><b>Data coverage</b> · ${q.present} of ${q.cats.length} evidence categories available for this pair.</p><p><b>Evidence agreement</b> · ${agree.state}. Classification describes projection-edge size, not probability or confidence.</p></div><div class="dr-quality-grid">${q.cats.map(x=>`<span class="${x[1].toLowerCase().replace(' ','-')}"><b>${x[0]}</b>${x[1]}</span>`).join('')}</div><p class="dr-stamp">Lineup Beat model ${safe(D.sources.model.updated_at)} · History: ${safe(D.sources.history.updated_at)} · Matchup: 2025 context · Market: unavailable${e?` · Editorial ${safe(e.evidence_date)} (historical/stale)`:''}</p></section>`}
function candidates(which){let other=which===A?B:A,base=D.players.filter(p=>p.id!==other.value);if(which===B&&!X.checked&&P[A.value])base=base.filter(p=>p.position===P[A.value].position);return base}
function setup(select,input,list){let active=-1;function close(){list.hidden=true;input.setAttribute('aria-expanded','false');active=-1}function show(){let q=input.value.toLowerCase(),rows=candidates(select).filter(p=>!q||(`${p.name} ${p.team} ${p.position}`).toLowerCase().includes(q)).slice(0,40);list.innerHTML=rows.length?rows.map((p,i)=>`<li role="option" data-id="${safe(p.id)}" id="${list.id}-${i}">${safe(p.name)}<small>${safe(p.team)} · ${safe(p.position)}</small></li>`).join(''):'<li class="dr-no-result">No matching players</li>';list.hidden=false;input.setAttribute('aria-expanded','true')}function choose(id){let p=P[id];if(!p)return;select.value=id;input.value=`${p.name} · ${p.team} ${p.position}`;close();select.dispatchEvent(new Event('change'))}input.addEventListener('focus',()=>{input.select();show()});input.addEventListener('input',show);input.addEventListener('keydown',e=>{let rows=[...list.querySelectorAll('[role=option]')];if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();active=Math.max(0,Math.min(rows.length-1,active+(e.key==='ArrowDown'?1:-1)));rows.forEach((x,i)=>x.setAttribute('aria-selected',i===active?'true':'false'));if(rows[active])rows[active].scrollIntoView({block:'nearest'})}else if(e.key==='Enter'&&rows[active]){e.preventDefault();choose(rows[active].dataset.id)}else if(e.key==='Escape')close()});list.addEventListener('mousedown',e=>{let row=e.target.closest('[role=option]');if(row){e.preventDefault();choose(row.dataset.id)}});select.addEventListener('change',()=>{let p=P[select.value];if(p)input.value=`${p.name} · ${p.team} ${p.position}`});document.addEventListener('click',e=>{if(!e.target.closest('.dr-picker'))close()});return{refresh:show}}
let Q=new URLSearchParams(location.search);A.value=P[Q.get('a')]?Q.get('a'):__DEFAULT_A__;B.value=P[Q.get('b')]&&Q.get('b')!==A.value?Q.get('b'):__DEFAULT_B__;if(F.querySelector(`option[value="${Q.get('format')}"]`))F.value=Q.get('format');let PA=setup(A,document.getElementById('dr-a-search'),document.getElementById('dr-a-list')),PB=setup(B,document.getElementById('dr-b-search'),document.getElementById('dr-b-list'));A.dispatchEvent(new Event('change'));B.dispatchEvent(new Event('change'));A.addEventListener('change',()=>{if(!X.checked&&P[A.value]&&(!P[B.value]||P[B.value].position!==P[A.value].position||A.value===B.value)){let next=D.players.find(p=>p.id!==A.value&&p.position===P[A.value].position);if(next){B.value=next.id;B.dispatchEvent(new Event('change'))}}draw()});[B,F].forEach(x=>x.addEventListener('change',draw));X.addEventListener('change',()=>{A.dispatchEvent(new Event('change'));PB.refresh()});document.querySelectorAll('.dr-open').forEach(x=>x.addEventListener('click',()=>{A.value=x.dataset.a;B.value=x.dataset.b;A.dispatchEvent(new Event('change'));B.dispatchEvent(new Event('change'));draw();document.getElementById('dr-compare-title').scrollIntoView({behavior:'smooth'})}));draw()})();'''
    return (script.replace("__DEFAULT_A__", json.dumps(default_a))
            .replace("__DEFAULT_B__", json.dumps(default_b)))


CSS = r'''
body{margin:0;--display:"Source Serif 4",Georgia,serif;font-family:var(--text)}.room-home-nav{display:flex;justify-content:space-between;gap:1rem;align-items:center;padding:.8rem 1rem;background:#050807;color:#fff;border-bottom:1px solid #29312d;font:700 .8rem var(--agate)}.room-home-nav a{color:#e8ece8}.room-home-nav nav{display:flex;gap:1rem;flex-wrap:wrap}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.sport-header{position:relative;z-index:50;background:#080b0b;color:#fff;border-bottom:1px solid #2d3532}.sport-header .tbrow{min-height:66px;display:flex;align-items:center;gap:1rem;padding:0 max(1rem,calc((100% - 1180px)/2))}.sport-header .logo{border:0;background:none;color:#fff;font:700 1.05rem var(--agate);text-decoration:none;white-space:nowrap}.sport-header .logo em{font-style:normal;color:#c6f53c}.sport-switch,.sport-activities{display:flex;gap:.3rem;align-items:center}.sport-header .vbtn{padding:.55rem .7rem;border:1px solid transparent;border-radius:999px;color:#d7ddd8;text-decoration:none;font:700 .7rem var(--agate);text-transform:uppercase;letter-spacing:.05em}.sport-header .sport-pill[aria-pressed=true],.sport-header .vbtn[aria-current=page]{background:#c6f53c;color:#0b100d}.sport-activities{margin-left:auto}.context-search{margin-left:.5rem}.context-search input{width:190px;padding:.65rem .75rem;border:1px solid #46504b;background:#101514;color:#fff}.college-search{color:#c6f53c;font:700 .72rem var(--agate);text-decoration:none}.sport-header .navbtn{display:none;background:#c6f53c;border:0;padding:.55rem .7rem;font-weight:800}.sport-header .navdrawer{background:#0b100f;border-top:1px solid #29312d}.sport-header .navlinks{display:flex;flex-direction:column;padding:1rem}.sport-header .navlinks>.context-search{margin:.5rem 0 0}.sport-header .navlinks .finder{display:none}
.lb-decision-hero{position:relative;overflow:hidden;min-height:720px;background:radial-gradient(circle at 37% 37%,rgba(35,43,45,.33),rgba(5,7,8,0) 55%),#050708}.lb-decision-hero:before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);background-size:72px 72px}.lb-decision-hero .lb-hero-inner{position:relative;z-index:5;width:min(1130px,calc(100% - 64px));margin:auto;display:grid;grid-template-columns:1.04fr .9fr;gap:72px;padding:70px 0 110px;align-items:center}.lb-decision-hero .lb-eyebrow{margin-bottom:20px;font:700 18px var(--agate);color:#c6f53c;letter-spacing:.045em}.lb-decision-hero .lb-hero-title{font:400 clamp(56px,4.5vw,72px)/.99 Georgia,serif;letter-spacing:-.033em;margin:0;color:#f3f5ef}.lb-decision-hero .lb-hero-title span{color:#c6f53c}.lb-decision-hero .lb-hero-description{max-width:590px;margin:28px 0 0;font:19px/1.7 Georgia,serif;color:#aeb7b0}.lb-decision-hero .lb-hero-actions{display:flex;gap:18px;margin-top:32px}.lb-decision-hero .lb-btn{min-height:66px;display:inline-flex;align-items:center;justify-content:center;padding:0 28px;border-radius:8px;text-decoration:none;font:700 16px var(--agate)}.lb-decision-hero .lb-btn-primary{gap:2rem;background:#c6f53c;color:#060806}.lb-decision-hero .lb-btn-secondary{border:1px solid #727b76;color:#fff}.lb-decision-hero .lb-proof-row{display:grid;grid-template-columns:repeat(4,1fr);margin-top:38px}.lb-decision-hero .lb-proof{padding:0 12px;min-width:0}.lb-decision-hero .lb-proof:first-child{padding-left:0}.lb-decision-hero .lb-proof+.lb-proof{border-left:1px solid #ffffff2e}.lb-decision-hero .lb-proof strong{display:block;font:700 25px var(--agate)}.lb-decision-hero .lb-proof span{display:block;margin-top:6px;font:600 9px var(--agate);letter-spacing:.06em;text-transform:uppercase;color:#8e9991}.lb-feature-card{position:relative;z-index:8;border-radius:18px!important;border-top:1px solid #ffffff38!important;box-shadow:0 32px 90px #0009!important;background:linear-gradient(180deg,#111516fa,#0d1112fa)!important}.lb-edge{position:absolute;top:35px;bottom:0;width:225px;z-index:2;pointer-events:none;opacity:.32}.lb-edge-left{left:0}.lb-edge-right{right:0}.lb-mini-panel{position:relative;margin:18px 8px;padding:13px;border:1px solid #ffffff29;border-radius:5px;background:#0d111285;color:#9ba69f;font:12px var(--agate)}.lb-mini-title{margin-bottom:8px;letter-spacing:.08em}.lb-data-number{font:700 28px var(--agate);color:#c6f53c}.lb-playbook{padding:30px;color:#c6f53c;font-size:30px;word-spacing:25px}.lb-decision-hero .hp-sport-choice{display:flex;gap:.4rem;margin-bottom:1.4rem}.lb-decision-hero .hp-sport-choice a{padding:.5rem 1rem;border:1px solid #4d5952;color:#fff;text-decoration:none;font:800 .72rem var(--agate)}.lb-decision-hero .hp-sport-choice a:first-child{background:#c6f53c;color:#101410}
.hp-shell{--lime:#c6f53c;--ink:#f3f5ef;--muted:#aeb7b0;--panel:#111715;background:#080c0b;color:var(--ink);font-family:var(--text)}.hp-nav{position:relative;z-index:10;display:flex;align-items:center;justify-content:space-between;padding:.8rem max(1rem,calc((100% - 1180px)/2));background:#050807;color:#fff;border-bottom:1px solid #2b332f}.hp-mark{font:900 1rem var(--agate);letter-spacing:.08em;color:#fff}.hp-mark b{color:#c6f53c}.hp-nav nav{display:flex;gap:1.15rem;align-items:center}.hp-nav nav a{color:#e4e9e4;font:800 .72rem var(--agate);letter-spacing:.06em;text-transform:uppercase}.hp-menu{display:none;background:#c6f53c;border:0;padding:.55rem .8rem;font-weight:800}.hp-hero{display:grid;grid-template-columns:1.05fr .95fr;gap:clamp(2rem,5vw,5rem);align-items:center;padding:clamp(3rem,7vw,7rem) max(1rem,calc((100% - 1180px)/2));background:radial-gradient(circle at 76% 18%,rgba(198,245,60,.13),transparent 28%),linear-gradient(145deg,#111817,#080b0b)}.hp-copy>small,.hp-section-head>small,.hp-beat-intro>small,.hp-feature>small,.hp-board-card>small,.hp-sport-card small{font:800 .7rem var(--agate);letter-spacing:.12em;text-transform:uppercase;color:var(--lime)}.hp-copy h1{font:800 clamp(3.5rem,7vw,6.7rem)/.87 var(--display);letter-spacing:-.055em;margin:.7rem 0 1.2rem}.hp-copy>p{max-width:620px;color:#ced5cf;font-size:clamp(1.05rem,2vw,1.28rem);line-height:1.55}.hp-sport-choice{display:flex;gap:.4rem;margin-bottom:1.5rem}.hp-sport-choice a{padding:.55rem 1rem;border:1px solid #4d5952;color:#fff;font-weight:800}.hp-sport-choice a:first-child{background:#c6f53c;color:#101410;border-color:#c6f53c}.hp-ctas{display:flex;gap:.7rem;flex-wrap:wrap;margin-top:2rem}.hp-ctas a{padding:.9rem 1.1rem;border:1px solid #79827c;color:#fff;font:800 .75rem var(--agate);text-transform:uppercase}.hp-ctas .hp-primary{background:#c6f53c;color:#101410;border-color:#c6f53c}.hp-feature{border:1px solid #3c4741;border-top:5px solid var(--c);background:#111715;padding:clamp(1rem,3vw,1.7rem);box-shadow:0 24px 70px #0008}.hp-feature-players{display:grid;grid-template-columns:1fr auto 1fr;align-items:end;gap:.5rem;margin:1rem 0}.hp-feature-players>div{min-width:0}.hp-feature-players img{display:block;width:100%;height:150px;object-fit:contain;background:linear-gradient(#1b231f,#101513)}.hp-feature-players span,.hp-feature-players b{display:block}.hp-feature-players span{font-size:.68rem;color:var(--muted);margin-top:.6rem}.hp-feature-players b{font:700 1.2rem var(--display)}.hp-feature-players i{padding-bottom:3rem;color:var(--lime);font:800 .7rem var(--agate)}.hp-feature h2{font:750 clamp(1.8rem,4vw,3rem) var(--display);margin:.8rem 0}.hp-feature>p b{color:var(--lime);font-size:1.5rem}.hp-boundary{padding:1rem;background:#e9efe6;color:#101410;margin:1rem 0;line-height:1.45}.hp-boundary strong{display:block;font:800 .7rem var(--agate);text-transform:uppercase}.hp-feature>a{color:var(--lime);font-weight:800}.hp-section{max-width:1180px;margin:auto;padding:clamp(3.2rem,6vw,5rem) 1rem;border-bottom:1px solid #29312d}.hp-section-head{display:flex;align-items:end;justify-content:space-between;gap:2rem;flex-wrap:wrap}.hp-section h2{font:750 clamp(2.2rem,5vw,4rem)/.95 var(--display);margin:.35rem 0}.hp-section-head p{color:var(--muted)}.hp-actions{display:grid;grid-template-columns:repeat(3,1fr);gap:.75rem;margin-top:1.5rem}.hp-actions a{border:1px solid #303a35;background:var(--panel);padding:1rem;color:#fff}.hp-actions b,.hp-actions span{display:block}.hp-actions span{color:var(--muted);margin-top:.35rem;font-size:.85rem}.hp-board{max-width:none;padding-left:max(1rem,calc((100% - 1180px)/2));padding-right:max(1rem,calc((100% - 1180px)/2));background:#0d1211}.hp-board-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:.7rem;margin-top:1.5rem}.hp-board-card{padding:1rem;background:#151c19;border:1px solid #303b35;color:#fff;min-height:125px}.hp-board-card h3{font:700 1.15rem var(--display);margin:.55rem 0}.hp-board-card i{color:var(--muted);font-size:.75rem}.hp-board-card p{color:var(--muted);font-size:.82rem;line-height:1.4}.hp-sport-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:1.5rem}.hp-sport-card{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:1.4rem;min-height:150px;padding:clamp(1.25rem,2.5vw,1.8rem);border:1px solid #39433e;border-top:5px solid var(--lime);color:#fff;background:linear-gradient(145deg,#151c19,#0e1311);text-decoration:none;transition:transform .18s,border-color .18s}.hp-sport-card:hover,.hp-sport-card:focus-visible{transform:translateY(-4px);border-color:var(--lime);outline:none}.hp-sport-code{display:block;min-width:104px;color:#f3f5ef18;font:800 clamp(3.2rem,5vw,5rem)/.8 var(--agate);letter-spacing:-.06em}.hp-sport-copy{min-width:0}.hp-sport-card h3{max-width:420px;font:700 clamp(1.55rem,2.6vw,2.25rem)/1.05 var(--display);margin:.45rem 0}.hp-sport-card b{display:block;margin-top:.9rem;color:var(--lime);font:800 .78rem var(--agate);text-transform:uppercase}.hp-college{background:linear-gradient(145deg,#13221e,#0d1412);border-top-color:var(--lime)}.hp-college .hp-sport-code{color:#c6f53c22}.hp-beat-intro{padding-bottom:1.5rem}.hp-beat-intro p{color:var(--muted);max-width:700px}.hp-beat-intro>a{color:var(--lime);font-weight:800}
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
.hp-experience[hidden]{display:none}.hp-experience{background:#080c0b}.hp-experience .lb-decision-hero{min-height:650px}.hp-experience .lb-hero-inner{padding:64px 0 78px}.hp-experience .lb-hero-title{display:block;max-width:650px;font-size:clamp(50px,5vw,76px);line-height:.98}.hp-experience .lb-hero-description{font-family:var(--agate);font-size:1.15rem;line-height:1.55;color:#cbd2cc}.hp-college-hero{background:radial-gradient(circle at 72% 20%,rgba(198,245,60,.1),transparent 32%),#050708}.hp-college-feature .hp-feature-players>div>img{height:128px;padding:12px}.hp-feature-players em{display:block;margin-top:.35rem;color:var(--lime);font:700 .8rem var(--agate);font-style:normal}.hp-feature-players>div{position:relative}.hp-feature-players .hp-team-mark{position:absolute;right:6px;top:6px;width:38px;height:38px;padding:4px;background:#0b100fd9;object-fit:contain;border-radius:7px}.hp-card-cta{display:inline-flex;padding:.7rem 0;text-decoration:none}.hp-section-head{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,420px);align-items:end}.hp-section-head small,.hp-section-head h2{grid-column:1}.hp-section-head p{grid-column:2;grid-row:1/3;margin:0}.hp-action-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-top:2rem}.hp-action{display:flex;gap:1rem;min-height:180px;padding:1.4rem;border:1px solid #354039;background:linear-gradient(145deg,#151b18,#0d1210);color:#fff;text-decoration:none;box-shadow:0 12px 30px #0003;transition:transform .18s,border-color .18s}.hp-action:hover,.hp-action:focus-visible{transform:translateY(-4px);border-color:var(--lime);outline:none}.hp-action-icon{display:grid;place-items:center;width:42px;height:42px;flex:none;border:1px solid #56654b;color:var(--lime);font:800 1.15rem var(--data)}.hp-action h3{margin:.1rem 0 .65rem;font:700 1.35rem var(--agate);text-transform:uppercase;letter-spacing:.02em}.hp-action p{margin:0;color:#bac4bc;font:1rem/1.5 var(--agate)}.hp-action b{display:block;margin-top:1rem;color:var(--lime);font:700 .78rem var(--agate);text-transform:uppercase}.hp-difference{max-width:none;padding-left:max(1rem,calc((100% - 1180px)/2));padding-right:max(1rem,calc((100% - 1180px)/2));background:#0d1211}.hp-signal-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-top:2rem}.hp-signal-card{position:relative;display:block;min-width:0;padding:1.2rem;border:1px solid #354039;background:linear-gradient(180deg,#171e1a,#101411);color:#fff;text-decoration:none;box-shadow:0 18px 35px #0004}.hp-signal-card:hover,.hp-signal-card:focus-visible{border-color:var(--lime);outline:none}.hp-signal-card>small{display:block;margin:1rem 0 .45rem;color:var(--lime);font:800 .68rem var(--agate);letter-spacing:.1em}.hp-signal-card>p{color:#c4ccc6;font:1rem/1.45 var(--agate)}.hp-signal-card>strong{font:700 1rem var(--data)}.hp-identity{display:grid;grid-template-columns:64px 24px 1fr;align-items:end;gap:.45rem;min-width:0}.hp-id-art{width:64px;height:72px;object-fit:contain;background:#0b100f}.hp-id-logo{width:24px;height:24px;object-fit:contain}.hp-identity b,.hp-identity span{display:block}.hp-identity b{font:700 1.05rem var(--agate);line-height:1.05}.hp-identity span{margin-top:.25rem;color:#aeb8b0;font:600 .75rem var(--agate)}.hp-call-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:2rem}.hp-call-card{display:block;padding:1.1rem;border:1px solid #354039;background:#111715;color:#fff;text-decoration:none}.hp-call-card:hover,.hp-call-card:focus-visible{border-color:var(--lime);outline:none}.hp-call-art{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:.55rem}.hp-call-art>.hp-identity{grid-template-columns:48px 1fr}.hp-call-art>.hp-identity .hp-id-art{width:48px;height:58px}.hp-call-art>.hp-identity .hp-id-logo{display:none}.hp-call-art>span{color:var(--lime);font:800 .67rem var(--agate)}.hp-call-data{display:grid;grid-template-columns:1fr auto;margin-top:1rem;padding-top:1rem;border-top:1px solid #303a35;gap:.45rem}.hp-call-data small{grid-column:1/3;color:#9eaaa1;font:700 .7rem var(--agate);text-transform:uppercase}.hp-call-data b{font:700 1.05rem var(--data)}.hp-call-data span{color:var(--lime);font:700 .75rem var(--agate)}.hp-mover-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:2rem}.hp-mover-card{padding:1.35rem;border:1px solid #354039;background:linear-gradient(145deg,#151b18,#0e1311)}.hp-mover-card small{color:var(--lime);font:700 .7rem var(--agate);text-transform:uppercase}.hp-mover-card h3{font:700 1.45rem var(--agate);margin:.5rem 0 1rem}.hp-mover-card>div{display:grid;grid-template-columns:repeat(3,1fr);gap:.4rem}.hp-mover-card>div span{padding:.7rem .35rem;background:#0a0f0d;color:#aeb8b0;text-align:center;font:600 .72rem var(--agate)}.hp-mover-card>div b{display:block;margin-top:.25rem;color:#fff;font:700 .95rem var(--data)}.hp-mover-card p{color:#bac3bc;font:1rem/1.45 var(--agate)}
.hp-brand{padding-top:clamp(4rem,8vw,7rem);padding-bottom:clamp(4rem,8vw,7rem)}.hp-method-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;margin-top:2.5rem;background:#354039;border:1px solid #354039}.hp-method-grid article{min-height:220px;padding:clamp(1.4rem,3vw,2.2rem);background:#111715}.hp-method-grid span,.hp-trust small{color:var(--lime);font:800 .7rem var(--agate);letter-spacing:.1em}.hp-method-grid h3,.hp-trust h3{font:700 clamp(1.5rem,3vw,2.15rem)/1.05 var(--display);margin:.7rem 0}.hp-method-grid p,.hp-trust p{color:#bac4bc;font:1rem/1.6 var(--agate)}.hp-trust{display:grid;grid-template-columns:1.05fr 1.3fr auto;gap:2rem;align-items:center;margin-top:1.5rem;padding:clamp(1.4rem,3vw,2.2rem);background:#e9efe6;color:#101410}.hp-trust h3{margin-bottom:0}.hp-trust p{margin:0;color:#364039}.hp-trust a{color:#101410;font:800 .76rem var(--agate);text-transform:uppercase;white-space:nowrap}
.hp-college-identity{grid-template-columns:64px 1fr}
.home-topbar .home-nav-cta{margin-left:auto;padding:.65rem .9rem;border:1px solid #637055;color:#c6f53c;text-decoration:none;font:800 .7rem var(--agate);letter-spacing:.07em;text-transform:uppercase}.hp-home-hero{position:relative;isolation:isolate;overflow:hidden;padding:clamp(3.5rem,7vw,6.5rem) max(1rem,calc((100% - 1180px)/2));background:radial-gradient(circle at 50% 20%,rgba(29,40,37,.72),transparent 47%),#050708}.hp-home-hero:before{content:"";position:absolute;z-index:-2;inset:0;background-image:linear-gradient(rgba(255,255,255,.026) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.026) 1px,transparent 1px);background-size:72px 72px;pointer-events:none}.hp-home-hero:after{content:"";position:absolute;z-index:-1;inset:0;background:linear-gradient(90deg,#05070882 0,transparent 18%,transparent 82%,#05070882 100%),linear-gradient(180deg,transparent 0,#050708 49%,transparent 65%);pointer-events:none}.hp-ambient-data{position:absolute;z-index:0;inset:0 0 auto;height:780px;overflow:hidden;pointer-events:none;color:var(--lime);opacity:.17;filter:saturate(.7)}.hp-ambient-card{position:absolute;width:220px;padding:1rem;border:1px solid #8c9a9238;border-radius:6px;background:#080d0c8f;box-shadow:0 18px 40px #0008;font:700 .62rem var(--agate);letter-spacing:.08em}.hp-ambient-card>span{display:block;margin-bottom:.8rem;color:#9aa39c}.hp-ambient-card svg{display:block;width:100%;height:auto}.hp-ambient-card polyline{fill:none;stroke:var(--lime);stroke-width:3}.hp-ambient-card ol{list-style:none;margin:0;padding:0}.hp-ambient-card li{display:flex;justify-content:space-between;padding:.38rem 0;border-bottom:1px solid #8c9a922b;color:#aeb8b0}.hp-ambient-card li b{color:var(--lime)}.hp-ambient-trend{left:-34px;top:78px}.hp-ambient-formats{left:-62px;top:310px}.hp-ambient-share{left:15px;top:540px;width:158px}.hp-ambient-ring{display:grid;place-items:center;width:86px;height:86px;margin:auto;border-radius:50%;background:radial-gradient(circle,#080d0c 50%,transparent 52%),conic-gradient(var(--lime) 0 63%,#313a35 63%)}.hp-ambient-ring b{color:#aeb8b0;font-size:1rem}.hp-ambient-status{right:-40px;top:105px;width:195px}.hp-ambient-status p{display:flex;gap:.75rem;margin:.55rem 0;color:#9aa39c}.hp-ambient-status p b{color:var(--lime)}.hp-ambient-volume{right:-58px;top:340px;width:240px}.hp-ambient-volume i{display:block;width:var(--w);height:7px;margin:13px 0;background:linear-gradient(90deg,var(--lime),#53631b);box-shadow:0 0 12px #c6f53c33}.hp-ambient-market{right:8px;top:585px;width:205px}.hp-home-copy,.hp-feature-intro,.hp-dual-feature{position:relative;z-index:2}.hp-home-copy{max-width:920px}.hp-home-copy .lb-eyebrow{color:var(--lime);font:800 .78rem var(--agate);letter-spacing:.13em}.hp-home-copy h1{max-width:900px;margin:.7rem 0 1.25rem;font:400 clamp(3.5rem,7vw,6.4rem)/.92 var(--display);letter-spacing:-.045em}.hp-home-copy h1 span{color:var(--lime)}.hp-home-copy>p{max-width:790px;color:#cbd2cc;font:1.15rem/1.65 var(--agate)}.hp-home-copy .lb-hero-actions{display:flex;gap:1rem;margin-top:2rem}.hp-home-copy .lb-btn{display:inline-flex;align-items:center;justify-content:space-between;gap:2rem;min-height:60px;padding:0 1.35rem;border:1px solid #727b76;color:#fff;text-decoration:none;font:800 .78rem var(--agate);letter-spacing:.04em;text-transform:uppercase}.hp-home-copy .lb-btn-primary{background:var(--lime);border-color:var(--lime);color:#080b09}.hp-home-copy .lb-proof-row{display:grid;grid-template-columns:repeat(4,1fr);max-width:790px;margin-top:2.4rem}.hp-home-copy .lb-proof{padding:0 1rem}.hp-home-copy .lb-proof:first-child{padding-left:0}.hp-home-copy .lb-proof+.lb-proof{border-left:1px solid #ffffff2e}.hp-home-copy .lb-proof strong,.hp-home-copy .lb-proof span{display:block}.hp-home-copy .lb-proof strong{font:700 1.15rem var(--agate)}.hp-home-copy .lb-proof span{margin-top:.3rem;color:#8e9991;font:700 .62rem var(--agate);letter-spacing:.07em;text-transform:uppercase}.hp-feature-intro{display:grid;grid-template-columns:1fr minmax(260px,430px);align-items:end;gap:2rem;margin-top:clamp(4rem,8vw,7rem);padding-top:2.5rem;border-top:1px solid #344038}.hp-feature-intro small{grid-column:1;color:var(--lime);font:800 .7rem var(--agate);letter-spacing:.12em}.hp-feature-intro h2{grid-column:1;margin:.35rem 0 0;font:700 clamp(2.3rem,5vw,4rem)/.95 var(--display)}.hp-feature-intro p{grid-column:2;grid-row:1/3;margin:0;color:#b9c2bb;font:1rem/1.55 var(--agate)}.hp-dual-feature{display:grid;grid-template-columns:1fr 1fr;gap:1.25rem;align-items:stretch;margin-top:2rem}.hp-dual-feature .hp-feature{display:flex;flex-direction:column;min-width:0}.hp-dual-feature .hp-feature .hp-card-cta{margin-top:auto}.hp-dual-feature .hp-feature h2{font-size:clamp(1.7rem,3vw,2.5rem)}.hp-dual-feature .hp-boundary{min-height:105px}.hp-sports{padding-top:clamp(3rem,6vw,5rem)}.hp-sports .hp-section-head{display:block}.hp-sports .hp-section-head h2{font-size:clamp(2.4rem,5vw,4.5rem)}
.hp-dual-feature .hp-feature-players{min-height:285px;align-items:start}.hp-dual-feature .hp-feature-players>div{display:grid;grid-template-rows:180px auto auto auto;align-content:start}.hp-dual-feature .hp-feature-players>div>img,.hp-dual-feature .hp-college-feature .hp-feature-players>div>img{height:180px;padding:0}.hp-dual-feature .hp-college-feature .hp-feature-players>div>img{padding:24px}.hp-dual-feature .hp-feature>h2{min-height:2.05em;margin-bottom:1rem}.hp-decision-summary{min-height:145px;padding:1rem 1.1rem;border-left:4px solid var(--lime);background:#151c19}.hp-decision-summary small,.hp-evidence-grid span{display:block;color:var(--lime);font:800 .64rem var(--agate);letter-spacing:.08em;text-transform:uppercase}.hp-decision-summary strong{display:block;margin:.35rem 0;font:700 1.7rem var(--display)}.hp-decision-summary p{margin:0;color:#c8d0ca;font:1rem/1.45 var(--agate)}.hp-evidence-grid{display:grid;grid-template-columns:1fr 1fr;min-height:176px;margin:1rem 0;border:1px solid #344039;background:#344039;gap:1px}.hp-evidence-grid>div{padding:.9rem;background:#101614}.hp-evidence-grid span{color:#8f9a92}.hp-evidence-grid b{display:block;margin-top:.45rem;color:#f2f5f0;font:700 .95rem/1.3 var(--agate)}.hp-dual-feature .hp-boundary{min-height:132px;margin-top:0}.hp-home-hero:before{display:none}
@media(max-width:900px){.hp-dual-feature .hp-feature-players,.hp-dual-feature .hp-feature>h2,.hp-decision-summary,.hp-evidence-grid,.hp-dual-feature .hp-boundary{min-height:0}.hp-dual-feature .hp-feature-players>div{grid-template-rows:140px auto auto auto}.hp-dual-feature .hp-feature-players>div>img,.hp-dual-feature .hp-college-feature .hp-feature-players>div>img{height:140px}.hp-dual-feature .hp-college-feature .hp-feature-players>div>img{padding:18px}}
@media(max-width:900px){.hp-board-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:1000px){.lb-edge,.hp-ambient-data{display:none}.lb-decision-hero .lb-hero-inner{width:min(920px,calc(100% - 32px));gap:32px}.sport-activities{display:none}.sport-header .navtoggle{display:block;margin-left:auto}}
@media(max-width:900px){.hp-action-grid,.hp-signal-grid{grid-template-columns:1fr 1fr}.hp-call-grid,.hp-method-grid{grid-template-columns:1fr}.hp-call-art>.hp-identity{grid-template-columns:60px 1fr}.hp-call-art>.hp-identity .hp-id-art{width:60px}.hp-trust{grid-template-columns:1fr}.hp-dual-feature{grid-template-columns:1fr}.hp-dual-feature .hp-boundary{min-height:0}}
@media(max-width:780px){.sport-header .tbrow{min-height:60px}.sport-header>.tbrow>.context-search{display:none}.sport-header .navdrawer:not([hidden]){display:block}.sport-switch .vbtn{padding:.45rem .55rem}.home-topbar .home-nav-cta{display:none}.lb-decision-hero{min-height:0}.lb-decision-hero .lb-hero-inner{grid-template-columns:1fr;padding:42px 0 54px;width:min(100% - 32px,580px);gap:34px}.hp-experience .lb-hero-title{font-size:clamp(42px,12vw,58px)}.lb-decision-hero .lb-hero-description{font-size:1.05rem;margin-top:20px}.lb-decision-hero .lb-hero-actions{flex-direction:column;align-items:stretch;margin-top:24px}.lb-decision-hero .lb-btn{min-height:54px}.lb-feature-card{margin:0!important}.hp-home-copy .lb-hero-actions{flex-direction:column}.hp-home-copy .lb-btn{min-height:54px}.hp-feature-intro{display:block}.hp-feature-intro p{margin-top:1rem}.hp-section{padding:3.2rem 1rem}.hp-section-head{display:block}.hp-section-head p{margin-top:1rem}.hp-sport-grid{grid-template-columns:1fr}.hp-actions{grid-template-columns:1fr 1fr}.dr-hero{padding-top:4rem}.dr-compare-head,.dr-section-head,.dr-verdict,.dr-evidence-title{align-items:stretch;flex-direction:column}.dr-selectors,.cdr-filters{grid-template-columns:1fr}.dr-selectors>b{text-align:center;padding:0}.dr-player-grid,.dr-future-grid,.dr-case-grid{grid-template-columns:1fr}.dr-boundary-grid,.dr-why-grid{grid-template-columns:1fr 1fr}.dr-quality-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.dr-card-grid,.dr-signal-grid,.dr-mover-grid{grid-template-columns:1fr}.dr-player-grid dl{grid-template-columns:1fr 1fr}.dr-adv{text-align:left}.dr-photo{max-width:44%}}
@media(max-width:520px){.hp-actions,.hp-board-grid,.hp-action-grid,.hp-signal-grid,.hp-mover-grid{grid-template-columns:1fr}.lb-decision-hero .lb-proof-row,.hp-home-copy .lb-proof-row{grid-template-columns:1fr 1fr;gap:1rem}.lb-decision-hero .lb-proof,.hp-home-copy .lb-proof{padding:0}.lb-decision-hero .lb-proof+.lb-proof,.hp-home-copy .lb-proof+.lb-proof{border-left:0}.hp-feature-players img{height:105px}.hp-feature-players .hp-team-mark{width:32px;height:32px}.hp-action{min-height:0}.hp-call-art{grid-template-columns:1fr}.hp-call-art>span{text-align:center}.hp-call-data{grid-template-columns:1fr}.hp-call-data small{grid-column:auto}.hp-mover-card>div{grid-template-columns:1fr 1fr 1fr}}
@media(max-width:430px){.dr-boundary-grid,.dr-player-grid dl,.dr-why-grid{grid-template-columns:1fr}.dr-hero>h1{font-size:clamp(2.65rem,15vw,3.35rem)}.dr-person{height:115px}.dr-photo{height:110px}}
@media(max-width:520px){.hp-sport-card{grid-template-columns:72px minmax(0,1fr);gap:.8rem;min-height:0}.hp-sport-code{min-width:72px;font-size:2.8rem}.hp-sport-card h3{font-size:1.5rem}}
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
    title = "Fantasy Football Rankings, Projections &amp; Decisions | LineupBeat"
    description = ("LineupBeat provides 2026 fantasy football rankings, NFL and college "
                   "projections, player comparisons, roster analysis, and clearly labeled "
                   "market context.")
    page = re.sub(r'<title>.*?</title>', f'<title>{title}</title>', page, count=1, flags=re.S)
    if re.search(r'<meta\s+name="description"[^>]*>', page, re.I):
        page = re.sub(r'<meta\s+name="description"[^>]*>',
                      f'<meta name="description" content="{description}">',
                      page, count=1, flags=re.I)
    else:
        page = page.replace("</head>", f'<meta name="description" content="{description}">\n</head>', 1)
    for property_name, content in (
            ("og:title", title), ("og:description", description),
            ("twitter:title", title), ("twitter:description", description)):
        pattern = rf'<meta\s+(?:property|name)="{re.escape(property_name)}"[^>]*>'
        attribute = "property" if property_name.startswith("og:") else "name"
        tag = f'<meta {attribute}="{property_name}" content="{content}">'
        if re.search(pattern, page, re.I):
            page = re.sub(pattern, tag, page, count=1, flags=re.I)
        else:
            page = page.replace("</head>", tag + "\n</head>", 1)
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
