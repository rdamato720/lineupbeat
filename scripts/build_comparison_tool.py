#!/usr/bin/env python3
"""Build the indexable Lineup Beat “Who Should I Draft?” comparison tool."""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_ranking_formats as formats  # noqa: E402
import build_rankings as base  # noqa: E402
import seo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
CONSISTENCY = ROOT / "data" / "nfl_player_consistency_2025.json"
ROSTER = ROOT / "rosters" / "nfl.csv"
PUBLICATIONS = ROOT / "data" / "wire_publications.json"
OUT = SITE / "nfl" / "who-should-i-draft"
FORMAT_LABELS = {"ppr": "PPR", "half_ppr": "Half-PPR",
                 "non_ppr": "Non-PPR", "superflex": "Superflex"}


def slug(name: str) -> str:
    return base.slugify(name)


def roster_data() -> dict[str, dict]:
    out = {}
    with ROSTER.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("position") not in base.POSITIONS:
                continue
            out[slug(row["name"])] = {
                "adp": float(row["adp"]) if row.get("adp") else None,
                "age": int(row["age"]) if row.get("age") else None,
                "depth": row.get("depth_order") or None,
                "injury": row.get("injury_status") or None,
            }
    return out


def consistency_data() -> dict[str, dict]:
    payload = json.loads(CONSISTENCY.read_text())
    out = {}
    for row in payload["players"]:
        out[slug(row["player_name"])] = row["formats"]
    return out


def latest_impacts() -> dict[str, dict]:
    payload = json.loads(PUBLICATIONS.read_text())
    out = {}
    for row in sorted(payload.get("publications", []),
                      key=lambda r: r.get("published_at", "")):
        out[slug(row.get("player_name", ""))] = {
            "label": row.get("reader_label"),
            "summary": row.get("public_evidence_summary"),
            "impact": row.get("lineupbeat_impact"),
            "updated": row.get("updated_at") or row.get("published_at"),
            "url": row.get("url"),
        }
    return out


def rank_sets() -> dict[str, list[dict]]:
    inputs = formats.read_projection_formats(formats.SOURCE)
    ranked = {key: formats.rank(inputs[key], key)[0]
              for key in ("ppr", "non_ppr", "superflex")}
    half = json.loads((ROOT / "data" / "nfl_rankings_2026.json").read_text())["players"]
    ranked["half_ppr"] = half
    return ranked


def player_payload() -> list[dict]:
    ranked = rank_sets()
    roster, history, impacts = roster_data(), consistency_data(), latest_impacts()
    names = {}
    for key, rows in ranked.items():
        for row in rows:
            if row.get("overall_rank") is None:
                continue
            s = slug(row["player_name"])
            p = names.setdefault(s, {"slug": s, "name": row["player_name"],
                                     "team": row["team"], "position": row["position"],
                                     "formats": {}})
            p["formats"][key] = {
                "overall_rank": row["overall_rank"],
                "position_rank": row["position_rank"],
                "projected_points": row["projected_points"],
                "vorp": row.get("vorp"),
            }
    for s, p in names.items():
        p.update(roster.get(s, {}))
        p["consistency"] = history.get(s, {})
        p["wire"] = impacts.get(s)
    return sorted(names.values(), key=lambda p: (p["position"], p["name"]))


def comparison_score(player: dict, scoring: str) -> float:
    f = player["formats"].get(scoring) or player["formats"].get("ppr")
    history_key = "ppr" if scoring == "superflex" else scoring
    h = player.get("consistency", {}).get(history_key, {})
    rank_value = max(0, 205 - f["overall_rank"]) / 2
    consistency = h.get("consistency_score", 50) * .22
    floor = h.get("floor_p25", 0) * .55
    return rank_value + consistency + floor


