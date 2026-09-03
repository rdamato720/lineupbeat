#!/usr/bin/env python3
"""Build the unlisted league-history development prototype."""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import seo

from league_history.demo import demo_history
from league_history.engine import summarize_history


DATA_OUT = ROOT / "site/data/league-history-demo.json"
PAGE_OUT = ROOT / "site/league-history/index.html"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def n(value: float, digits: int = 1) -> str:
    return f"{value:,.{digits}f}"


def pct(value: float) -> str:
    return f"{value:.3f}".lstrip("0")


def franchise_rows(summary: dict) -> str:
    rows = []
    for rank, row in enumerate(summary["franchises"], start=1):
        record = f'{row["wins"]}-{row["losses"]}' + (f'-{row["ties"]}' if row["ties"] else "")
        rows.append(f'''<tr><td class="rank">{rank}</td><td><strong>{esc(row["manager"])}</strong>
          <small>{esc(row["franchise"])}</small></td><td>{record}</td><td>{pct(row["winPct"])}</td>
          <td>{n(row["pointsFor"], 2)}</td><td>{n(row["pointsPerGame"])}</td>
          <td>{n(row["expectedWins"])}</td><td class="{'up' if row['luck'] >= 0 else 'down'}">{row['luck']:+.1f}</td>
          <td>{n(row["elo"])}</td><td>{row["titles"]}</td></tr>''')
    return "".join(rows)


def manager_cards(summary: dict) -> str:
    cards = []
    for row in summary["franchises"]:
        record = f'{row["wins"]}-{row["losses"]}'
        cards.append(f'''<article class="manager-card"><div><span>{esc(row["manager"])}</span>
          <small>{esc(row["franchise"])}</small></div><b>{record}</b>
          <dl><div><dt>Win pct</dt><dd>{pct(row["winPct"])}</dd></div>
          <div><dt>Points</dt><dd>{n(row["pointsFor"], 1)}</dd></div>
          <div><dt>Elo</dt><dd>{n(row["elo"])}</dd></div>
          <div><dt>Best run</dt><dd>{row["longestWinStreak"]}W</dd></div></dl></article>''')
    return "".join(cards)


def record_cards(summary: dict) -> str:
    r = summary["records"]
    values = (
        ("Highest week", r["highestWeek"]["franchise"], n(r["highestWeek"]["score"], 2), r["highestWeek"]),
        ("Lowest week", r["lowestWeek"]["franchise"], n(r["lowestWeek"]["score"], 2), r["lowestWeek"]),
        ("Biggest blowout", r["biggestBlowout"]["winner"], n(r["biggestBlowout"]["margin"], 2), r["biggestBlowout"]),
        ("Closest game", r["closestGame"]["winner"], n(r["closestGame"]["margin"], 2), r["closestGame"]),
        ("Highest combined", f'{r["highestScoringGame"]["home"]} vs {r["highestScoringGame"]["away"]}', n(r["highestScoringGame"]["total"], 2), r["highestScoringGame"]),
    )
    return "".join(f'''<article class="record-card"><small>{esc(label)}</small><b>{esc(value)}</b>
      <h3>{esc(owner)}</h3><p>{detail["season"]} · Week {detail["week"]}</p></article>'''
                   for label, owner, value, detail in values)


def season_cards(canonical: dict) -> str:
    cards = []
    manager_by_id = {row["id"]: row["displayName"] for row in canonical["managers"]}
    games_by_year = {}
    for game in canonical["matchups"]:
        games_by_year[game["season"]] = games_by_year.get(game["season"], 0) + 1
    for season in sorted(canonical["seasons"], key=lambda row: row["year"], reverse=True):
        champion = manager_by_id.get(season.get("championFranchiseId"), "Not awarded")
        state = "Complete" if season["complete"] else "Preseason / incomplete"
        cards.append(f'''<article class="season-card"><div><small>{state}</small><h3>{season["year"]}</h3></div>
          <dl><div><dt>Champion</dt><dd>{esc(champion)}</dd></div>
          <div><dt>Teams</dt><dd>{len(season["activeFranchiseIds"])}</dd></div>
          <div><dt>Games imported</dt><dd>{games_by_year.get(season["year"], 0)}</dd></div>
          <div><dt>Regular season</dt><dd>{season["regularSeasonWeeks"]} weeks</dd></div></dl></article>''')
    return "".join(cards)


