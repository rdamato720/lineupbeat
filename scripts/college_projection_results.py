"""Grade the frozen pregame forecast against saved final box-score statistics."""
import hashlib
import html
import json
import unicodedata
from datetime import datetime
from pathlib import Path
from statistics import mean
from college_releases import load_release

ROOT = Path(__file__).resolve().parents[1]
ACTUALS = ROOT/'data/college/2026/week-1/results/actuals.json'
RESULT_PATH = '/college-fantasy-football/week-1/results/'
POSITIONS = ('QB','RB','WR','TE')
WEIGHTS = {'passYds':.04,'passTd':4,'int':-1,'rushYds':.1,'rushTd':6,'rec':1,'recYds':.1,'recTd':6}
e = lambda value: html.escape(str(value), quote=True)


def identity(value):
    return unicodedata.normalize('NFKC',value).replace('’',"'").strip().casefold()


def points(stats):
    return sum(stats.get(k,0)*weight for k,weight in WEIGHTS.items())


def metrics(rows):
    return {'count':len(rows),'mae':mean(abs(r['actual']-r['projected']) for r in rows) if rows else None}


def evaluate():
    release, forecast = load_release('2026/week-1/v1.1')
    actuals=json.loads(ACTUALS.read_text())
    if hashlib.sha256((release/'college_week1_site_projections_2026.json').read_bytes()).hexdigest()!=actuals['forecastSha256']:
        raise ValueError('Forecast does not match the pregame archive')
    published=datetime.fromisoformat(actuals['forecastCommittedAt'].replace('Z','+00:00'))
    schedule=json.loads((release/'provenance/college_week1_schedule_2026.json').read_text())['games']
    games={g['event_id']:g for g in schedule}
    index={}
    for row in actuals['players']:
        key=(row['team'],identity(row['name']),row['eventId'])
        if key in index:raise ValueError('Ambiguous actual player identity')
        if set(row['stats'])-set(WEIGHTS):raise ValueError('Unknown scoring component')
        index[key]=row
    rows=[]
    for p in forecast['players']:
        if published>=datetime.fromisoformat(p['gameDate'].replace('Z','+00:00')):
            raise ValueError('Forecast was not stored before kickoff')
        game=actuals['games'].get(p['team'])
        if game:
            scheduled=games.get(game['id'])
            names={('Pitt' if n=='Pittsburgh' else n) for n in (scheduled or {}).values() if isinstance(n,str)}
            if not game['completed'] or not scheduled or p['team'] not in names or p['opponent'] not in names:
                raise ValueError('Actual game does not match forecast matchup')
        actual=index.get((p['team'],identity(p['name']),game['id'])) if game else None
        rows.append({'id':p['id'],'name':p['name'],'team':p['team'],'pos':p['pos'],
                     'rank':p['rank'],'projected':p['pts'],'actual':round(points(actual['stats']),2) if actual else None,
                     'source':game['source'] if game else None,
                     'status':'graded' if actual else 'No matched offensive stat line'})
    graded=[r for r in rows if r['actual'] is not None]
    focus=[r for r in graded if r['rank']<=30]
    return {'rows':rows,'summary':metrics(focus),'all':metrics(graded),
            'positions':{pos:metrics([r for r in focus if r['pos']==pos]) for pos in POSITIONS},
            'teams':len(actuals['games']),'total':len(rows),'capturedAt':actuals['capturedAt']}


