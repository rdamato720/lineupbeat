const fs=require('fs'),vm=require('vm'),assert=require('assert');
vm.runInThisContext(fs.readFileSync('my-team/league-adapter.js','utf8'));
vm.runInThisContext(fs.readFileSync('my-team/espn-adapter.js','utf8'));

function modelPlayer(id,name,team,position,points){return{id,name,team,position,providerIds:{espn:id},formats:{half_ppr:{projectedPoints:points}},expectedOpportunity:{}}}

async function main(){
  const raw={provider:'espn',league:{id:'1',name:'League',season:2026,scoringSettings:{receptionPoints:.5},cookie:'secret'},team:{id:'2',name:'Team',manager:'private'},sessionToken:'secret',roster:[
    {providerPlayerId:'99',name:'Starter Back',team:'BUF',position:'RB',lineupSlot:'RB'},
    {providerPlayerId:'missing',name:'Travis Etienne',team:'NO',position:'RB',lineupSlot:'BE'},
    {providerPlayerId:'',name:'Bills D/ST',team:'BUF',position:'D/ST',lineupSlot:'D/ST'}
  ]};
  const model={players:[
    {id:'starter',name:'Starter Back',team:'BUF',position:'RB',providerIds:{espn:'99'},formats:{half_ppr:{projectedPoints:10}},expectedOpportunity:{carries:10,targets:2}},
    {id:'etienne',name:'Travis Etienne Jr.',team:'NO',position:'RB',providerIds:{},formats:{half_ppr:{projectedPoints:12}},expectedOpportunity:{carries:12,targets:3}}
  ]};
  let league=LineupBeatEspnAdapter.adapt(raw);
  assert.equal(league.league.scoring.format,'half_ppr');
  assert(!JSON.stringify(league).includes('private'));
  assert(!JSON.stringify(league).includes('secret'));
  league=LineupBeatLeagueAdapter.match(league,model);
  assert.equal(league.roster.starters[0].matchStatus,'matched_provider_id');
  assert.equal(league.roster.bench[0].matchStatus,'matched_identity');
  assert.equal(league.roster.starters[1].matchStatus,'unsupported_position');
  assert(league.roster.starters[1].unresolvedReason.includes('does not guess'));
  const decisions=LineupBeatLeagueAdapter.lineupDecisions(league,model,'half_ppr');
  assert.equal(decisions[0].action,'consider_swap');
  assert.equal(decisions[0].classification,'Edge');
  assert.equal(LineupBeatLeagueAdapter.actionableDecisions(league,model,'half_ppr').length,1);
  assert.equal(LineupBeatLeagueAdapter.classify(10,9.9),'Toss-Up');
  assert.equal(LineupBeatLeagueAdapter.normalizeName('Travis Etienne Jr.'),'travis etienne');
  const qRaw={provider:'espn',league:{id:'1',name:'League',season:2026,scoringSettings:{receptionPoints:.5}},team:{id:'2',name:'Team'},roster:[
    {providerPlayerId:'q-provider',name:'Q',team:'BUF',position:'QB',lineupSlot:'QB',espnStatus:'Q'},
    {providerPlayerId:'q-missing',name:'Q',team:'ATL',position:'RB',lineupSlot:'RB',espnStatus:'Q'},
    {providerPlayerId:'exact-missing',name:"D'Andre Example Jr.",team:'NO',position:'RB',lineupSlot:'BE',espnStatus:'Q'}
  ]};
  const qModel={players:[
    {id:'canonical-qb',name:'A.J. Canonical III',team:'BUF',position:'QB',providerIds:{espn:'q-provider'}},
    {id:'canonical-rb',name:"D'Andre Example Jr.",team:'NO',position:'RB',providerIds:{}}
  ]};
  const qLeague=LineupBeatLeagueAdapter.match(LineupBeatEspnAdapter.adapt(qRaw),qModel);
  assert.equal(qLeague.roster.starters[0].matchStatus,'matched_provider_id');
  assert.deepEqual(LineupBeatLeagueAdapter.displayIdentity(qLeague.roster.starters[0]),{name:'A.J. Canonical III',team:'BUF',position:'QB'});
  assert.equal(qLeague.roster.starters[1].matchStatus,'unresolved_identity');
  assert(qLeague.roster.starters[1].unresolvedReason.includes('status designation'));
  assert.equal(qLeague.roster.bench[0].matchStatus,'matched_identity');
  assert.equal(qLeague.roster.bench[0].espnStatus,'Q');
  assert(!LineupBeatLeagueAdapter.validPlayerName('Q'));
  assert(LineupBeatLeagueAdapter.validPlayerName("D'Andre Example Jr."));
  const liveSupported=Array.from({length:15},(_,index)=>({
    providerPlayerId:index<12?'live-'+index:'unmapped-'+index,
    name:'Live Player '+index,team:index%2?'ATL':'BUF',position:['QB','RB','WR','TE'][index%4],
    lineupSlot:index<9?['QB','RB','WR','TE'][index%4]:'BE',espnStatus:index>=12?'Q':''
  }));
  const liveRaw={provider:'espn',league:{id:'live',name:'BG-N-Co.',season:2026,scoringSettings:{receptionPoints:.5}},team:{id:'3',name:'Some Pulp'},roster:liveSupported.concat([
    {providerPlayerId:'',name:'Bills D/ST',team:'BUF',position:'D/ST',lineupSlot:'D/ST'}
  ])};
  const liveModel={players:liveSupported.map((row,index)=>({id:'model-'+index,name:row.name,team:row.team,position:row.position,providerIds:index<12?{espn:row.providerPlayerId}:{}}))};
  const liveLeague=LineupBeatLeagueAdapter.match(LineupBeatEspnAdapter.adapt(liveRaw),liveModel);
  const livePlayers=LineupBeatLeagueAdapter.allPlayers(liveLeague);
  assert.deepEqual(liveLeague.roster.starters.length,10);
  assert.deepEqual(liveLeague.roster.bench.length,6);
  assert.deepEqual(liveLeague.roster.reserve.length,0);
  assert.equal(livePlayers.filter(row=>row.identity).length,15);
  assert.equal(livePlayers.filter(row=>row.matchStatus==='unsupported_position').length,1);
  assert.equal(livePlayers.filter(row=>row.matchStatus==='unresolved_identity').length,0);
  assert.throws(()=>LineupBeatEspnAdapter.adapt({provider:'espn',league:{id:'1',name:'Missing id',season:2026},team:{id:'2',name:'Missing id'},roster:[
    {providerPlayerId:'',name:'Supported Player',team:'BUF',position:'RB',lineupSlot:'RB'}
  ]}),/player 0 is incomplete/);

  const expectedSlots={FLEX:['RB','WR','TE'],'RB/WR/TE':['RB','WR','TE'],'WR/RB/TE':['RB','WR','TE'],'RB/WR':['RB','WR'],'WR/RB':['RB','WR'],'WR/TE':['WR','TE'],'RB/TE':['RB','TE'],OP:['QB','RB','WR','TE'],SUPERFLEX:['QB','RB','WR','TE']};
  const slotRaw={provider:'espn',league:{id:'1',name:'Slots',season:2026,scoringSettings:{receptionPoints:.5}},team:{id:'2',name:'Slots'},roster:Object.keys(expectedSlots).map((slot,index)=>({providerPlayerId:String(index),name:'Player '+index,team:'BUF',position:'RB',lineupSlot:slot})).concat([{providerPlayerId:'unknown',name:'Unknown Slot',team:'BUF',position:'RB',lineupSlot:'W/R/T'}])};
  const normalizedSlots=LineupBeatEspnAdapter.adapt(slotRaw).startingLineupSlots;
  Object.entries(expectedSlots).forEach(([slot,allowed])=>assert.deepEqual(normalizedSlots.find(x=>x.slotId===slot).allowedPositions,allowed));
  assert.deepEqual(normalizedSlots.find(x=>x.slotId==='W/R/T').allowedPositions,[]);

  const flexRaw={provider:'espn',league:{id:'1',name:'Flex',season:2026,scoringSettings:{receptionPoints:.5}},team:{id:'2',name:'Flex'},roster:[
    {providerPlayerId:'flex-rb',name:'Flex Back',team:'BUF',position:'RB',lineupSlot:'RB/WR/TE'},
    {providerPlayerId:'bench-wr',name:'Bench Wideout',team:'NO',position:'WR',lineupSlot:'BE'}
  ]};
  const flexModel={players:[modelPlayer('flex-rb','Flex Back','BUF','RB',10),modelPlayer('bench-wr','Bench Wideout','NO','WR',14)]};
  const flexLeague=LineupBeatLeagueAdapter.match(LineupBeatEspnAdapter.adapt(flexRaw),flexModel);
  const flexDecision=LineupBeatLeagueAdapter.lineupDecisions(flexLeague,flexModel,'half_ppr')[0];
  assert.equal(flexDecision.action,'consider_swap');
  assert.equal(flexDecision.bench.position,'WR');
  assert.equal(flexDecision.starter.position,'RB');
  assert.equal(flexDecision.starter.lineupSlot,'RB/WR/TE');
  assert.deepEqual(LineupBeatLeagueAdapter.actionableDecisions(flexLeague,flexModel,'half_ppr')[0],flexDecision);

  const trivialModel={players:[modelPlayer('flex-rb','Flex Back','BUF','RB',10),modelPlayer('bench-wr','Bench Wideout','NO','WR',10.1)]};
  assert.equal(LineupBeatLeagueAdapter.actionableDecisions(flexLeague,trivialModel,'half_ppr').length,0);

  const positions=['RB','WR','TE','QB'];
  const ordinaryRaw={provider:'espn',league:{id:'1',name:'Ordinary',season:2026,scoringSettings:{receptionPoints:.5}},team:{id:'2',name:'Ordinary'},roster:positions.flatMap(position=>[
    {providerPlayerId:'start-'+position,name:'Start '+position,team:'BUF',position,lineupSlot:position},
    {providerPlayerId:'bench-'+position,name:'Bench '+position,team:'NO',position,lineupSlot:'BE'}
  ])};
  const ordinaryModel={players:positions.flatMap(position=>[modelPlayer('start-'+position,'Start '+position,'BUF',position,10),modelPlayer('bench-'+position,'Bench '+position,'NO',position,14)])};
  const ordinaryLeague=LineupBeatLeagueAdapter.match(LineupBeatEspnAdapter.adapt(ordinaryRaw),ordinaryModel);
  const ordinaryDecisions=LineupBeatLeagueAdapter.lineupDecisions(ordinaryLeague,ordinaryModel,'half_ppr');
  assert.equal(ordinaryDecisions.length,4);
  assert(ordinaryDecisions.every(row=>row.bench.position===row.starter.position));
  assert.deepEqual(new Set(ordinaryDecisions.map(row=>row.starter.position)),new Set(positions));
  const unknownRaw={provider:'espn',league:{id:'1',name:'Unknown',season:2026,scoringSettings:{receptionPoints:.5}},team:{id:'2',name:'Unknown'},roster:[
    {providerPlayerId:'unknown-start',name:'Unknown Start',team:'BUF',position:'RB',lineupSlot:'W/R/T'},
    {providerPlayerId:'unknown-bench',name:'Unknown Bench',team:'NO',position:'RB',lineupSlot:'BE'}
  ]};
  const unknownModel={players:[modelPlayer('unknown-start','Unknown Start','BUF','RB',10),modelPlayer('unknown-bench','Unknown Bench','NO','RB',14)]};
  const unknownLeague=LineupBeatLeagueAdapter.match(LineupBeatEspnAdapter.adapt(unknownRaw),unknownModel);
  assert.equal(LineupBeatLeagueAdapter.lineupDecisions(unknownLeague,unknownModel,'half_ppr').length,0);

  let listener,store={},opened=[];
  const chrome={runtime:{onMessage:{addListener(fn){listener=fn}}},tabs:{create({url}){opened.push(url);return Promise.resolve({id:1,url})}},storage:{local:{set(value){Object.assign(store,value);return Promise.resolve()},get(key){return Promise.resolve({[key]:store[key]})},remove(key){delete store[key];return Promise.resolve()}}}};
  vm.runInNewContext(fs.readFileSync('extensions/lineupbeat-espn/background.js','utf8'),{chrome,URL});
  const send=(type,url,payload)=>new Promise(resolve=>listener({type,version:1,payload},{url},resolve));
  const roster={private:'browser-local-test'};
  const captured=await send('LB_CAPTURE_ESPN_ROSTER','https://fantasy.espn.com/football/team?leagueId=1',roster);
  assert.equal(captured.ok,true);assert.equal(captured.opened,true);
  assert.deepEqual(opened,['https://lineupbeat.com/my-team/']);
  for(const url of ['https://lineupbeat-dev.pages.dev/','https://lineupbeat-dev.pages.dev/decision-room/nfl/','https://lineupbeat.com/','https://lineupbeat.com/decision-room/nfl/','http://localhost/my-team/','http://127.0.0.1/my-team/']){
    const get=await send('LB_GET_ESPN_ROSTER',url);
    assert.equal(get.ok,false);assert.equal(get.payload,undefined);
    const clear=await send('LB_CLEAR_ESPN_ROSTER',url);
    assert.equal(clear.ok,false);assert(store.lineupBeatEspnRosterV1);
  }
  const validGet=await send('LB_GET_ESPN_ROSTER','https://lineupbeat-dev.pages.dev/my-team/?league=1');
  assert.deepEqual(validGet.payload,roster);
  assert.deepEqual((await send('LB_GET_ESPN_ROSTER','https://lineupbeat.com/my-team/')).payload,roster);
  assert.deepEqual((await send('LB_GET_ESPN_ROSTER','https://www.lineupbeat.com/my-team/')).payload,roster);
  assert.equal((await send('LB_CLEAR_ESPN_ROSTER','https://lineupbeat-dev.pages.dev/my-team/')).ok,true);
  assert.equal((await send('LB_GET_ESPN_ROSTER','https://lineupbeat-dev.pages.dev/my-team/')).payload,null);
  const reviewRoster={provider:'espn',league:{id:'review'},roster:[]};
  assert.equal((await send('LB_SAVE_REVIEW_DEMO_ROSTER','https://lineupbeat-dev.pages.dev/my-team/?reviewer=1',reviewRoster)).ok,true);
  assert.deepEqual((await send('LB_GET_ESPN_ROSTER','https://lineupbeat-dev.pages.dev/my-team/')).payload,reviewRoster);
  assert.equal((await send('LB_SAVE_REVIEW_DEMO_ROSTER','https://lineupbeat-dev.pages.dev/decision-room/nfl/',reviewRoster)).ok,false);
  assert.deepEqual((await send('LB_GET_ESPN_ROSTER','https://lineupbeat-dev.pages.dev/my-team/')).payload,reviewRoster);
  assert.equal((await send('LB_CLEAR_ESPN_ROSTER','https://lineupbeat-dev.pages.dev/my-team/')).ok,true);
  for(const url of ['https://lineupbeat-dev.pages.dev/my-team/','https://fantasy.espn.com/','https://fantasy.espn.com/baseball/','https://fantasy.espn.com.evil.example/football/']){
    const badCapture=await send('LB_CAPTURE_ESPN_ROSTER',url,roster);
    assert.equal(badCapture.ok,false);
  }

  let fallbackListener,fallbackStore={};
  const fallbackChrome={runtime:{onMessage:{addListener(fn){fallbackListener=fn}}},tabs:{create(){return Promise.reject(new Error('blocked'))}},storage:{local:{set(value){Object.assign(fallbackStore,value);return Promise.resolve()},get(key){return Promise.resolve({[key]:fallbackStore[key]})},remove(key){delete fallbackStore[key];return Promise.resolve()}}}};
  vm.runInNewContext(fs.readFileSync('extensions/lineupbeat-espn/background.js','utf8'),{chrome:fallbackChrome,URL});
  const fallbackSend=(type,url,payload)=>new Promise(resolve=>fallbackListener({type,version:1,payload},{url},resolve));
  const fallback=await fallbackSend('LB_CAPTURE_ESPN_ROSTER','https://fantasy.espn.com/football/team',roster);
  assert.equal(fallback.ok,true);assert.equal(fallback.opened,false);
  const content=fs.readFileSync('extensions/lineupbeat-espn/content.js','utf8');
  assert(content.includes("open.textContent = 'Open My Team'"));
  assert(content.includes('Roster saved locally. Use Open My Team to continue.'));
  assert(!content.includes('fetch('));assert(!content.includes('XMLHttpRequest'));
  const myTeam=fs.readFileSync('my-team/my-team.js','utf8');
  assert(myTeam.includes('display=LineupBeatLeagueAdapter.displayIdentity(player)'));
  assert(myTeam.includes("Q:'Questionable'"));
  assert(myTeam.includes('aria-label="ESPN status: ${escape(label)}"'));
  assert(myTeam.includes('Week 1 ${escape(labels[format])} pts'));
  assert(myTeam.includes('2025 prior-season context'));
  assert(myTeam.includes('Open full player comparison'));
  assert(myTeam.includes('Your strongest lineup is already set'));
  assert(myTeam.includes('LineupBeatLeagueAdapter.actionableDecisions'));
  assert(myTeam.includes('ESPN extension detected, but no saved roster was found'));
  assert(!myTeam.includes('matched identity'));
  assert(!myTeam.includes('<h3>${escape(player.name)}</h3>'));

  console.log('My Team browser adapter and extension worker tests passed');
}
main().catch(error=>{console.error(error);process.exitCode=1});
