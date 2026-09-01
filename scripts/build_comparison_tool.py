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
TEAM_COLORS = {
    "ARI": ("#97233F", "#FFB612"), "ATL": ("#A71930", "#A5ACAF"),
    "BAL": ("#241773", "#9E7C0C"), "BUF": ("#00338D", "#C60C30"),
    "CAR": ("#0085CA", "#BFC0BF"), "CHI": ("#0B162A", "#C83803"),
    "CIN": ("#FB4F14", "#000000"), "CLE": ("#FF3C00", "#311D00"),
    "DAL": ("#003594", "#869397"), "DEN": ("#FB4F14", "#002244"),
    "DET": ("#0076B6", "#B0B7BC"), "GB": ("#203731", "#FFB612"),
    "HOU": ("#03202F", "#A71930"), "IND": ("#002C5F", "#A2AAAD"),
    "JAX": ("#006778", "#D7A22A"), "KC": ("#E31837", "#FFB81C"),
    "LV": ("#292929", "#A5ACAF"), "LAC": ("#0080C6", "#FFC20E"),
    "LAR": ("#003594", "#FFA300"), "MIA": ("#008E97", "#FC4C02"),
    "MIN": ("#4F2683", "#FFC62F"), "NE": ("#002244", "#C60C30"),
    "NO": ("#101820", "#D3BC8D"), "NYG": ("#0B2265", "#A71930"),
    "NYJ": ("#125740", "#FFFFFF"), "PHI": ("#004C54", "#A5ACAF"),
    "PIT": ("#101820", "#FFB612"), "SF": ("#AA0000", "#B3995D"),
    "SEA": ("#002244", "#69BE28"), "TB": ("#D50A0A", "#FF7900"),
    "TEN": ("#0C2340", "#4B92DB"), "WAS": ("#5A1414", "#FFB612"),
}


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
                "photo": (f'https://sleepercdn.com/content/nfl/players/thumb/'
                          f'{re.sub(r"^[a-z]+-", "", row["id"])}.jpg')
                         if row.get("id") else None,
                "photo_fallback": (f'https://a.espncdn.com/i/headshots/nfl/players/full/'
                                   f'{row["espn_id"]}.png') if row.get("espn_id") else None,
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
        p["team_logo"] = f'https://a.espncdn.com/i/teamlogos/nfl/500/{p["team"].lower()}.png'
        p["team_colors"] = TEAM_COLORS.get(p["team"], ("#263238", "#68757B"))
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
/* Match the homepage's editorial typography: Source Serif for editorial
   headlines and body copy, Barlow Condensed for players and UI labels, and
   the monospace face for comparison data. */
.cmpwrap{position:relative;max-width:1430px;padding:3.8rem 2rem 6rem}
.cmpwrap:before{content:"";position:fixed;z-index:-1;inset:0;pointer-events:none;
  background-image:linear-gradient(rgba(198,245,60,.035) 1px,transparent 1px),
  linear-gradient(90deg,rgba(198,245,60,.035) 1px,transparent 1px),
  radial-gradient(circle at 50% 25%,rgba(35,48,34,.2),transparent 44%);
  background-size:98px 98px,98px 98px,auto}
.cmphead{max-width:980px;margin:2.4rem auto 2.8rem}
.cmphead h1,.verdict h2{font-family:var(--text);font-weight:400;letter-spacing:-.025em}
.cmphead h1{font-size:clamp(3.6rem,7vw,6.7rem);line-height:.9;margin:.7rem 0 1.5rem}
.cmphead h1 span{color:var(--signal)}
.cmphead p,.verdict p{color:var(--muted)}
.cmphead>p:not(.rkeyebrow):not(.rkstatus){max-width:760px;margin-left:auto;margin-right:auto;font-size:1.25rem;line-height:1.55}
.cmphead .rkeyebrow{font-size:.92rem;letter-spacing:.13em}
.cmpbox{max-width:1180px;margin:0 auto;border:1px solid #3b4241;border-top:1px solid #3b4241;
  border-radius:24px;background:linear-gradient(145deg,rgba(18,23,22,.98),rgba(8,11,11,.98));
  padding:2rem;box-shadow:0 24px 80px rgba(0,0,0,.28)}
