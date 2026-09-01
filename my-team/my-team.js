(function(){
  'use strict';
  const $=id=>document.getElementById(id),escape=value=>String(value==null?'':value).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const state={model:null,league:null,extension:false};
  const labels={ppr:'PPR',half_ppr:'Half-PPR',non_ppr:'Non-PPR'};
  function sentence(value){const text=String(value||'').trim();return /[.!?]$/.test(text)?text:text+'.'}
  function setStatus(message,tone){const node=$('mt-status');node.textContent=message;node.dataset.tone=tone||'neutral'}
  function opportunity(player){const o=player.expectedOpportunity||{};return player.position==='QB'?Number(o.passAttempts||0).toFixed(1)+' modeled pass attempts':Number(o.carries||0).toFixed(1)+' carries and '+Number(o.targets||0).toFixed(1)+' targets'}
  function modelPlayer(rosterPlayer){return state.model.players.find(p=>rosterPlayer.identity&&p.id===rosterPlayer.identity.playerId)}
  function playerRow(player){
    const model=modelPlayer(player),status=player.matchStatus.replaceAll('_',' '),reason=player.unresolvedReason||'';
    return `<article class="mt-player"><div class="mt-player-art">${model?`<img src="${escape(model.photo||model.teamLogo)}" alt="" onerror="this.src='${escape(model.teamLogo)}'"><img class="mt-logo" src="${escape(model.teamLogo)}" alt="">`:''}</div><div><small>${escape(player.lineupSlot)} · ${escape(player.position||'Unknown')}</small><h3>${escape(player.name)}</h3><p>${model?`${escape(model.team)} · ${escape(status)}`:`${escape(status)} · ${escape(reason)}`}</p></div></article>`;
  }
  function decisionCard(row,format){
    const b=modelPlayer(row.bench),s=modelPlayer(row.starter),swap=row.action==='consider_swap';
    const title=swap?`Consider ${sentence(row.bench.name).replace(/[.]$/,'')} over ${sentence(row.starter.name).replace(/[.]$/,'')}`:'No clear model edge';
    const call=swap?`${row.classification} · ${row.bench.name} projects ${Math.abs(row.gap).toFixed(1)} ${labels[format]} points ahead.`:`${row.classification} · ${row.bench.name} and ${row.starter.name} remain inside the deterministic no-call band.`;
    return `<article class="mt-decision"><div class="mt-decision-head"><small>${escape(row.bench.position)} decision · ${labels[format]}</small><h3>${escape(title)}</h3><p>${escape(call)}</p></div><div class="mt-versus"><div><img src="${escape(b.photo||b.teamLogo)}" alt=""><b>${escape(row.bench.name)}</b><span>Bench · ${row.benchPoints.toFixed(1)}</span></div><i>vs.</i><div><img src="${escape(s.photo||s.teamLogo)}" alt=""><b>${escape(row.starter.name)}</b><span>Starter · ${row.starterPoints.toFixed(1)}</span></div></div><div class="mt-evidence"><p><b>Projection</b>${escape(call)}</p><p><b>Opportunity</b>${escape(row.bench.name)}: ${escape(opportunity(b))}. ${escape(row.starter.name)}: ${escape(opportunity(s))}.</p><p><b>Opponent context</b>${escape(row.bench.name)} ${b.home?'vs.':'at'} ${escape(b.opponent)} (${Number(b.matchupFactor||1).toFixed(2)} factor); ${escape(row.starter.name)} ${s.home?'vs.':'at'} ${escape(s.opponent)} (${Number(s.matchupFactor||1).toFixed(2)} factor). 2025 prior-season context.</p><p><b>Availability limits</b>Current injury reports and sportsbook evidence are unavailable.</p></div><p class="mt-caution">One Lineup Beat model supplies these projection, opportunity and matchup signals. They are not independent corroboration, predictive lift or certainty.</p></article>`;
  }
  function render(){
    const league=state.league,format=league.league.scoring.format;
    $('mt-connect').hidden=true;$('mt-disconnect').hidden=false;$('mt-team').hidden=false;
    $('mt-league-name').textContent=league.league.name;$('mt-team-name').textContent=league.team.name;
    $('mt-league-meta').textContent=`ESPN · ${league.league.season} · ${labels[format]} · browser-local extension`;
    const groups=LineupBeatLeagueAdapter.GROUPS.map(group=>`<section class="mt-roster-group"><div><small>${group}</small><b>${league.roster[group].length}</b></div><div class="mt-player-grid">${league.roster[group].map(playerRow).join('')||'<p class="mt-empty">No players captured in this group.</p>'}</div></section>`).join('');
    $('mt-roster').innerHTML=groups;
    const decisions=LineupBeatLeagueAdapter.lineupDecisions(league,state.model,format);
    $('mt-decisions').innerHTML=decisions.length?decisions.map(row=>decisionCard(row,format)).join(''):'<article class="mt-empty"><h3>No supported starter/bench comparison is available.</h3><p>This can happen when the roster has no matched bench player eligible for a captured starting slot. Lineup Beat will not invent a comparison.</p></article>';
    const players=LineupBeatLeagueAdapter.allPlayers(league),matched=players.filter(p=>p.identity).length,unsupported=players.filter(p=>p.matchStatus==='unsupported_position').length,unresolved=players.length-matched-unsupported;
    setStatus(`Connected locally: ${matched} matched, ${unsupported} unsupported, ${unresolved} unresolved. No roster data was uploaded.`,unresolved?'warning':'good');
  }
  async function connect(){
    if(!state.model){setStatus('The public Week 1 model is still loading.','warning');return}
    setStatus('Looking for the Lineup Beat ESPN extension in this browser…');
    window.postMessage({type:'LB_MY_TEAM_CONNECT_REQUEST',version:1},location.origin);
    setTimeout(()=>{if(!state.extension&&!state.league)setStatus('ESPN extension not detected. Install the development extension, open your ESPN roster, capture it, then return here.','warning')},1200);
  }
  function disconnect(){state.league=null;window.postMessage({type:'LB_MY_TEAM_CLEAR_REQUEST',version:1},location.origin);$('mt-team').hidden=true;$('mt-connect').hidden=false;$('mt-disconnect').hidden=true;setStatus('Disconnected. The extension was asked to clear its browser-local roster copy.','good')}
  window.addEventListener('message',event=>{
    if(event.source!==window||event.origin!==location.origin||!event.data)return;
    if(event.data.type==='LB_MY_TEAM_EXTENSION_READY'){state.extension=true;if(event.data.hasRoster)window.postMessage({type:'LB_MY_TEAM_CONNECT_REQUEST',version:1},location.origin)}
    if(event.data.type==='LB_MY_TEAM_ESPN_ROSTER'){
      try{state.extension=true;state.league=LineupBeatLeagueAdapter.match(LineupBeatEspnAdapter.adapt(event.data.payload),state.model);render()}
      catch(error){setStatus('Roster could not be normalized: '+error.message,'error')}
    }
    if(event.data.type==='LB_MY_TEAM_CLEAR_COMPLETE')setStatus('Disconnected and cleared from extension-local storage.','good');
  });
  $('mt-connect').addEventListener('click',connect);$('mt-disconnect').addEventListener('click',disconnect);
  fetch('/data/my-team-week1.json',{credentials:'omit',cache:'no-store'}).then(response=>{if(!response.ok)throw new Error('public model unavailable');return response.json()}).then(model=>{state.model=model;setStatus('Public Week 1 model ready. Connect the ESPN browser extension when your roster has been captured.','good')}).catch(error=>setStatus('My Team cannot load the public Week 1 model: '+error.message,'error'));
})();
