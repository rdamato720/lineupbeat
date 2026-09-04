(function(root){
  'use strict';
  const A=root.LineupBeatLeagueAdapter;
  const reserve=new Set(['IR','RES','RESERVE','TAXI']),bench=new Set(['BE','BENCH']);
  const flex={FLEX:['RB','WR','TE'],'RB/WR/TE':['RB','WR','TE'],'WR/RB/TE':['RB','WR','TE'],'RB/WR':['RB','WR'],'WR/RB':['RB','WR'],'WR/TE':['WR','TE'],'RB/TE':['RB','TE'],OP:['QB','RB','WR','TE'],SUPERFLEX:['QB','RB','WR','TE']};
  function text(v,fallback){v=String(v==null?'':v).trim();return v||fallback}
  function scoring(settings){const points=Number(settings&&settings.receptionPoints||0);return{format:points>=.75?'ppr':points>=.25?'half_ppr':'non_ppr',receptionPoints:points}}
  function adapt(raw){
    const providers=new Set(['espn','yahoo','cbs']);
    if(!raw||!providers.has(raw.provider))throw new Error('The extension did not provide a supported roster payload.');
    const groups={starters:[],bench:[],reserve:[]},counts=new Map();
    (raw.roster||[]).forEach(source=>{
      const position=text(source.position,'').toUpperCase(),slot=text(source.lineupSlot,position).toUpperCase();
      const group=reserve.has(slot)?'reserve':bench.has(slot)?'bench':'starters';
      if(group==='starters')counts.set(slot,(counts.get(slot)||0)+1);
      const supported=A.SUPPORTED.has(position);
      groups[group].push({providerPlayerId:text(source.providerPlayerId,''),name:text(source.name,''),providerTeam:text(source.team,'').toUpperCase()||null,position,lineupSlot:slot,lineupGroup:{starters:'starter',bench:'bench',reserve:'reserve'}[group],providerStatus:text(source.providerStatus||source.espnStatus,'').toUpperCase()||null,identity:null,matchStatus:supported?'pending':'unsupported_position',unresolvedReason:supported?null:(position||'Unknown position')+' is not supported by the Week 1 model.'});
    });
    const slots=Array.from(counts.entries()).sort().map(([slot,count])=>({slotId:slot,label:slot,allowedPositions:flex[slot]||(A.SUPPORTED.has(slot)?[slot]:[]),count}));
    const label={espn:'ESPN',yahoo:'Yahoo',cbs:'CBS'}[raw.provider];
    const payload={schemaVersion:A.VERSION,provider:raw.provider,connectionType:'browser_extension',league:{id:text(raw.league&&raw.league.id,'unknown'),name:text(raw.league&&raw.league.name,label+' league'),season:Number(raw.league&&raw.league.season||2026),scoring:scoring(raw.league&&raw.league.scoringSettings)},team:{id:text(raw.team&&raw.team.id,'unknown'),name:text(raw.team&&raw.team.name,'My '+label+' team')},startingLineupSlots:slots,roster:groups};
    const errors=A.validate(payload);if(errors.length)throw new Error(errors.join('; '));return payload;
  }
  root.LineupBeatFantasyAdapter={adapt,scoring};
  root.LineupBeatEspnAdapter=root.LineupBeatFantasyAdapter;
})(typeof globalThis!=='undefined'?globalThis:window);
