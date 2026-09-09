#!/usr/bin/env python3
"""Build frozen college weekly boards, including the Week 1 archive."""
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
sys.path.insert(0, str(ROOT / "scripts"))
import seo
from college_team_logos import CSS as COLLEGE_LOGO_CSS, logo_html
from college_releases import load_release, active_release

week = 1
data = {}
players = []

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
    if key == "availability":
        status = player.get('availability', {}).get('status', 'Not tracked')
        source = player.get('availability', {}).get('source')
        label = status + (' · if active' if player.get('availability', {}).get('conditional') else '')
        return f'<a href="{e(source)}" target="_blank" rel="noopener">{e(label)}</a>' if source else e(label)
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
    if week > 1:
        cols = cols[:5] + (("availability", "Status"),) + cols[5:]
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
.whero h1{font-size:clamp(2.4rem,5vw,4.3rem);line-height:1.02;letter-spacing:-.035em;margin:.4rem 0 .7rem}.whero p{color:var(--quiet);line-height:1.55}
.wmeta{font-family:var(--agate);font-size:.7rem;letter-spacing:.08em;text-transform:uppercase}
.wtabs{display:flex;gap:.45rem;flex-wrap:wrap;margin:1rem 0}.wtabs a{border:1px solid var(--rule);border-radius:999px;padding:.4rem .8rem;text-decoration:none;color:var(--quiet);font-family:var(--agate);font-size:.72rem;text-transform:uppercase;letter-spacing:.08em}.wtabs a[aria-current=page]{background:var(--signal);color:#081006;border-color:var(--signal)}
.wtools{display:flex;gap:.6rem;flex-wrap:wrap;margin:.8rem 0}.wtools input,.wtools select{background:var(--card);color:var(--ink);border:1px solid var(--rule);border-radius:8px;padding:.5rem .7rem}.wnote{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:.75rem;color:var(--quiet);font-size:.84rem;line-height:1.5}.wtable{overflow-x:auto;border:1px solid var(--rule);border-radius:10px;margin-top:.8rem}table{width:100%;border-collapse:separate;border-spacing:0;font-size:.86rem}th,td{padding:.52rem .62rem;white-space:nowrap;box-shadow:inset 0 -1px 0 var(--rule)}th{font-family:var(--agate);font-size:.67rem;text-transform:uppercase;letter-spacing:.06em;color:var(--quiet);background:var(--card);position:sticky;top:0}td.num,th.num{text-align:right}td:nth-child(5){color:var(--signal);font-weight:700}th:first-child,td:first-child{position:sticky;left:0;background:var(--card);z-index:2}th:nth-child(2),td:nth-child(2){position:sticky;left:3rem;background:var(--card);z-index:2;box-shadow:inset 0 -1px 0 var(--rule),inset -1px 0 0 var(--rule)}.wsection{margin:1.5rem 0}.wsection h2{font-size:1.5rem}.wmore{color:var(--signal);font-family:var(--agate);font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;text-decoration:none}@media(max-width:640px){.whero h1{font-size:2rem}}
"""


def _page(position=None):
    css, header, footer = chrome()
    title = f"College Fantasy Football Week {week} Projections and Rankings"
    if position:
        title = f"College Fantasy Football Week {week} {position} Rankings"
    path = f"/college-fantasy-football/week-{week}/" + (f"{position.lower()}/" if position else "")
    selected = [p for p in players if not position or p["pos"] == position]
    selected.sort(key=lambda p: p["rank"] if position else p["overallRank"])
    teams = sorted({p["team"] for p in selected})
    tabs = []
    for pos in (None,) + positions:
        href = f"/college-fantasy-football/week-{week}/" + (f"{pos.lower()}/" if pos else "")
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
    description = f"Free 2026 college fantasy football Week {week} projections and rankings for QB, RB, WR and TE using Yahoo scoring, with player stats and matchups."
    dates = "September 3–7" if week == 1 else "September 10–14"
    note = ("Archived Week 1 projections, saved before kickoff. "
            '<a href="/college-fantasy-football/week-2/">View Week 2 rankings</a>.' if week == 1 else
            'Injury reports checked September 9. Questionable players are projected assuming they play; ruled-out players have zero projected points. '
            '<a href="/college-fantasy-football/week-1/">Week 1 archive</a>.')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} | LineupBeat</title><meta name="description" content="{e(description)}"><link rel="canonical" href="https://lineupbeat.com{path}">{seo.social_meta(title + " | LineupBeat", description, "https://lineupbeat.com" + path)}<style>{css}{seo.CRUMB_CSS}{seo.UI_CSS}{CSS}</style></head><body>{header}<main class="wwrap"><nav class="crumbs"><a href="/">Home</a><span>/</span><a href="/college-fantasy-football/projections/">College projections</a><span>/</span><b>Week {week}</b></nav><header class="whero"><p class="wmeta">2026 · Week {week} · Updated {updated}</p><h1>{e(title)}</h1><p>Week {week} projections for {data["counts"]["players"]:,} players on {data["counts"]["teams"]} teams playing from {dates}. Rankings are built directly from each projected Yahoo-scoring stat line.</p></header><nav class="wtabs">{"".join(tabs)}</nav><div class="wtools"><input id="search" type="search" placeholder="Search players"><select id="team"><option value="">All teams</option>{"".join(f"<option>{e(t)}</option>" for t in teams)}</select></div><p class="wnote">{note}</p>{content}</main>{footer}<script>(()=>{{let s=document.querySelector('#search'),t=document.querySelector('#team');function f(){{let q=s.value.toLowerCase(),tm=t.value;document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!!((q&&!r.dataset.name.includes(q))||(tm&&r.dataset.team!==tm)))}}s.addEventListener('input',f);t.addEventListener('change',f)}})()</script></body></html>'''


def page(position=None):
    """Add full-pool discovery without rendering 2,205 rows on the overview."""
    document = _page(position)
    index = [{"name": p["name"], "team": p["team"], "pos": p["pos"]}
             for p in players] if position is None else []
    payload = json.dumps(index, separators=(",", ":")).replace("</", "<\\/")
    datalist = ""
    if position is None:
        options = "".join(
            f'<option value="{e(p["name"])}" label="{e(p["team"])} · {e(p["pos"])}"></option>'
            for p in players)
        datalist = f'<datalist id="college-weekly-player-list">{options}</datalist>'
        document = document.replace(
            '<input id="search" type="search" placeholder="Search players">',
            '<input id="search" type="search" list="college-weekly-player-list" '
            f'placeholder="Search all {len(players):,} players">' + datalist, 1)
    script = f'''<script>(()=>{{
const overview={str(position is None).lower()},index={payload},input=document.getElementById('search');
const norm=value=>String(value||'').trim().toLowerCase();
function match(){{const query=norm(input.value);if(!query)return null;return index.find(p=>norm(p.name)===query)||index.find(p=>norm(p.name).startsWith(query))||index.find(p=>norm(p.name).includes(query));}}
function openMatch(){{if(!overview)return;const hit=match();if(hit)location.href='/college-fantasy-football/week-{week}/'+hit.pos.toLowerCase()+'/?q='+encodeURIComponent(hit.name);}}
input.addEventListener('change',()=>{{if(match()&&norm(match().name)===norm(input.value))openMatch();}});
input.addEventListener('keydown',event=>{{if(event.key==='Enter'&&overview){{event.preventDefault();openMatch();}}}});
const query=new URLSearchParams(location.search).get('q');if(query&&!overview){{input.value=query;input.dispatchEvent(new Event('input'));}}
}})()</script>'''
    return document.replace('</body>', script + '</body>', 1)


for version in dict.fromkeys(('2026/week-1/v1.1', active_release())):
    release, data = load_release(version)
    week, players = data['week'], data['players']
    out = SITE / 'college-fantasy-football' / f'week-{week}'
    for position in (None,) + positions:
        target = out if position is None else out / position.lower()
        target.mkdir(parents=True, exist_ok=True)
        (target / 'index.html').write_text(page(position))
    print(f'  Week {week} college: {len(players)} players, manifest verified')
