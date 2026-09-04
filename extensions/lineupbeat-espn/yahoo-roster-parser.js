(function (root, factory) {
  'use strict';
  const parser = factory();
  if (typeof module === 'object' && module.exports) module.exports = parser;
  root.LineupBeatYahooRosterParser = parser;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const PLAYER_SELECTOR = 'a[data-ys-playerid],a.name[href*="/nfl/players/"]';
  const EMPTY_ERROR = 'No visible Yahoo roster rows were found. Open your team roster page and try again.';
  const AMBIGUOUS_ERROR = 'Ambiguous or duplicate Yahoo roster rows were found. Capture stopped without saving; copy safe diagnostics for review.';
  const SLOTS = new Set(['QB','RB','WR','TE','W/R/T','W/R','W/T','Q/W/R/T','FLEX','K','DEF','D/ST','BN','BE','IR','IR+','NA']);
  const POSITIONS = new Set(['QB','RB','WR','TE','K','DEF','D/ST']);
  const TEAM_ALIASES = {JAC:'JAX',WSH:'WAS'};

  const clean = value => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  const upper = value => clean(value).toUpperCase();
  function visible(node) {
    if (!node) return false;
    for (let current = node; current && current.nodeType === 1; current = current.parentElement) {
      if (current.hidden || current.getAttribute('aria-hidden') === 'true') return false;
      const style = current.style || {};
      if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return true;
  }
  function normalizeSlot(value) {
    const slot = upper(value);
    if (!SLOTS.has(slot)) return '';
    return ({'W/R/T':'FLEX','W/R':'RB/WR','W/T':'WR/TE','Q/W/R/T':'OP',BN:'BE',DEF:'D/ST'})[slot] || slot;
  }
  function metadata(value) {
    const match = clean(value).match(/(?:^|\s)([A-Za-z]{2,3})\s*-\s*(D\/ST|DEF|QB|RB|WR|TE|K)(?:\s|$)/i);
    if (!match) return null;
    const team = TEAM_ALIASES[upper(match[1])] || upper(match[1]);
    const position = upper(match[2]) === 'DEF' ? 'D/ST' : upper(match[2]);
    return POSITIONS.has(position) ? {team, position} : null;
  }
  function parseEntries(entries) {
    const roster = [], seen = new Set();
    for (const entry of entries || []) {
      const lineupSlot = normalizeSlot(entry.slot);
      const meta = metadata(entry.meta);
      const name = clean(entry.name);
      const providerPlayerId = clean(entry.playerId || (clean(entry.href).match(/\/nfl\/players\/(\d+)/) || [])[1]);
      if (!lineupSlot || !meta || !name || !providerPlayerId) continue;
      const key = `${lineupSlot}|${providerPlayerId}`;
      if (seen.has(key)) throw new Error(AMBIGUOUS_ERROR);
      seen.add(key);
      roster.push({providerPlayerId,name,team:meta.team,position:meta.position,lineupSlot,providerStatus:upper(entry.status) || ''});
    }
    return roster;
  }
  function entries(document) {
    const tables = Array.from(document.querySelectorAll('table')).filter(table =>
      table.id === 'statTable0' || table.querySelector('[data-pos]'));
    return tables.flatMap(table => Array.from(table.querySelectorAll('tbody tr')).filter(visible).map(row => {
      const anchor = row.querySelector(PLAYER_SELECTOR);
      const slotNode = row.querySelector('[data-pos]') || row.querySelector('td');
      const metaNode = Array.from(row.querySelectorAll('span,small')).find(node => metadata(node.textContent));
      return anchor ? {
        slot: slotNode && (slotNode.getAttribute('data-pos') || slotNode.textContent),
        name: anchor.textContent,
        playerId: anchor.getAttribute('data-ys-playerid'),
        href: anchor.getAttribute('href'),
        meta: metaNode ? metaNode.textContent : row.textContent,
        status: (row.querySelector('[title="Questionable"],[title="Out"],[title="Doubtful"]') || {}).title || ''
      } : null;
    }).filter(Boolean));
  }
  function inspect(document) {
    const found = entries(document), roster = parseEntries(found);
    return {candidateRows: found.length, acceptedRows: roster.length, rejectionCounts:{unusable:found.length-roster.length}};
  }
  function requireRoster(document) {
    const roster = parseEntries(entries(document));
    if (!roster.length) throw new Error(EMPTY_ERROR);
    return roster;
  }
  return {PLAYER_SELECTOR,EMPTY_ERROR,AMBIGUOUS_ERROR,metadata,normalizeSlot,parseEntries,inspect,requireRoster};
});
