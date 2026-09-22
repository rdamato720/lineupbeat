import test from 'node:test';
import assert from 'node:assert/strict';
import {normalize,ttl,marketDay,dates,fbsTeams} from '../src/model.mjs';
import {ScoreCache} from '../src/worker.mjs';
const event={event_id:'abc123',sport_id:2,event_date:'2026-09-11T00:35:00Z',teams:[{team_id:91,name:'San Francisco',mascot:'49ers',is_away:true},{team_id:90,name:'Los Angeles',mascot:'Rams',is_home:true}],score:{event_status:'STATUS_SCHEDULED',score_away:0,score_home:0}};
test('scheduled zeros are not played scores; no invented live clock',()=>{
  assert.equal(normalize([event],'nfl')[0].away.score,null);
  const live=structuredClone(event); live.score.event_status='STATUS_IN_PROGRESS';
  assert.equal(normalize([live],'nfl')[0].away.score,0);
  assert.equal(normalize([live],'nfl')[0].detail,'Live');
});
test('FBS filter retains FBS vs FCS and excludes lower division games',()=>{
  const e={...event,sport_id:1};
  assert.equal(normalize([e],'college',['91']).length,1);
  assert.equal(normalize([e],'college',['333']).length,0);
  assert.throws(()=>fbsTeams({teams:[]}),/requires review/);
});
test('date boundary and near-start refresh',()=>{
  const now=Date.parse('2026-09-11T00:30:00Z');
  assert.equal(marketDay(now),'2026-09-10'); assert.equal(dates(now).length,8);
  assert.equal(ttl(normalize([event],'nfl'),now,'2026-09-10'),60000);
});
function cache(){const data=new Map();return new ScoreCache({storage:{get:async k=>data.get(k),put:async(k,v)=>data.set(k,structuredClone(v)),list:async()=>data,delete:async k=>data.delete(k),setAlarm:async()=>{}}},{THERUNDOWN_API_KEY:'test-secret'});}
test('403 blocks subsequent requests and never exposes provider body',async()=>{
  const original=global.fetch;let calls=0;global.fetch=async()=>{calls++;return new Response('sensitive upstream body',{status:403});};
  try {const c=cache();const a=await c.fetch(new Request('https://scores.lineupbeat.com/nfl'));assert.equal(a.status,503);assert.equal((await a.text()).includes('sensitive'),false);await c.fetch(new Request('https://scores.lineupbeat.com/nfl'));assert.equal(calls,1);} finally{global.fetch=original;}
});
test('simultaneous requests share persistent cache and bill once per date',async()=>{
 const original=global.fetch;let calls=0;global.fetch=async()=>{calls++;return new Response(JSON.stringify({events:[]}),{headers:{'x-datapoints':'2','x-datapoints-remaining':'5000000','x-data-delay-seconds':'30'}});};
 try {const c=cache();await Promise.all(Array.from({length:8},()=>c.fetch(new Request('https://scores.lineupbeat.com/nfl'))));assert.equal(calls,8);const r=await(await c.fetch(new Request('https://scores.lineupbeat.com/nfl'))).json();assert.equal(r.complete,true);assert.equal(calls,8);}finally{global.fetch=original;}
});
test('ticker budget blocks calls before exhaustion',async()=>{
 const c=cache();await c.ctx.storage.put('usage',[{at:Date.now(),points:999999}]);
 await assert.rejects(c.provider('/sports/2/teams'),/budget/);
});

test('health reports sanitized upstream status without making another provider call',async()=>{
 const original=global.fetch;let calls=0;
 global.fetch=async()=>{calls++;return new Response('private upstream details',{status:502});};
 try {const c=cache();await c.fetch(new Request('https://scores.lineupbeat.com/nfl'));
 const r=await(await c.fetch(new Request('https://scores.lineupbeat.com/health'))).json();
 assert.equal(r.diagnostic.status,502);assert.equal(calls,1);
 assert.equal(JSON.stringify(r).includes('private upstream'),false);
 }finally{global.fetch=original;}
});
