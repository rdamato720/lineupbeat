(function(){
  'use strict';
  const $=id=>document.getElementById(id),escape=value=>String(value==null?'':value).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const state={model:null,league:null,extension:false};
  const labels={ppr:'PPR',half_ppr:'Half-PPR',non_ppr:'Non-PPR'};
  const statusLabels={Q:'Questionable',O:'Out',D:'Doubtful',IR:'Injured reserve',PUP:'PUP',SUS:'Suspended',EXE:'Exempt',NFI:'NFI',COVID:'COVID list',NA:'Not active',OUT:'Out',DOUBTFUL:'Doubtful',QUESTIONABLE:'Questionable',PROBABLE:'Probable'};
  function setStatus(message,tone){const node=$('mt-status');node.textContent=message;node.dataset.tone=tone||'neutral'}
  function modelPlayer(rosterPlayer){return state.model.players.find(p=>rosterPlayer.identity&&p.id===rosterPlayer.identity.playerId)}
  function projection(player,format){const row=player&&player.formats&&player.formats[format];return row&&Number.isFinite(Number(row.projectedPoints))?Number(row.projectedPoints):null}
  function opportunity(player){
    const o=player&&player.expectedOpportunity||{},parts=[];
    if(player&&player.position==='QB'&&Number(o.passAttempts)>0)parts.push(`${Number(o.passAttempts).toFixed(1)} modeled pass attempts`);
    if(player&&player.position!=='QB'){
      if(Number(o.carries)>0)parts.push(`${Number(o.carries).toFixed(1)} carries`);
      if(Number(o.targets)>0)parts.push(`${Number(o.targets).toFixed(1)} targets`);
    }
    return parts.join(' · ');
  }
  function opponent(player){return player&&player.opponent?`${player.home?'vs.':'at'} ${player.opponent}`:'Opponent unavailable'}
  function matchup(player){
    if(!player||!player.opponent||!Number.isFinite(Number(player.matchupFactor)))return'';
    const change=Math.round((Number(player.matchupFactor)-1)*100),factor=change===0?'neutral':`${change>0?'+':'−'}${Math.abs(change)}% model factor`;
    return `${player.matchupLabel||'2025 prior-season context'} · ${factor}`;
  }
  function statusBadge(player){
    const raw=String(player&&player.providerStatus||'').toUpperCase(),label=statusLabels[raw];
    return label?`<span class="mt-status-badge" aria-label="Provider status: ${escape(label)}">${escape(label)}</span>`:'';
  }
  function comparisonUrl(a,b,format){
    const query=new URLSearchParams({a:a.id,format});if(b)query.set('b',b.id);
    return `/decision-room/nfl/?${query.toString()}`;
  }
  function playerRow(player,format){
    const model=modelPlayer(player),display=LineupBeatLeagueAdapter.displayIdentity(player),role=player.lineupGroup==='starter'?'Starter':player.lineupGroup==='bench'?'Bench':'Reserve';
    if(!model){
      const reason=player.unresolvedReason||'Validated Week 1 evidence is unavailable.';
      return `<article class="mt-player mt-player-unavailable"><div class="mt-player-art"></div><div><div class="mt-player-top"><small>${escape(role)} · ${escape(player.lineupSlot)} · ${escape(display.position||'Unknown')}</small>${statusBadge(player)}</div><h3>${escape(display.name)}</h3><p class="mt-player-projection">Projection unavailable</p><p>${escape(reason)}</p></div></article>`;
    }
    const points=projection(model,format),usage=opportunity(model),context=matchup(model);
    return `<article class="mt-player"><div class="mt-player-art"><img src="${escape(model.photo||model.teamLogo)}" alt="" onerror="this.src='${escape(model.teamLogo)}'"><img class="mt-logo" src="${escape(model.teamLogo)}" alt=""></div><div><div class="mt-player-top"><small>${escape(role)} · ${escape(player.lineupSlot)} · ${escape(display.position)}</small>${statusBadge(player)}</div><h3>${escape(display.name)}</h3><div class="mt-player-projection"><strong>${points==null?'—':points.toFixed(1)}</strong><span>Week 1 ${escape(labels[format])} pts</span></div><p class="mt-player-context"><b>${escape(opponent(model))}</b>${context?`<span>${escape(context)}</span>`:''}</p>${usage?`<p class="mt-player-usage">${escape(usage)}</p>`:''}<a class="mt-player-link" href="${escape(comparisonUrl(model,null,format))}">Open full player comparison</a></div></article>`;
  }
  function decisionCard(row,format){
    const bench=modelPlayer(row.bench),starter=modelPlayer(row.starter),gap=Math.abs(row.gap).toFixed(1),slot=row.starter.lineupSlot;
    const reason=`${bench.name} is eligible for the ${slot} slot and projects ${gap} points higher (${row.benchPoints.toFixed(1)} to ${row.starterPoints.toFixed(1)}), clearing the ${row.classification} threshold.`;
    const benchUsage=opportunity(bench),starterUsage=opportunity(starter);
    return `<article class="mt-decision"><div class="mt-decision-head"><small>Actionable ${escape(row.classification)} · ${escape(labels[format])}</small><h3>Start ${escape(bench.name)} over ${escape(starter.name)}</h3><p>${escape(reason)}</p></div><div class="mt-versus"><div><img src="${escape(bench.photo||bench.teamLogo)}" alt=""><b>${escape(bench.name)}</b><span>Bench · ${row.benchPoints.toFixed(1)} pts ${statusBadge(row.bench)}</span></div><i>over</i><div><img src="${escape(starter.photo||starter.teamLogo)}" alt=""><b>${escape(starter.name)}</b><span>Starter · ${row.starterPoints.toFixed(1)} pts ${statusBadge(row.starter)}</span></div></div><div class="mt-evidence"><p><b>Why change</b>${escape(reason)}</p><p><b>Week 1 opponents</b>${escape(bench.name)} ${escape(opponent(bench))}; ${escape(starter.name)} ${escape(opponent(starter))}.</p>${benchUsage||starterUsage?`<p><b>Modeled opportunity</b>${escape(bench.name)}: ${escape(benchUsage||'unavailable')}. ${escape(starter.name)}: ${escape(starterUsage||'unavailable')}.</p>`:''}<p><b>Matchup context</b>${escape(matchup(bench)||'Unavailable')}; ${escape(matchup(starter)||'Unavailable')}.</p><p><b>Availability limits</b>Current injury reports and sportsbook evidence are unavailable.</p></div><a class="mt-button secondary mt-compare" href="${escape(comparisonUrl(bench,starter,format))}">Open full comparison</a><p class="mt-caution">One Lineup Beat model supplies these projection, opportunity and 2025 context signals. They are not independent corroboration, predictive lift or certainty.</p></article>`;
  }
  function teamOutlook(league,format,actions){
    const starters=league.roster.starters.map(player=>({player,model:modelPlayer(player)})).filter(row=>row.model&&projection(row.model,format)!=null);
    const total=starters.reduce((sum,row)=>sum+projection(row.model,format),0),matched=LineupBeatLeagueAdapter.allPlayers(league).filter(player=>player.identity).length;
    const call=actions.length?`${actions.length} eligible lineup change${actions.length===1?'':'s'} clear${actions.length===1?'s':''} the meaningful-decision threshold. The strongest is ${modelPlayer(actions[0].bench).name} over ${modelPlayer(actions[0].starter).name} by ${actions[0].gap.toFixed(1)} points.`:'Your strongest lineup is already set. No eligible bench player projects far enough ahead to clear the meaningful-decision threshold.';
    return `<article class="mt-outlook-card"><div><small>Week ${state.model.week} team outlook</small><h3>${escape(league.team.name)}</h3><p>${escape(call)}</p></div><div class="mt-outlook-metrics"><span><strong>${total.toFixed(1)}</strong>supported starter points</span><span><strong>${matched}</strong>matched skill players</span><span><strong>${actions.length}</strong>actionable changes</span></div></article>`;
  }
  function render(){
    const league=state.league,format=league.league.scoring.format;
    $('mt-connect').hidden=true;$('mt-disconnect').hidden=false;$('mt-team').hidden=false;
    $('mt-league-name').textContent=league.league.name;$('mt-team-name').textContent=league.team.name;
    const provider={espn:'ESPN',yahoo:'Yahoo',cbs:'CBS'}[league.provider]||league.provider;
    $('mt-league-meta').textContent=`${provider} · ${league.league.season} · ${labels[format]} · browser-local extension`;
    const actions=LineupBeatLeagueAdapter.actionableDecisions(league,state.model,format);
    $('mt-outlook').innerHTML=teamOutlook(league,format,actions);
    $('mt-decisions').innerHTML=actions.length?actions.map(row=>decisionCard(row,format)).join(''):'<article class="mt-empty mt-lineup-set"><h3>Your strongest lineup is already set</h3><p>No eligible bench player clears the existing meaningful-decision threshold. Tiny projection differences remain no-calls.</p></article>';
    $('mt-roster').innerHTML=LineupBeatLeagueAdapter.GROUPS.map(group=>`<section class="mt-roster-group"><div><small>${group}</small><b>${league.roster[group].length}</b></div><div class="mt-player-grid">${league.roster[group].map(player=>playerRow(player,format)).join('')||'<p class="mt-empty">No players captured in this group.</p>'}</div></section>`).join('');
    const players=LineupBeatLeagueAdapter.allPlayers(league),matched=players.filter(p=>p.identity).length,unsupported=players.filter(p=>p.matchStatus==='unsupported_position').length,unresolved=players.length-matched-unsupported;
    setStatus(`Connected locally: ${matched} matched, ${unsupported} unsupported, ${unresolved} unresolved. No roster data was uploaded.`,unresolved?'warning':'good');
  }
  async function connect(){
    if(!state.model){setStatus('The public Week 1 model is still loading.','warning');return}
    setStatus('Looking for the Lineup Beat Fantasy extension in this browser…');window.postMessage({type:'LB_MY_TEAM_CONNECT_REQUEST',version:1},location.origin);
    setTimeout(()=>{if(!state.league)setStatus(state.extension?'Fantasy extension detected, but no saved roster was found. Open your provider roster, capture it, then return here.':'Fantasy extension not detected. Install it, open your ESPN, Yahoo, or CBS roster, capture it, then return here.','warning')},1200);
  }
  function disconnect(){state.league=null;window.postMessage({type:'LB_MY_TEAM_CLEAR_REQUEST',version:1},location.origin);$('mt-team').hidden=true;$('mt-connect').hidden=false;$('mt-disconnect').hidden=true;setStatus('Disconnected. The extension was asked to clear its browser-local roster copy.','good')}
  function reviewDemo(){
    const wanted=[['00-0034857','QB'],['00-0040719','RB'],['00-0037744','TE'],['00-0035261','BE']];
    const roster=wanted.map(([id,lineupSlot])=>{const player=state.model.players.find(row=>row.id===id);if(!player)throw new Error('The public reviewer fixture is unavailable.');return{providerPlayerId:String(player.providerIds.espn||`review-${id}`),name:player.name,team:player.team,position:player.position,lineupSlot,espnStatus:id==='00-0035261'?'Q':''}});
    roster.push({providerPlayerId:'review-dst',name:'Bills D/ST',team:'BUF',position:'D/ST',lineupSlot:'D/ST'});
    return{provider:'espn',connectionType:'browser_extension',league:{id:'review-demo',name:'BG-N-Co.',season:state.model.season,scoringSettings:{receptionPoints:.5}},team:{id:'review-demo-team',name:'Some Pulp'},roster};
  }
  function loadReviewDemo(){
    if(!state.model){setStatus('The public Week 1 model is still loading.','warning');return}
    try{setStatus('Loading the public reviewer demo into extension-local storage…');window.postMessage({type:'LB_MY_TEAM_REVIEW_DEMO_REQUEST',version:1,payload:reviewDemo()},location.origin);setTimeout(()=>{if(!state.league)setStatus('Reviewer demo handoff was not detected. Confirm the beta extension is installed, then try again.','warning')},1500)}catch(error){setStatus(error.message,'error')}
  }
  window.addEventListener('message',event=>{
    if(event.source!==window||event.origin!==location.origin||!event.data)return;
    if(event.data.type==='LB_MY_TEAM_EXTENSION_READY'){state.extension=true;if(event.data.hasRoster)window.postMessage({type:'LB_MY_TEAM_CONNECT_REQUEST',version:1},location.origin);else if(!state.league)setStatus('Fantasy extension detected, but no saved roster was found. Open your provider roster, capture it, then return here.','warning')}
    if(event.data.type==='LB_MY_TEAM_ROSTER'||event.data.type==='LB_MY_TEAM_ESPN_ROSTER'){try{state.extension=true;state.league=LineupBeatFantasyAdapter.adapt(event.data.payload);state.league=LineupBeatLeagueAdapter.match(state.league,state.model);render()}catch(error){setStatus('Roster could not be normalized: '+error.message,'error')}}
    if(event.data.type==='LB_MY_TEAM_CLEAR_COMPLETE')setStatus('Disconnected and cleared from extension-local storage.','good');
  });
  $('mt-connect').addEventListener('click',connect);$('mt-disconnect').addEventListener('click',disconnect);$('mt-demo').addEventListener('click',loadReviewDemo);
  if(new URLSearchParams(location.search).get('reviewer')==='1')$('mt-demo').hidden=false;
  fetch('/data/my-team-week1.json',{credentials:'omit',cache:'no-store'}).then(response=>{if(!response.ok)throw new Error('public model unavailable');return response.json()}).then(model=>{state.model=model;setStatus('Public Week 1 model ready. Connect the Fantasy extension after capturing an ESPN, Yahoo, or CBS roster.','good')}).catch(error=>setStatus('My Team cannot load the public Week 1 model: '+error.message,'error'));
})();
