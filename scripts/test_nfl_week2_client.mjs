import vm from 'node:vm';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const {data,code}=JSON.parse(fs.readFileSync(0,'utf8'));
function page(query){
 const elements={};
 function element(value=''){return{value,textContent:'',hidden:false,handlers:{},addEventListener(type,fn){this.handlers[type]=fn;}};}
 for(const name of ['format','league','position','team','search','count','caption','empty','lineup-note'])elements['wb-'+name]=element();
 elements['wb-format'].value='half_ppr';elements['wb-league'].value='one_qb';
 elements['week1-data']={textContent:JSON.stringify(data)};
 const body={rows:data.rankings.players.filter(p=>p.eligible).map(p=>({dataset:{id:p.id},hidden:false,cells:{},querySelector(key){return this.cells[key]??=(element());}})),appendChild(row){const i=this.rows.indexOf(row);if(i>=0)this.rows.splice(i,1);this.rows.push(row);}};
 elements['wb-rows']=body;
 const location={search:query,href:'https://lineupbeat.com/nfl/week-2/rankings/'+query};
 const history={replaceState(_a,_b,url){location.href=String(url);}};
 vm.runInNewContext(code,{document:{getElementById:id=>elements[id]},location,history,URL,URLSearchParams});
 return {elements,body,location};
}
for(const format of ['ppr','half_ppr','non_ppr'])for(const league of ['one_qb','superflex']){
 const {elements,body,location}=page('?format='+format+'&league='+league);
 const expected=data.rankings.players.filter(p=>p.eligible).sort((a,b)=>a.formats[format].leagues[league].overall_rank-b.formats[format].leagues[league].overall_rank);
 assert.deepEqual(body.rows.map(r=>r.dataset.id),expected.map(r=>r.id));
 assert.equal(elements['wb-format'].value,format);assert.equal(elements['wb-league'].value,league);
 const byId=new Map(data.players.map(p=>[p.id,p]));
 for(const row of body.rows)assert.equal(row.querySelector('[data-col=points]').textContent,byId.get(row.dataset.id).formats[format].projected_points.toFixed(1));
 elements['wb-position'].value='QB';elements['wb-position'].handlers.change();
 assert.ok(body.rows.filter(r=>!r.hidden).every(r=>byId.get(r.dataset.id).position==='QB'));
 assert.ok(location.href.includes('position=QB'));
 const visible=body.rows.filter(r=>!r.hidden);
 assert.deepEqual(visible.map(r=>Number(r.querySelector('[data-col=rank]').textContent)),Array.from({length:visible.length},(_,i)=>i+1));
 elements['wb-search'].value='not a real player xyz';elements['wb-search'].handlers.input();
 assert.equal(elements['wb-empty'].hidden,false);assert.equal(body.rows.filter(r=>!r.hidden).length,0);
}
assert.equal(page('?format=hppr&league=superflex').elements['wb-format'].value,'half_ppr');
assert.equal(page('?format=bad&league=bad').elements['wb-league'].value,'one_qb');
console.log('All six ranking views, scoring totals, position order, URL state and empty search passed.');
