#!/usr/bin/env python3
"""Build the 2026 college fantasy Week 1 projections and rankings."""
import hashlib
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
OUT = SITE / "college-fantasy-football" / "week-1"
EXPECTED_SHA = "9b3436d1df2869c9b02b1e6bb905f5a430ee331dd2625cd62264ee79f216fca2"
sys.path.insert(0, str(ROOT / "scripts"))
import seo
from college_team_logos import CSS as COLLEGE_LOGO_CSS, logo_html

cfg = json.loads((ROOT / "data/college/config.json").read_text())
release = ROOT / "data/college" / cfg["activeCollegeWeeklyProjectionVersion"]
manifest_bytes = (release / "manifest.json").read_bytes()
found = hashlib.sha256(manifest_bytes).hexdigest()
if found != EXPECTED_SHA:
    raise SystemExit(f"Week 1 manifest SHA mismatch: {found}")
manifest = json.loads(manifest_bytes)
data = json.loads((release / "college_week1_site_projections_2026.json").read_text())
players = data["players"]
if data["counts"] != {"players": 2205, "teams": 64, "games": 55}:
    raise SystemExit(f"unexpected Week 1 totals: {data['counts']}")

e = lambda value: html.escape(str(value), quote=True)
positions = ("QB", "RB", "WR", "TE")
columns = {
    "QB": (("rank", "Rank"), ("name", "Player"), ("team", "Team"),
           ("matchup", "Matchup"), ("pts", "Points"), ("passYds", "Pass Yds"),
           ("passTd", "Pass TD"), ("rushYds", "Rush Yds"), ("rushTd", "Rush TD")),
    "RB": (("rank", "Rank"), ("name", "Player"), ("team", "Team"),
           ("matchup", "Matchup"), ("pts", "Points"), ("rushAtt", "Carries"),
           ("rushYds", "Rush Yds"), ("rushTd", "Rush TD"), ("rec", "Rec"),
           ("recYds", "Rec Yds"), ("recTd", "Rec TD")),
    "WR": (("rank", "Rank"), ("name", "Player"), ("team", "Team"),
           ("matchup", "Matchup"), ("pts", "Points"), ("rec", "Rec"),
           ("recYds", "Rec Yds"), ("recTd", "Rec TD"), ("rushYds", "Rush Yds")),
}
columns["TE"] = columns["WR"]
numeric = {"rank", "pts", "passYds", "passTd", "rushAtt", "rushYds",
           "rushTd", "rec", "recYds", "recTd"}


def chrome():
    source = (SITE / "template.html").read_text()
    css = re.search(r"<style>(.*?)</style>", source, re.S)
    return css.group(1), seo.site_nav("rankings", "college"), seo.site_footer()


def matchup(player):
    return ("vs " if player["home"] else "@ ") + player["opponent"]


def value(player, key):
    if key == "matchup":
        return matchup(player)
    raw = player.get(key, 0)
    if key == "team":
        return (f'<span class="college-team-cell">{logo_html(player["teamId"], player["team"])}'
                f'<span>{e(player["team"])}</span></span>')
    if key not in numeric:
        return e(raw)
    return f"{raw:,.1f}" if key in {"pts", "passTd", "rushTd", "rec", "recTd"} else f"{raw:,.0f}"


def table(position, rows):
    cols = columns[position]
    head = "".join(f'<th class="{"num" if key in numeric else ""}">{e(label)}</th>'
                   for key, label in cols)
    body = []
    for player in rows:
        cells = "".join(
            f'<td class="{"num" if key in numeric else ""}">{value(player, key)}</td>'
            for key, _ in cols)
        body.append(f'<tr data-team="{e(player["team"])}" data-name="{e(player["name"].lower())}">{cells}</tr>')
    return f'<div class="wtable" tabindex="0"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