def build_page(canonical: dict, summary: dict) -> str:
    power = sorted(summary["franchises"], key=lambda row: -row["elo"])[:5]
    manager = {row["id"]: row["displayName"] for row in canonical["managers"]}
    power_rows = "".join(f'<li><b>{rank}</b><span>{esc(row["manager"])}</span><strong>{n(row["elo"])}</strong></li>' for rank, row in enumerate(power, start=1))
    trophy = next(row for row in sorted(canonical["seasons"], key=lambda row: row["year"], reverse=True)
                  if row.get("championFranchiseId"))
    captured = canonical["import"].get("capturedAt", "")[:10]
    title = esc(summary["league"]["name"])
    styles = r'''
    :root{--gold:#d6ad55;--gold2:#f4d88a;--panel:#111518;--panel2:#161b1f}
    body{margin:0;background:#08090b;color:#f2f1ec;font-family:var(--text);font-size:16px}
    a{color:inherit}.lh{max-width:76rem;margin:0 auto;padding:1.4rem 1rem 5rem}
    .lh-status{display:flex;gap:.65rem;align-items:center;color:var(--muted);font:700 .72rem/1.2 var(--agate);letter-spacing:.08em;text-transform:uppercase}
    .lh-status i{width:.5rem;height:.5rem;border-radius:50%;background:var(--signal);box-shadow:0 0 0 .25rem #c6f53c22}
    .lh-head{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2rem;align-items:end;padding:1.8rem 0 1.2rem;border-bottom:1px solid #3a4145}
    .lh-kicker,.eyebrow{font:800 .76rem/1 var(--agate);letter-spacing:.12em;text-transform:uppercase;color:var(--gold2)}
    .lh h1{margin:.35rem 0 0;font:700 clamp(2.5rem,7vw,5.6rem)/.9 var(--agate);letter-spacing:-.035em;text-transform:uppercase}
    .lh-meta{display:flex;gap:1.7rem}.lh-meta div{display:grid;gap:.15rem}.lh-meta b{font:800 1.65rem/1 var(--data)}.lh-meta span{color:var(--muted);font:.72rem var(--agate);text-transform:uppercase;letter-spacing:.08em}
    .tabs{display:flex;gap:.2rem;overflow:auto;padding:.9rem 0;border-bottom:1px solid #252b2f;position:sticky;top:3.8rem;background:#08090bf2;z-index:12}
    .tab{border:0;background:transparent;color:var(--muted);padding:.65rem .85rem;font:800 .78rem var(--agate);letter-spacing:.06em;text-transform:uppercase;cursor:pointer;white-space:nowrap;border-radius:.2rem}
    .tab[aria-selected=true]{background:var(--gold);color:#0b0c0d}.panel{display:none;padding-top:1.4rem}.panel.active{display:block}
    .dashboard{display:grid;grid-template-columns:1.25fr .75fr;gap:1rem}.card{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid #2b3338;padding:1.15rem}
    .card h2,.section-head h2{margin:.25rem 0;font:700 1.8rem/1 var(--agate);text-transform:uppercase}.card p{color:var(--muted);margin:.45rem 0 0;line-height:1.45}
    .champ{min-height:15rem;display:grid;align-content:space-between;border-top:4px solid var(--gold)}.champ strong{font:800 clamp(2rem,5vw,4.5rem)/.92 var(--agate);text-transform:uppercase;max-width:12ch}.champ .season-mark{font:900 4rem/.8 var(--data);color:#ffffff12;justify-self:end}
    .power ol{list-style:none;margin:1rem 0 0;padding:0}.power li{display:grid;grid-template-columns:2rem 1fr auto;gap:.5rem;padding:.65rem 0;border-top:1px solid #2d3438}.power li b,.power li strong{font-family:var(--data)}
    .notice{margin-top:1rem;border-left:3px solid var(--signal);background:#111518;padding:1rem;color:var(--muted)}.notice strong{color:var(--ink)}
    .section-head{display:flex;justify-content:space-between;gap:1rem;align-items:end;margin-bottom:1rem}.section-head p{margin:0;max-width:42rem;color:var(--muted)}
    .table-wrap{overflow:auto;border:1px solid #2b3338}.history-table{border-collapse:collapse;width:100%;min-width:62rem;background:#0e1113}.history-table th{position:sticky;top:0;background:#171c20;color:#aeb5b0;font:800 .7rem var(--agate);letter-spacing:.06em;text-transform:uppercase;text-align:right}.history-table th:nth-child(2){text-align:left}.history-table td,.history-table th{padding:.72rem;border-bottom:1px solid #252b2f}.history-table td{text-align:right;font-family:var(--data);font-size:.82rem}.history-table td:nth-child(2){text-align:left;font-family:var(--agate);font-size:1rem}.history-table td small{display:block;color:var(--muted);font:400 .75rem var(--text)}.rank{color:#737b77}.up{color:#9de59d}.down{color:#ef8e86}
    .record-grid,.manager-grid,.season-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.8rem}.record-card,.manager-card,.season-card{border:1px solid #30373b;background:#111518;padding:1rem}.record-card small,.season-card small{font:800 .7rem var(--agate);letter-spacing:.08em;text-transform:uppercase;color:var(--gold2)}.record-card>b{display:block;font:900 2.35rem var(--data);margin:.75rem 0}.record-card h3{font:700 1.3rem var(--agate);margin:0}.record-card p{color:var(--muted);margin:.2rem 0 0}
    .manager-card>div{min-height:3.2rem}.manager-card span{font:700 1.2rem var(--agate)}.manager-card small{display:block;color:var(--muted)}.manager-card>b{display:block;font:900 2rem var(--data);margin:.8rem 0}.manager-card dl,.season-card dl{display:grid;grid-template-columns:1fr 1fr;margin:0}.manager-card dl div,.season-card dl div{padding:.55rem 0;border-top:1px solid #2b3338}.manager-card dt,.season-card dt{font:700 .68rem var(--agate);text-transform:uppercase;color:var(--muted)}.manager-card dd,.season-card dd{margin:.12rem 0 0;font:700 .85rem var(--data)}
    .season-card{display:grid;grid-template-columns:.4fr 1fr;gap:1rem}.season-card h3{font:900 2.5rem var(--data);margin:.25rem 0}.season-card dl{gap:0 1rem}
    .source-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.source-grid code{color:var(--gold2)}.source-grid ul{color:var(--muted);line-height:1.55;margin:.6rem 0 0;padding-left:1.2rem}
    .lh-footer{margin-top:2rem;padding-top:1rem;border-top:1px solid #2b3338;color:var(--muted);font-size:.85rem;display:flex;justify-content:space-between;gap:1rem}
    @media(max-width:760px){.lh-head{grid-template-columns:1fr}.lh-meta{justify-content:space-between}.dashboard,.source-grid{grid-template-columns:1fr}.record-grid,.manager-grid{grid-template-columns:1fr 1fr}.season-grid{grid-template-columns:1fr}.tabs{top:3.4rem}}
    @media(max-width:500px){.record-grid,.manager-grid{grid-template-columns:1fr}.lh-meta{gap:.8rem}.lh-meta b{font-size:1.25rem}.lh-footer{display:block}.season-card{grid-template-columns:1fr}}
    '''
    script = r'''
    <script>(function(){
      var tabs=[].slice.call(document.querySelectorAll('.tab'));
      var panels=[].slice.call(document.querySelectorAll('.panel'));
      function select(id){tabs.forEach(function(t){var on=t.dataset.tab===id;t.setAttribute('aria-selected',String(on));});panels.forEach(function(p){p.classList.toggle('active',p.id===id);});}
      tabs.forEach(function(t){t.addEventListener('click',function(){select(t.dataset.tab);history.replaceState(null,'','#'+t.dataset.tab);});});
      var initial=location.hash.slice(1);if(document.getElementById(initial))select(initial);
    }());</script>'''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{title} League History | LineupBeat</title><meta name="robots" content="noindex,nofollow">
    <meta name="description" content="Development prototype for the LineupBeat fantasy football league history tracker.">
    <style>{seo.SHELL_CSS}{seo.TEAMS_CSS}{seo.NAV_CSS}{styles}</style></head><body>
    {seo.site_nav('data', 'nfl')}
    <main class="lh"><div class="lh-status"><i></i>Development prototype · fictional league · ESPN importer not connected</div>
      <header class="lh-head"><div><span class="lh-kicker">League history</span><h1>{title}</h1></div>
      <div class="lh-meta"><div><b>{summary['counts']['seasons']}</b><span>seasons found</span></div><div><b>{summary['counts']['games']}</b><span>games</span></div><div><b>{summary['counts']['franchises']}</b><span>franchises</span></div></div></header>
      <nav class="tabs" aria-label="League history sections">
        <button class="tab" data-tab="overview" aria-selected="true">Overview</button><button class="tab" data-tab="trophies" aria-selected="false">Trophy case</button>
        <button class="tab" data-tab="all-time" aria-selected="false">All-time</button><button class="tab" data-tab="managers" aria-selected="false">Managers</button>
        <button class="tab" data-tab="seasons" aria-selected="false">Seasons</button><button class="tab" data-tab="records" aria-selected="false">Records</button>
      </nav>
      <section class="panel active" id="overview"><div class="dashboard">
        <article class="card champ"><div><span class="eyebrow">Defending champion · {trophy['year']}</span></div><strong>{esc(manager[trophy['championFranchiseId']])}</strong><span class="season-mark">01</span></article>
        <article class="card power"><span class="eyebrow">Preseason Elo</span><h2>Power five</h2><ol>{power_rows}</ol></article></div>
        <div class="notice"><strong>Prototype boundary:</strong> Every name, team and score on this page is fictional. The live ESPN importer and commissioner identity review are the next build phase.</div></section>
      <section class="panel" id="trophies"><div class="section-head"><div><span class="eyebrow">Hardware</span><h2>Trophy case</h2></div><p>Championships and regular-season scoring crowns stay separate, preserving both playoff results and season-long dominance.</p></div>
        <div class="record-grid"><article class="record-card"><small>{trophy['year']} champion</small><b>🏆</b><h3>{esc(manager[trophy['championFranchiseId']])}</h3><p>Final standing: 1</p></article>
        <article class="record-card"><small>{trophy['year']} runner-up</small><b>02</b><h3>{esc(manager[trophy['runnerUpFranchiseId']])}</h3><p>Championship finalist</p></article>
        <article class="record-card"><small>{trophy['year']} scoring crown</small><b>SC</b><h3>{esc(manager[trophy['scoringCrownFranchiseId']])}</h3><p>Regular-season points leader</p></article></div></section>
      <section class="panel" id="all-time"><div class="section-head"><div><span class="eyebrow">Franchise ledger</span><h2>All-time table</h2></div><p>Every metric is recomputed from canonical matchups. Elo uses K=24, margin weighting, and 30% offseason regression.</p></div>
        <div class="table-wrap"><table class="history-table"><thead><tr><th>#</th><th>Franchise</th><th>W-L</th><th>Win%</th><th>PF</th><th>PPG</th><th>xW</th><th>Luck</th><th>Elo</th><th>Titles</th></tr></thead><tbody>{franchise_rows(summary)}</tbody></table></div></section>
      <section class="panel" id="managers"><div class="section-head"><div><span class="eyebrow">Identity</span><h2>Manager files</h2></div><p>Manager identity is permanent; changing a team name never splits the franchise record.</p></div><div class="manager-grid">{manager_cards(summary)}</div></section>
      <section class="panel" id="seasons"><div class="section-head"><div><span class="eyebrow">Archive</span><h2>Seasons</h2></div><p>Incomplete seasons remain visible as gaps so commissioners can backfill them later by CSV or manual entry.</p></div><div class="season-grid">{season_cards(canonical)}</div></section>
      <section class="panel" id="records"><div class="section-head"><div><span class="eyebrow">Record book</span><h2>League records</h2></div><p>These marks come directly from {summary['counts']['games']} fictional matchups, including playoff weeks.</p></div><div class="record-grid">{record_cards(summary)}</div>
        <div class="source-grid" style="margin-top:1rem"><article class="card"><span class="eyebrow">Design provenance</span><h2>Public reference</h2><p>Architecture and rating behavior informed by the public BGNCo repository. No participant records are copied into LineupBeat.</p></article>
        <article class="card"><span class="eyebrow">Privacy model</span><h2>League controlled</h2><ul><li>Private, unlisted, or public publishing</li><li>Permanent franchise IDs with alias review</li><li>Ledger records obligations; it never holds money</li></ul></article></div></section>
      <footer class="lh-footer"><span>Fictional demonstration data · prototype calculations by LineupBeat.</span><span>Demo snapshot {esc(captured)}</span></footer>
    </main>{script}</body></html>'''


def main() -> int:
    canonical = demo_history()
    summary = summarize_history(canonical)
    payload = {"canonical": canonical, "recordBook": summary}
    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    PAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUT.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    PAGE_OUT.write_text(build_page(canonical, summary))
    print(f"Built {PAGE_OUT.relative_to(ROOT)} from {summary['counts']['games']} matchups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
