import {DAY, dates, ttl, normalize, fbsTeams, sortGames} from './model.mjs';
const LIMIT = 1000000, RESERVE = 10000;
const json = (body, status=200) => new Response(JSON.stringify(body), {status, headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== 'GET' || !['/nfl','/college','/health'].includes(url.pathname)) return json({error:'Not found'},404);
    const response = await env.SCORE_CACHE.get(env.SCORE_CACHE.idFromName('football-v1')).fetch(request);
    const headers = new Headers(response.headers);
    const origin = request.headers.get('Origin');
    if (['https://lineupbeat.com','https://www.lineupbeat.com'].includes(origin)) headers.set('Access-Control-Allow-Origin',origin);
    headers.set('Vary','Origin');
    return new Response(response.body,{status:response.status,headers});
  }
};
export class ScoreCache {
  constructor(ctx,env) { this.ctx=ctx; this.env=env; this.queue=Promise.resolve(); }
  fetch(request) {
    const next=this.queue.then(()=>this.handle(new URL(request.url).pathname));
    this.queue=next.catch(()=>{}); return next;
  }
  async provider(path) {
    const now=Date.now(), storage=this.ctx.storage;
    let usage=await storage.get('usage') || [];
    usage=usage.filter(x=>x.at>now-7*DAY);
    const block=await storage.get('block');
    if (block && (block.permanent || block.until>now)) throw Error(block.reason);
    if (!this.env.THERUNDOWN_API_KEY) throw Error('Key not configured');
    if (usage.reduce((n,x)=>n+x.points,0)+RESERVE>LIMIT) throw Error('Ticker budget reached');
    // Persist a reservation before the request, including network failures.
    const entry={at:now,points:RESERVE}; usage.push(entry); await storage.put('usage',usage);
    let response;
    try {
      response=await fetch('https://therundown.io/api/v2'+path,{headers:{'X-TheRundown-Key':this.env.THERUNDOWN_API_KEY},redirect:'manual',signal:AbortSignal.timeout(15000)});
    } catch (error) {
      await storage.put('diagnostic',{at:new Date(now).toISOString(),kind:['TimeoutError','AbortError'].includes(error?.name)?'timeout':'network_error'});
      await storage.put('block',{until:now+15*60000,reason:'Provider temporarily unavailable'}); throw Error('Provider temporarily unavailable');
    }
    await storage.put('diagnostic',{at:new Date(now).toISOString(),kind:'http',status:response.status});
    const billed=response.headers.get('x-datapoints');
    if (billed!==null && /^\d+$/.test(billed)) {entry.points=Number(billed); await storage.put('usage',usage);}
    if ([401,403].includes(response.status)) {
      await storage.put('block',{permanent:true,reason:'Provider access requires review'}); throw Error('Provider access requires review');
    }
    if (!response.ok) {
      const retry=Number(response.headers.get('retry-after'));
      await storage.put('block',{until:now+Math.max(15*60000,Number.isFinite(retry)?retry*1000:0),reason:'Provider temporarily unavailable'});
      throw Error('Provider temporarily unavailable');
    }
    const remaining=response.headers.get('x-datapoints-remaining');
    if (billed===null || !/^\d+$/.test(billed) || remaining===null || !/^\d+$/.test(remaining)) {
      await storage.put('block',{permanent:true,reason:'Usage headers require review'}); throw Error('Usage headers require review');
    }
    if (Number(remaining)<100000 || entry.points>RESERVE) {
      await storage.put('block',{until:now+DAY,reason:'Provider usage limit reached'}); throw Error('Provider usage limit reached');
    }
    const delay=Number(response.headers.get('x-data-delay-seconds'));
    if (response.headers.get('x-data-delay-seconds')===null || !Number.isFinite(delay)) throw Error('Missing delay information');
    await storage.put('delay',delay);
    const body=await response.text();
    if (body.length>4*1024*1024) throw Error('Provider response too large');
    return JSON.parse(body);
  }
  async handle(path) {
    const now=Date.now(), s=this.ctx.storage;
    if (path==='/health') return json({configured:!!this.env.THERUNDOWN_API_KEY,block:await s.get('block')||null,diagnostic:await s.get('diagnostic')||null});
    const sport=path.slice(1), days=dates(now);
    await this.alarm();
    let failure=null, fbs=[];
    try {
      if (sport==='college') {
        let directory=await s.get('directory');
        if (!directory || now-directory.at>23*3600000) {
          directory={ids:fbsTeams(await this.provider('/sports/1/teams')),at:now};
          await s.put('directory',directory);
        }
        fbs=directory.ids;
      }
      // Bound each refresh to two dates so active games cannot starve upcoming dates.
      let refreshed=0;
      for (const day of [...days].sort((a,b)=>Math.abs(Date.parse(a)-now)-Math.abs(Date.parse(b)-now))) {
        const key=sport+':'+day, cached=await s.get(key);
        if (cached && cached.next>now && now-cached.at<DAY) continue;
        const payload=await this.provider(`/sports/${sport==='nfl'?2:1}/events/${day}?market_ids=1&affiliate_ids=3&main_line=true&hide_closed=true&hide_no_markets=false&offset=300`);
        const games=normalize(payload.events,sport,fbs);
        await s.put(key,{games,at:now,next:now+ttl(games,now,day)});
        if (++refreshed === 2) break;
      }
    } catch {failure='Scores temporarily unavailable';}
    const records=await Promise.all(days.map(day=>s.get(sport+':'+day)));
    const valid=records.filter(r=>r && now-r.at<DAY);
    await s.setAlarm(now+DAY);
    const games=sortGames([...new Map(valid.flatMap(r=>r.games).map(g=>[g.id,g])).values()]);
    const complete=valid.length===days.length;
    const stale=!!failure || valid.some(r=>r.next<now-60000);
    return json({games,complete,stale,updatedAt:valid.length?new Date(Math.min(...valid.map(r=>r.at))).toISOString():null,
      delaySeconds:await s.get('delay')??null,fbsTeams:sport==='college'?fbs.length:undefined,error:failure},valid.length?200:503);
  }
  async alarm() {
    const entries=await this.ctx.storage.list();
    for (const [key,value] of entries) if ((key.includes(':') || key==='directory') && Date.now()-value.at>=DAY) await this.ctx.storage.delete(key);
  }
}