def recommendation(a: dict, b: dict, scoring: str) -> dict:
    sa, sb = comparison_score(a, scoring), comparison_score(b, scoring)
    winner, other = (a, b) if sa >= sb else (b, a)
    gap = abs(sa - sb)
    confidence = "Strong" if gap >= 18 else "Moderate" if gap >= 8 else "Slight"
    wf = winner["formats"].get(scoring) or winner["formats"]["ppr"]
    of = other["formats"].get(scoring) or other["formats"]["ppr"]
    reasons = [f'{winner["name"]} ranks {wf["overall_rank"]}th overall versus {of["overall_rank"]}th for {other["name"]}.']
    hk = "ppr" if scoring == "superflex" else scoring
    wh, oh = winner.get("consistency", {}).get(hk), other.get("consistency", {}).get(hk)
    if wh and oh:
        if wh["floor_p25"] > oh["floor_p25"]:
            reasons.append(f'{winner["name"]} had the stronger 2025 weekly floor ({wh["floor_p25"]} vs. {oh["floor_p25"]}).')
        elif wh["ceiling_p75"] > oh["ceiling_p75"]:
            reasons.append(f'{winner["name"]} had the stronger 2025 weekly ceiling ({wh["ceiling_p75"]} vs. {oh["ceiling_p75"]}).')
    return {"winner": winner["name"], "winner_slug": winner["slug"],
            "confidence": confidence, "reasons": reasons}


CSS = r"""
.cmpwrap{max-width:1180px;margin:auto;padding:2.2rem 1rem 5rem}.cmphead{text-align:center;max-width:850px;margin:0 auto 2rem}.cmphead h1{font:700 clamp(2.5rem,6vw,5.4rem)/.94 var(--display);margin:.4rem 0 1rem}.cmphead p{color:var(--text);font-size:1.08rem}.cmpbox{border:1px solid var(--rule);background:linear-gradient(145deg,#111716,#080b0b);padding:1.2rem;border-top:4px solid var(--signal)}.cmpselectors{display:grid;grid-template-columns:1fr auto 1fr;gap:1rem;align-items:end}.cmpselect label,.fmt label{display:block;font:700 .72rem var(--agate);letter-spacing:.11em;color:var(--quiet);text-transform:uppercase;margin-bottom:.45rem}.cmpselect select,.fmt select{width:100%;background:#090d0d;color:var(--ink);border:1px solid var(--rule);padding:.85rem;font:600 1rem var(--text)}.versus{font:800 1rem var(--agate);color:var(--signal);padding-bottom:1rem}.fmt{max-width:250px;margin:1rem auto}.cmpresult{display:none}.cmpresult.ready{display:block}.verdict{margin:1.5rem 0;border:1px solid #35411d;background:#11170d;padding:1.4rem;text-align:center}.verdict .pick{color:var(--signal);font:800 .74rem var(--agate);letter-spacing:.14em;text-transform:uppercase}.verdict h2{font:700 clamp(2rem,4vw,3.4rem)/1 var(--display);margin:.4rem 0}.verdict p{max-width:760px;margin:.5rem auto;color:var(--text)}.players{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.pcard{border:1px solid var(--rule);background:#0d1111;padding:1.2rem}.pcard h3{font:700 1.8rem var(--display);margin:.3rem 0}.chips{display:flex;gap:.35rem;flex-wrap:wrap}.chip{border:1px solid var(--rule);padding:.25rem .55rem;font:700 .68rem var(--agate);letter-spacing:.08em}.metrics{display:grid;grid-template-columns:repeat(2,1fr);gap:.6rem;margin-top:1rem}.metric{background:#131818;padding:.75rem}.metric b{display:block;font:700 1.35rem var(--display)}.metric span{font:600 .67rem var(--agate);color:var(--quiet);letter-spacing:.08em;text-transform:uppercase}.edges{margin-top:1rem;border-top:1px solid var(--rule)}.edge{display:grid;grid-template-columns:1fr auto 1fr;gap:.8rem;padding:.85rem 0;border-bottom:1px solid var(--rule);align-items:center}.edge .left{text-align:right}.edge .right{text-align:left}.edge strong{color:var(--signal)}.edge small{display:block;color:var(--quiet);font:600 .65rem var(--agate);text-transform:uppercase;letter-spacing:.08em}.wireimpact{margin-top:1rem;padding:1rem;border-left:3px solid var(--signal);background:#13190f}.wireimpact b{color:var(--signal);font:700 .7rem var(--agate);text-transform:uppercase;letter-spacing:.1em}.popular{margin-top:3rem}.popular h2{font:700 2rem var(--display)}.pairgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:.7rem}.pairgrid a{display:block;border:1px solid var(--rule);padding:1rem;color:var(--ink);text-decoration:none}.pairgrid a:hover{border-color:var(--signal)}.method{margin-top:3rem;padding:1.4rem;border:1px solid var(--rule)}
@media(max-width:760px){.cmpselectors{grid-template-columns:1fr}.versus{text-align:center;padding:0}.players,.pairgrid{grid-template-columns:1fr}.edge{grid-template-columns:1fr}.edge .left,.edge .right{text-align:center}.metrics{grid-template-columns:repeat(2,1fr)}}
"""

