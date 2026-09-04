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
DASHBOARD_SOURCE = ROOT / "league_history/dashboard.js"
DASHBOARD_OUT = ROOT / "site/assets/league-history-dashboard.js"


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
          <small>{esc(row["franchise"])} · {row["seasons"]} seasons</small></td>
          <td>{record}</td><td>{pct(row["winPct"])}</td>
          <td>{n(row["pointsPerGame"])}</td><td>{row["titles"]}</td></tr>''')
    return "".join(rows)


def manager_cards(summary: dict) -> str:
    cards = []
    for row in summary["franchises"]:
        record = f'{row["wins"]}-{row["losses"]}'
        cards.append(f'''<article class="manager-card"><div class="manager-card__head"><div><span>{esc(row["manager"])}</span>
          <small>{esc(row["franchise"])}</small></div><b>{record}</b></div>
          <dl><div><dt>Seasons</dt><dd>{row["seasons"]}</dd></div>
          <div><dt>Win pct</dt><dd>{pct(row["winPct"])}</dd></div>
          <div><dt>Titles</dt><dd>{row["titles"]}</dd></div>
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
    .lh h1{margin:.35rem 0 0;font:700 clamp(2.2rem,4vw,3.6rem)/.94 var(--agate);letter-spacing:-.035em;text-transform:uppercase}
    .lh-meta{display:flex;gap:1.7rem}.lh-meta div{display:grid;gap:.15rem}.lh-meta b{font:800 1.65rem/1 var(--data)}.lh-meta span{color:var(--muted);font:.72rem var(--agate);text-transform:uppercase;letter-spacing:.08em}
    .import-card{margin:1rem 0;border:1px solid #3b454a;background:#101417;padding:1rem}.import-line{display:flex;align-items:center;justify-content:space-between;gap:1rem}.import-copy{display:grid;gap:.18rem}.import-copy strong{font:750 1.05rem var(--agate)}.import-copy span{color:var(--muted);font-size:.95rem}.import-actions{display:flex;flex-wrap:wrap;gap:.5rem}.import-actions button{border:0;border-radius:.2rem;padding:.72rem .95rem;background:var(--signal);color:#09100d;font:800 .78rem var(--agate);letter-spacing:.03em;text-transform:uppercase;cursor:pointer}.import-actions button:disabled{cursor:not-allowed;opacity:.38}.import-actions .quiet{background:#242c30;color:var(--ink)}.import-actions button[hidden]{display:none}.import-summary{display:none;margin-top:1rem;border-top:1px solid #2c3438;padding-top:1.25rem}.import-summary.open{display:block}.capture-stats{display:flex;flex-wrap:wrap;gap:.55rem;margin-bottom:1rem}.capture-stats[hidden],.match-flow[hidden],.match-card[hidden],.match-complete[hidden]{display:none}.capture-stats span{border:1px solid #343d42;padding:.45rem .62rem;color:var(--muted);font:.82rem var(--data)}.review-head{margin-bottom:1rem}.review-step{display:block;color:var(--gold2);font:800 .75rem var(--agate);letter-spacing:.09em;text-transform:uppercase}.review-head h2{margin:.35rem 0 0;font:700 1.6rem var(--agate)}.review-head p{margin:.35rem 0 0;color:var(--muted);font-size:1rem;line-height:1.45}.match-card{border:1px solid #3c4449;background:#0a0c0e;padding:1.25rem}.match-progress{display:flex;justify-content:space-between;gap:1rem;color:var(--muted);font:700 .82rem var(--agate)}.match-people{display:grid;grid-template-columns:1fr auto 1fr;gap:1rem;align-items:stretch;margin:1rem 0}.match-or{align-self:center;color:var(--muted);font:800 .72rem var(--agate);letter-spacing:.08em;text-transform:uppercase}.person-card{border:1px solid #30383d;background:#111518;padding:1rem;min-width:0}.person-card small{display:block;color:var(--muted);font:800 .7rem var(--agate);letter-spacing:.08em;text-transform:uppercase}.person-card strong{display:block;margin:.3rem 0 .55rem;font:750 1.2rem var(--agate)}.person-meta{color:var(--muted);font:.9rem var(--agate)}.person-aliases{margin-top:.55rem}.person-aliases summary{cursor:pointer;color:var(--gold2);font:700 .82rem var(--agate)}.person-aliases p{margin:.35rem 0 0;color:var(--muted);font:.82rem/1.45 var(--agate)}.match-question{margin:.1rem 0 .75rem;text-align:center;font:750 1.1rem var(--agate)}.choice-actions{display:grid;grid-template-columns:1fr 1fr;gap:.65rem}.choice-actions button{border:1px solid #4a555b;border-radius:.2rem;background:#171c20;color:var(--ink);padding:.85rem 1rem;font:800 .9rem var(--agate);cursor:pointer}.choice-actions button:first-child{border-color:var(--signal);color:var(--signal)}.choice-actions button:hover,.choice-actions button:focus-visible{background:#22292d}.choice-actions button[aria-pressed=true]{background:#2a3217;border-color:var(--gold2);color:var(--gold2)}.match-complete{border:1px solid #3c4449;background:#0a0c0e;padding:1.25rem}.match-complete h3{margin:0;font:750 1.25rem var(--agate)}.match-complete p{margin:.35rem 0 0;color:var(--muted);font:1rem/1.45 var(--agate)}.decision-summary{display:grid;gap:.45rem;margin:1rem 0}.decision-row{display:flex;justify-content:space-between;gap:1rem;border-top:1px solid #273035;padding-top:.55rem;font:.9rem var(--agate)}.decision-row b{color:var(--gold2)}.review-footer{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-top:1rem}.review-footer p{margin:0;color:var(--muted);font:.9rem var(--agate)}.review-result{min-height:1.2em;color:var(--gold2);font:.9rem var(--agate);margin:.8rem 0 0}.has-import .import-card{max-width:50rem;margin:2rem auto;padding:1.25rem}.has-import .import-line{padding-bottom:.2rem}.has-import .import-copy strong{font-size:.9rem;color:var(--gold2);text-transform:uppercase;letter-spacing:.06em}.has-import .import-actions .quiet{padding:.5rem .65rem;background:transparent;color:var(--muted);border:1px solid #343d42;font-size:.7rem}.has-import .tabs,.has-import .panel,.has-import .lh-footer{display:none}
    .review-head{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:1rem;align-items:start}.review-head .quiet{align-self:start;border:1px solid #3d474c;background:transparent;color:var(--muted);padding:.5rem .7rem}.manager-review.is-complete{border-bottom:1px solid #2c3438;margin-bottom:1.25rem}.manager-review.is-complete .review-head{margin-bottom:1.15rem}.manager-review.is-complete .match-flow,.manager-review.is-complete .review-footer,.manager-review.is-complete .review-result{display:none}.setup-ready[hidden]{display:none}.setup-ready{border:1px solid #506426;background:#12170b;padding:1.25rem;margin-top:1.25rem}.setup-ready small{color:var(--signal);font:800 .72rem var(--agate);letter-spacing:.09em;text-transform:uppercase}.setup-ready h2{margin:.35rem 0;font:750 1.5rem var(--agate)}.setup-ready p{margin:0;color:var(--muted);font:1rem/1.45 var(--agate)}
    .tabs{display:flex;gap:.2rem;overflow:auto;padding:.9rem 0;border-bottom:1px solid #252b2f;position:sticky;top:3.8rem;background:#08090bf2;z-index:12}
    .tab{border:0;background:transparent;color:var(--muted);padding:.65rem .85rem;font:800 .78rem var(--agate);letter-spacing:.06em;text-transform:uppercase;cursor:pointer;white-space:nowrap;border-radius:.2rem}
    .tab[aria-selected=true]{background:var(--gold);color:#0b0c0d}.panel{display:none;padding-top:1.4rem}.panel.active{display:block}
    .has-import:not(.history-ready) .tabs,.has-import:not(.history-ready) .panel,.has-import:not(.history-ready) .lh-footer{display:none!important}
    .history-ready .import-card{max-width:none;margin:1rem 0;padding:.75rem 1rem}.history-ready .import-summary{display:none}.history-ready .tabs{display:flex}.history-ready .panel{display:none}.history-ready .panel.active{display:block}.history-ready .lh-footer{display:flex}
    .import-copy span,.review-head p,.review-result{font-family:var(--agate)}
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

    /* League History uses the same visual system as the Decision Room. */
    :root{--history-bg:#080c0b;--history-panel:#111715;--history-panel-2:#0d1210;--history-line:#29312d;--history-muted:#aeb7b0}
    body{position:relative;isolation:isolate;background:radial-gradient(circle at 50% 9rem,rgba(29,40,37,.52),transparent 34rem),var(--history-bg);color:var(--ink);font-family:var(--agate)}
    .lh{position:relative;z-index:2;max-width:74rem;padding:1rem 1.25rem 5rem}
    .lh-atmosphere{position:absolute;z-index:0;inset:3.8rem 0 auto;height:52rem;overflow:hidden;pointer-events:none}
    .lh-atmosphere::before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.022) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.022) 1px,transparent 1px);background-size:72px 72px;mask-image:linear-gradient(to bottom,#000 0,rgba(0,0,0,.38) 62%,transparent 100%)}
    .lh-ambient-card{position:absolute;width:11.5rem;padding:1rem;border:1px solid #8c9a922e;border-radius:.25rem;background:#080d0c8f;color:#9aa39c;opacity:.15;font:700 .7rem/1.2 var(--agate);letter-spacing:.08em;text-transform:uppercase}
    .lh-ambient-card>span{display:block;margin-bottom:.7rem}.lh-ambient-card strong{display:block;color:var(--signal);font:800 2rem/1 var(--data);letter-spacing:-.04em}
    .lh-ambient-card small{display:block;margin-top:.35rem;color:#9aa39c;font:700 .65rem/1.3 var(--agate);letter-spacing:.07em}
    .lh-ambient-seasons{left:1rem;top:9rem}.lh-ambient-games{right:1rem;top:20rem}
    .lh-ambient-trace{display:block;width:100%;height:3.2rem;margin-top:.9rem}.lh-ambient-trace polyline{fill:none;stroke:var(--signal);stroke-width:2.5}
    .lh-ambient-bars{display:grid;gap:.55rem;margin-top:1rem}.lh-ambient-bars i{display:block;width:var(--w);height:.35rem;background:linear-gradient(90deg,var(--signal),#52621c)}
    .lh-status{color:var(--history-muted);font-size:.68rem}
    .lh-head{position:relative;overflow:hidden;min-height:10rem;padding:2.2rem 1.5rem 1.4rem;margin:1rem 0 0;border:1px solid var(--history-line);background:radial-gradient(circle at 80% 0,#c6f53c16,transparent 38%),linear-gradient(145deg,#111815,#0a0f0d)}
    .lh-kicker,.eyebrow,.review-step{color:var(--signal)}
    .lh h1{font:700 clamp(2.5rem,6vw,4.9rem)/.86 var(--display);letter-spacing:-.05em;text-transform:none;max-width:12ch}
    .lh-meta{align-self:center}.lh-meta div{min-width:5.5rem}.lh-meta b{font:800 1.8rem/1 var(--data)}
    .import-card,.card,.record-card,.manager-card,.season-card{border-color:var(--history-line);background:var(--history-panel)}
    .history-ready .import-card{margin:.75rem 0 0;padding:.75rem 1rem;background:#0d1210}
    .import-copy strong{color:var(--ink);font-size:.9rem}.history-ready .import-copy strong{color:var(--signal)}
    .import-copy span{font-size:.82rem}.import-actions button{border-radius:0;background:var(--signal)}
    .import-actions .quiet,.has-import .import-actions .quiet{background:transparent;border:1px solid #39433e;color:var(--history-muted)}
    .tabs{top:3.65rem;gap:1.4rem;padding:1rem .15rem .65rem;background:#080c0bf2;border-color:var(--history-line)}
    .tab{position:relative;padding:.55rem 0;border-radius:0;background:transparent;color:var(--history-muted);font-size:.72rem}
    .tab[aria-selected=true]{background:transparent;color:var(--ink)}
    .tab[aria-selected=true]::after{content:"";position:absolute;left:0;right:0;bottom:-.68rem;height:3px;background:var(--signal)}
    .panel{padding-top:2.1rem}.section-head{display:block;margin-bottom:1.1rem}.section-head p{margin:.45rem 0 0;max-width:38rem;font:400 .92rem/1.5 var(--agate)}
    .card h2,.section-head h2{margin:.35rem 0 0;font:700 clamp(1.7rem,3vw,2.5rem)/1 var(--display);letter-spacing:-.025em;text-transform:none}
    .dashboard{grid-template-columns:minmax(0,1.35fr) minmax(18rem,.65fr);gap:.8rem}
    .card{padding:1.25rem}.champ{min-height:13rem;border-top:3px solid var(--signal)}
    .champ strong{font:700 clamp(2.2rem,5vw,4.1rem)/.92 var(--display);letter-spacing:-.04em;text-transform:none}
    .champ .season-mark{font-size:3rem;color:#c6f53c1a}.power li{border-color:var(--history-line);font-size:.88rem}
    .notice{border-left:0;border-top:1px solid var(--history-line);background:transparent;padding:1rem 0;font:400 .85rem/1.5 var(--agate)}
    .history-snapshot{display:grid;grid-template-columns:repeat(3,1fr);gap:.8rem;margin-top:.8rem}
    .snapshot-card{border:1px solid var(--history-line);background:var(--history-panel-2);padding:1rem}
    .snapshot-card small{display:block;color:var(--history-muted);font:800 .67rem var(--agate);letter-spacing:.08em;text-transform:uppercase}
    .snapshot-card strong{display:block;margin-top:.3rem;font:700 1.35rem var(--display)}
    .snapshot-card span{display:block;margin-top:.2rem;color:var(--history-muted);font-size:.78rem}
    .table-wrap{border-color:var(--history-line);border-radius:.1rem}.history-table{min-width:42rem;background:var(--history-panel-2)}
    .history-table th{background:#171e1b;color:var(--history-muted);font-size:.66rem}.history-table td,.history-table th{padding:.85rem .8rem;border-color:var(--history-line)}
    .history-table td{font-size:.85rem}.history-table td:nth-child(2){font-size:.95rem}.history-table td small{margin-top:.15rem;color:var(--history-muted);font:500 .72rem var(--agate)}
    .record-grid,.manager-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:.8rem}.season-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:.8rem}
    .record-card,.manager-card,.season-card{padding:1.1rem}.record-card small,.season-card small{color:var(--signal)}
    .record-card>b{font-size:2rem;margin:.55rem 0}.record-card h3{font:700 1.15rem var(--display)}.record-card p{font:.82rem/1.4 var(--agate)}
    .manager-card__head{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;min-height:0!important}
    .manager-card span{font:700 1.3rem var(--display)}.manager-card small{margin-top:.18rem;color:var(--history-muted);font:500 .78rem var(--agate)}
    .manager-card__head>b{font:800 1.2rem var(--data);white-space:nowrap}.manager-card>b{display:none}
    .manager-card dl,.season-card dl{gap:0 1rem;margin-top:1rem}.manager-card dl div,.season-card dl div{border-color:var(--history-line)}
    .manager-card dt,.season-card dt{font-size:.64rem}.manager-card dd,.season-card dd{font-size:.82rem}
    .team-history{margin-top:.9rem;padding-top:.75rem;border-top:1px solid var(--history-line)}.team-history summary{cursor:pointer;color:var(--signal);font:800 .7rem var(--agate);letter-spacing:.04em;text-transform:uppercase}.team-history p{margin:.6rem 0 0;color:var(--history-muted);font:500 .78rem/1.5 var(--agate)}
    .season-card{grid-template-columns:6rem 1fr}.season-card h3{font-size:2rem}.source-grid{grid-template-columns:1fr 1fr}.source-grid code{color:var(--signal)}
    .source-grid ul{font-family:var(--agate)}.lh-footer{border-color:var(--history-line);font:500 .75rem var(--agate)}

    .publish-panel[hidden],.publish-result[hidden]{display:none}.publish-panel{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:1rem 2rem;align-items:start;margin:.75rem 0 0;padding:1rem 1.1rem;border:1px solid var(--history-line);background:radial-gradient(circle at 85% 0,#c6f53c12,transparent 18rem),var(--history-panel-2)}
    .publish-copy{max-width:34rem}.publish-copy .eyebrow{font-size:.72rem}.publish-copy h2{margin:.35rem 0 .25rem;font:700 1.35rem/1.1 var(--display)}.publish-copy p{margin:0;color:var(--history-muted);font:500 .9rem/1.45 var(--agate)}
    .publish-controls{display:grid;gap:.7rem;min-width:20rem}.visibility-options{display:grid;grid-template-columns:1fr 1fr;gap:.5rem}.visibility-option{position:relative;display:grid;gap:.15rem;padding:.7rem .8rem;border:1px solid #39433e;background:#0a0e0c;cursor:pointer}.visibility-option:has(input:checked){border-color:var(--signal);background:#141b0d}.visibility-option input{position:absolute;opacity:0}.visibility-option strong{font:800 .82rem var(--agate)}.visibility-option span{color:var(--history-muted);font:500 .75rem/1.35 var(--agate)}
    .publish-action{display:flex;justify-content:flex-end}.publish-action button,.publish-result button{border:0;border-radius:0;background:var(--signal);color:#08100c;padding:.72rem .9rem;font:800 .78rem var(--agate);letter-spacing:.03em;text-transform:uppercase;cursor:pointer}.publish-action button:disabled{opacity:.45;cursor:wait}.publish-status{grid-column:1/-1;min-height:1.3rem;margin:0;color:var(--gold2);font:500 .85rem/1.4 var(--agate)}
    .publish-result{grid-column:1/-1;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.6rem;align-items:center;padding-top:.8rem;border-top:1px solid var(--history-line)}.publish-result a{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--signal);font:600 .9rem var(--agate)}.publish-result button{background:transparent;color:var(--ink);border:1px solid #39433e}
    .shared-history.history-ready .import-card,.shared-history .publish-panel{display:none}.shared-history:not(.history-ready) .tabs,.shared-history:not(.history-ready) .panel,.shared-history:not(.history-ready) .lh-footer{display:none!important}.shared-history-error .import-card{display:block}.shared-history-error .import-actions{display:none}

    .history-subsection{margin-top:2.5rem}.history-subsection>.section-head{margin-bottom:1rem}
    #records .record-grid,#trophies .record-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
    .trophy-card{display:grid;gap:1rem;border-top:1px solid var(--history-line);padding:1rem 1.1rem}
    .trophy-card__identity h3{margin:0;font:700 1.15rem var(--display)}
    .trophy-card__identity p{margin:.2rem 0 0;color:var(--history-muted);font:500 .82rem/1.35 var(--agate);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .trophy-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border-top:1px solid var(--history-line);padding-top:.85rem}
    .trophy-stat{min-width:0;padding:0 .75rem;border-right:1px solid var(--history-line)}
    .trophy-stat:first-child{padding-left:0}.trophy-stat:last-child{padding-right:0;border-right:0}
    .trophy-stat strong{display:block;font:800 1.35rem/1 var(--data)}.trophy-stat.is-title strong{color:var(--signal)}
    .trophy-stat span{display:block;margin-top:.35rem;color:var(--history-muted);font:800 .7rem/1.25 var(--agate);letter-spacing:.03em;text-transform:uppercase}
    .trophy-ledger{min-width:48rem}.trophy-ledger th:first-child,.trophy-ledger td:first-child{text-align:left}
    .h2h-table{min-width:max-content}.h2h-table th,.h2h-table td{min-width:4.5rem;text-align:center!important;font-size:.875rem}.h2h-table th:first-child,.h2h-table td:first-child{position:sticky;left:0;min-width:10rem;text-align:left!important;z-index:2}.h2h-table th:first-child{background:#171e1b}.h2h-table td:first-child{background:var(--history-panel-2);font-family:var(--display);font-weight:700}.h2h-table .self{background:repeating-linear-gradient(135deg,#151b19,#151b19 6px,#101513 6px,#101513 12px)}.h2h-table .empty{color:#68726c}
    .rivalry-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:.8rem;margin-top:.8rem}.rivalry-card{border:1px solid var(--history-line);background:var(--history-panel);padding:1rem}.rivalry-card small{color:var(--signal);font:800 .75rem var(--agate);text-transform:uppercase}.rivalry-card strong{display:block;margin:.45rem 0 .15rem;font:700 1.1rem var(--display)}.rivalry-card span{color:var(--history-muted);font-size:.875rem}
    .manager-browser{display:grid;grid-template-columns:14rem minmax(0,1fr);gap:1rem}.manager-list{display:grid;align-content:start;border:1px solid var(--history-line);background:var(--history-panel)}.manager-list button{display:grid;gap:.18rem;width:100%;border:0;border-bottom:1px solid var(--history-line);background:transparent;color:var(--ink);padding:.85rem 1rem;text-align:left;cursor:pointer}.manager-list button:last-child{border-bottom:0}.manager-list button[aria-selected=true]{background:#1a241f;box-shadow:inset 3px 0 0 var(--signal)}.manager-list strong{font:700 .95rem var(--display)}.manager-list span{color:var(--history-muted);font-size:.78rem}
    .manager-detail{min-width:0}.career-head{padding:.2rem 0 1rem}.career-head h3{margin:.25rem 0;font:700 clamp(2rem,4vw,3.5rem)/.95 var(--display);letter-spacing:-.035em}.career-head p{margin:0;color:var(--history-muted);font-size:.875rem}
    .career-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid var(--history-line);background:var(--history-panel);margin-bottom:1rem}.career-stat{padding:1rem;border-right:1px solid var(--history-line);border-bottom:1px solid var(--history-line)}.career-stat:nth-child(4n){border-right:0}.career-stat:nth-last-child(-n+4){border-bottom:0}.career-stat strong{display:block;font:800 1.3rem var(--data)}.career-stat span{display:block;margin-top:.25rem;color:var(--history-muted);font:800 .68rem var(--agate);letter-spacing:.06em;text-transform:uppercase}
    .career-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(17rem,.8fr);gap:.8rem}.career-panel{border:1px solid var(--history-line);background:var(--history-panel);padding:1rem}.career-panel h4{margin:0 0 .75rem;font:700 1.15rem var(--display)}.career-panel+.career-panel{margin-top:.8rem}.career-table{min-width:34rem}.career-table th:first-child,.career-table td:first-child{text-align:left}.career-note{display:flex;justify-content:space-between;gap:1rem;padding:.65rem 0;border-top:1px solid var(--history-line);font-size:.875rem}.career-note span{color:var(--history-muted);text-align:right}.career-aliases{margin:0 0 1rem}
    .weeks-head{display:flex;align-items:end;justify-content:space-between;gap:1rem}.segmented{display:flex;border:1px solid var(--history-line)}.segmented button{border:0;background:transparent;color:var(--history-muted);padding:.6rem .8rem;font:800 .75rem var(--agate);text-transform:uppercase;cursor:pointer}.segmented button[aria-pressed=true]{background:var(--signal);color:#08100c}.top-weeks{min-width:46rem}.top-weeks th:first-child,.top-weeks td:first-child{text-align:left}
    @media(max-width:1450px){.lh-ambient-card{display:none}}
    @media(max-width:900px){#records .record-grid,#trophies .record-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.career-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.career-stat:nth-child(2n){border-right:0}.career-stat:nth-last-child(-n+4){border-bottom:1px solid var(--history-line)}.career-stat:nth-last-child(-n+2){border-bottom:0}.career-grid{grid-template-columns:1fr}.rivalry-grid{grid-template-columns:1fr}}
    @media(max-width:760px){.lh{padding-inline:.85rem}.lh-head{grid-template-columns:1fr;min-height:0;padding:1.6rem 1rem}.lh-meta{justify-content:flex-start;gap:1.2rem}.lh-meta div{min-width:auto}.import-line{align-items:flex-start;flex-direction:column}.publish-panel{grid-template-columns:1fr}.publish-controls{min-width:0}.publish-action{justify-content:flex-start}.review-head{grid-template-columns:1fr}.match-people{grid-template-columns:1fr}.match-or{text-align:center}.choice-actions{grid-template-columns:1fr}.review-footer{align-items:flex-start;flex-direction:column}.dashboard,.source-grid{grid-template-columns:1fr}.record-grid,.manager-grid,.season-grid{grid-template-columns:1fr}.tabs{top:3.4rem}.history-snapshot{grid-template-columns:1fr 1fr}.champ{min-height:11rem}.manager-browser{grid-template-columns:1fr}.manager-list{display:flex;overflow:auto}.manager-list button{min-width:10rem;border-bottom:0;border-right:1px solid var(--history-line)}.manager-list button[aria-selected=true]{box-shadow:inset 0 -3px 0 var(--signal)}.weeks-head{align-items:flex-start;flex-direction:column}}
    @media(max-width:500px){.lh-meta{gap:.9rem}.lh-meta b{font-size:1.25rem}.lh-meta span{font-size:.6rem}.tabs{gap:1.1rem}.history-snapshot{grid-template-columns:1fr}.lh-footer{display:block}.season-card{grid-template-columns:1fr}.manager-card__head{display:block}.manager-card__head>b{display:block;margin-top:.8rem}#records .record-grid,#trophies .record-grid{grid-template-columns:1fr}.career-stats{grid-template-columns:1fr}.career-stat,.career-stat:nth-child(n){border-right:0;border-bottom:1px solid var(--history-line)}.career-stat:last-child{border-bottom:0}}
    '''
    script = r'''
    <script>(function(){
      var tabs=[].slice.call(document.querySelectorAll('.tab'));
      var panels=[].slice.call(document.querySelectorAll('.panel'));
      function select(id){tabs.forEach(function(t){var on=t.dataset.tab===id;t.setAttribute('aria-selected',String(on));});panels.forEach(function(p){p.classList.toggle('active',p.id===id);});}
      tabs.forEach(function(t){t.addEventListener('click',function(){select(t.dataset.tab);history.replaceState(null,'','#'+t.dataset.tab);});});
      var initial=location.hash.slice(1);if(document.getElementById(initial))select(initial);
      var state={capture:null,review:null,identities:[],pairs:[],choices:{},pairIndex:0,dirty:false};
      var status=document.getElementById('import-status');
      var detail=document.getElementById('import-summary');
      var stats=document.getElementById('capture-stats');
      var flow=document.getElementById('match-flow');
      var matchCard=document.getElementById('match-card');
      var complete=document.getElementById('match-complete');
      var progress=document.getElementById('match-progress');
      var decisions=document.getElementById('decision-summary');
      var approve=document.getElementById('save-manager-matches');
      var managerReview=document.getElementById('manager-review');
      var managerStep=document.getElementById('manager-step');
      var managerTitle=document.getElementById('manager-title');
      var managerCopy=document.getElementById('manager-copy');
      var reviewManagers=document.getElementById('review-managers');
      var setupReady=document.getElementById('setup-ready');
      var clear=document.getElementById('clear-import');
      var check=document.getElementById('check-extension');
      var result=document.getElementById('review-result');
      var leagueTitle=document.getElementById('league-title');
      var headerSeasons=document.getElementById('header-seasons');
      var headerGames=document.getElementById('header-games');
      var headerTeams=document.getElementById('header-teams');
      var ambientSeasons=document.getElementById('ambient-seasons');
      var ambientGames=document.getElementById('ambient-games');
      function say(text){status.textContent=text;}
      function pairKey(pair){return [pair.a,pair.b].sort().join('::');}
      function identity(id){return state.identities.find(function(row){return row.identityId===id;});}
      function yearsText(row){var years=row.seasons.slice().sort(function(a,b){return a-b;});var range=years.length===1?String(years[0]):years[0]+'–'+years[years.length-1];return range+' · '+years.length+' season'+(years.length===1?'':'s');}
      function fillPerson(prefix,row){document.getElementById(prefix+'-name').textContent=row.displayName;document.getElementById(prefix+'-meta').textContent=yearsText(row);document.getElementById(prefix+'-teams-summary').textContent=row.teamNames.length+' team name'+(row.teamNames.length===1?'':'s');document.getElementById(prefix+'-teams').textContent=row.teamNames.join(' · ');}
      function savedChoice(pair){if(!state.review)return null;var links={};(state.review.identities||[]).forEach(function(row){links[row.identityId]=links[row.identityId]||[];if(row.mergeInto){links[row.mergeInto]=links[row.mergeInto]||[];links[row.identityId].push(row.mergeInto);links[row.mergeInto].push(row.identityId);}});var queue=[pair.a],seen={};while(queue.length){var id=queue.shift();if(id===pair.b)return 'same';if(seen[id])continue;seen[id]=true;(links[id]||[]).forEach(function(next){if(!seen[next])queue.push(next);});}return 'different';}
      function allAnswered(){return state.pairs.every(function(pair){return Boolean(state.choices[pairKey(pair)]);});}
      function updateSave(){var ready=allAnswered();approve.disabled=!ready||(!state.dirty&&Boolean(state.review));approve.textContent=!state.dirty&&state.review?'Saved':'Save manager matches';}
      function showComplete(){matchCard.hidden=true;complete.hidden=false;decisions.replaceChildren();if(!state.pairs.length){document.getElementById('complete-title').textContent='Manager list looks good';document.getElementById('complete-copy').textContent='ESPN did not find any likely duplicate accounts.';}else{document.getElementById('complete-title').textContent='Manager matches complete';document.getElementById('complete-copy').textContent='You can change any answer before saving.';state.pairs.forEach(function(pair){var a=identity(pair.a),b=identity(pair.b);var row=document.createElement('div');row.className='decision-row';var names=document.createElement('span');names.textContent=a.displayName+' + '+b.displayName;var answer=document.createElement('b');answer.textContent=state.choices[pairKey(pair)]==='same'?'Same person':'Different people';row.append(names,answer);decisions.appendChild(row);});}updateSave();}
      function showPair(index){state.pairIndex=index;complete.hidden=true;matchCard.hidden=false;var pair=state.pairs[index],a=identity(pair.a),b=identity(pair.b);progress.textContent='Match '+(index+1)+' of '+state.pairs.length;fillPerson('person-a',a);fillPerson('person-b',b);var choice=state.choices[pairKey(pair)]||'';document.getElementById('same-person').setAttribute('aria-pressed',String(choice==='same'));document.getElementById('different-people').setAttribute('aria-pressed',String(choice==='different'));}
      function choose(value){var pair=state.pairs[state.pairIndex];state.choices[pairKey(pair)]=value;state.dirty=true;result.textContent='';for(var offset=1;offset<=state.pairs.length;offset+=1){var index=(state.pairIndex+offset)%state.pairs.length;if(!state.choices[pairKey(state.pairs[index])]){showPair(index);updateSave();return;}}showComplete();}
      function compactManagerReview(){managerReview.classList.add('is-complete');managerStep.textContent='Complete';managerTitle.textContent='Managers matched';managerCopy.textContent=state.pairs.length+' manager match'+(state.pairs.length===1?'':'es')+' saved.';reviewManagers.hidden=false;}
      function finishSetup(){compactManagerReview();setupReady.hidden=false;}
      function render(record){
        state.capture=record.payload;state.review=record.review||null;
        var p=state.capture;detail.classList.add('open');clear.hidden=false;check.hidden=true;document.body.classList.add('has-import');
        leagueTitle.textContent=p.league.name;
        document.title=p.league.name+' League History | LineupBeat';
        headerSeasons.textContent=p.counts.seasons;
        headerGames.textContent=p.counts.matchups;
        headerTeams.textContent=p.counts.teams;
        ambientSeasons.textContent=p.counts.seasons;
        ambientGames.textContent=p.counts.matchups;
        stats.replaceChildren();stats.hidden=true;
        if(p.incomplete&&p.incomplete.length){var gap=document.createElement('span');gap.textContent='Unavailable seasons: '+p.incomplete.map(function(x){return x.year;}).join(', ');stats.appendChild(gap);stats.hidden=false;}
        state.identities=p.identityReview.identities||[];state.pairs=[];state.choices={};state.dirty=false;
        var seen={};(p.identityReview.suggestions||[]).forEach(function(pair){var key=pairKey(pair);if(!seen[key]&&identity(pair.a)&&identity(pair.b)){seen[key]=true;state.pairs.push(pair);}});
        state.pairs.forEach(function(pair){var saved=savedChoice(pair);if(saved)state.choices[pairKey(pair)]=saved;});
        flow.hidden=false;document.getElementById('other-manager-count').textContent=(state.identities.length-new Set(state.pairs.flatMap(function(pair){return [pair.a,pair.b];})).size)+' other managers already look distinct.';
        if(allAnswered())showComplete();else showPair(state.pairs.findIndex(function(pair){return !state.choices[pairKey(pair)];}));
        result.textContent=state.review?'Manager matches are saved on this device.':'Answer each match, then save.';
        managerReview.classList.remove('is-complete');managerStep.textContent='One quick step';managerTitle.textContent='Match managers';managerCopy.textContent='ESPN found accounts with similar names. Tell us whether each pair belongs to the same person.';reviewManagers.hidden=true;setupReady.hidden=true;
        if(state.review)finishSetup();
        say('ESPN import connected');
      }
      document.getElementById('check-extension').addEventListener('click',function(){say('Checking for a local ESPN import…');window.postMessage({type:'LB_LEAGUE_HISTORY_CONNECT_REQUEST',version:1},location.origin);});
      clear.addEventListener('click',function(){window.postMessage({type:'LB_LEAGUE_HISTORY_CLEAR_REQUEST',version:1},location.origin);});
      document.getElementById('same-person').addEventListener('click',function(){choose('same');});
      document.getElementById('different-people').addEventListener('click',function(){choose('different');});
      document.getElementById('change-answers').addEventListener('click',function(){if(state.pairs.length)showPair(0);});
      reviewManagers.addEventListener('click',function(){managerReview.classList.remove('is-complete');managerStep.textContent='One quick step';managerTitle.textContent='Match managers';managerCopy.textContent='ESPN found accounts with similar names. Tell us whether each pair belongs to the same person.';reviewManagers.hidden=true;setupReady.hidden=true;if(allAnswered())showComplete();else showPair(0);});
      approve.addEventListener('click',function(){
        if(!state.capture)return;
        var parent={};state.identities.forEach(function(row){parent[row.identityId]=row.identityId;});
        function find(id){while(parent[id]!==id){parent[id]=parent[parent[id]];id=parent[id];}return id;}
        function union(a,b){a=find(a);b=find(b);if(a!==b)parent[b]=a;}
        state.pairs.forEach(function(pair){if(state.choices[pairKey(pair)]==='same')union(pair.a,pair.b);});
        var groups={};state.identities.forEach(function(row){var root=find(row.identityId);(groups[root]||(groups[root]=[])).push(row);});
        var canonical={};Object.keys(groups).forEach(function(root){groups[root].sort(function(a,b){var ay=Math.min.apply(null,a.seasons),by=Math.min.apply(null,b.seasons);return ay-by||b.seasons.length-a.seasons.length||a.identityId.localeCompare(b.identityId);});canonical[root]=groups[root][0].identityId;});
        var identities=state.identities.map(function(row){var master=canonical[find(row.identityId)];return {identityId:row.identityId,displayName:row.displayName,mergeInto:master===row.identityId?null:master};});
        var review={schemaVersion:'lineupbeat-history-identity-review-v1',capturedAt:state.capture.capturedAt,approvedAt:new Date().toISOString(),leagueId:state.capture.league.id,identities:identities};
        state.review=review;window.postMessage({type:'LB_LEAGUE_HISTORY_SAVE_REVIEW_REQUEST',version:1,review:review},location.origin);result.textContent='Saving…';
      });
      window.addEventListener('message',function(event){
        if(event.source!==window||event.origin!==location.origin||!event.data||event.data.version!==1)return;
        if(event.data.type==='LB_LEAGUE_HISTORY_EXTENSION_READY'){say(event.data.hasHistory?'ESPN import found. Loading review…':'Connector ready. Import from an ESPN league page.');clear.hidden=!event.data.hasHistory;}
        if(event.data.type==='LB_LEAGUE_HISTORY_CAPTURE')render({payload:event.data.payload,review:event.data.review});
        if(event.data.type==='LB_LEAGUE_HISTORY_REVIEW_COMPLETE'){if(event.data.ok){state.dirty=false;updateSave();result.textContent='Manager matches saved.';finishSetup();}else result.textContent='Manager matches could not be saved.';}
        if(event.data.type==='LB_LEAGUE_HISTORY_CLEAR_COMPLETE'){state.capture=null;state.review=null;state.identities=[];state.pairs=[];state.choices={};detail.classList.remove('open');managerReview.classList.remove('is-complete');setupReady.hidden=true;clear.hidden=true;check.hidden=false;document.body.classList.remove('has-import');leagueTitle.textContent=leagueTitle.dataset.demo;headerSeasons.textContent=headerSeasons.dataset.demo;headerGames.textContent=headerGames.dataset.demo;headerTeams.textContent=headerTeams.dataset.demo;ambientSeasons.textContent=ambientSeasons.dataset.demo;ambientGames.textContent=ambientGames.dataset.demo;document.title=leagueTitle.dataset.demo+' League History | LineupBeat';say('Local ESPN import cleared.');}
      });
    }());</script>'''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{title} League History | LineupBeat</title><meta name="robots" content="noindex,nofollow">
    <meta name="description" content="Development prototype for the LineupBeat fantasy football league history tracker.">
    <style>{seo.SHELL_CSS}{seo.TEAMS_CSS}{seo.NAV_CSS}{styles}</style></head><body>
    {seo.site_nav('data', 'nfl')}
    <div class="lh-atmosphere" aria-hidden="true">
      <div class="lh-ambient-card lh-ambient-seasons"><span>Season archive</span><strong id="ambient-seasons" data-demo="{summary['counts']['seasons']}">{summary['counts']['seasons']}</strong><small>Seasons indexed</small><svg class="lh-ambient-trace" viewBox="0 0 180 52" focusable="false"><polyline points="2,43 24,31 47,36 70,18 94,28 119,11 145,21 178,5"/></svg></div>
      <div class="lh-ambient-card lh-ambient-games"><span>Matchup ledger</span><strong id="ambient-games" data-demo="{summary['counts']['games']}">{summary['counts']['games']}</strong><small>Games preserved</small><div class="lh-ambient-bars"><i style="--w:91%"></i><i style="--w:73%"></i><i style="--w:58%"></i><i style="--w:42%"></i></div></div>
    </div>
    <main class="lh"><div class="lh-status"><i></i>private local import</div>
      <header class="lh-head"><div><span class="lh-kicker">League history</span><h1 id="league-title" data-demo="{title}">{title}</h1></div>
      <div class="lh-meta"><div><b id="header-seasons" data-demo="{summary['counts']['seasons']}">{summary['counts']['seasons']}</b><span>seasons</span></div><div><b id="header-games" data-demo="{summary['counts']['games']}">{summary['counts']['games']}</b><span>matchups</span></div><div><b id="header-teams" data-demo="{summary['counts']['franchises']}">{summary['counts']['franchises']}</b><span>teams</span></div></div></header>
      <section class="import-card" aria-labelledby="import-title"><div class="import-line"><div class="import-copy"><strong id="import-title">ESPN history import</strong><span id="import-status" role="status">Install connector 0.3.0, then import from your ESPN league page.</span></div><div class="import-actions"><button id="check-extension" type="button">Check connector</button><button id="edit-manager-matches" class="quiet" type="button" hidden>Manager matches</button><button id="clear-import" class="quiet" type="button" hidden>Remove import</button></div></div>
        <div class="import-summary" id="import-summary"><div class="capture-stats" id="capture-stats" hidden></div><section class="manager-review" id="manager-review"><div class="review-head"><div><span class="review-step" id="manager-step">One quick step</span><h2 id="manager-title">Match managers</h2><p id="manager-copy">ESPN found accounts with similar names. Tell us whether each pair belongs to the same person.</p></div><button class="quiet" id="review-managers" type="button" hidden>Review</button></div><div class="match-flow" id="match-flow" hidden><section class="match-card" id="match-card"><div class="match-progress"><span id="match-progress">Match 1</span><span>Possible duplicate</span></div><div class="match-people"><article class="person-card"><small>ESPN account A</small><strong id="person-a-name"></strong><div class="person-meta" id="person-a-meta"></div><details class="person-aliases"><summary id="person-a-teams-summary"></summary><p id="person-a-teams"></p></details></article><span class="match-or">and</span><article class="person-card"><small>ESPN account B</small><strong id="person-b-name"></strong><div class="person-meta" id="person-b-meta"></div><details class="person-aliases"><summary id="person-b-teams-summary"></summary><p id="person-b-teams"></p></details></article></div><p class="match-question">Are these the same person?</p><div class="choice-actions"><button id="same-person" type="button" aria-pressed="false">Yes, same person</button><button id="different-people" type="button" aria-pressed="false">No, different people</button></div></section><section class="match-complete" id="match-complete" hidden><h3 id="complete-title">Manager matches complete</h3><p id="complete-copy">You can change any answer before saving.</p><div class="decision-summary" id="decision-summary"></div><div class="import-actions"><button class="quiet" id="change-answers" type="button">Change answers</button></div></section></div><div class="review-footer"><p id="other-manager-count">Other managers already look distinct.</p><div class="import-actions"><button id="save-manager-matches" type="button" disabled>Save manager matches</button></div></div><p class="review-result" id="review-result" role="status"></p></section><section class="setup-ready" id="setup-ready" hidden><small>Setup complete</small><h2>Your league history is ready</h2><p>Every historical team and season is included automatically. Manager matches are saved on this device.</p></section></div></section>
      <section class="publish-panel" id="publish-panel" aria-labelledby="publish-title" hidden><div class="publish-copy"><span class="eyebrow">Share with your league</span><h2 id="publish-title">Publish league history</h2><p>Create one permanent, view-only link. Future imports update the same page.</p></div><div class="publish-controls"><div class="visibility-options"><label class="visibility-option"><input type="radio" name="league-visibility" value="unlisted" checked><strong>Unlisted</strong><span>Only people with the link</span></label><label class="visibility-option"><input type="radio" name="league-visibility" value="public"><strong>Public</strong><span>Ready for future discovery</span></label></div><div class="publish-action"><button id="publish-league" type="button">Create share link</button></div></div><div class="publish-result" id="publish-result" hidden><a id="published-url" href="#" target="_blank" rel="noopener"></a><button id="copy-published-url" type="button">Copy link</button></div><p class="publish-status" id="publish-status" role="status"></p></section>
      <nav class="tabs" aria-label="League history sections">
        <button class="tab" data-tab="overview" aria-selected="true">Overview</button><button class="tab" data-tab="trophies" aria-selected="false">Trophy case</button>
        <button class="tab" data-tab="all-time" aria-selected="false">All-time</button><button class="tab" data-tab="managers" aria-selected="false">Managers</button>
        <button class="tab" data-tab="seasons" aria-selected="false">Seasons</button><button class="tab" data-tab="records" aria-selected="false">Records</button>
      </nav>
      <section class="panel active" id="overview"><div class="dashboard">
        <article class="card champ"><div><span class="eyebrow">Defending champion · {trophy['year']}</span></div><strong>{esc(manager[trophy['championFranchiseId']])}</strong><span class="season-mark">01</span></article>
        <article class="card power"><span class="eyebrow">Preseason Elo</span><h2>Power five</h2><ol>{power_rows}</ol></article></div>
        <div class="notice"><strong>Prototype boundary:</strong> Dashboard results below remain fictional. Imported ESPN history appears only in the private review panel above and stays in this browser.</div></section>
      <section class="panel" id="trophies"><div class="section-head"><div><span class="eyebrow">Hardware</span><h2>Trophy case</h2></div><p>Titles, scoring crowns, and runner-up finishes.</p></div>
        <div class="record-grid" id="trophy-cabinet"><article class="record-card"><small>{trophy['year']} champion</small><b>🏆</b><h3>{esc(manager[trophy['championFranchiseId']])}</h3><p>Final standing: 1</p></article>
        <article class="record-card"><small>{trophy['year']} runner-up</small><b>02</b><h3>{esc(manager[trophy['runnerUpFranchiseId']])}</h3><p>Championship finalist</p></article>
        <article class="record-card"><small>{trophy['year']} scoring crown</small><b>SC</b><h3>{esc(manager[trophy['scoringCrownFranchiseId']])}</h3><p>Regular-season points leader</p></article></div>
        <div class="history-subsection"><div class="section-head"><span class="eyebrow">By season</span><h2>Championship ledger</h2></div><div class="table-wrap"><table class="history-table trophy-ledger" id="trophy-ledger"></table></div></div></section>
      <section class="panel" id="all-time"><div class="section-head"><div><span class="eyebrow">Standings</span><h2>All-time leaders</h2></div><p>Career results across every season and team name.</p></div>
        <div class="table-wrap"><table class="history-table"><thead><tr><th>#</th><th>Manager</th><th>Record</th><th>Win%</th><th>PPG</th><th>Titles</th></tr></thead><tbody>{franchise_rows(summary)}</tbody></table></div>
        <div class="history-subsection"><div class="section-head"><span class="eyebrow">Matchups</span><h2>Head-to-head</h2><p>Read across each row to see the all-time series.</p></div><div class="table-wrap"><table class="history-table h2h-table" id="head-to-head"></table></div><div class="rivalry-grid" id="rivalries"></div></div></section>
      <section class="panel" id="managers"><div class="section-head"><div><span class="eyebrow">Careers</span><h2>Managers</h2></div><p>Career totals, season results, team names, and every matchup.</p></div><div class="manager-browser"><nav class="manager-list" id="manager-list" aria-label="Managers"></nav><div class="manager-detail" id="manager-detail"><div class="manager-grid">{manager_cards(summary)}</div></div></div></section>
      <section class="panel" id="seasons"><div class="section-head"><div><span class="eyebrow">Archive</span><h2>Seasons</h2></div><p>Every season and team stays in the archive.</p></div><div class="season-grid">{season_cards(canonical)}</div></section>
      <section class="panel" id="records"><div class="section-head"><div><span class="eyebrow">Record book</span><h2>League records</h2></div><p>Career, season, and single-game records.</p></div><div class="record-grid" id="record-book">{record_cards(summary)}</div>
        <div class="history-subsection"><div class="section-head weeks-head"><div><span class="eyebrow">Single weeks</span><h2 id="weeks-title">Biggest weeks ever</h2></div><div class="segmented" id="weeks-toggle"><button type="button" data-kind="best" aria-pressed="true">Highest</button><button type="button" data-kind="worst" aria-pressed="false">Lowest</button></div></div><div class="table-wrap"><table class="history-table top-weeks" id="top-weeks"></table></div></div>
        <div class="source-grid" style="margin-top:1rem"><article class="card"><span class="eyebrow">Design provenance</span><h2>Public reference</h2><p>Architecture and rating behavior informed by the public BGNCo repository. No participant records are copied into LineupBeat.</p></article>
        <article class="card"><span class="eyebrow">Privacy model</span><h2>League controlled</h2><ul><li>Private, unlisted, or public publishing</li><li>Permanent franchise IDs with alias review</li><li>Ledger records obligations; it never holds money</li></ul></article></div></section>
      <footer class="lh-footer"><span>Fictional demonstration data · prototype calculations by LineupBeat.</span><span>Demo snapshot {esc(captured)}</span></footer>
    </main>{script}<script src="/assets/league-history-dashboard.js"></script></body></html>'''


def main() -> int:
    canonical = demo_history()
    summary = summarize_history(canonical)
    payload = {"canonical": canonical, "recordBook": summary}
    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    PAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_OUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUT.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    PAGE_OUT.write_text(build_page(canonical, summary))
    DASHBOARD_OUT.write_text(DASHBOARD_SOURCE.read_text())
    print(f"Built {PAGE_OUT.relative_to(ROOT)} from {summary['counts']['games']} matchups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
