"""Separate weekly projections and rankings, with shareable scoring/lineup filters."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import seo

JS = r'''
(()=>{
const data=JSON.parse(document.getElementById('week1-data').textContent),isRanks=data.kind==='rankings';
const form=document.getElementById('wb-format'),league=document.getElementById('wb-league'),pos=document.getElementById('wb-position'),team=document.getElementById('wb-team'),search=document.getElementById('wb-search');
const body=document.getElementById('wb-rows'),rows=new Map(Array.from(body.rows,r=>[r.dataset.id,r]));
const ranks=new Map((data.rankings?.players||[]).map(r=>[r.id,r]));
const labels={half_ppr:'Half-PPR',ppr:'PPR',non_ppr:'Non-PPR'},leagues={one_qb:'1QB',superflex:'Superflex'};
const params=new URLSearchParams(location.search),aliases={hppr:'half_ppr','half-ppr':'half_ppr','non-ppr':'non_ppr',standard:'non_ppr'};
const requested=aliases[params.get('format')]||params.get('format');
if(labels[requested])form.value=requested;
if(league&&leagues[params.get('league')])league.value=params.get('league');
if(['QB','RB','WR','TE'].includes(params.get('position')))pos.value=params.get('position');
function render(updateUrl=false){
 const fmt=form.value,lineup=league?.value||'one_qb',q=search.value.trim().toLowerCase();let count=0;
 const ranking=p=>ranks.get(p.id)?.formats[fmt]?.leagues[lineup];
 const sorted=[...data.players].filter(p=>!isRanks||ranks.get(p.id)?.eligible).sort((a,b)=>isRanks?ranking(a).overall_rank-ranking(b).overall_rank:a.formats[fmt].overall_rank-b.formats[fmt].overall_rank);
 for(const p of sorted){const row=rows.get(p.id),f=p.formats[fmt],rank=isRanks?ranking(p):null;
  row.hidden=!!((pos.value&&p.position!==pos.value)||(team.value&&p.team!==team.value)||(q&&!p.name.toLowerCase().includes(q)));
  if(!row.hidden)count++;
  if(isRanks){row.querySelector('[data-col=rank]').textContent=pos.value?rank.position_rank:rank.overall_rank;row.querySelector('[data-col=position-rank]').textContent=p.position+rank.position_rank;}
  row.querySelector('[data-col=points]').textContent=f.projected_points.toFixed(1);body.appendChild(row);
 }
 const label=labels[fmt]+(isRanks?' · '+leagues[lineup]:'');
 document.getElementById('wb-count').textContent=count+(count===1?' player':' players')+' · '+label;
 document.getElementById('wb-caption').textContent=label+' · '+(pos.value||'All positions');
 document.getElementById('wb-empty').hidden=count!==0;
 if(league)document.getElementById('wb-lineup-note').textContent='Overall ranks use a 12-team league with 1 QB, 2 RB, 2 WR, 1 TE and 1 FLEX'+(lineup==='superflex'?', plus 1 Superflex (QB/RB/WR/TE).':'.')+' Position ranks compare players within the same position.';
 if(updateUrl){const url=new URL(location.href);url.searchParams.set('format',fmt);if(league)url.searchParams.set('league',lineup);if(pos.value)url.searchParams.set('position',pos.value);else url.searchParams.delete('position');history.replaceState(null,'',url);}
}
[form,league,pos,team].filter(Boolean).forEach(el=>el.addEventListener('change',()=>render(true)));search.addEventListener('input',()=>render());
render();
})();
'''


def render(payload, kind, site, scorecard=None):
    from build_nfl_week1_boards import CSS, FORMATS, STATS, e
    is_ranks = kind == 'rankings'
    title = f'2026 NFL Week 2 {kind.title()}'
    path = f'/nfl/week-2/{kind}/'
    rank_data = payload['rankings']
    ranks = {p['id']:p for p in rank_data['players']}
    def order(p):
        return ranks[p['id']]['formats']['half_ppr']['leagues']['one_qb']['overall_rank'] if is_ranks else p['formats']['half_ppr']['overall_rank']
    players = sorted((p for p in payload['players'] if not is_ranks or ranks[p['id']]['eligible']),key=order)
    rows = []
    projection_stats = [('attempts','Pass Att'),('completions','Comp'),*STATS[:6],('targets','Targets'),*STATS[6:]]
    for p in players:
        url = '/nfl/'+p['slug']+'/'
        name = f'<a href="{e(url)}">{e(p["name"])}</a>' if (site/url.lstrip('/')/'index.html').is_file() else e(p['name'])
        photo = p.get('photo') or '/assets/player-placeholder.svg'
        portrait = f'<img class="wb-photo" src="{e(photo)}" alt="" width="48" height="54" loading="lazy" decoding="async" onerror="this.onerror=null;this.src=\'/assets/player-placeholder.svg\'">'
        logo = f'<img class="wb-logo" src="{e(p["team_logo"])}" alt="" width="18" height="18" loading="lazy" onerror="this.hidden=true">'
        identity = f'<div class="wb-identity">{portrait}<div><span class="wb-player-name">{name}</span><small>{logo}{e(p["team"])} · {e(p["position"])}</small></div></div>'
        f = p['formats']['half_ppr']
        r = ranks[p['id']]['formats']['half_ppr']['leagues']['one_qb'] if is_ranks else None
        first = f'<td data-col="rank">{r["overall_rank"]}</td>' if is_ranks else ''
        position = f'<td data-col="position-rank">{p["position"]}{r["position_rank"]}</td>' if is_ranks else f'<td>{p["position"]}</td>'
        stats = ''.join(f'<td>{p["stat_projection"][key]:.1f}</td>' for key,_ in projection_stats) if not is_ranks else ''
        matchup = ('vs ' if p['home'] else '@ ')+p['opponent']
        rows.append(f'<tr data-id="{e(p["id"])}">{first}<td class="player">{identity}</td>{position}<td>{e(matchup)}</td><td class="points" data-col="points">{f["projected_points"]:.1f}</td><td class="status">{e(p["availability"]["status"])}</td>{stats}</tr>')
    options = ''.join(f'<option value="{key}">{label}</option>' for key,label in FORMATS.items())
    teams = ''.join(f'<option>{e(t)}</option>' for t in sorted({p['team'] for p in players}))
    lineup = '<label>League<select id="wb-league"><option value="one_qb">1QB</option><option value="superflex">Superflex</option></select></label>' if is_ranks else ''
    links = ''.join(f'<a href="/nfl/week-2/{key}/"'+(' aria-current="page"' if key==kind else '')+f'>{key.title()}</a>' for key in ('rankings','projections'))
    links += '<a href="/nfl/week-2/rankings/?league=superflex">Superflex rankings</a><a href="/nfl/week-1/results/">Week 1 results</a>'
    intro = ('Set your weekly board. Independent rankings for PPR, Half-PPR and Non-PPR, with 1QB and Superflex league options.' if is_ranks else 'Expected fantasy points and the stats behind them. Compare passing, rushing and receiving projections in your scoring format.')
    note = ('Rankings combine expected production with recent scoring history, with positional value shaping the overall board. Players can rank differently from projected-point order. Superflex changes the ranking, never the projected points.' if is_ranks else 'Projections estimate average scoring outcomes and are sorted by projected points. For player priority by position and league type, open the separate rankings board.')
    note += ' Confirmed unavailable players project for zero and are unranked. Questionable and Doubtful tags do not reduce projections.'
    current = datetime.fromisoformat(payload['updated_at'].replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York'))
    stamp = current.strftime('%B %d, %Y · %I:%M %p ET')
    header = ('<th scope="col">Rank</th>' if is_ranks else '')+'<th scope="col" class="player">Player</th>'
    header += '<th scope="col">'+('Pos rank' if is_ranks else 'Position')+'</th><th scope="col">Matchup</th><th scope="col">Proj. pts</th><th scope="col">Status</th>'
    if not is_ranks:
        header += ''.join(f'<th scope="col">{label}</th>' for _,label in projection_stats)
    data = {'kind':kind,'season':2026,'week':2,'updated_at':payload['updated_at'],
            'players':[{k:p[k] for k in ('id','name','team','position','formats')} for p in payload['players']]}
    if is_ranks:
        data['rankings'] = rank_data
    encoded = json.dumps(data,separators=(',',':')).replace('<','\\u003c')
    league_note = '<p class="wb-note" id="wb-lineup-note">Overall ranks use a 12-team league with 1 QB, 2 RB, 2 WR, 1 TE and 1 FLEX. Position ranks compare players within the same position.</p>' if is_ranks else ''
    extra_css = '.wb-controls.rank-controls{grid-template-columns:repeat(4,minmax(100px,1fr)) minmax(160px,1.6fr)}@media(max-width:760px){.wb-controls.rank-controls{grid-template-columns:1fr 1fr}.rank-controls label:last-child{grid-column:1/-1}}'
    return seo.check_page(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} | LineupBeat</title><meta name="description" content="{e(intro)}"><link rel="canonical" href="https://lineupbeat.com{path}"><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600&amp;family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&amp;display=swap" rel="stylesheet"><style>{CSS}{extra_css}</style></head><body>
{seo.site_nav('week2_'+kind,'nfl')}<main class="wb"><header class="wb-hero"><p class="wb-meta wb-eyebrow">2026 FANTASY FOOTBALL · WEEK 2</p><h1>NFL Week 2 <span>{kind.title()}</span></h1><p class="wb-intro">{intro}</p><p class="wb-meta">Updated <time datetime="{e(payload['updated_at'])}">{stamp}</time></p></header><nav class="wb-links" aria-label="Week 2 boards">{links}</nav>
{scorecard or ''}<div class="wb-controls{' rank-controls' if is_ranks else ''}"><label>Scoring<select id="wb-format">{options}</select></label>{lineup}<label>Position<select id="wb-position"><option value="">All positions</option><option>QB</option><option>RB</option><option>WR</option><option>TE</option></select></label><label>Team<select id="wb-team"><option value="">All teams</option>{teams}</select></label><label>Player<input id="wb-search" type="search" placeholder="Search players" autocomplete="off"></label></div>
<p id="wb-count" class="wb-meta" role="status">{len(players)} players · Half-PPR{' · 1QB' if is_ranks else ''}</p>{league_note}
<div class="wb-table" tabindex="0" role="region" aria-label="Week 2 {kind} table"><table><caption id="wb-caption">Half-PPR{' · 1QB' if is_ranks else ''} · All positions</caption><thead><tr>{header}</tr></thead><tbody id="wb-rows">{''.join(rows)}</tbody></table><p id="wb-empty" class="wb-empty" hidden>No players match these filters.</p></div><p class="wb-note">{note}</p>
<p class="wb-note">QB, RB, WR and TE. Statistics and usage: <a href="https://github.com/nflverse/nflverse-data">nflverse · CC BY 4.0</a>. Status: <a href="https://www.espn.com/nfl/injuries">ESPN</a>. <a href="/nfl/week-2/methodology/">Model and accuracy details</a>.</p>
<nav class="wb-links wb-footer" aria-label="Season boards"><a href="/nfl/rankings/">Season rankings</a><a href="/nfl/projections/">Season projections</a></nav></main>{seo.site_footer()}<script id="week1-data" type="application/json">{encoded}</script><script>{JS}</script></body></html>''',path)


def build_methodology(payload, site):
    from pathlib import Path
    from build_nfl_week1_boards import CSS, FORMATS, e
    root=Path(__file__).resolve().parents[1]
    report=json.loads((root/'data/nfl_weekly/model-v2/validation.json').read_text())
    model=json.loads((root/'data/nfl_weekly/model-v2/model.json').read_text())
    results=report['holdout']
    rows=[]
    for fmt,label in FORMATS.items():
        result=results['formats'][fmt]['overall']
        rows.append(f'<tr><td class="player">{label}</td><td>{result["model"]["mae"]:.2f}</td><td>{result["baseline"]["mae"]:.2f}</td><td>{result["model"]["rmse"]:.2f}</td><td>{result["baseline"]["rmse"]:.2f}</td></tr>')
    history_weight=model['ranking_history_weight']
    typical_weight=model['ranking_typical_weight']
    rank_rows=[]
    for fmt,label in FORMATS.items():
        result=report['ranking_holdout'][fmt]
        rank_rows.append(f'<tr><td class="player">{label}</td><td>{result["ranking_order_accuracy"]:.2%}</td><td>{result["projection_order_accuracy"]:.2%}</td><td>{result["unequal_actual_pairs"]:,}</td></tr>')
    doc=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NFL Week 2 Model and Accuracy | LineupBeat</title><meta name="description" content="How LineupBeat builds independent weekly projections, 1QB and Superflex rankings, and checks accuracy against historical outcomes."><link rel="canonical" href="https://lineupbeat.com/nfl/week-2/methodology/"><link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600&amp;display=swap" rel="stylesheet"><style>{CSS}.wb-method p,.wb-method li{{font-size:19px;line-height:1.55;color:#c1cabf;max-width:850px}}.wb-method h2{{font-size:32px;margin-top:36px}}.wb-method h3{{font-size:24px}}</style></head><body>{seo.site_nav(None,'nfl')}<main class="wb wb-method"><header class="wb-hero"><p class="wb-eyebrow">2026 NFL · WEEK 2</p><h1>Behind <span>the numbers.</span></h1><p class="wb-intro">Projected production. Independent player priority. Measured against real results.</p></header><nav class="wb-links"><a href="/nfl/week-2/projections/">Projections</a><a href="/nfl/week-2/rankings/">Rankings</a><a href="/nfl/week-2/rankings/?league=superflex">Superflex</a><a href="/nfl/week-1/results/">Week 1 results</a></nav>
<h2>Start with current football data</h2><p>Week 2 uses current roster identities and depth charts, Week 1 player and team statistics, offensive snaps, and play-by-play opportunities near the goal line and in the red zone. Historical usage and efficiency provide a starting point when current-season evidence is limited. A player no longer needs a preseason workbook row to receive a forecast.</p><p>A fitted target-share model uses recorded offensive participation, targets, carries, depth position and competition from teammates. Its estimate is blended equally with the prior usage allocation. Players without a recorded appearance in their team’s previous game retain the prior role estimate; missing data are not a zero workload. Team attempts, carries and targets are allocated across available players. Receiving yards, receptions and touchdowns reconcile with the projected passing offense. New players without NFL history use broad historical workload and position-efficiency priors.</p><p>Questionable and Doubtful are informational tags. Only confirmed Out, Injured Reserve and suspended statuses set points to zero. The update runs once daily; forecasts for a team are locked when its game starts.</p>
<h2>Projections and rankings answer different questions</h2><p>Projections estimate average fantasy production from a stat line. Rankings express player priority. The ranking model is trained for player ordering separately from the projection model’s point-error objective.</p><p>The selected ranking score gives {(1-history_weight)*typical_weight:.0%} weight to a modeled typical scoring outcome, {(1-history_weight)*(1-typical_weight):.0%} to expected points and {history_weight:.0%} to recent recorded scoring history. Players without prior appearances use the current forecast alone. Typical outcomes account for touchdown uncertainty; they are not guaranteed floors.</p><p>Within each position, the ranking score sets the order. Overall rankings compare that score with the next player outside a reference 12-team lineup: 1 QB, 2 RB, 2 WR, 1 TE and 1 FLEX. Superflex adds one slot eligible for QB, RB, WR or TE, changing positional demand. It does not change anyone’s projected stats or points.</p>
<h2>Historical projection check</h2><p>The target-share model was fitted on 2023 and 2024 games, with later 2024 games used to choose its settings. The frozen revision was replayed on 2025 Weeks 2–18. Because those results were examined during earlier revisions, this is a regression check, not a new independent test. Improvements over the previous release are small. Each forecast uses earlier games, prior-week rosters and pregame depth information. The fixed sample takes the top 30 players at each position by their prior eight-appearance scoring average. {results['matched']:,} of {results['population']:,} player-games matched final statistics; {results['ungraded']} remain ungraded.</p><div class="wb-table" tabindex="0" role="region" aria-label="Historical projection error"><table><caption>2025 historical evaluation · points of error · lower is better</caption><thead><tr><th class="player">Scoring</th><th>Model avg. miss</th><th>Baseline avg. miss</th><th>Model RMSE</th><th>Baseline RMSE</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><p>The baseline is the average over up to eight earlier recorded appearances. RMSE gives larger misses more weight. The production component formula was replayed; these are not scores from the old preseason workbook model.</p>
<h2>Historical ranking check</h2><p>Ranking weights were selected using 2024 player ordering, then evaluated on 2025. The comparison asks which of two players at the same position scored more in the same week. Actual ties are omitted and predicted ties receive half credit. These small gains do not establish a large or lasting advantage.</p><div class="wb-table" tabindex="0" role="region" aria-label="Historical ranking accuracy"><table><caption>2025 player ordering · higher is better</caption><thead><tr><th class="player">Scoring</th><th>Ranking order</th><th>Projection order</th><th>Pairs</th></tr></thead><tbody>{''.join(rank_rows)}</tbody></table></div>
<h2>What this check can establish</h2><p>The historical test does not reconstruct every pregame injury report. Missing stat lines remain ungraded, and historical weekly roster/depth snapshots have timing limitations. The Week 1 results page continues to grade the original committed pregame forecasts; it has not been replaced with a revised retrospective forecast.</p><p>No expert-site projections or rankings are model inputs. A higher projection from another provider is a reason to inspect assumptions, not proof of greater accuracy. No current betting, weather or live route-participation adjustment is included. Recorded offensive snaps inform the target-share estimate. Snaps are not routes run. The carry and team-volume formulas retain their previously tested settings.</p><h2>Scoring and sources</h2><p>{e(payload['methodology']['scoring'])}</p><p>Statistics, roster, depth and play-by-play: <a href="https://github.com/nflverse/nflverse-data">nflverse · CC BY 4.0</a>. <a href="https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html">Data availability and timing</a>. Status facts: <a href="https://www.espn.com/nfl/injuries">ESPN injury reports</a>. <a href="https://github.com/rdamato720/lineupbeat/blob/main/data/nfl_weekly/model-v2/validation.json">Reproducible validation record</a>.</p></main>{seo.site_footer()}</body></html>'''
    out=site/'nfl/week-2/methodology/index.html'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(seo.check_page(doc,'/nfl/week-2/methodology/'))