JS = r"""
(()=>{const DATA=JSON.parse(document.getElementById('cmpdata').textContent),by=Object.fromEntries(DATA.map(p=>[p.slug,p]));const a=document.getElementById('pa'),b=document.getElementById('pb'),fmt=document.getElementById('format'),out=document.getElementById('cmpresult');const labels={ppr:'PPR',half_ppr:'Half-PPR',non_ppr:'Non-PPR',superflex:'Superflex'};function n(v,d='—'){return v===null||v===undefined?d:v}function hist(p,k){return p.consistency[k==='superflex'?'ppr':k]||{}}function rank(p,k){return p.formats[k]||p.formats.ppr}function edge(label,av,bv,low=false,suffix=''){let win=av===bv?'Even':((low?av<bv:av>bv)?'a':'b');return `<div class="edge"><div class="left ${win==='a'?'win':''}">${win==='a'?'<strong>':''}${n(av)}${suffix}${win==='a'?'</strong>':''}</div><small>${label}</small><div class="right ${win==='b'?'win':''}">${win==='b'?'<strong>':''}${n(bv)}${suffix}${win==='b'?'</strong>':''}</div></div>`}function card(p,k){let f=rank(p,k),h=hist(p,k);return `<article class="pcard"><div class="chips"><span class="chip">${p.team} ${p.position}</span><span class="chip">${p.position}${f.position_rank}</span>${p.adp?`<span class="chip">ADP ${p.adp}</span>`:''}</div><h3>${p.name}</h3><div class="metrics"><div class="metric"><b>${f.overall_rank}</b><span>Overall rank</span></div><div class="metric"><b>${f.projected_points}</b><span>Projected pts</span></div><div class="metric"><b>${n(h.average)}</b><span>2025 avg</span></div><div class="metric"><b>${n(h.consistency_score)}</b><span>Consistency /100</span></div></div>${p.wire?`<div class="wireimpact"><b>Latest Lineup Beat impact</b><p>${p.wire.impact}</p></div>`:''}</article>`}function recommendation(x,y,k){let fx=rank(x,k),fy=rank(y,k),hx=hist(x,k),hy=hist(y,k);let sx=(205-fx.overall_rank)/2+n(hx.consistency_score,50)*.22+n(hx.floor_p25,0)*.55,sy=(205-fy.overall_rank)/2+n(hy.consistency_score,50)*.22+n(hy.floor_p25,0)*.55,w=sx>=sy?x:y,o=sx>=sy?y:x,g=Math.abs(sx-sy),c=g>=18?'Strong':g>=8?'Moderate':'Slight';return {w,o,c}}function draw(){let x=by[a.value],y=by[b.value],k=fmt.value;if(!x||!y||x.slug===y.slug){out.className='cmpresult';return}let r=recommendation(x,y,k),fx=rank(x,k),fy=rank(y,k),hx=hist(x,k),hy=hist(y,k);out.innerHTML=`<section class="verdict"><div class="pick">${r.c} edge · ${labels[k]}</div><h2>Draft ${r.w.name}</h2><p>${r.w.name} gets the Lineup Beat edge over ${r.o.name} after combining current rank and projection with weekly floor and consistency. Use the category breakdown below to decide whether your roster needs safety or ceiling.</p></section><div class="players">${card(x,k)}${card(y,k)}</div><div class="edges">${edge('Overall rank',fx.overall_rank,fy.overall_rank,true)}${edge('Projected points',fx.projected_points,fy.projected_points)}${edge('2025 points per game',hx.average,hy.average)}${edge('Weekly floor · 25th percentile',hx.floor_p25,hy.floor_p25)}${edge('Weekly ceiling · 75th percentile',hx.ceiling_p75,hy.ceiling_p75)}${edge('Consistency score',hx.consistency_score,hy.consistency_score)}${edge('Boom games',hx.boom_rate,hy.boom_rate,false,'%')}${edge('Bust games',hx.bust_rate,hy.bust_rate,true,'%')}${edge('ADP',x.adp,y.adp,true)}</div>`;out.className='cmpresult ready';history.replaceState(null,'',`?player1=${x.slug}&player2=${y.slug}&format=${k}`)}function options(sel,chosen){sel.innerHTML=DATA.map(p=>`<option value="${p.slug}" ${p.slug===chosen?'selected':''}>${p.name} · ${p.team} ${p.position}</option>`).join('')}let q=new URLSearchParams(location.search),one=q.get('player1')||document.body.dataset.a||'bijan-robinson',two=q.get('player2')||document.body.dataset.b||'jahmyr-gibbs';options(a,one);options(b,two);fmt.value=q.get('format')||'ppr';[a,b,fmt].forEach(el=>el.addEventListener('change',draw));draw()})();
"""