CSS='''
.pr-strip{margin:24px 0;border:1px solid #3b4933;border-top:3px solid #c6f53c;border-radius:12px;background:radial-gradient(ellipse at top right,#c6f53c12,transparent 65%),#101611;overflow:hidden;color:#f3f6ef;font-family:var(--agate)}.pr-top{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:16px 22px;border-bottom:1px solid #ffffff12}.pr-kicker{font:700 12px var(--agate);letter-spacing:.13em;text-transform:uppercase;color:#c6f53c}.pr-top h2{font:700 26px/1.1 var(--agate);margin:5px 0 0;color:#f3f6ef}.pr-link{font:600 15px var(--agate);color:#c6f53c;text-decoration:none;white-space:nowrap}.pr-link:hover{text-decoration:underline}.pr-link:focus-visible{outline:2px solid #c6f53c;outline-offset:5px}.pr-grid{display:grid;grid-template-columns:1.6fr repeat(4,1fr)}.pr-stat{padding:17px 22px;border-right:1px solid #ffffff12;min-width:0}.pr-stat:last-child{border:0}.pr-label{color:#c0c8bb;font:600 12px var(--agate);text-transform:uppercase;letter-spacing:.07em}.pr-number{font:700 36px/1.2 var(--agate);font-variant-numeric:tabular-nums;color:#f3f6ef;margin:3px 0}.pr-number small{font-size:15px;font-weight:400;color:#aeb8a8}.pr-stat:first-child .pr-number{color:#c6f53c;font-size:44px}.pr-sample{font:13px var(--agate);color:#abb6a4}.pr-note{margin:0;padding:12px 22px;background:#05090555;color:#b9c3b3;font:14px/1.5 var(--agate)}.pr-method{border:1px solid #354239;background:#111715;border-radius:10px;padding:22px;margin:26px 0;font:16px/1.6 var(--agate);color:#bec8bc}.pr-method h2{color:#f3f6ef;margin-top:0}.pr-method summary{cursor:pointer;color:#c6f53c;font-weight:600}.pr-method a{color:#c6f53c}.pr-report-tools{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}.pr-report-tools input,.pr-report-tools select{background:#0b110d;color:#edf3e8;border:1px solid #536159;border-radius:6px;padding:12px;font:16px var(--agate);min-height:46px;max-width:100%}.pr-report-tools input{flex:1;min-width:200px}.pr-report-table{overflow:auto;border:1px solid #354239;border-radius:10px}.pr-report-table table{width:100%;border-collapse:collapse;font:16px var(--agate)}.pr-report-table th,.pr-report-table td{padding:12px 16px;text-align:right;border-bottom:1px solid #263126;white-space:nowrap;font-variant-numeric:tabular-nums}.pr-report-table th{color:#c6f53c;background:#141d15}.pr-report-table td:first-child,.pr-report-table th:first-child{text-align:left}.pr-report-table a{color:#f1f5ec}.pr-report-table tr[hidden]{display:none}.pr-team{display:block;font-size:13px;color:#aeb8a8;margin-top:3px}.pr-report-table tbody tr:hover{background:#c6f53c08}@media(max-width:640px){.pr-top{padding:16px;align-items:flex-start}.pr-top h2{font-size:23px}.pr-link{font-size:14px}.pr-grid{grid-template-columns:repeat(4,1fr)}.pr-stat{padding:14px 10px;text-align:center}.pr-stat:first-child{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;gap:10px;text-align:left;padding:12px 16px;border-right:0;border-bottom:1px solid #ffffff12}.pr-stat:first-child .pr-number{font-size:38px}.pr-stat:first-child .pr-sample{max-width:95px;text-align:right}.pr-number{font-size:27px}.pr-number small{display:block;font-size:12px}.pr-note{padding:12px 16px}.pr-sample{font-size:12px}.pr-report-table th,.pr-report-table td{padding:12px}.pr-method{padding:18px}}
'''


def strip(result=None, link=True):
    result=result or evaluate();summary=result['summary']
    cells=f'<div class="pr-stat"><div class="pr-label">Average miss</div><div class="pr-number">{summary["mae"]:.1f}<small> pts</small></div><div class="pr-sample">{summary["count"]} of 120 players graded</div></div>'
    for pos in POSITIONS:
        m=result['positions'][pos]
        value=f'{m["mae"]:.1f}' if m['mae'] is not None else '—'
        cells+=f'<div class="pr-stat"><div class="pr-label">{pos}</div><div class="pr-number">{value}<small> pts</small></div><div class="pr-sample">{m["count"]} of 30 graded</div></div>'
    action=f'<a class="pr-link" href="{RESULT_PATH}">Full results &rarr;</a>' if link else '<a class="pr-link" href="/college-fantasy-football/week-2/">Week 2 &rarr;</a>'
    return f'<section class="pr-strip" aria-label="Previous week projection results"><div class="pr-top"><div><div class="pr-kicker">The weekly scorecard</div><h2>Week 1. How we did.</h2></div>{action}</div><div class="pr-grid">{cells}</div><p class="pr-note">Our pregame top 30 at each position · Average distance from actual fantasy points; lower is better. Players without a matched offensive stat line are excluded.</p></section>'


