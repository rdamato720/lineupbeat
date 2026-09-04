(function (root, factory) {
  'use strict';
  const parser = factory();
  if (typeof module === 'object' && module.exports) module.exports = parser;
  root.LineupBeatCbsRosterParser = parser;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const PLAYER_SELECTOR = 'a[href*="playerpage"],a[href*="/fantasy/football/players/"],a[href*="/players/"]';
  const EMPTY_ERROR = 'No visible CBS roster rows were found. Open My Team, then Roster, and try again.';
  const AMBIGUOUS_ERROR = 'Ambiguous or duplicate CBS roster rows were found. Capture stopped without saving; copy safe diagnostics for review.';
  const SLOTS = new Set(['QB','RB','WR','TE','FLEX','RB/WR/TE','WR/RB/TE','OP','SUPERFLEX','K','DST','D/ST','DEF','BN','BE','BENCH','IR','RES','RESERVE']);
  const TEAM_ALIASES = {JAC:'JAX',WSH:'WAS'};
  const clean = value => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  const upper = value => clean(value).toUpperCase();
  function visible(node) {
    if (!node) return false;
    for (let current=node; current && current.nodeType===1; current=current.parentElement) {
      if (current.hidden || current.getAttribute('aria-hidden') === 'true') return false;
      const style=current.style||{}; if(style.display==='none'||style.visibility==='hidden')return false;
    }
    return true;
  }
  function normalizeName(value) {
    const name=clean(value).replace(/\s+(?:Q|O|D|IR|PUP|SUS)$/i,'');
    const comma=name.match(/^([^,]+),\s*(.+)$/);
    if(!comma)return name;
    const given=comma[2].split(/\s+/),suffix=/^(?:Jr\.?|Sr\.?|II|III|IV|V)$/i.test(given[given.length-1])?given.pop():'';
    return clean(`${given.join(' ')} ${comma[1]} ${suffix}`);
  }
  function normalizeSlot(value) {
    const token=upper(value).split(/\s+/)[0];
    if(!SLOTS.has(token))return'';
    return ({BN:'BE',BENCH:'BE',RESERVE:'RES',DST:'D/ST',DEF:'D/ST'})[token]||token;
  }
  function metadata(value) {
    const text=upper(value).replace(/[|,·-]+/g,' ');
    let match=text.match(/(?:^|\s)([A-Z]{2,3})\s+(D\/ST|DST|DEF|QB|RB|WR|TE|K)(?:\s|$)/);
    if(!match){const reverse=text.match(/(?:^|\s)(D\/ST|DST|DEF|QB|RB|WR|TE|K)\s+([A-Z]{2,3})(?:\s|$)/);if(reverse)match=[reverse[0],reverse[2],reverse[1]];}
    if(!match)return null;
    return {team:TEAM_ALIASES[match[1]]||match[1],position:['DST','DEF'].includes(match[2])?'D/ST':match[2]};
  }
  function parseEntries(entries) {
    const roster=[],seen=new Set();
    for(const entry of entries||[]){
      const lineupSlot=normalizeSlot(entry.slot),meta=metadata(entry.meta),name=normalizeName(entry.name);
      const href=clean(entry.href),providerPlayerId=clean(entry.playerId||(href.match(/(?:playerpage|players)\/(\d+)/i)||[])[1]||href);
      if(!lineupSlot||!meta||!name||!providerPlayerId)continue;
      const key=`${lineupSlot}|${providerPlayerId}`;if(seen.has(key))throw new Error(AMBIGUOUS_ERROR);seen.add(key);
      roster.push({providerPlayerId,name,team:meta.team,position:meta.position,lineupSlot,providerStatus:upper(entry.status)||''});
    }
    return roster;
  }
  function entries(document){
    return Array.from(document.querySelectorAll('table')).flatMap(table=>{
      const heading=upper((table.querySelector('thead')||{}).textContent||'');
      if(!(heading.includes('PLAYER')&&(heading.includes('POS')||heading.includes('ROSTER')||heading.includes('SLOT'))))return[];
      return Array.from(table.querySelectorAll('tbody tr')).filter(visible).map(row=>{
        const anchor=row.querySelector(PLAYER_SELECTOR),cells=Array.from(row.querySelectorAll('td'));
        if(!anchor)return null;
        const slotCell=cells.find(cell=>normalizeSlot(cell.textContent));
        const metaCell=cells.find(cell=>metadata(cell.textContent));
        return {slot:slotCell&&slotCell.textContent,name:anchor.textContent,href:anchor.getAttribute('href'),playerId:anchor.getAttribute('data-player-id'),meta:metaCell?metaCell.textContent:row.textContent,status:(row.querySelector('[data-status]')||{}).textContent||''};
      }).filter(Boolean);
    });
  }
  function inspect(document){const found=entries(document),roster=parseEntries(found);return{candidateRows:found.length,acceptedRows:roster.length,rejectionCounts:{unusable:found.length-roster.length}};}
  function requireRoster(document){const roster=parseEntries(entries(document));if(!roster.length)throw new Error(EMPTY_ERROR);return roster;}
  return{PLAYER_SELECTOR,EMPTY_ERROR,AMBIGUOUS_ERROR,metadata,normalizeName,normalizeSlot,parseEntries,inspect,requireRoster};
});
