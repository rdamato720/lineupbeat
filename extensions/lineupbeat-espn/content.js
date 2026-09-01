(function(){
  'use strict';
  const isEspn=location.hostname==='fantasy.espn.com';
  function queryValue(key){return new URL(location.href).searchParams.get(key)||''}
  function firstText(selectors,fallback){for(const selector of selectors){const node=document.querySelector(selector);if(node&&node.textContent.trim())return node.textContent.trim()}return fallback}
  function parseRow(row){
    const link=row.querySelector('a[href*="/nfl/player/_/id/"],a[href*="playerId="]');if(!link)return null;
    const href=link.href||'',id=(href.match(/\/id\/(\d+)/)||href.match(/[?&]playerId=(\d+)/)||[])[1];if(!id)return null;
    const name=(link.textContent||'').trim(),text=(row.innerText||'').replace(/\s+/g,' ').trim();
    const positions=['D/ST','QB','RB','WR','TE','K'],position=positions.find(p=>new RegExp('(?:^|\\s)'+p.replace('/','\\/')+'(?:\\s|$)').test(text))||'';
    const teams=['ARI','ATL','BAL','BUF','CAR','CHI','CIN','CLE','DAL','DEN','DET','GB','HOU','IND','JAX','KC','LV','LAC','LAR','MIA','MIN','NE','NO','NYG','NYJ','PHI','PIT','SEA','SF','TB','TEN','WAS'];
    const team=teams.find(code=>new RegExp('(?:^|\\s)'+code+'(?:\\s|$)').test(text))||'';
    const firstCell=row.querySelector('td,th'),slot=(firstCell&&firstCell.textContent||position).trim().split(/\s+/)[0].toUpperCase();
    return{providerPlayerId:id,name,team,position,lineupSlot:slot};
  }
  function capture(receptionPoints){
    const roster=Array.from(document.querySelectorAll('tr,[role="row"]')).map(parseRow).filter(Boolean);
    if(!roster.length)throw new Error('No visible ESPN roster rows were found. Open the team roster page and try again.');
    const season=Number(queryValue('seasonId')||new Date().getFullYear());
    return{provider:'espn',connectionType:'browser_extension',league:{id:queryValue('leagueId')||'unknown',name:firstText(['[data-testid="league-name"]','.league-name','header h1'],'ESPN league'),season,scoringSettings:{receptionPoints:Number(receptionPoints)}},team:{id:queryValue('teamId')||'unknown',name:firstText(['[data-testid="team-name"]','.team-name','main h1'],'My ESPN team')},roster};
  }
  if(isEspn){
    const panel=document.createElement('div'),select=document.createElement('select'),button=document.createElement('button');
    select.setAttribute('aria-label','Reception scoring');select.innerHTML='<option value="">Choose scoring</option><option value="1">PPR</option><option value="0.5">Half-PPR</option><option value="0">Non-PPR</option>';
    button.type='button';button.textContent='Save roster locally for My Team';button.setAttribute('aria-label','Save visible ESPN roster locally for My Team');
    Object.assign(panel.style,{position:'fixed',right:'18px',bottom:'18px',zIndex:'2147483647',display:'flex',gap:'6px',padding:'8px',borderRadius:'6px',background:'#0b100f',boxShadow:'0 8px 30px rgba(0,0,0,.35)'});
    Object.assign(select.style,{padding:'10px',background:'#fff',color:'#0b100f'});Object.assign(button.style,{padding:'12px 16px',border:'0',borderRadius:'4px',background:'#c6f53c',color:'#0b100f',fontWeight:'800',cursor:'pointer'});
    button.addEventListener('click',()=>{if(select.value===''){button.textContent='Choose scoring first';return}try{const payload=capture(select.value);chrome.runtime.sendMessage({type:'LB_CAPTURE_ESPN_ROSTER',version:1,payload},response=>{button.textContent=response&&response.ok?'Roster saved locally — open Lineup Beat':'Capture failed — try again'})}catch(error){button.textContent=error.message}});
    panel.append(select,button);document.documentElement.appendChild(panel);return;
  }
  function ready(){chrome.runtime.sendMessage({type:'LB_GET_ESPN_ROSTER',version:1},response=>window.postMessage({type:'LB_MY_TEAM_EXTENSION_READY',version:1,hasRoster:Boolean(response&&response.payload)},location.origin))}
  window.addEventListener('message',event=>{
    if(event.source!==window||event.origin!==location.origin||!event.data||event.data.version!==1)return;
    if(event.data.type==='LB_MY_TEAM_CONNECT_REQUEST')chrome.runtime.sendMessage({type:'LB_GET_ESPN_ROSTER',version:1},response=>{if(response&&response.payload)window.postMessage({type:'LB_MY_TEAM_ESPN_ROSTER',version:1,payload:response.payload},location.origin)});
    if(event.data.type==='LB_MY_TEAM_CLEAR_REQUEST')chrome.runtime.sendMessage({type:'LB_CLEAR_ESPN_ROSTER',version:1},()=>window.postMessage({type:'LB_MY_TEAM_CLEAR_COMPLETE',version:1},location.origin));
  });
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ready,{once:true});else ready();
})();