def build_report(css, header, footer):
    import seo
    r=evaluate();graded=[x for x in r['rows'] if x['actual'] is not None]
    ordered=sorted(graded,key=lambda x:(POSITIONS.index(x['pos']),x['rank']))
    body=''
    for p in ordered:
        miss=abs(p['actual']-p['projected'])
        body+=f'<tr data-name="{e((p["name"]+" "+p["team"]).lower())}" data-pos="{p["pos"]}" data-top="{str(p["rank"]<=30).lower()}"><td><a href="{e(p["source"])}" target="_blank" rel="noopener">{e(p["name"])}</a><span class="pr-team">{e(p["team"])} · {p["pos"]}{p["rank"]}</span></td><td>{p["projected"]:.1f}</td><td>{p["actual"]:.1f}</td><td>{miss:.1f}</td></tr>'
    missing=[p for p in r['rows'] if p['rank']<=30 and p['actual'] is None]
    missing_text='; '.join(f'{e(p["name"])} ({e(p["team"])} {p["pos"]})' for p in missing)
    title='College Fantasy Week 1 Projection Results'
    description='See how LineupBeat’s pregame college Week 1 projections compared with final player statistics, including average point errors and results by position.'
    doc=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} | LineupBeat</title><meta name="description" content="{e(description)}"><link rel="canonical" href="https://lineupbeat.com{RESULT_PATH}">{seo.social_meta(title,description,'https://lineupbeat.com'+RESULT_PATH)}<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600&amp;family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&amp;display=swap" rel="stylesheet"><style>{css}{CSS}.pr-wrap{{width:min(calc(100% - 32px),1180px);margin:40px auto 64px}}.pr-wrap h1{{font:700 clamp(36px,6vw,60px)/1.05 var(--agate);color:#f3f6ef;margin:12px 0}}.pr-intro{{font:18px/1.5 var(--agate);color:#bdc7b8;max-width:700px}}</style></head><body>{header}<main class="pr-wrap"><a class="pr-link" href="/college-fantasy-football/week-2/">&larr; This week’s projections</a><h1>Every week. On the record.</h1><p class="pr-intro">Our Week 1 projections, measured against the final box scores. The close calls and the misses, in one place.</p>{strip(r,False)}<section class="pr-method"><h2>What these numbers mean</h2><p>The scorecard grades the top 30 players at each position in our saved pregame rankings. {r['summary']['count']} of those 120 players had a matching passing, rushing or receiving stat line. The remaining {len(missing)} are ungraded, not counted as zero-point games.</p><p>Across the full forecast, {r['all']['count']:,} of {r['total']:,} players were matched, with an average miss of {r['all']['mae']:.1f} points. Final box scores cover all {r['teams']} projected teams. Players without a matching offensive stat line are excluded; that can include inactive players and players who appeared without recording those stats.</p><details><summary>Scoring, coverage and ungraded players</summary><p>Average miss is the mean absolute difference between projected and actual points. We use the same components as the weekly board: 0.04 per passing yard, 4 per passing touchdown, −1 per interception, 0.1 per rushing or receiving yard, 6 per rushing or receiving touchdown, and 1 per reception. This comparison excludes fumbles, return scores, two-point conversions and bonuses.</p><p>The forecast was saved September 3 before the first kickoff. Player positions and ranks stay as originally published. Matches use player name and team within the scheduled game; we do not guess absent players’ results. Saved ESPN final box scores were collected September 9. Select a player below to open their source box score.</p><p><strong>Ungraded top-30 players:</strong> {missing_text}.</p><p><a href="/college-fantasy-football/week-1/">View the original forecast</a></p></details></section><h2>Projection vs. result</h2><div class="pr-report-tools"><input type="search" id="result-search" aria-label="Search results by player or team" placeholder="Search player or team"><select id="result-position" aria-label="Filter results by position"><option value="">All positions</option>{''.join(f'<option>{p}</option>' for p in POSITIONS)}</select><select id="result-scope" aria-label="Result sample"><option value="top">Pregame top 30 per position</option><option value="all">All matched players</option></select></div><p id="result-count" class="pr-intro" role="status"></p><div class="pr-report-table" tabindex="0" aria-label="Player projection results, scroll horizontally"><table><thead><tr><th scope="col">Player / pregame rank</th><th scope="col">Projected</th><th scope="col">Actual</th><th scope="col">Miss</th></tr></thead><tbody>{body}</tbody></table></div><p id="result-empty" class="pr-intro" hidden>No players match these filters.</p></main>{footer}<script>(()=>{{const q=document.getElementById('result-search'),p=document.getElementById('result-position'),s=document.getElementById('result-scope'),rows=[...document.querySelectorAll('tbody tr')];function draw(){{let n=0;for(const r of rows){{r.hidden=!(r.dataset.name.includes(q.value.trim().toLowerCase())&&(!p.value||r.dataset.pos===p.value)&&(s.value==='all'||r.dataset.top==='true'));if(!r.hidden)n++;}}document.getElementById('result-count').textContent=n+' players';document.getElementById('result-empty').hidden=n>0;}}q.addEventListener('input',draw);p.addEventListener('change',draw);s.addEventListener('change',draw);draw();}})()</script></body></html>'''
    target=ROOT/'site'/RESULT_PATH.strip('/')/'index.html';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(doc)
    return target