CSS = COLLEGE_LOGO_CSS + """
.wwrap{max-width:1180px;margin:auto;padding:1.25rem 1rem 3rem}.whero{max-width:800px}
.whero h1{font-size:2rem;line-height:1.12;margin:.4rem 0}.whero p{color:var(--quiet);line-height:1.55}
.wmeta{font-family:var(--agate);font-size:.7rem;letter-spacing:.08em;text-transform:uppercase}
.wtabs{display:flex;gap:.45rem;flex-wrap:wrap;margin:1rem 0}.wtabs a{border:1px solid var(--rule);border-radius:999px;padding:.4rem .8rem;text-decoration:none;color:var(--quiet);font-family:var(--agate);font-size:.72rem;text-transform:uppercase;letter-spacing:.08em}.wtabs a[aria-current=page]{background:var(--signal);color:#081006;border-color:var(--signal)}
.wtools{display:flex;gap:.6rem;flex-wrap:wrap;margin:.8rem 0}.wtools input,.wtools select{background:var(--card);color:var(--ink);border:1px solid var(--rule);border-radius:8px;padding:.5rem .7rem}.wnote{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:.75rem;color:var(--quiet);font-size:.84rem;line-height:1.5}.wtable{overflow-x:auto;border:1px solid var(--rule);border-radius:10px;margin-top:.8rem}table{width:100%;border-collapse:separate;border-spacing:0;font-size:.86rem}th,td{padding:.52rem .62rem;white-space:nowrap;box-shadow:inset 0 -1px 0 var(--rule)}th{font-family:var(--agate);font-size:.67rem;text-transform:uppercase;letter-spacing:.06em;color:var(--quiet);background:var(--card);position:sticky;top:0}td.num,th.num{text-align:right}td:nth-child(5){color:var(--signal);font-weight:700}th:first-child,td:first-child{position:sticky;left:0;background:var(--card);z-index:2}th:nth-child(2),td:nth-child(2){position:sticky;left:3rem;background:var(--card);z-index:2;box-shadow:inset 0 -1px 0 var(--rule),inset -1px 0 0 var(--rule)}.wsection{margin:1.5rem 0}.wsection h2{font-size:1.15rem}.wmore{color:var(--signal);font-family:var(--agate);font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;text-decoration:none}@media(max-width:640px){.whero h1{font-size:1.5rem}}
"""


def page(position=None):
    css, header, footer = chrome()
    title = "College Fantasy Football Week 1 Projections and Rankings"
    if position:
        title = f"College Fantasy Football Week 1 {position} Rankings"
    path = "/college-fantasy-football/week-1/" + (f"{position.lower()}/" if position else "")
    selected = [p for p in players if not position or p["pos"] == position]
    selected.sort(key=lambda p: p["rank"] if position else p["overallRank"])
    teams = sorted({p["team"] for p in selected})
    tabs = []
    for pos in (None,) + positions:
        href = "/college-fantasy-football/week-1/" + (f"{pos.lower()}/" if pos else "")
        tabs.append(f'<a href="{href}"{" aria-current=page" if pos == position else ""}>{pos or "All"}</a>')
    if position:
        content = table(position, selected)
    else:
        sections = []
        for pos in positions:
            top = sorted((p for p in players if p["pos"] == pos), key=lambda p: p["rank"])[:30]
            sections.append(f'<section class="wsection"><h2>{pos} rankings</h2>{table(pos, top)}<p><a class="wmore" href="{path}{pos.lower()}/">View every {pos} &rarr;</a></p></section>')
        content = "".join(sections)
    updated = datetime.fromisoformat(data["generatedAt"]).strftime("%B %-d, %Y")
    description = "Free 2026 college fantasy football Week 1 projections and rankings for QB, RB, WR and TE using Yahoo scoring and matchup-adjusted stat lines."
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} | LineupBeat</title><meta name="description" content="{e(description)}"><link rel="canonical" href="https://lineupbeat.com{path}">{seo.social_meta(title + " | LineupBeat", description, "https://lineupbeat.com" + path)}<style>{css}{seo.CRUMB_CSS}{seo.UI_CSS}{CSS}</style></head><body>{header}<main class="wwrap"><nav class="crumbs"><a href="/">Home</a><span>/</span><a href="/college-fantasy-football/projections/">College projections</a><span>/</span><b>Week 1</b></nav><header class="whero"><p class="wmeta">2026 · Week 1 · Updated {updated}</p><h1>{e(title)}</h1><p>Matchup-adjusted projections for {data["counts"]["players"]:,} players on 64 modeled teams playing from September 3–7. Rankings are built directly from each projected Yahoo-scoring stat line.</p></header><nav class="wtabs">{"".join(tabs)}</nav><div class="wtools"><input id="search" type="search" placeholder="Search players"><select id="team"><option value="">All teams</option>{"".join(f"<option>{e(t)}</option>" for t in teams)}</select></div><p class="wnote"><strong>How to use this:</strong> Points and ranks account for the opponent, market-implied team total, game total and expected game script. Positions come from school roster listings and may differ from fantasy-platform eligibility. Confirm late injury and depth-chart news before kickoff.</p>{content}</main>{footer}<script>(()=>{{let s=document.querySelector('#search'),t=document.querySelector('#team');function f(){{let q=s.value.toLowerCase(),tm=t.value;document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!!((q&&!r.dataset.name.includes(q))||(tm&&r.dataset.team!==tm)))}}s.addEventListener('input',f);t.addEventListener('change',f)}})()</script></body></html>'''


for position in (None,) + positions:
    target = OUT if position is None else OUT / position.lower()
    target.mkdir(parents=True, exist_ok=True)
    (target / "index.html").write_text(page(position))
print(f"  Week 1 college: {len(players)} players, manifest verified")
