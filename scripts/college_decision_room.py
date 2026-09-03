#!/usr/bin/env python3
"""Development-only College Decision Room shell and lazy client renderer."""

COLLEGE_PAYLOAD_URL = "/data/decision-room-college.json"

SHELL = '''
<main id="college-decision-room" class="dr-shell cdr" hidden aria-busy="true">
  <section class="dr-hero">
    <div class="dr-kicker">College Fantasy Football</div>
    <div class="dr-mode">Validated weekly mode · Yahoo scoring</div>
    <h1 id="cdr-title">College Decision Room</h1>
    <p class="dr-lede">Compare projection, expected opportunity, opponent, and delayed sportsbook evidence—then see what could change the call.</p>
    <p id="cdr-meta" class="dr-week-note">Loading the validated College Week 1 evidence…</p>
    <p class="dr-week-note">Validated scope: 2,205 players across 64 teams · Yahoo scoring only · 30-second-delayed game markets for every modeled team · exact player-component markets for 112 players. Player-market coverage varies; a market input is not an outcome or guarantee. Validated College ADP is not available. Current player availability is also unavailable.</p>
    <section class="dr-compare" aria-labelledby="cdr-compare-title">
      <div class="dr-compare-head"><div><small>College decision</small><h2 id="cdr-compare-title">Player vs. player</h2></div><strong>Yahoo scoring</strong></div>
      <div class="cdr-filters"><label>Position<select id="cdr-position"><option value="">All positions</option><option>QB</option><option>RB</option><option>WR</option><option>TE</option></select></label><label>Team<select id="cdr-team"><option value="">All teams</option></select></label></div>
      <div class="dr-selectors"><label>Player one<span id="cdr-a-team" class="cdr-selector-team" aria-live="polite"></span><input id="cdr-a" type="search" list="cdr-a-list" autocomplete="off"><datalist id="cdr-a-list"></datalist></label><b>VS</b><label>Player two<span id="cdr-b-team" class="cdr-selector-team" aria-live="polite"></span><input id="cdr-b" type="search" list="cdr-b-list" autocomplete="off"><datalist id="cdr-b-list"></datalist></label></div>
      <label class="dr-cross"><input type="checkbox" id="cdr-cross-position"> Compare across positions</label>
      <div id="cdr-result" aria-live="polite"></div>
    </section>
  </section>
  <section class="dr-section"><div class="dr-section-head"><div><small>Decision pressure</small><h2>Closest Calls</h2></div><p>Same-position Week 1 projections with the smallest displayed point gaps.</p></div><div id="cdr-closest" class="dr-card-grid"></div></section>
  <section class="dr-section"><div class="dr-section-head"><div><small>Separation</small><h2>Strongest Projection Edges</h2></div><p>Largest adjacent same-position edges in the validated Week 1 rankings.</p></div><div id="cdr-edges" class="dr-card-grid"></div></section>
  <section class="dr-section"><div class="dr-section-head"><div><small>Format sensitivity</small><h2>Scoring-Format Movers</h2></div></div><article class="dr-empty"><h3>One validated scoring format</h3><p>The college release currently supports Yahoo scoring only, so scoring-format movement or reversals cannot be calculated honestly.</p></article></section>
  <section class="dr-future-grid"><article class="dr-empty"><small>Coming next</small><h2>Decision Inbox</h2><p>Connect your college league to see the decisions that matter on your roster.</p><button disabled>College league sync not yet available</button></article><article class="dr-empty"><small>Accountability layer</small><h2>Decision Record</h2><p>No college decisions have been recorded. Future snapshots will preserve the recommendation, inputs, timestamp, and outcome.</p><div class="dr-empty-line">No saved college decisions yet</div></article></section>
  <nav class="dr-tools" aria-label="College fantasy tools"><span>College tools</span><a href="/college-fantasy-football/week-1/">Week 1 rankings</a><a href="/college-fantasy-football/projections/">Season projections</a><a href="/decision-room/college/" aria-current="page">College Decision Room</a></nav>
</main>'''

