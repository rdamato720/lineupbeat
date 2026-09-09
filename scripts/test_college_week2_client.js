/* Run the shipped College Decision Room client against its release payload. */
const fs=require('fs'),vm=require('vm'),assert=require('assert'),cp=require('child_process');
const code=cp.execFileSync('python',['-c','import sys;sys.path.insert(0,"scripts");import college_decision_room;print(college_decision_room.JS)'],{encoding:'utf8'});
const D=JSON.parse(fs.readFileSync('site/data/decision-room-college.json','utf8'));
const id=name=>D.players.find(p=>p.name===name).id;
async function run(a,b){
 const nodes={};
 const node=id=>nodes[id]||(nodes[id]={value:'',innerHTML:'',textContent:'',hidden:true,checked:false,attrs:{},events:{},options:[],setAttribute(k,v){this.attrs[k]=v},addEventListener(k,f){this.events[k]=f},add(v){this.options.push(v)},scrollIntoView(){}});
 const document={body:{dataset:{defaultSport:'college'}},getElementById:node,querySelectorAll:()=>[]};
 const location={pathname:'/decision-room/college/',search:`?a=${id(a)}&b=${id(b)}`};
 vm.runInNewContext(code,{document,location,URLSearchParams,Option:function(text,value){this.text=text;this.value=value},fetch:async()=>({ok:true,json:async()=>D})});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(node('college-decision-room').attrs['aria-busy'],'false');
 assert(!node('cdr-meta').textContent.includes('could not be loaded'));
 assert(!/sportsbook capture|T\d\d:/.test(node('cdr-meta').textContent));
 assert(node('cdr-meta').textContent.includes('Updated September 9, 2026'));
 return {nodes,node};
}
(async()=>{
 let {node}=await run('Cam Pickett','Malachi Toney');
 assert(node('cdr-result').innerHTML.includes('Check player availability'));
 assert(node('cdr-result').innerHTML.includes('Questionable · projection assumes he plays'));
 assert(!node('cdr-result').innerHTML.includes('Pick: Malachi Toney'));
 ({node}=await run('Aaron Philo','AJ Surace'));
 assert(node('cdr-result').innerHTML.includes('Game lines unavailable'));
 assert(!/NaN|undefined|30-second|112 players/.test(node('cdr-result').innerHTML));
 assert(node('cdr-result').innerHTML.includes('Pick:'));
 node('cdr-team').value='Rutgers';node('cdr-team').events.change();
 assert(node('cdr-a-list').innerHTML.includes('AJ Surace'));
 assert(!node('cdr-a-list').innerHTML.includes('Aaron Philo'));
 assert(node('cdr-a').value.includes('Rutgers'));
 node('cdr-team').value='';node('cdr-position').value='RB';node('cdr-position').events.change();
 assert(node('cdr-a').value.endsWith(' RB'));
 assert(node('cdr-b').value.endsWith(' RB'));
 node('cdr-a').value='Not a player';node('cdr-a').events.change();
 assert(node('cdr-result').innerHTML.includes('Choose two different college players'));
 ({node}=await run('Ahmad Hardy','Kewan Lacy'));
 assert(node('cdr-result').innerHTML.includes('Check player availability'));
 assert(node('cdr-result').innerHTML.includes('Out'));
 assert(node('cdr-result').innerHTML.includes('Mon, Sep 7'));
 assert(!node('cdr-result').innerHTML.includes('2001'));
 const lacy=D.players.find(p=>p.name==='Kewan Lacy');lacy.availability.reported_at='2026-09-07';
 ({node}=await run('Ahmad Hardy','Kewan Lacy'));
 assert(node('cdr-result').innerHTML.includes('September 7, 2026'));
 console.log('PASS: shipped client renders injury and missing-market cases, filters teams, and rejects invalid selections');
})();
