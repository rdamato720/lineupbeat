// Checks the actual deployed runtime; never requests or prints a private key.
for (const sport of ['nfl','college']) {
  let ready=false;
  for(let attempt=0;attempt<8;attempt++) {
    const response=await fetch(`https://scores.lineupbeat.com/${sport}`,{headers:{Origin:'https://lineupbeat.com'},signal:AbortSignal.timeout(45000)});
    if(!response.ok) throw Error(`${sport}: HTTP ${response.status}. Homepage has not been switched. Inspect worker /health; do not repeatedly retry blocked access.`);
    if(response.headers.get('access-control-allow-origin')!=='https://lineupbeat.com') throw Error('Homepage access header missing');
    const data=await response.json();
    if(data.error) throw Error(`${sport}: ${data.error}. Homepage has not been switched.`);
    if(data.complete && !data.stale) {
      if(!Array.isArray(data.games) || !data.games.length) throw Error(`${sport}: no games to validate. Review the schedule before switching.`);
      if(sport==='college' && !(data.fbsTeams>=130 && data.fbsTeams<=160)) throw Error('Review FBS classifications');
      console.log(`${sport}: ${data.games.length} games; ${data.delaySeconds}s provider delay${sport==='college'?`; ${data.fbsTeams} FBS teams`:''}`);
      ready=true;break;
    }
  }
  if(!ready) throw Error(`${sport}: cache did not become ready; homepage has not been switched.`);
}
if(process.argv.includes('--homepage')) {
  const response=await fetch(`https://lineupbeat.com/?scores-check=${Date.now()}`,{signal:AbortSignal.timeout(20000)});
  if(!response.ok) throw Error(`Homepage HTTP ${response.status}`);
  const html=await response.text();
  const start=html.indexOf('id="lb-scores"'), end=html.indexOf('</script>',start);
  const ticker=html.slice(start,end);
  if(start<0 || end<0 || !ticker.includes('https://scores.lineupbeat.com/') || !ticker.includes('Data provided by TheRundown') || /espn/i.test(ticker)) throw Error('Deployed homepage ticker verification failed. Check the build before claiming it is live.');
  console.log('Production homepage ticker verified.');
}