JS = r'''(()=>{
const root=document.getElementById('college-decision-room'),Q=new URLSearchParams(location.search);
if(!location.pathname.includes('/decision-room/college')&&document.body.dataset.defaultSport!=='college')return;
root.hidden=false;
fetch('/data/decision-room-college.json',{credentials:'same-origin'}).then(r=>{if(!r.ok)throw Error();return r.json()}).then(D=>{
if(D.sport!=='college'||D.mode!=='weekly'||D.market?.state!=='available_delayed_market_context')throw Error();
root.setAttribute('aria-busy','false');
const safe=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const P=Object.fromEntries(D.players.map(p=>[p.id,p])),label=p=>`${p.name} · ${p.team} ${p.position}`,byLabel=Object.fromEntries(D.players.map(p=>[label(p),p]));
const A=document.getElementById('cdr-a'),B=document.getElementById('cdr-b'),AL=document.getElementById('cdr-a-list'),BL=document.getElementById('cdr-b-list'),POS=document.getElementById('cdr-position'),TEAM=document.getElementById('cdr-team'),X=document.getElementById('cdr-cross-position'),O=document.getElementById('cdr-result');
const shown=p=>+Number(p.formats.yahoo.projected_points).toFixed(1),num=v=>Number(v).toFixed(1),pct=(g,r)=>+(g/Math.max(Math.abs(r),.1)*100).toFixed(1),market=p=>D.market_context_by_team[p.team_id];
function cls(g,r){let q=pct(g,r);if(g<=.5||q<=3)return'Toss-Up';if(q<=7)return'Lean';if(q<=15)return'Edge';return'Strong Edge'}
function initials(p){return p.team.split(/\s+/).map(x=>x[0]).join('').slice(0,3)}
function logo(p,c='cdr-mini-logo'){return `<span class="cdr-logo-wrap"><img class="${c}" src="${safe(p.team_logo)}" alt="${safe(p.team)}" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><b hidden>${initials(p)}</b></span>`}
function crest(p){return `<div class="cdr-crest" style="--team-accent:${safe(p.team_color)}">${logo(p,'cdr-crest-logo')}</div>`}
function selector(id,p){document.getElementById(id).innerHTML=p?`${logo(p)}<span>${safe(p.team)} · ${safe(p.position)}</span>`:''}
function opportunityValue(p){let o=p.expected_opportunity||{};return p.position==='QB'?Number(o.pass_attempts||0):Number(o.carries||0)+Number(o.receptions||0)}
function opportunity(p){let o=p.expected_opportunity||{};if(p.position==='QB')return`${num(o.pass_attempts||0)} pass attempts`;return`${num(o.carries||0)} carries · ${num(o.receptions||0)} receptions`}
function spread(v){let n=Number(v);return`${n>0?'+':''}${n.toFixed(1)}`}
function marketLine(p){let m=market(p);return`${num(m.team_implied_total)} implied points · ${spread(m.team_spread)} spread · ${num(m.game_total)} game total`}
function playerMarket(p){let m=p.player_market||{},c=m.components||[];if(c.length)return`Anchored: ${c.map(x=>x.replaceAll('_',' ')).join(', ')}`;if(m.role_evidence)return'Role evidence only';return'No matched player market'}
function cards(a,b){return `<div class="dr-player-grid">${[a,b].map(p=>`<article><div class="dr-person" style="--team-accent:${safe(p.team_color)}">${crest(p)}<div><small>${safe(p.team)} · ${safe(p.position)}</small><h3>${safe(p.name)}</h3></div></div><dl><div><dt>Projected points</dt><dd>${num(shown(p))}</dd></div><div><dt>Overall rank</dt><dd>#${p.formats.yahoo.overall_rank}</dd></div><div><dt>Modeled opportunity</dt><dd>${opportunity(p)}</dd></div><div><dt>Player market evidence</dt><dd>${safe(playerMarket(p))}</dd></div><div><dt>Sportsbook environment</dt><dd>${marketLine(p)}</dd></div></dl></article>`).join('')}</div>`}
function spotWord(n){return Number(n)===1?'spot':'spots'}
function terminalName(p){let name=safe(p.name);return/[.!?]$/.test(String(p.name))?name:`${name}.`}
function caseFor(p,o){let facts=[],pm=market(p),om=market(o);if(shown(p)>shown(o))facts.push(`Projects ${num(shown(p)-shown(o))} Week 1 points higher.`);if(opportunityValue(p)>opportunityValue(o))facts.push(`Higher modeled opportunity: ${opportunity(p)}.`);if((p.player_market?.components||[]).length)facts.push(`Exact player market supports ${playerMarket(p).replace('Anchored: ','')}.`);if(pm.team_implied_total>om.team_implied_total)facts.push(`Sportsbook environment is ${num(pm.team_implied_total-om.team_implied_total)} implied team points higher.`);if(p.formats.yahoo.overall_rank<o.formats.yahoo.overall_rank){let g=o.formats.yahoo.overall_rank-p.formats.yahoo.overall_rank;facts.push(`Ranks ${g} overall ${spotWord(g)} higher.`)}if(pm.blowout_risk)facts.push(`${spread(pm.team_spread)} spread creates possible late-game workload risk.`);return(facts.length?facts:['No additional validated edge is available.']).slice(0,4).map(x=>`<li>${x}</li>`).join('')}
function draw(){
let a=byLabel[A.value],b=byLabel[B.value];selector('cdr-a-team',a);selector('cdr-b-team',b);
if(!a||!b||a.id===b.id){O.innerHTML='<p class="dr-error">Choose two different college players.</p>';return}
let ap=shown(a),bp=shown(b),gap=Math.abs(ap-bp),gp=pct(gap,Math.max(ap,bp)),lead=ap===bp?null:(ap>bp?a:b),c=cls(gap,Math.max(ap,bp));
let rank=a.formats.yahoo.overall_rank<b.formats.yahoo.overall_rank?a:b,workload=opportunityValue(a)===opportunityValue(b)?null:(opportunityValue(a)>opportunityValue(b)?a:b),am=market(a),bm=market(b),marketLead=am.team_implied_total===bm.team_implied_total?null:(am.team_implied_total>bm.team_implied_total?a:b);
let playerEvidence=[a,b].filter(p=>(p.player_market?.components||[]).length),aligned=lead&&workload?.id===lead.id&&marketLead?.id===lead.id,state=lead?(aligned?'Aligned':'Mixed'):'Toss-Up',w=c==='Toss-Up'?null:lead,r=w?(w.id===a.id?b:a):null;
let call=!w?'No clear edge':aligned?`${c==='Lean'?'Evidence leans':'Evidence favors'} ${safe(w.name)}`:`Mixed case — projection favors ${safe(w.name)}`;
let projection=w?`${safe(w.name)} projects ${num(gap)} points (${gp.toFixed(1)}%) ahead of ${terminalName(r)}`:`The ${num(gap)}-point gap (${gp.toFixed(1)}%) is inside the weekly no-call band.`;
let environment=`${safe(a.team)}: ${marketLine(a)}. ${safe(b.team)}: ${marketLine(b)}.`;
let marketAgreement=!marketLead?'Even':(lead&&marketLead.id===lead.id?'Supports projection':'Opposes projection');
let workloadAgreement=!workload?'Even':(lead&&workload.id===lead.id?'Supports projection':'Opposes projection');
let need=c==='Toss-Up'?+(Math.max(.5,Math.max(ap,bp)*.03)+.1).toFixed(1):+(gap+.1).toFixed(1);
let blowout=[a,b].filter(p=>market(p).blowout_risk);
O.innerHTML=`<section class="dr-verdict"><div><small>Lineup Beat call · ${state} · Yahoo</small><h2>${call}</h2><p><strong>${projection}</strong> Modeled opportunity ${workloadAgreement.toLowerCase()}; delayed sportsbook environment ${marketAgreement.toLowerCase()}. ${playerEvidence.length?`${playerEvidence.length} player${playerEvidence.length===1?' has':'s have'} exact component-market evidence.`:'Neither player has a matched component market.'}</p></div><div class="dr-adv"><b>${gap?'+':''}${num(gap)}</b><span>Week 1 point difference</span></div></section>${cards(a,b)}
<section class="dr-evidence"><div class="dr-evidence-title"><small>Evidence stack 02</small><h2>Why</h2></div><div class="dr-why-grid">
<article><h3>Projection edge</h3><p>${c} · ${num(gap)} points · ${gp.toFixed(1)}% difference.</p></article>
<article><h3>Expected opportunity</h3><p>${safe(a.name)}: ${opportunity(a)}. ${safe(b.name)}: ${opportunity(b)}. ${workloadAgreement}.</p></article>
<article><h3>Sportsbook environment</h3><p>${environment}</p></article>
<article><h3>Market agreement</h3><p>${marketAgreement}. Consensus uses ${am.consensus_book_count} books for ${safe(a.team)} and ${bm.consensus_book_count} for ${safe(b.team)}.</p></article>
<article><h3>Ranks</h3><p>${safe(rank.name)} is ranked ${Math.abs(a.formats.yahoo.overall_rank-b.formats.yahoo.overall_rank)} overall ${spotWord(Math.abs(a.formats.yahoo.overall_rank-b.formats.yahoo.overall_rank))} higher.</p></article>
<article><h3>Evidence limits</h3><p>Player-market coverage varies. Current player availability, College ADP, and additional scoring formats are unavailable.</p></article>
</div></section>
<section class="dr-cases"><div class="dr-evidence-title"><small>Balanced evidence 03</small><h2>Case for each player</h2></div><div class="dr-case-grid"><article><h3>${safe(a.name)}</h3><ul>${caseFor(a,b)}</ul></article><article><h3>${safe(b.name)}</h3><ul>${caseFor(b,a)}</ul></article></div></section>
<section class="dr-boundary"><div class="dr-boundary-title"><small>Decision boundaries 04</small><h2>What changes the call</h2></div><div class="dr-boundary-grid">
<article><b>+${num(need)}</b><span>${c==='Toss-Up'?'A displayed difference beyond the weekly no-call boundary is required for a Lean.':`${safe(r.name)} needs this projection gain to move ahead.`}</span></article>
<article><b>Role</b><span>A material change to carries, receptions, or pass attempts can outweigh the current projection gap.</span></article>
<article><b>Market</b><span>A meaningful spread or implied-total move can change the game-environment evidence, but cannot substitute for player usage.</span></article>
<article><b>${blowout.length?'Blowout risk':'No flag'}</b><span>${blowout.length?`${blowout.map(p=>safe(p.team)+' '+spread(market(p).team_spread)).join(' and ')} may reduce late-game starter volume.`:'Neither team crosses the 21-point blowout-risk flag.'}</span></article>
</div></section>
<section class="dr-quality"><div class="dr-evidence-title"><small>Transparency 05</small><h2>Data coverage and evidence agreement</h2><p><b>Evidence agreement</b> · ${state}. The label summarizes direction, not probability or certainty.</p></div><div class="dr-quality-grid">${[['Projection','Present'],['Ranks','Present'],['Game market','Present'],['Player market',playerEvidence.length===2?'Present':playerEvidence.length?'Partial':'Unavailable'],['Opportunity','Present'],['Opponent','Present'],['ADP','Unavailable'],['History','Unavailable'],['Availability','Unavailable']].map(x=>`<span class="${x[1].toLowerCase().replace(' ','-')}"><b>${x[0]}</b>${x[1]}</span>`).join('')}</div><p class="dr-stamp">Projection and ranks ${safe(D.updated_at)} · TheRundown markets captured ${safe(D.market.captured_on)} · ${D.market.data_delay_seconds}-second plan delay · exact player components only · market inputs are not outcomes or guarantees</p></section>`
}
function pool(which){let rows=D.players.filter(p=>(!POS.value||p.position===POS.value)&&(!TEAM.value||p.team===TEAM.value)),a=byLabel[A.value];if(which===B&&!X.checked&&a)rows=rows.filter(p=>p.position===a.position);return rows}
function lists(){AL.innerHTML=pool(A).map(p=>`<option value="${safe(label(p))}"></option>`).join('');BL.innerHTML=pool(B).map(p=>`<option value="${safe(label(p))}"></option>`).join('')}
function mini(x){let a=P[x.a],b=P[x.b],w=x.winner?P[x.winner]:null;return `<article class="dr-mini"><div class="cdr-mini-team">${logo(a)}<span>${safe(a.team)}</span></div><div class="dr-mini-pair"><span>${safe(a.name)}</span><i>${w?'over':'vs.'}</i><span>${safe(b.name)}</span></div><div class="cdr-mini-team">${logo(b)}<span>${safe(b.team)}</span></div><p>${safe(a.position)}${a.formats.yahoo.position_rank} vs ${safe(b.position)}${b.formats.yahoo.position_rank} · ${num(x.gap)}-point projection gap</p><p class="dr-market">Team totals ${num(market(a).team_implied_total)} vs ${num(market(b).team_implied_total)}</p><p class="dr-recommendation">${w?(x.confidence==='Lean'?'Projection leans ':'Projection favors ')+safe(w.name):'No clear projection edge'}</p><button type="button" class="cdr-open" data-a="${safe(x.a)}" data-b="${safe(x.b)}">Compare all evidence</button></article>`}
document.getElementById('cdr-title').textContent=D.title;
document.getElementById('cdr-meta').textContent=`${D.projection_horizon} · 2026 Week ${D.week} · ${D.scoring_label} · projections ${D.updated_at} · sportsbook capture ${D.market.captured_on}`;
[...new Set(D.players.map(p=>p.team))].sort().forEach(t=>TEAM.add(new Option(t,t)));
document.getElementById('cdr-closest').innerHTML=D.closest_calls.map(mini).join('');document.getElementById('cdr-edges').innerHTML=D.strongest_edges.map(mini).join('');
let first=D.closest_calls[0],pa=P[Q.get('a')]||P[first.a],pb=P[Q.get('b')]||P[first.b];A.value=label(pa);B.value=label(pb);lists();draw();
A.addEventListener('change',()=>{let a=byLabel[A.value];if(a&&!X.checked){let b=pool(B).find(p=>p.id!==a.id);if(b)B.value=label(b)}lists();draw()});B.addEventListener('change',draw);[POS,TEAM,X].forEach(x=>x.addEventListener('change',()=>{lists();draw()}));document.querySelectorAll('.cdr-open').forEach(x=>x.addEventListener('click',()=>{A.value=label(P[x.dataset.a]);B.value=label(P[x.dataset.b]);draw();document.getElementById('cdr-compare-title').scrollIntoView({behavior:'smooth'})}))
}).catch(()=>{root.hidden=false;root.setAttribute('aria-busy','false');document.getElementById('cdr-meta').textContent='Validated College Week 1 evidence could not be loaded.'})
})();'''
