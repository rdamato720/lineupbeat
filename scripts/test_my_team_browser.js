const fs=require('fs'),vm=require('vm'),assert=require('assert');
vm.runInThisContext(fs.readFileSync('my-team/league-adapter.js','utf8'));
vm.runInThisContext(fs.readFileSync('my-team/espn-adapter.js','utf8'));

const raw={provider:'espn',league:{id:'1',name:'League',season:2026,scoringSettings:{receptionPoints:.5},cookie:'secret'},team:{id:'2',name:'Team',manager:'private'},sessionToken:'secret',roster:[
  {providerPlayerId:'99',name:'Starter Back',team:'BUF',position:'RB',lineupSlot:'RB'},
  {providerPlayerId:'missing',name:'Travis Etienne',team:'NO',position:'RB',lineupSlot:'BE'},
  {providerPlayerId:'dst',name:'Bills D/ST',team:'BUF',position:'D/ST',lineupSlot:'D/ST'}
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
assert.equal(LineupBeatLeagueAdapter.classify(10,9.9),'Toss-Up');
assert.equal(LineupBeatLeagueAdapter.normalizeName('Travis Etienne Jr.'),'travis etienne');
console.log('My Team browser adapter tests passed');
