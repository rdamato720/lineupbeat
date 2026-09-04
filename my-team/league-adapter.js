(function(root){
  'use strict';
  const VERSION='lineupbeat-league-v1';
  const SUPPORTED=new Set(['QB','RB','WR','TE']);
  const GROUPS=['starters','bench','reserve'];
  const suffixes=new Set(['jr','sr','ii','iii','iv','v']);
  const designations=new Set(['Q','O','D','IR','PUP','SUS','EXE','NFI','COVID','NA','OUT','DOUBTFUL','QUESTIONABLE','PROBABLE']);
  const teamAliases={JAC:'JAX',WSH:'WAS',LA:'LAR'};
  function clean(value){return String(value==null?'':value).trim()}
  function normalizeName(value){
    let tokens=clean(value).normalize('NFKD').replace(/[\u0300-\u036f]/g,'')
      .toLowerCase().replace(/[^a-z0-9\s]/g,'').split(/\s+/).filter(Boolean);
    while(tokens.length&&suffixes.has(tokens[tokens.length-1]))tokens.pop();
    return tokens.join(' ');
  }
  function normalizeTeam(value){let team=clean(value).toUpperCase();return teamAliases[team]||team}
  function validPlayerName(value){const name=clean(value),words=name.match(/[A-Za-z\u00c0-\u024f]+/g)||[];return Boolean(name&&!designations.has(name.toUpperCase())&&words.length>=2)}
  function displayIdentity(player){const identity=player&&player.identity;return{name:clean(identity&&identity.name)||clean(player&&player.name),team:clean(identity&&identity.team)||clean(player&&player.providerTeam),position:clean(identity&&identity.position)||clean(player&&player.position)}}
  function allPlayers(league){return GROUPS.flatMap(group=>(league.roster&&league.roster[group])||[])}
  function validate(league){
    const errors=[];
    if(!league||typeof league!=='object')return['payload must be an object'];
    if(league.schemaVersion!==VERSION)errors.push('unexpected schemaVersion');
    ['provider','connectionType'].forEach(k=>{if(!clean(league[k]))errors.push(k+' is required')});
    if(!league.league||!clean(league.league.id)||!clean(league.league.name)||!Number.isInteger(league.league.season))errors.push('league identity is incomplete');
    if(!league.team||!clean(league.team.id)||!clean(league.team.name))errors.push('team identity is incomplete');
    if(!Array.isArray(league.startingLineupSlots))errors.push('startingLineupSlots must be an array');
    GROUPS.forEach(group=>{if(!league.roster||!Array.isArray(league.roster[group]))errors.push('roster.'+group+' must be an array')});
    allPlayers(league).forEach((p,index)=>{
      if(!clean(p.name)||!clean(p.position)||!clean(p.lineupSlot)||
          (!clean(p.providerPlayerId)&&SUPPORTED.has(clean(p.position).toUpperCase())))errors.push('player '+index+' is incomplete');
      if(!clean(p.matchStatus))errors.push('player '+index+' has no matchStatus');
      if(['unresolved_identity','ambiguous_identity','unsupported_position'].includes(p.matchStatus)&&!clean(p.unresolvedReason))errors.push('player '+index+' has no unresolvedReason');
    });
    return errors;
  }
  function modelIndex(model,providerName){
    const provider=new Map(), identity=new Map(), ambiguous=new Set();
    (model.players||[]).forEach(p=>{
      const providerId=clean(p.providerIds&&p.providerIds[providerName]);
      if(providerId){if(provider.has(providerId))throw new Error('ambiguous '+providerName+' provider id '+providerId);provider.set(providerId,p)}
      const key=[normalizeName(p.name),normalizeTeam(p.team),clean(p.position).toUpperCase()].join('|');
      if(identity.has(key))ambiguous.add(key); else identity.set(key,p);
    });
    return {provider,identity,ambiguous};
  }
  function match(league,model){
    const index=modelIndex(model,league.provider);
    allPlayers(league).forEach(p=>{
      p.identity=null;
      const position=clean(p.position).toUpperCase();
      if(!SUPPORTED.has(position)){
        p.matchStatus='unsupported_position';
        p.unresolvedReason=(position||'Unknown position')+' is unsupported; Lineup Beat does not guess a D/ST projection.';
        return;
      }
      let hit=index.provider.get(clean(p.providerPlayerId));
      let status='matched_provider_id';
      if(!hit){
        if(!validPlayerName(p.name)){p.matchStatus='unresolved_identity';p.unresolvedReason='The captured player label is a status designation, not a valid player name.';return}
        const key=[normalizeName(p.name),normalizeTeam(p.providerTeam),position].join('|');
        if(index.ambiguous.has(key)){
          p.matchStatus='ambiguous_identity';p.unresolvedReason='More than one Lineup Beat identity has this exact normalized name, team, and position.';return;
        }
        hit=index.identity.get(key);status='matched_identity';
      }
      if(!hit){p.matchStatus='unresolved_identity';p.unresolvedReason='No unambiguous Lineup Beat identity matched the exact normalized name, team, and position.';return}
      p.identity={playerId:hit.id,name:hit.name,team:hit.team,position:hit.position};
      p.matchStatus=status;p.unresolvedReason=null;
    });
    const errors=validate(league);if(errors.length)throw new Error(errors.join('; '));
    return league;
  }
  function classify(a,b){
    const gap=Math.abs(Number(a)-Number(b)),reference=Math.max(Math.abs(Number(a)),Math.abs(Number(b)),.1),pct=gap/reference*100;
    if(gap<=.5||pct<=3)return'Toss-Up';if(gap<2||pct<10)return'Lean';if(gap<4||pct<20)return'Edge';return'Strong Edge';
  }
  function lineupDecisions(league,model,format){
    const byId=new Map((model.players||[]).map(p=>[p.id,p]));
    const score=p=>{const row=p.identity&&byId.get(p.identity.playerId);return row&&row.formats&&row.formats[format]?Number(row.formats[format].projectedPoints):null};
    const starters=(league.roster.starters||[]).filter(p=>p.identity&&score(p)!=null),bench=(league.roster.bench||[]).filter(p=>p.identity&&score(p)!=null),out=[];
    bench.forEach(b=>starters.forEach(s=>{
      const slot=(league.startingLineupSlots||[]).find(x=>x.slotId===s.lineupSlot);
      const eligible=Boolean(slot&&slot.allowedPositions.includes(b.position));
      if(!eligible)return;
      const bp=score(b),sp=score(s),classification=classify(bp,sp),gap=+(bp-sp).toFixed(1);
      out.push({bench:b,starter:s,benchPoints:bp,starterPoints:sp,gap,classification,action:gap>0&&classification!=='Toss-Up'?'consider_swap':'hold_or_toss_up'});
    }));
    return out.sort((a,b)=>(b.action==='consider_swap')-(a.action==='consider_swap')||b.gap-a.gap).slice(0,8);
  }
  function actionableDecisions(league,model,format){
    const bench=new Set(),starters=new Set(),out=[];
    for(const row of lineupDecisions(league,model,format)){
      if(row.action!=='consider_swap'||bench.has(row.bench)||starters.has(row.starter))continue;
      bench.add(row.bench);starters.add(row.starter);out.push(row);
      if(out.length===3)break;
    }
    return out;
  }
  root.LineupBeatLeagueAdapter={VERSION,SUPPORTED,GROUPS,normalizeName,normalizeTeam,validPlayerName,displayIdentity,validate,match,classify,lineupDecisions,actionableDecisions,allPlayers};
})(typeof globalThis!=='undefined'?globalThis:window);
