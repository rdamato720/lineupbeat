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
    .lh h1{margin:.35rem 0 0;font:700 clamp(2.2rem,4vw,3.6rem)/.94 var(--agate);letter-spacing:-.035em;text-transform:uppercase}
    .lh-meta{display:flex;gap:1.7rem}.lh-meta div{display:grid;gap:.15rem}.lh-meta b{font:800 1.65rem/1 var(--data)}.lh-meta span{color:var(--muted);font:.72rem var(--agate);text-transform:uppercase;letter-spacing:.08em}
    .import-card{margin:1rem 0;border:1px solid #3b454a;background:#101417;padding:1rem}.import-line{display:flex;align-items:center;justify-content:space-between;gap:1rem}.import-copy{display:grid;gap:.18rem}.import-copy strong{font:750 1.05rem var(--agate)}.import-copy span{color:var(--muted);font-size:.95rem}.import-actions{display:flex;flex-wrap:wrap;gap:.5rem}.import-actions button{border:0;border-radius:.2rem;padding:.72rem .95rem;background:var(--signal);color:#09100d;font:800 .78rem var(--agate);letter-spacing:.03em;text-transform:uppercase;cursor:pointer}.import-actions button:disabled{cursor:not-allowed;opacity:.38}.import-actions .quiet{background:#242c30;color:var(--ink)}.import-actions button[hidden]{display:none}.import-summary{display:none;margin-top:1rem;border-top:1px solid #2c3438;padding-top:1.25rem}.import-summary.open{display:block}.capture-stats{display:flex;flex-wrap:wrap;gap:.55rem;margin-bottom:1rem}.capture-stats[hidden],.match-flow[hidden],.match-card[hidden],.match-complete[hidden]{display:none}.capture-stats span{border:1px solid #343d42;padding:.45rem .62rem;color:var(--muted);font:.82rem var(--data)}.review-head{margin-bottom:1rem}.review-step{display:block;color:var(--gold2);font:800 .75rem var(--agate);letter-spacing:.09em;text-transform:uppercase}.review-head h2{margin:.35rem 0 0;font:700 1.6rem var(--agate)}.review-head p{margin:.35rem 0 0;color:var(--muted);font-size:1rem;line-height:1.45}.match-card{border:1px solid #3c4449;background:#0a0c0e;padding:1.25rem}.match-progress{display:flex;justify-content:space-between;gap:1rem;color:var(--muted);font:700 .82rem var(--agate)}.match-people{display:grid;grid-template-columns:1fr auto 1fr;gap:1rem;align-items:stretch;margin:1rem 0}.match-or{align-self:center;color:var(--muted);font:800 .72rem var(--agate);letter-spacing:.08em;text-transform:uppercase}.person-card{border:1px solid #30383d;background:#111518;padding:1rem;min-width:0}.person-card small{display:block;color:var(--muted);font:800 .7rem var(--agate);letter-spacing:.08em;text-transform:uppercase}.person-card strong{display:block;margin:.3rem 0 .55rem;font:750 1.2rem var(--agate)}.person-meta{color:var(--muted);font:.9rem var(--agate)}.person-aliases{margin-top:.55rem}.person-aliases summary{cursor:pointer;color:var(--gold2);font:700 .82rem var(--agate)}.person-aliases p{margin:.35rem 0 0;color:var(--muted);font:.82rem/1.45 var(--agate)}.match-question{margin:.1rem 0 .75rem;text-align:center;font:750 1.1rem var(--agate)}.choice-actions{display:grid;grid-template-columns:1fr 1fr;gap:.65rem}.choice-actions button{border:1px solid #4a555b;border-radius:.2rem;background:#171c20;color:var(--ink);padding:.85rem 1rem;font:800 .9rem var(--agate);cursor:pointer}.choice-actions button:first-child{border-color:var(--signal);color:var(--signal)}.choice-actions button:hover,.choice-actions button:focus-visible{background:#22292d}.choice-actions button[aria-pressed=true]{background:#2a3217;border-color:var(--gold2);color:var(--gold2)}.match-complete{border:1px solid #3c4449;background:#0a0c0e;padding:1.25rem}.match-complete h3{margin:0;font:750 1.25rem var(--agate)}.match-complete p{margin:.35rem 0 0;color:var(--muted);font:1rem/1.45 var(--agate)}.decision-summary{display:grid;gap:.45rem;margin:1rem 0}.decision-row{display:flex;justify-content:space-between;gap:1rem;border-top:1px solid #273035;padding-top:.55rem;font:.9rem var(--agate)}.decision-row b{color:var(--gold2)}.review-footer{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-top:1rem}.review-footer p{margin:0;color:var(--muted);font:.9rem var(--agate)}.review-result{min-height:1.2em;color:var(--gold2);font:.9rem var(--agate);margin:.8rem 0 0}.has-import .import-card{max-width:50rem;margin:2rem auto;padding:1.25rem}.has-import .import-line{padding-bottom:.2rem}.has-import .import-copy strong{font-size:.9rem;color:var(--gold2);text-transform:uppercase;letter-spacing:.06em}.has-import .import-actions .quiet{padding:.5rem .65rem;background:transparent;color:var(--muted);border:1px solid #343d42;font-size:.7rem}.has-import .tabs,.has-import .panel,.has-import .lh-footer{display:none}
    .review-head{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:1rem;align-items:start}.review-head .quiet{align-self:start;border:1px solid #3d474c;background:transparent;color:var(--muted);padding:.5rem .7rem}.manager-review.is-complete{border-bottom:1px solid #2c3438;margin-bottom:1.25rem}.manager-review.is-complete .review-head{margin-bottom:1.15rem}.manager-review.is-complete .match-flow,.manager-review.is-complete .review-footer,.manager-review.is-complete .review-result{display:none}.franchise-review{border-top:1px solid #2c3438;padding-top:1.25rem}.franchise-review[hidden],.setup-ready[hidden]{display:none}.team-transition{display:grid;grid-template-columns:1fr auto 1fr;gap:1rem;align-items:stretch;margin:1rem 0}.team-period{border:1px solid #30383d;background:#111518;padding:1rem;min-width:0}.team-period small{display:block;color:var(--gold2);font:800 .7rem var(--agate);letter-spacing:.08em;text-transform:uppercase}.team-period strong{display:block;margin:.3rem 0 .25rem;font:750 1.15rem var(--agate)}.team-period span{color:var(--muted);font:.9rem var(--agate)}.team-arrow{align-self:center;color:var(--muted);font:900 1.25rem var(--agate)}.setup-ready{border:1px solid #506426;background:#12170b;padding:1.25rem;margin-top:1.25rem}.setup-ready small{color:var(--signal);font:800 .72rem var(--agate);letter-spacing:.09em;text-transform:uppercase}.setup-ready h2{margin:.35rem 0;font:750 1.5rem var(--agate)}.setup-ready p{margin:0;color:var(--muted);font:1rem/1.45 var(--agate)}
    .tabs{display:flex;gap:.2rem;overflow:auto;padding:.9rem 0;border-bottom:1px solid #252b2f;position:sticky;top:3.8rem;background:#08090bf2;z-index:12}
    .tab{border:0;background:transparent;color:var(--muted);padding:.65rem .85rem;font:800 .78rem var(--agate);letter-spacing:.06em;text-transform:uppercase;cursor:pointer;white-space:nowrap;border-radius:.2rem}
    .tab[aria-selected=true]{background:var(--gold);color:#0b0c0d}.panel{display:none;padding-top:1.4rem}.panel.active{display:block}
    .has-import .tabs,.has-import .panel,.has-import .lh-footer{display:none!important}
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
    @media(max-width:760px){.lh-head{grid-template-columns:1fr}.lh-meta{justify-content:space-between}.import-line{align-items:flex-start;flex-direction:column}.review-head{grid-template-columns:1fr}.match-people,.team-transition{grid-template-columns:1fr}.match-or,.team-arrow{text-align:center}.choice-actions{grid-template-columns:1fr}.review-footer{align-items:flex-start;flex-direction:column}.dashboard,.source-grid{grid-template-columns:1fr}.record-grid,.manager-grid{grid-template-columns:1fr 1fr}.season-grid{grid-template-columns:1fr}.tabs{top:3.4rem}}
    @media(max-width:500px){.record-grid,.manager-grid{grid-template-columns:1fr}.lh-meta{gap:.8rem}.lh-meta b{font-size:1.25rem}.lh-footer{display:block}.season-card{grid-template-columns:1fr}}
    '''
    script = r'''
    <script>(function(){
      var tabs=[].slice.call(document.querySelectorAll('.tab'));
      var panels=[].slice.call(document.querySelectorAll('.panel'));
      function select(id){tabs.forEach(function(t){var on=t.dataset.tab===id;t.setAttribute('aria-selected',String(on));});panels.forEach(function(p){p.classList.toggle('active',p.id===id);});}
      tabs.forEach(function(t){t.addEventListener('click',function(){select(t.dataset.tab);history.replaceState(null,'','#'+t.dataset.tab);});});
      var initial=location.hash.slice(1);if(document.getElementById(initial))select(initial);
      var state={capture:null,review:null,identities:[],pairs:[],choices:{},pairIndex:0,dirty:false,teamTransitions:[],teamChoices:{},teamIndex:0,teamDirty:false,pendingSave:''};
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
      var teamReview=document.getElementById('franchise-review');
      var teamCard=document.getElementById('team-card');
      var teamComplete=document.getElementById('team-complete');
      var teamDecisions=document.getElementById('team-decision-summary');
      var saveTeams=document.getElementById('save-team-history');
      var teamResult=document.getElementById('team-result');
      var setupReady=document.getElementById('setup-ready');
      var clear=document.getElementById('clear-import');
      var check=document.getElementById('check-extension');
      var result=document.getElementById('review-result');
      var leagueTitle=document.getElementById('league-title');
      var headerSeasons=document.getElementById('header-seasons');
      var headerGames=document.getElementById('header-games');
      var headerTeams=document.getElementById('header-teams');
      function say(text){status.textContent=text;}
      function pairKey(pair){return [pair.a,pair.b].sort().join('::');}
      function identity(id){return state.identities.find(function(row){return row.identityId===id;});}
      function yearsText(row){var years=row.seasons.slice().sort(function(a,b){return a-b;});var range=years.length===1?String(years[0]):years[0]+'–'+years[years.length-1];return range+' · '+years.length+' season'+(years.length===1?'':'s');}
      function fillPerson(prefix,row){document.getElementById(prefix+'-name').textContent=row.displayName;document.getElementById(prefix+'-meta').textContent=yearsText(row);document.getElementById(prefix+'-teams-summary').textContent=row.teamNames.length+' team name'+(row.teamNames.length===1?'':'s');document.getElementById(prefix+'-teams').textContent=row.teamNames.join(' · ');}
      function savedChoice(pair){if(!state.review)return null;var links={};(state.review.identities||[]).forEach(function(row){links[row.identityId]=links[row.identityId]||[];if(row.mergeInto){links[row.mergeInto]=links[row.mergeInto]||[];links[row.identityId].push(row.mergeInto);links[row.mergeInto].push(row.identityId);}});var queue=[pair.a],seen={};while(queue.length){var id=queue.shift();if(id===pair.b)return 'same';if(seen[id])continue;seen[id]=true;(links[id]||[]).forEach(function(next){if(!seen[next])queue.push(next);});}return 'different';}
      function allAnswered(){return state.pairs.every(function(pair){return Boolean(state.choices[pairKey(pair)]);});}
      function updateSave(){var ready=allAnswered();approve.disabled=!ready||!state.dirty;approve.textContent=!state.dirty&&state.review?'Saved':'Save manager matches';}
      function showComplete(){matchCard.hidden=true;complete.hidden=false;decisions.replaceChildren();if(!state.pairs.length){document.getElementById('complete-title').textContent='Manager list looks good';document.getElementById('complete-copy').textContent='ESPN did not find any likely duplicate accounts.';}else{document.getElementById('complete-title').textContent='Manager matches complete';document.getElementById('complete-copy').textContent='You can change any answer before saving.';state.pairs.forEach(function(pair){var a=identity(pair.a),b=identity(pair.b);var row=document.createElement('div');row.className='decision-row';var names=document.createElement('span');names.textContent=a.displayName+' + '+b.displayName;var answer=document.createElement('b');answer.textContent=state.choices[pairKey(pair)]==='same'?'Same person':'Different people';row.append(names,answer);decisions.appendChild(row);});}updateSave();}
      function showPair(index){state.pairIndex=index;complete.hidden=true;matchCard.hidden=false;var pair=state.pairs[index],a=identity(pair.a),b=identity(pair.b);progress.textContent='Match '+(index+1)+' of '+state.pairs.length;fillPerson('person-a',a);fillPerson('person-b',b);var choice=state.choices[pairKey(pair)]||'';document.getElementById('same-person').setAttribute('aria-pressed',String(choice==='same'));document.getElementById('different-people').setAttribute('aria-pressed',String(choice==='different'));}
      function choose(value){var pair=state.pairs[state.pairIndex];state.choices[pairKey(pair)]=value;state.dirty=true;result.textContent='';for(var offset=1;offset<=state.pairs.length;offset+=1){var index=(state.pairIndex+offset)%state.pairs.length;if(!state.choices[pairKey(state.pairs[index])]){showPair(index);updateSave();return;}}showComplete();}
      function reviewedManagerMap(){var parent={};state.identities.forEach(function(row){parent[row.identityId]=row.identityId;});function find(id){while(parent[id]&&parent[id]!==id){parent[id]=parent[parent[id]];id=parent[id];}return id;}function union(a,b){if(!parent[a])parent[a]=a;if(!parent[b])parent[b]=b;a=find(a);b=find(b);if(a!==b)parent[b]=a;}(state.review&&state.review.identities||[]).forEach(function(row){if(row.mergeInto)union(row.identityId,row.mergeInto);});var groups={};state.identities.forEach(function(row){var root=find(row.identityId);(groups[root]||(groups[root]=[])).push(row);});var map={};Object.keys(groups).forEach(function(root){groups[root].sort(function(a,b){var ay=Math.min.apply(null,a.seasons),by=Math.min.apply(null,b.seasons);return ay-by||b.seasons.length-a.seasons.length||a.identityId.localeCompare(b.identityId);});var master=groups[root][0].identityId;groups[root].forEach(function(row){map[row.identityId]=master;});});return map;}
      function teamKey(row){return row.teamId+':'+row.fromYear+':'+row.toYear;}
      function buildTeamTransitions(){var managerMap=reviewedManagerMap(),previous={},changes=[],renames=0;function owners(team){var ids=(team.ownerIds||[]).map(function(id){return managerMap[id]||id;});return Array.from(new Set(ids)).sort();}function names(ids){return ids.map(function(id){var row=identity(id);return row?row.displayName:'Unknown manager';}).join(' & ');}var seasons=(state.capture.seasons||[]).slice().sort(function(a,b){return a.year-b.year;});seasons.forEach(function(season){(season.teams||[]).forEach(function(team){var current={teamId:String(team.teamId),year:season.year,teamName:team.teamName,owners:owners(team)};var before=previous[current.teamId];if(before){if(before.teamName!==current.teamName)renames+=1;if(before.owners.join('|')!==current.owners.join('|'))changes.push({teamId:current.teamId,fromYear:before.year,toYear:current.year,fromTeamName:before.teamName,toTeamName:current.teamName,fromOwners:before.owners,fromOwnerNames:names(before.owners),toOwners:current.owners,toOwnerNames:names(current.owners)});}previous[current.teamId]=current;});});state.teamTransitions=changes;state.teamChoices={};state.teamDirty=false;var saved=state.review&&state.review.franchiseReview;var savedById={};(saved&&saved.decisions||[]).forEach(function(row){savedById[row.transitionId]=row.continuity;});changes.forEach(function(row){var value=savedById[teamKey(row)];if(value==='inherit'||value==='new')state.teamChoices[teamKey(row)]=value;});document.getElementById('rename-count').textContent=renames+' team-name change'+(renames===1?' was':'s were')+' kept automatically.';if(!saved&&!changes.length)state.teamDirty=true;}
      function teamAllAnswered(){return state.teamTransitions.every(function(row){return Boolean(state.teamChoices[teamKey(row)]);});}
      function updateTeamSave(){var saved=state.review&&state.review.franchiseReview;saveTeams.disabled=!teamAllAnswered()||!state.teamDirty;saveTeams.textContent=!state.teamDirty&&saved?'Saved':'Save team history';}
      function showTeamComplete(){teamCard.hidden=true;teamComplete.hidden=false;teamDecisions.replaceChildren();if(!state.teamTransitions.length){document.getElementById('team-complete-title').textContent='Team history looks good';document.getElementById('team-complete-copy').textContent='No ownership changes need review.';}else{document.getElementById('team-complete-title').textContent='Team history complete';document.getElementById('team-complete-copy').textContent='You can change any answer before saving.';state.teamTransitions.forEach(function(change){var row=document.createElement('div');row.className='decision-row';var label=document.createElement('span');label.textContent=change.fromTeamName+' → '+change.toTeamName;var answer=document.createElement('b');answer.textContent=state.teamChoices[teamKey(change)]==='inherit'?'History continues':'New franchise';row.append(label,answer);teamDecisions.appendChild(row);});}updateTeamSave();}
      function showTeamTransition(index){state.teamIndex=index;teamComplete.hidden=true;teamCard.hidden=false;var change=state.teamTransitions[index],choice=state.teamChoices[teamKey(change)]||'';document.getElementById('team-progress').textContent='Change '+(index+1)+' of '+state.teamTransitions.length;document.getElementById('team-before-year').textContent=change.fromYear;document.getElementById('team-before-name').textContent=change.fromTeamName;document.getElementById('team-before-owner').textContent=change.fromOwnerNames;document.getElementById('team-after-year').textContent=change.toYear;document.getElementById('team-after-name').textContent=change.toTeamName;document.getElementById('team-after-owner').textContent=change.toOwnerNames;document.getElementById('team-question').textContent='Did '+change.toOwnerNames+' inherit this franchise?';document.getElementById('keep-franchise').setAttribute('aria-pressed',String(choice==='inherit'));document.getElementById('new-franchise').setAttribute('aria-pressed',String(choice==='new'));}
      function chooseTeam(value){var change=state.teamTransitions[state.teamIndex];state.teamChoices[teamKey(change)]=value;state.teamDirty=true;teamResult.textContent='';for(var offset=1;offset<=state.teamTransitions.length;offset+=1){var index=(state.teamIndex+offset)%state.teamTransitions.length;if(!state.teamChoices[teamKey(state.teamTransitions[index])]){showTeamTransition(index);updateTeamSave();return;}}showTeamComplete();}
      function compactManagerReview(){managerReview.classList.add('is-complete');managerStep.textContent='Step 1 complete';managerTitle.textContent='Managers matched';managerCopy.textContent=state.pairs.length+' manager match'+(state.pairs.length===1?'':'es')+' saved.';reviewManagers.hidden=false;}
      function activateTeamReview(){compactManagerReview();buildTeamTransitions();teamReview.hidden=false;setupReady.hidden=!(state.review&&state.review.franchiseReview);if(teamAllAnswered())showTeamComplete();else showTeamTransition(state.teamTransitions.findIndex(function(row){return !state.teamChoices[teamKey(row)];}));teamResult.textContent=state.review&&state.review.franchiseReview?'Team history is saved on this device.':'Review each ownership change, then save.';}
      function render(record){
        state.capture=record.payload;state.review=record.review||null;
        var p=state.capture;detail.classList.add('open');clear.hidden=false;check.hidden=true;document.body.classList.add('has-import');
        leagueTitle.textContent=p.league.name;
        document.title=p.league.name+' League History | LineupBeat';
        headerSeasons.textContent=p.counts.seasons;
        headerGames.textContent=p.counts.matchups;
        headerTeams.textContent=p.counts.teams;
        stats.replaceChildren();stats.hidden=true;
        if(p.incomplete&&p.incomplete.length){var gap=document.createElement('span');gap.textContent='Unavailable seasons: '+p.incomplete.map(function(x){return x.year;}).join(', ');stats.appendChild(gap);stats.hidden=false;}
        state.identities=p.identityReview.identities||[];state.pairs=[];state.choices={};state.dirty=false;
        var seen={};(p.identityReview.suggestions||[]).forEach(function(pair){var key=pairKey(pair);if(!seen[key]&&identity(pair.a)&&identity(pair.b)){seen[key]=true;state.pairs.push(pair);}});
        state.pairs.forEach(function(pair){var saved=savedChoice(pair);if(saved)state.choices[pairKey(pair)]=saved;});
        flow.hidden=false;document.getElementById('other-manager-count').textContent=(state.identities.length-new Set(state.pairs.flatMap(function(pair){return [pair.a,pair.b];})).size)+' other managers already look distinct.';
        if(allAnswered())showComplete();else showPair(state.pairs.findIndex(function(pair){return !state.choices[pairKey(pair)];}));
        result.textContent=state.review?'Manager matches are saved on this device.':'Answer each match, then save.';
        managerReview.classList.remove('is-complete');managerStep.textContent='Step 1 of 2';managerTitle.textContent='Match managers';managerCopy.textContent='ESPN found accounts with similar names. Tell us whether each pair belongs to the same person.';reviewManagers.hidden=true;teamReview.hidden=true;setupReady.hidden=true;
        if(state.review)activateTeamReview();
        say('ESPN import connected');
      }
      document.getElementById('check-extension').addEventListener('click',function(){say('Checking for a local ESPN import…');window.postMessage({type:'LB_LEAGUE_HISTORY_CONNECT_REQUEST',version:1},location.origin);});
      clear.addEventListener('click',function(){window.postMessage({type:'LB_LEAGUE_HISTORY_CLEAR_REQUEST',version:1},location.origin);});
      document.getElementById('same-person').addEventListener('click',function(){choose('same');});
      document.getElementById('different-people').addEventListener('click',function(){choose('different');});
      document.getElementById('change-answers').addEventListener('click',function(){if(state.pairs.length)showPair(0);});
      reviewManagers.addEventListener('click',function(){managerReview.classList.remove('is-complete');managerStep.textContent='Step 1 of 2';managerTitle.textContent='Match managers';managerCopy.textContent='ESPN found accounts with similar names. Tell us whether each pair belongs to the same person.';reviewManagers.hidden=true;teamReview.hidden=true;setupReady.hidden=true;if(allAnswered())showComplete();else showPair(0);});
      document.getElementById('keep-franchise').addEventListener('click',function(){chooseTeam('inherit');});
      document.getElementById('new-franchise').addEventListener('click',function(){chooseTeam('new');});
      document.getElementById('change-team-answers').addEventListener('click',function(){if(state.teamTransitions.length)showTeamTransition(0);});
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
        state.review=review;state.pendingSave='managers';window.postMessage({type:'LB_LEAGUE_HISTORY_SAVE_REVIEW_REQUEST',version:1,review:review},location.origin);result.textContent='Saving…';
      });
      saveTeams.addEventListener('click',function(){if(!state.review||!teamAllAnswered())return;var decisions=state.teamTransitions.map(function(change){return {transitionId:teamKey(change),espnTeamId:change.teamId,fromYear:change.fromYear,toYear:change.toYear,continuity:state.teamChoices[teamKey(change)]};});var review=Object.assign({},state.review,{approvedAt:new Date().toISOString(),franchiseReview:{schemaVersion:'lineupbeat-history-franchise-review-v1',approvedAt:new Date().toISOString(),decisions:decisions}});state.review=review;state.pendingSave='teams';window.postMessage({type:'LB_LEAGUE_HISTORY_SAVE_REVIEW_REQUEST',version:1,review:review},location.origin);teamResult.textContent='Saving…';});
      window.addEventListener('message',function(event){
        if(event.source!==window||event.origin!==location.origin||!event.data||event.data.version!==1)return;
        if(event.data.type==='LB_LEAGUE_HISTORY_EXTENSION_READY'){say(event.data.hasHistory?'ESPN import found. Loading review…':'Connector ready. Import from an ESPN league page.');clear.hidden=!event.data.hasHistory;}
        if(event.data.type==='LB_LEAGUE_HISTORY_CAPTURE')render({payload:event.data.payload,review:event.data.review});
        if(event.data.type==='LB_LEAGUE_HISTORY_REVIEW_COMPLETE'){if(event.data.ok&&state.pendingSave==='managers'){state.dirty=false;updateSave();result.textContent='Manager matches saved.';activateTeamReview();}else if(event.data.ok&&state.pendingSave==='teams'){state.teamDirty=false;updateTeamSave();teamResult.textContent='Team history saved.';setupReady.hidden=false;}else if(!event.data.ok){(state.pendingSave==='teams'?teamResult:result).textContent=state.pendingSave==='teams'?'Team history could not be saved.':'Manager matches could not be saved.';}state.pendingSave='';}
        if(event.data.type==='LB_LEAGUE_HISTORY_CLEAR_COMPLETE'){state.capture=null;state.review=null;state.identities=[];state.pairs=[];state.choices={};state.teamTransitions=[];state.teamChoices={};detail.classList.remove('open');managerReview.classList.remove('is-complete');teamReview.hidden=true;setupReady.hidden=true;clear.hidden=true;check.hidden=false;document.body.classList.remove('has-import');leagueTitle.textContent=leagueTitle.dataset.demo;headerSeasons.textContent=headerSeasons.dataset.demo;headerGames.textContent=headerGames.dataset.demo;headerTeams.textContent=headerTeams.dataset.demo;document.title=leagueTitle.dataset.demo+' League History | LineupBeat';say('Local ESPN import cleared.');}
      });
    }());</script>'''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{title} League History | LineupBeat</title><meta name="robots" content="noindex,nofollow">
    <meta name="description" content="Development prototype for the LineupBeat fantasy football league history tracker.">
    <style>{seo.SHELL_CSS}{seo.TEAMS_CSS}{seo.NAV_CSS}{styles}</style></head><body>
    {seo.site_nav('data', 'nfl')}
    <main class="lh"><div class="lh-status"><i></i>Development prototype · private local import</div>
      <header class="lh-head"><div><span class="lh-kicker">League history</span><h1 id="league-title" data-demo="{title}">{title}</h1></div>
      <div class="lh-meta"><div><b id="header-seasons" data-demo="{summary['counts']['seasons']}">{summary['counts']['seasons']}</b><span>seasons</span></div><div><b id="header-games" data-demo="{summary['counts']['games']}">{summary['counts']['games']}</b><span>matchups</span></div><div><b id="header-teams" data-demo="{summary['counts']['franchises']}">{summary['counts']['franchises']}</b><span>teams</span></div></div></header>
      <section class="import-card" aria-labelledby="import-title"><div class="import-line"><div class="import-copy"><strong id="import-title">ESPN history import</strong><span id="import-status" role="status">Install connector 0.3.0, then import from your ESPN league page.</span></div><div class="import-actions"><button id="check-extension" type="button">Check connector</button><button id="clear-import" class="quiet" type="button" hidden>Remove import</button></div></div>
        <div class="import-summary" id="import-summary"><div class="capture-stats" id="capture-stats" hidden></div><section class="manager-review" id="manager-review"><div class="review-head"><div><span class="review-step" id="manager-step">Step 1 of 2</span><h2 id="manager-title">Match managers</h2><p id="manager-copy">ESPN found accounts with similar names. Tell us whether each pair belongs to the same person.</p></div><button class="quiet" id="review-managers" type="button" hidden>Review</button></div><div class="match-flow" id="match-flow" hidden><section class="match-card" id="match-card"><div class="match-progress"><span id="match-progress">Match 1</span><span>Possible duplicate</span></div><div class="match-people"><article class="person-card"><small>ESPN account A</small><strong id="person-a-name"></strong><div class="person-meta" id="person-a-meta"></div><details class="person-aliases"><summary id="person-a-teams-summary"></summary><p id="person-a-teams"></p></details></article><span class="match-or">and</span><article class="person-card"><small>ESPN account B</small><strong id="person-b-name"></strong><div class="person-meta" id="person-b-meta"></div><details class="person-aliases"><summary id="person-b-teams-summary"></summary><p id="person-b-teams"></p></details></article></div><p class="match-question">Are these the same person?</p><div class="choice-actions"><button id="same-person" type="button" aria-pressed="false">Yes, same person</button><button id="different-people" type="button" aria-pressed="false">No, different people</button></div></section><section class="match-complete" id="match-complete" hidden><h3 id="complete-title">Manager matches complete</h3><p id="complete-copy">You can change any answer before saving.</p><div class="decision-summary" id="decision-summary"></div><div class="import-actions"><button class="quiet" id="change-answers" type="button">Change answers</button></div></section></div><div class="review-footer"><p id="other-manager-count">Other managers already look distinct.</p><div class="import-actions"><button id="save-manager-matches" type="button" disabled>Save manager matches</button></div></div><p class="review-result" id="review-result" role="status"></p></section><section class="franchise-review" id="franchise-review" hidden><div class="review-head"><div><span class="review-step">Step 2 of 2</span><h2>Review team history</h2><p>We only need to check seasons when a team changed owners. Team-name changes stay with the franchise automatically.</p></div></div><div class="match-flow" id="team-flow"><section class="match-card" id="team-card"><div class="match-progress"><span id="team-progress">Change 1</span><span>Ownership change</span></div><div class="team-transition"><article class="team-period"><small id="team-before-year">Before</small><strong id="team-before-name"></strong><span id="team-before-owner"></span></article><span class="team-arrow">→</span><article class="team-period"><small id="team-after-year">After</small><strong id="team-after-name"></strong><span id="team-after-owner"></span></article></div><p class="match-question" id="team-question">Did the new manager inherit this franchise?</p><div class="choice-actions"><button id="keep-franchise" type="button" aria-pressed="false">Yes, keep the history</button><button id="new-franchise" type="button" aria-pressed="false">No, start a new franchise</button></div></section><section class="match-complete" id="team-complete" hidden><h3 id="team-complete-title">Team history complete</h3><p id="team-complete-copy">You can change any answer before saving.</p><div class="decision-summary" id="team-decision-summary"></div><div class="import-actions"><button class="quiet" id="change-team-answers" type="button">Change answers</button></div></section></div><div class="review-footer"><p id="rename-count">Team-name changes are handled automatically.</p><div class="import-actions"><button id="save-team-history" type="button" disabled>Save team history</button></div></div><p class="review-result" id="team-result" role="status"></p></section><section class="setup-ready" id="setup-ready" hidden><small>Setup complete</small><h2>Your league history is ready</h2><p>Manager identities and franchise ownership are saved on this device.</p></section></div></section>
      <nav class="tabs" aria-label="League history sections">
        <button class="tab" data-tab="overview" aria-selected="true">Overview</button><button class="tab" data-tab="trophies" aria-selected="false">Trophy case</button>
        <button class="tab" data-tab="all-time" aria-selected="false">All-time</button><button class="tab" data-tab="managers" aria-selected="false">Managers</button>
        <button class="tab" data-tab="seasons" aria-selected="false">Seasons</button><button class="tab" data-tab="records" aria-selected="false">Records</button>
      </nav>
      <section class="panel active" id="overview"><div class="dashboard">
        <article class="card champ"><div><span class="eyebrow">Defending champion · {trophy['year']}</span></div><strong>{esc(manager[trophy['championFranchiseId']])}</strong><span class="season-mark">01</span></article>
        <article class="card power"><span class="eyebrow">Preseason Elo</span><h2>Power five</h2><ol>{power_rows}</ol></article></div>
        <div class="notice"><strong>Prototype boundary:</strong> Dashboard results below remain fictional. Imported ESPN history appears only in the private review panel above and stays in this browser.</div></section>
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
