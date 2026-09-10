import test from 'node:test';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {Miniflare, createFetchMock} from 'miniflare';

test('actual Worker runtime can request the feed without redirect errors',async()=>{
 const mock=createFetchMock();mock.disableNetConnect();
 mock.get('https://therundown.io').intercept({path:/^\/api\/v2\/sports\/2\/events\//,method:'GET'}).reply(200,{events:[]},{headers:{'content-type':'application/json','x-datapoints':'1','x-datapoints-remaining':'5000000','x-data-delay-seconds':'30'}}).persist();
 const mf=new Miniflare({modules:true,scriptPath:fileURLToPath(new URL('../src/worker.mjs',import.meta.url)),compatibilityDate:'2026-09-01',durableObjects:{SCORE_CACHE:{className:'ScoreCache',useSQLite:true}},bindings:{THERUNDOWN_API_KEY:'test-secret'},fetchMock:mock});
 try {
  const response=await mf.dispatchFetch('https://scores.lineupbeat.com/nfl');
  assert.equal(response.status,200);
  const body=await response.json();assert.equal(body.error,null);assert.equal(body.delaySeconds,30);
  const health=await(await mf.dispatchFetch('https://scores.lineupbeat.com/health')).json();
  assert.equal(health.diagnostic.status,200);
 }finally{await mf.dispose();}
});

test('Worker refuses redirects without sending the key to another host',async()=>{
 let calls=0;
 const mf=new Miniflare({modules:true,scriptPath:fileURLToPath(new URL('../src/worker.mjs',import.meta.url)),compatibilityDate:'2026-09-01',durableObjects:{SCORE_CACHE:{className:'ScoreCache',useSQLite:true}},bindings:{THERUNDOWN_API_KEY:'test-secret'},outboundService:async request=>{
  calls++;assert.equal(new URL(request.url).hostname,'therundown.io');
  return new Response(null,{status:302,headers:{location:'https://untrusted.invalid/'}});
 }});
 try {
  const response=await mf.dispatchFetch('https://scores.lineupbeat.com/nfl');assert.equal(response.status,503);
  const health=await(await mf.dispatchFetch('https://scores.lineupbeat.com/health')).json();
  assert.equal(health.diagnostic.status,302);assert.equal(health.diagnostic.kind,'http');assert.equal(calls,1);
 }finally{await mf.dispose();}
});