.cmpselectors{gap:1.5rem}.cmpselect label,.fmt label{font-size:.78rem;letter-spacing:.13em}
.cmpselect select,.fmt select{min-height:56px;border-color:#343b3e;border-radius:10px;background:#0b1012;
  font-family:var(--text);font-weight:600}.versus{font-size:1.15rem;letter-spacing:.12em}
.fmt{max-width:300px;margin:1.35rem auto}.verdict{border-radius:16px;border-color:#445224;
  background:linear-gradient(110deg,#11170d,#17200d);padding:1.8rem}
.pcard h3,.popular h2,.method h2{font-family:var(--agate);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.pcard{border-radius:15px;border-color:#303638;padding:1.5rem}.pcard h3{font-size:2.1rem}
.pcard{position:relative;overflow:hidden;padding:0;background:linear-gradient(145deg,color-mix(in srgb,var(--team) 42%,#0b1010),#0b1010 68%)}
.pcard:before{content:"";position:absolute;inset:0;background:radial-gradient(circle at 82% 22%,color-mix(in srgb,var(--team2) 28%,transparent),transparent 45%);pointer-events:none}
.pcardhero{position:relative;min-height:245px;padding:1.5rem;overflow:hidden}.pcopy{position:relative;z-index:3;max-width:66%}
.pcard h3{font-size:2.35rem;line-height:.95;margin-top:1rem}.pheadshot{position:absolute;z-index:2;right:-1%;bottom:-10px;width:47%;height:94%;object-fit:contain;object-position:center bottom;filter:drop-shadow(0 15px 18px rgba(0,0,0,.4))}
.pwatermark{position:absolute;z-index:1;right:5%;top:15%;width:37%;height:55%;object-fit:contain;opacity:.12;filter:grayscale(1)}
.teamchip{display:inline-flex;align-items:center;gap:.35rem}.teamchip img{width:19px;height:19px;object-fit:contain}
.pcard .metrics{position:relative;z-index:4;margin:0;padding:1rem;grid-template-columns:repeat(4,1fr);background:rgba(5,8,8,.72);border-top:1px solid rgba(255,255,255,.11)}
.pcard .metric{padding:.8rem .65rem;background:rgba(255,255,255,.04)}.pcard .metric b{font-size:1rem}
.metric b,.edge .left,.edge .right{font-family:var(--data)}
.metric{border-radius:8px;background:#141918}.edge{font-size:1rem}.wireimpact{border-radius:0 10px 10px 0}
.chip{text-transform:uppercase}
.popular,.method{max-width:1180px;width:100%;margin-left:auto;margin-right:auto}.popular{margin-top:4rem}
.popular h2,.method h2{font-size:1.55rem;letter-spacing:.09em}.pairgrid a{border-radius:10px;background:#0d1111}
.method{margin-top:4rem;border-color:#303638;border-radius:16px;background:#0d1111;padding:2rem}
/* The comparison landing page uses the homepage hero itself as the layout
   model: 1130px two-column frame, 72px field grid, editorial copy on the
   left and one tall utility panel on the right. */
.cmpwrap{display:grid;grid-template-columns:1.04fr .9fr;gap:0 72px;align-items:start;
  max-width:none;min-height:790px;margin:0;padding:40px max(32px,calc((100vw - 1130px)/2)) 110px;
  background:radial-gradient(circle at 37% 20%,rgba(35,43,45,.33) 0%,rgba(12,15,16,.16) 32%,rgba(5,7,8,0) 55%),#050708}
.cmpwrap:before{display:none}.cmpwrap>*{position:relative;z-index:1}
.cmpwrap>.crumbs{display:none}.cmphead{grid-column:1;margin:23px 0 0;text-align:left;max-width:650px}
.topbar .tbrow{height:56px;padding-top:0;padding-bottom:0}.topbar .stamp{margin-left:auto;white-space:nowrap;color:var(--quiet);font-size:11px}
.cmphead .rkeyebrow{margin:0 0 23px;font-size:20px;font-weight:700;letter-spacing:.045em;color:var(--signal)}
.cmphead h1{max-width:650px;margin:0;font-size:clamp(56px,4.5vw,72px);line-height:.99;letter-spacing:-.033em}
.cmphead>p:not(.rkeyebrow):not(.rkstatus){max-width:590px;margin:33px 0 0;font-size:19px;line-height:1.7}
.cmphead .rkstatus{margin-top:30px;font-size:12px;letter-spacing:.085em}
.cmpbox{grid-column:2;width:100%;max-width:475px;height:auto;margin:43px 0 0;justify-self:end;
  padding:0 26px 30px;border:1px solid rgba(255,255,255,.22);border-radius:18px;
  background:linear-gradient(180deg,rgba(17,21,22,.97),rgba(13,17,18,.97));box-shadow:0 32px 90px rgba(0,0,0,.38)}
.cmpbox:before{content:"PLAYER COMPARISON";display:flex;align-items:center;height:67px;margin:0 -26px 28px;
  padding:0 26px;border-bottom:1px solid rgba(255,255,255,.13);color:var(--signal);
  font:700 18px var(--agate);letter-spacing:.05em;text-transform:uppercase}
.cmpselectors{grid-template-columns:1fr;gap:14px}.versus{text-align:center;padding:0;color:var(--signal)}
.cmpselect select,.fmt select{height:62px;font-size:18px}.fmt{max-width:none;margin:18px 0 0}
.cmpselect select,.fmt select{font-family:var(--agate);font-size:20px;font-weight:700;letter-spacing:.015em}
.cmpgo{width:100%;height:72px;margin-top:24px;border:0;border-radius:8px;background:var(--signal);
  color:#060806;cursor:pointer;font:700 18px var(--agate);letter-spacing:.055em;text-transform:uppercase}
.cmpgo:hover{background:#d4ff4b;transform:translateY(-2px)}
.cmpactions{display:flex;gap:18px;margin-top:35px}.cmpaction{height:72px;display:inline-flex;align-items:center;
  justify-content:center;min-width:220px;padding:0 28px;border:1px solid rgba(255,255,255,.3);
  border-radius:8px;color:var(--ink);font:700 18px var(--agate);letter-spacing:.045em;text-decoration:none}
.cmpaction.primary{background:var(--signal);border-color:var(--signal);color:#060806}.cmpaction:hover{border-color:var(--signal)}
.cmpproof{display:grid;grid-template-columns:1fr 1fr 1.15fr;margin-top:47px}.cmpproof div{min-height:75px;
  padding:0 20px;display:flex;flex-direction:column;justify-content:center}.cmpproof div:first-child{padding-left:0}
.cmpproof div+div{border-left:1px solid rgba(255,255,255,.18)}.cmpproof strong{font:700 34px/.95 var(--agate)}
.cmpproof span{margin-top:8px;color:var(--quiet);font:600 11px var(--agate);letter-spacing:.085em;text-transform:uppercase}
.cmpedge{position:absolute;z-index:0;top:60px;bottom:0;width:205px;opacity:.22;pointer-events:none}
.cmpedge.left{left:0}.cmpedge.right{right:0}.cmpmini{position:absolute;width:185px;padding:13px;border:1px solid rgba(255,255,255,.16);
  border-radius:5px;background:rgba(13,17,18,.52);color:var(--quiet);font:500 12px var(--agate);letter-spacing:.05em;text-transform:uppercase}
.cmpedge.left .cmpmini:nth-child(1){top:20px;left:0}.cmpedge.left .cmpmini:nth-child(2){top:260px;left:15px}
.cmpedge.right .cmpmini:nth-child(1){top:70px;right:0}.cmpedge.right .cmpmini:nth-child(2){top:360px;right:15px}
.cmpbars i{display:block;height:5px;margin:14px 0;background:linear-gradient(90deg,var(--signal) var(--w),rgba(255,255,255,.05) var(--w))}
.cmpresult.ready{grid-column:1/-1;margin-top:80px}.cmpresult .verdict{max-width:1130px;margin:0 auto 1.5rem}
.cmpresult .players,.cmpresult .edges{max-width:1130px;margin-left:auto;margin-right:auto}
.cmpresult .edges{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:18px;border:0}
.cmpresult .edge{grid-template-columns:1fr 1fr;grid-template-areas:"label label" "left right";gap:7px 18px;
  padding:18px 22px;border:1px solid #303638;border-radius:12px;background:linear-gradient(145deg,#111615,#0b0f0f)}
.cmpresult .edge small{grid-area:label;text-align:center;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,.08)}
.cmpresult .edge .left{grid-area:left;text-align:center;font-size:1.35rem}.cmpresult .edge .right{grid-area:right;text-align:center;font-size:1.35rem}
.cmpresult .edge strong{display:inline-block;padding:2px 8px;border-radius:5px;background:rgba(198,245,60,.1)}
.popular,.method,.faq,.related{grid-column:1/-1}.popular{margin-top:80px}
@media(max-width:1100px){.cmpedge{display:none}}
@media(max-width:900px){.cmpwrap{grid-template-columns:1fr;gap:36px;padding:45px 24px 80px}.cmphead,.cmpbox{grid-column:1}.cmpbox{justify-self:stretch;max-width:none;height:auto;min-height:0;margin-top:0}.cmpresult.ready,.popular{margin-top:35px}.topbar .stamp{display:none}.cmpactions{flex-wrap:wrap}.cmpproof{grid-template-columns:1fr 1fr 1fr}}
@media(max-width:760px){.cmpselectors{grid-template-columns:1fr}.versus{text-align:center;padding:0}.players,.pairgrid,.cmpresult .edges{grid-template-columns:1fr}.edge .left,.edge .right{text-align:center}.metrics,.pcard .metrics{grid-template-columns:repeat(2,1fr)}.pcardhero{min-height:220px}.pcard h3{font-size:1.9rem}.pheadshot{width:50%}}
"""

JS = r"""
(()=>{const DATA=JSON.parse(document.getElementById('cmpdata').textContent),by=Object.fromEntries(DATA.map(p=>[p.slug,p]));const a=document.getElementById('pa'),b=document.getElementById('pb'),fmt=document.getElementById('format'),out=document.getElementById('cmpresult');const labels={ppr:'PPR',half_ppr:'Half-PPR',non_ppr:'Non-PPR',superflex:'Superflex'};function n(v,d='—'){return v===null||v===undefined?d:v}function hist(p,k){return p.consistency[k==='superflex'?'ppr':k]||{}}function rank(p,k){return p.formats[k]||p.formats.ppr}function edge(label,av,bv,low=false,suffix=''){let win=av===bv?'Even':((low?av<bv:av>bv)?'a':'b');return `<div class="edge"><div class="left ${win==='a'?'win':''}">${win==='a'?'<strong>':''}${n(av)}${suffix}${win==='a'?'</strong>':''}</div><small>${label}</small><div class="right ${win==='b'?'win':''}">${win==='b'?'<strong>':''}${n(bv)}${suffix}${win==='b'?'</strong>':''}</div></div>`}function card(p,k){let f=rank(p,k),h=hist(p,k);return `<article class="pcard"><div class="chips"><span class="chip">${p.team} ${p.position}</span><span class="chip">${p.position}${f.position_rank}</span>${p.adp?`<span class="chip">ADP ${p.adp}</span>`:''}</div><h3>${p.name}</h3><div class="metrics"><div class="metric"><b>${f.overall_rank}</b><span>Overall rank</span></div><div class="metric"><b>${f.projected_points}</b><span>Projected pts</span></div><div class="metric"><b>${n(h.average)}</b><span>2025 avg</span></div><div class="metric"><b>${n(h.consistency_score)}</b><span>Consistency /100</span></div></div>${p.wire?`<div class="wireimpact"><b>Approved decision context</b><p>${p.wire.impact}</p></div>`:''}</article>`}function recommendation(x,y,k){let fx=rank(x,k),fy=rank(y,k),hx=hist(x,k),hy=hist(y,k);let sx=(205-fx.overall_rank)/2+n(hx.consistency_score,50)*.22+n(hx.floor_p25,0)*.55,sy=(205-fy.overall_rank)/2+n(hy.consistency_score,50)*.22+n(hy.floor_p25,0)*.55,w=sx>=sy?x:y,o=sx>=sy?y:x,g=Math.abs(sx-sy),c=g>=18?'Strong':g>=8?'Moderate':'Slight';return {w,o,c}}function draw(){let x=by[a.value],y=by[b.value],k=fmt.value;if(!x||!y||x.slug===y.slug){out.className='cmpresult';return}let r=recommendation(x,y,k),fx=rank(x,k),fy=rank(y,k),hx=hist(x,k),hy=hist(y,k);out.innerHTML=`<section class="verdict"><div class="pick">${r.c} edge · ${labels[k]}</div><h2>Draft ${r.w.name}</h2><p>${r.w.name} gets the Lineup Beat edge over ${r.o.name} after combining current rank and projection with weekly floor and consistency. Use the category breakdown below to decide whether your roster needs safety or ceiling.</p></section><div class="players">${card(x,k)}${card(y,k)}</div><div class="edges">${edge('Overall rank',fx.overall_rank,fy.overall_rank,true)}${edge('Projected points',fx.projected_points,fy.projected_points)}${edge('2025 points per game',hx.average,hy.average)}${edge('Weekly floor · 25th percentile',hx.floor_p25,hy.floor_p25)}${edge('Weekly ceiling · 75th percentile',hx.ceiling_p75,hy.ceiling_p75)}${edge('Consistency score',hx.consistency_score,hy.consistency_score)}${edge('Boom games',hx.boom_rate,hy.boom_rate,false,'%')}${edge('Bust games',hx.bust_rate,hy.bust_rate,true,'%')}${edge('ADP',x.adp,y.adp,true)}</div>`;out.className='cmpresult ready';history.replaceState(null,'',`?player1=${x.slug}&player2=${y.slug}&format=${k}`)}function options(sel,chosen){sel.innerHTML=DATA.map(p=>`<option value="${p.slug}" ${p.slug===chosen?'selected':''}>${p.name} · ${p.team} ${p.position}</option>`).join('')}let q=new URLSearchParams(location.search),one=q.get('player1')||document.body.dataset.a||'bijan-robinson',two=q.get('player2')||document.body.dataset.b||'jahmyr-gibbs';options(a,one);options(b,two);fmt.value=q.get('format')||'ppr';[a,b,fmt].forEach(el=>el.addEventListener('change',draw));draw()})();
"""

# Give each side of the comparison the same visual identity used on player
# pages: team colour, team mark and a real player headshot. Keeping this as
# a focused replacement leaves the calculation code below unchanged.
JS = re.sub(
    r"function card\(p,k\)\{.*?\}function recommendation",
    r'''function card(p,k){let f=rank(p,k),h=hist(p,k),c=p.team_colors||['#263238','#68757B'],photo=p.photo||p.team_logo;return `<article class="pcard" style="--team:${c[0]};--team2:${c[1]}"><div class="pcardhero"><img class="pwatermark" src="${p.team_logo}" alt=""><div class="pcopy"><div class="chips"><span class="chip teamchip"><img src="${p.team_logo}" alt="">${p.team} ${p.position}</span><span class="chip">${p.position}${f.position_rank}</span>${p.adp?`<span class="chip">ADP ${p.adp}</span>`:''}</div><h3>${p.name}</h3></div><img class="pheadshot" src="${photo}" alt="${p.name}" onerror="this.onerror=null;this.src='${p.team_logo}'"></div><div class="metrics"><div class="metric"><b>${f.overall_rank}</b><span>Overall rank</span></div><div class="metric"><b>${f.projected_points}</b><span>Projected pts</span></div><div class="metric"><b>${n(h.average)}</b><span>2025 avg</span></div><div class="metric"><b>${n(h.consistency_score)}</b><span>Consistency /100</span></div></div>${p.wire?`<div class="wireimpact"><b>Approved decision context</b><p>${p.wire.impact}</p></div>`:''}</article>`}function recommendation''',
    JS,
    count=1,
    flags=re.S,
)
JS = JS.replace("this.src='${p.team_logo}'",
                "this.src='${p.photo_fallback||p.team_logo}'")


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
        desc = f'Compare {a["name"]} and {b["name"]} using 2026 projections, rankings, ADP, and validated 2025 weekly consistency, floor, and ceiling.'
        path = f'/nfl/who-should-i-draft/{a["slug"]}-vs-{b["slug"]}/'
        intro = f'<p><strong>Lineup Beat PPR pick: {base.esc(rec["winner"])}</strong>. Change the scoring format below to see whether the recommendation moves.</p>'
    else:
        title = "Who Should I Draft? Fantasy Football Comparison Tool | LineupBeat"
        desc = "Advanced NFL Draft Comparison using 2026 rankings, projections, ADP, and validated 2025 weekly floor, ceiling, and consistency."
        path = "/nfl/who-should-i-draft/"; intro = ""
    css, _, footer = base.site_chrome()
    search = ('<input type="search" placeholder="Search any player" '
              'autocomplete="off" aria-label="Search any player">')
    header = seo.site_nav("data", search=search).replace(
        '  </div>\n',
        f'    <span class="stamp agate">UPDATED {built:%b %d}</span>\n  </div>\n',
        1,
    )
    links = pairs or []
    pair_html = "".join(f'<a href="/nfl/who-should-i-draft/{x["slug"]}-vs-{y["slug"]}/"><b>{base.esc(x["name"])}</b> or <b>{base.esc(y["name"])}</b>?</a>' for x, y in links[:36])
    # Pair pages need only their two records. Repeating the entire comparison
    # payload on every indexable matchup inflated the deploy by tens of MB.
    embedded = [a, b] if is_pair else players
    data = json.dumps(embedded, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    faq = [("What does the consistency score measure?", "It combines the variation in a player's 2025 weekly fantasy scoring with the size of his games-played sample. A higher score means steadier production, not necessarily more points."),
           ("Does the tool use last year's average alone?", "No. The recommendation combines current 2026 ranking and projection with validated 2025 average, floor, ceiling, boom rate, bust rate, and ADP."),
           ("Can the recommendation change by scoring format?", "Yes. PPR, Half-PPR, Non-PPR and Superflex use different rankings, projections and historical scoring results.")]
    schema = seo.graph({"@type": "WebApplication", "name": title, "url": "https://lineupbeat.com" + path,
                        "applicationCategory": "SportsApplication", "description": desc},
                       seo.faq_schema(faq), seo.ORGANISATION)
    body_attrs = f'data-a="{a["slug"] if a else ""}" data-b="{b["slug"] if b else ""}"'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{base.esc(title)}</title><meta name="description" content="{base.esc(desc)}"><link rel="canonical" href="https://lineupbeat.com{path}">{seo.social_meta(title, desc, "https://lineupbeat.com" + path, base.OG_IMAGE)}<script type="application/ld+json">{schema}</script><style>{css}{base.PAGE_CSS}{seo.CRUMB_CSS}{seo.UI_CSS}{seo.RELATED_CSS}{CSS}</style></head><body {body_attrs}>{header}<main class="cmpwrap"><nav class="crumbs"><a href="/">Home</a><span>/</span><a href="/decision-room/nfl/">Decision Room</a><span>/</span><b>Draft Comparison</b></nav><header class="cmphead"><p class="rkeyebrow">ADVANCED DRAFT COMPARISON</p><h1>Who Should I Draft?</h1><p>This advanced comparison adds validated 2025 weekly floor, ceiling, consistency, and Superflex context to current projections. For a faster projection-boundary call, use the <a href="/decision-room/nfl/">basic Decision Room</a>.</p>{intro}<p class="rkstatus">Updated {built:%B %d, %Y} · 2026 projections · 2025 weekly results</p></header><section class="cmpbox"><div class="cmpselectors"><div class="cmpselect"><label for="pa">Player one</label><select id="pa"></select></div><div class="versus">VS</div><div class="cmpselect"><label for="pb">Player two</label><select id="pb"></select></div></div><div class="fmt"><label for="format">Scoring format</label><select id="format"><option value="ppr">PPR</option><option value="half_ppr">Half-PPR</option><option value="non_ppr">Non-PPR</option><option value="superflex">Superflex</option></select></div><div id="cmpresult" class="cmpresult"></div></section>{f'<section class="popular"><h2>Popular draft decisions</h2><div class="pairgrid">{pair_html}</div></section>' if pair_html else ''}<section class="method"><h2>More than an average</h2><p>A season average can hide a player who alternates between 25 points and five. Lineup Beat shows the median, standard deviation, 25th-percentile floor, 75th-percentile ceiling, boom rate and bust rate from every 2025 regular-season appearance. The recommendation then balances that history against the current 2026 projection and rank.</p><p>Historical results describe what happened; they do not guarantee the same role or health.</p></section>{seo.faq_html(faq)}{seo.related_html('rankings')}</main><script id="cmpdata" type="application/json">{data}</script><script>{JS}</script>{footer}{seo.TRACKING}{seo.VIEW_CONTENT}</body></html>'''


def main() -> int:
    players = player_payload()
    if len(players) < 180:
        raise SystemExit("comparison player pool is unexpectedly small")
    built = formats.source_updated(formats.SOURCE)
    pairs = popular_pairs(players)
    OUT.mkdir(parents=True, exist_ok=True)
    font_links = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
                  '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
                  '<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap" rel="stylesheet">')
    compare_button = '<button class="cmpgo" id="cmpgo" type="button">Compare players &rarr;</button>'
    move_result = ('<script>const result=document.getElementById("cmpresult");'
                   'document.querySelector(".cmpbox").after(result);'
                   'document.getElementById("cmpgo").addEventListener("click",()=>'
                   'result.scrollIntoView({behavior:"smooth",block:"start"}));</script>')
    hero_extras = (
        '<div class="cmpactions"><a class="cmpaction primary" href="#pa">COMPARE PLAYERS &rarr;</a>'
        '<a class="cmpaction" href="/nfl/rankings/">VIEW RANKINGS</a></div>'
        f'<div class="cmpproof"><div><strong>{len(players)}</strong><span>Players to compare</span></div>'
        '<div><strong>4</strong><span>Scoring formats</span></div>'
        '<div><strong>2025</strong><span>Weekly results analyzed</span></div></div>')
    edge_panels = (
        '<div class="cmpedge left" aria-hidden="true"><div class="cmpmini">Fantasy points'
        '<div class="cmpbars"><i style="--w:72%"></i><i style="--w:55%"></i><i style="--w:83%"></i></div></div>'
        '<div class="cmpmini">Consistency<br><strong>Floor &middot; Ceiling &middot; Boom</strong></div></div>'
        '<div class="cmpedge right" aria-hidden="true"><div class="cmpmini">Draft value'
        '<div class="cmpbars"><i style="--w:64%"></i><i style="--w:79%"></i><i style="--w:48%"></i></div></div>'
        '<div class="cmpmini">Four formats<br><strong>PPR &middot; Half &middot; Standard &middot; SF</strong></div></div>')
    hub_html = html(players, built, pairs=pairs).replace(
        '<meta name="viewport"', font_links + '<meta name="viewport"', 1
    ).replace(
        "<h1>Who Should I Draft?</h1>",
        "<h1>Choose with confidence<br><span>before</span> your draft<br>clock runs out.</h1>",
    ).replace(
        'This advanced comparison adds validated 2025 weekly floor, ceiling, consistency, and Superflex context to current projections. For a faster projection-boundary call, use the <a href="/decision-room/nfl/">basic Decision Room</a>.',
        f"This advanced {len(players)}-player mode combines current rankings, projections and ADP with validated 2025 weekly consistency, floor, ceiling and Superflex context. Use the basic Decision Room for the faster 177-player projection-boundary experience.",
    ).replace('<div id="cmpresult"', compare_button + '<div id="cmpresult"', 1
    ).replace('</header><section class="cmpbox">',
              hero_extras + '</header><section class="cmpbox">', 1
    ).replace('<main class="cmpwrap">', '<main class="cmpwrap">' + edge_panels, 1
    ).replace('</body>', move_result + '</body>')
    hub = seo.check_page(hub_html, str(OUT / "index.html"))
    (OUT / "index.html").write_text(hub)
    for a, b in pairs:
        dest = OUT / f'{a["slug"]}-vs-{b["slug"]}' / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        related = [(x, y) for x, y in pairs
                   if (x["position"] == a["position"] and
                       {x["slug"], y["slug"]} != {a["slug"], b["slug"]})][:12]
        pair_html = html(players, built, a, b, related).replace(
            '<meta name="viewport"', font_links + '<meta name="viewport"', 1
        ).replace(
            "<h1>Who Should I Draft?</h1>",
            f'<h1>{base.esc(a["name"])} or<br><span>{base.esc(b["name"])}?</span></h1>',
        ).replace('<div id="cmpresult"', compare_button + '<div id="cmpresult"', 1
        ).replace('</header><section class="cmpbox">',
                  hero_extras + '</header><section class="cmpbox">', 1
        ).replace('<main class="cmpwrap">', '<main class="cmpwrap">' + edge_panels, 1
        ).replace('</body>', move_result + '</body>')
        dest.write_text(seo.check_page(pair_html, str(dest)))
    print(f"  comparison pool: {len(players)} players")
    print(f"  wrote {1 + len(pairs)} pages under {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