def popular_pairs(players: list[dict]) -> list[tuple[dict, dict]]:
    by_pos = {}
    for p in players:
        f = p["formats"].get("ppr")
        if f and f["position_rank"] <= 30:
            by_pos.setdefault(p["position"], []).append(p)
    pairs, seen = [], set()
    for group in by_pos.values():
        group.sort(key=lambda p: p["formats"]["ppr"]["position_rank"])
        for i, a in enumerate(group):
            for b in group[i + 1:i + 3]:
                key = tuple(sorted((a["slug"], b["slug"])))
                if key not in seen:
                    seen.add(key); pairs.append((a, b))
    return pairs


def html(players: list[dict], built: datetime, a: dict | None = None,
         b: dict | None = None, pairs: list[tuple[dict, dict]] | None = None) -> str:
    is_pair = bool(a and b)
    if is_pair:
        rec = recommendation(a, b, "ppr")
        title = f'{a["name"]} or {b["name"]}: Who Should I Draft? | LineupBeat'
        desc = f'Compare {a["name"]} and {b["name"]} for 2026 fantasy football using rankings, projections, ADP, weekly consistency, floor, ceiling and recent news.'
        path = f'/nfl/who-should-i-draft/{a["slug"]}-vs-{b["slug"]}/'
        intro = f'<p><strong>Lineup Beat PPR pick: {base.esc(rec["winner"])}</strong>. Change the scoring format below to see whether the recommendation moves.</p>'
    else:
        title = "Who Should I Draft? Fantasy Football Comparison Tool | LineupBeat"
        desc = "Compare two 2026 fantasy football players by rankings, projections, ADP, weekly consistency, floor, ceiling, durability and recent news."
        path = "/nfl/who-should-i-draft/"; intro = ""
    css, header, footer = base.site_chrome()
    links = pairs or []
    pair_html = "".join(f'<a href="/nfl/who-should-i-draft/{x["slug"]}-vs-{y["slug"]}/"><b>{base.esc(x["name"])}</b> or <b>{base.esc(y["name"])}</b>?</a>' for x, y in links[:36])
    # Pair pages need only their two records. Repeating the entire 218-player
    # payload on every indexable matchup inflated the deploy by tens of MB.
    embedded = [a, b] if is_pair else players
    data = json.dumps(embedded, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    faq = [("What does the consistency score measure?", "It combines the variation in a player's 2025 weekly fantasy scoring with the size of his games-played sample. A higher score means steadier production, not necessarily more points."),
           ("Does the tool use last year's average alone?", "No. The recommendation combines current 2026 ranking and projection with 2025 average, floor, ceiling, boom rate, bust rate, ADP and recent Lineup Beat context."),
           ("Can the recommendation change by scoring format?", "Yes. PPR, Half-PPR, Non-PPR and Superflex use different rankings, projections and historical scoring results.")]
    schema = seo.graph({"@type": "WebApplication", "name": title, "url": "https://lineupbeat.com" + path,
                        "applicationCategory": "SportsApplication", "description": desc},
                       seo.faq_schema(faq), seo.ORGANISATION)
    body_attrs = f'data-a="{a["slug"] if a else ""}" data-b="{b["slug"] if b else ""}"'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{base.esc(title)}</title><meta name="description" content="{base.esc(desc)}"><link rel="canonical" href="https://lineupbeat.com{path}">{seo.social_meta(title, desc, "https://lineupbeat.com" + path, base.OG_IMAGE)}<script type="application/ld+json">{schema}</script><style>{css}{base.PAGE_CSS}{seo.CRUMB_CSS}{seo.UI_CSS}{seo.RELATED_CSS}{CSS}</style></head><body {body_attrs}>{header}<main class="cmpwrap"><nav class="crumbs"><a href="/">Home</a><span>/</span><a href="/nfl/rankings/">Rankings</a><span>/</span><b>Who Should I Draft?</b></nav><header class="cmphead"><p class="rkeyebrow">LINEUP BEAT DRAFT LAB</p><h1>Who Should I Draft?</h1><p>Put two players head to head. Compare what they are projected to do with how they actually scored week to week.</p>{intro}<p class="rkstatus">Updated {built:%B %d, %Y} · 2026 projections · 2025 weekly results</p></header><section class="cmpbox"><div class="cmpselectors"><div class="cmpselect"><label for="pa">Player one</label><select id="pa"></select></div><div class="versus">VS</div><div class="cmpselect"><label for="pb">Player two</label><select id="pb"></select></div></div><div class="fmt"><label for="format">Scoring format</label><select id="format"><option value="ppr">PPR</option><option value="half_ppr">Half-PPR</option><option value="non_ppr">Non-PPR</option><option value="superflex">Superflex</option></select></div><div id="cmpresult" class="cmpresult"></div></section>{f'<section class="popular"><h2>Popular draft decisions</h2><div class="pairgrid">{pair_html}</div></section>' if pair_html else ''}<section class="method"><h2>More than an average</h2><p>A season average can hide a player who alternates between 25 points and five. Lineup Beat shows the median, standard deviation, 25th-percentile floor, 75th-percentile ceiling, boom rate and bust rate from every 2025 regular-season appearance. The recommendation then balances that history against the current 2026 projection and rank.</p><p>Historical results describe what happened; they do not guarantee the same role or health. Recent approved Lineup Beat impact is displayed separately when available.</p></section>{seo.faq_html(faq)}{seo.related_html('rankings')}</main><script id="cmpdata" type="application/json">{data}</script><script>{JS}</script>{footer}{seo.TRACKING}{seo.VIEW_CONTENT}</body></html>'''


def main() -> int:
    players = player_payload()
    if len(players) < 180:
        raise SystemExit("comparison player pool is unexpectedly small")
    built = formats.source_updated(formats.SOURCE)
    pairs = popular_pairs(players)
    OUT.mkdir(parents=True, exist_ok=True)
    hub = seo.check_page(html(players, built, pairs=pairs), str(OUT / "index.html"))
    (OUT / "index.html").write_text(hub)
    for a, b in pairs:
        dest = OUT / f'{a["slug"]}-vs-{b["slug"]}' / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        related = [(x, y) for x, y in pairs
                   if (x["position"] == a["position"] and
                       {x["slug"], y["slug"]} != {a["slug"], b["slug"]})][:12]
        dest.write_text(seo.check_page(html(players, built, a, b, related), str(dest)))
    print(f"  comparison pool: {len(players)} players")
    print(f"  wrote {1 + len(pairs)} pages under {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
