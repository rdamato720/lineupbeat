(function (root, factory) {
  'use strict';
  const parser = factory();
  if (typeof module === 'object' && module.exports) module.exports = parser;
  root.LineupBeatEspnRosterParser = parser;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const PLAYER_SELECTOR = [
    'a[href*="/nfl/player/_/id/"]',
    'a[href*="/football/player/_/id/"]',
    'a[href*="playerId="]',
    '[data-playerid]',
    '[data-player-id]'
  ].join(',');
  const HEADER_SELECTOR = [
    'th', '[role="columnheader"]', '[data-column-header]',
    '[data-testid*="header"]', '.Table__TH'
  ].join(',');
  const CELL_SELECTOR = [
    'td', 'th', '[role="cell"]', '[role="gridcell"]',
    '[data-lineup-slot]', '[data-slot]', '[data-slot-id]', '.Table__TD'
  ].join(',');
  const TABLE_BOUNDARY_SELECTOR = 'table,[role="table"],section,article,main';
  const EXPLICIT_SLOT_ATTRIBUTES = ['data-lineup-slot', 'data-slot', 'data-slot-id'];
  const SLOT_ALIASES = [
    'RB/WR/TE', 'WR/RB/TE', 'SUPERFLEX', 'RB/WR', 'WR/RB', 'WR/TE', 'RB/TE',
    'D/ST', 'FLEX', 'BENCH', 'RESERVE', 'OP', 'QB', 'RB', 'WR', 'TE', 'DST',
    'K', 'BE', 'BN', 'IR', 'RES'
  ];
  const SLOT_CANONICAL = {DST: 'D/ST', BENCH: 'BE', BN: 'BE', RESERVE: 'RES'};
  const POSITIONS = ['D/ST', 'DST', 'QB', 'RB', 'WR', 'TE', 'K'];
  const TEAMS = [
    'ARI', 'ATL', 'BAL', 'BUF', 'CAR', 'CHI', 'CIN', 'CLE', 'DAL', 'DEN', 'DET',
    'GB', 'HOU', 'IND', 'JAX', 'KC', 'LV', 'LAC', 'LAR', 'MIA', 'MIN', 'NE',
    'NO', 'NYG', 'NYJ', 'PHI', 'PIT', 'SEA', 'SF', 'TB', 'TEN', 'WAS'
  ];
  const EMPTY_ERROR = 'No visible ESPN roster rows were found. Open the team roster page and try again.';

  function text(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }

  function upper(value) {
    return text(value).toUpperCase();
  }

  function matches(node, selector) {
    return Boolean(node && typeof node.matches === 'function' && node.matches(selector));
  }

  function queryAll(node, selector) {
    return node && typeof node.querySelectorAll === 'function'
      ? Array.from(node.querySelectorAll(selector)) : [];
  }

  function isVisible(node) {
    for (let current = node; current && current.nodeType === 1; current = current.parentElement) {
      if (current.hidden || current.hasAttribute('hidden') || current.hasAttribute('inert') ||
          upper(current.getAttribute('aria-hidden')) === 'TRUE') return false;
      const style = current.style || {};
      if (style.display === 'none' || style.visibility === 'hidden') return false;
      const view = current.ownerDocument && current.ownerDocument.defaultView;
      if (view && typeof view.getComputedStyle === 'function') {
        const computed = view.getComputedStyle(current);
        if (computed.display === 'none' || computed.visibility === 'hidden') return false;
      }
      if (upper(current.tagName) === 'TEMPLATE') return false;
    }
    return true;
  }

  function playerId(node) {
    const direct = node.getAttribute('data-playerid') || node.getAttribute('data-player-id');
    if (direct && /^\d+$/.test(direct)) return direct;
    const href = node.getAttribute('href') || node.href || '';
    return ((href.match(/\/id\/(\d+)/) || href.match(/[?&]playerId=(\d+)/) || [])[1]) || '';
  }

  function playerName(node) {
    return text(node.getAttribute('data-player-name') || node.textContent);
  }

  function slotFromText(value) {
    const candidate = upper(value);
    for (const alias of SLOT_ALIASES) {
      if (candidate === alias || candidate.startsWith(`${alias} `)) {
        return SLOT_CANONICAL[alias] || alias;
      }
    }
    return '';
  }

  function explicitSlot(row) {
    for (const attribute of EXPLICIT_SLOT_ATTRIBUTES) {
      const value = upper(row.getAttribute(attribute));
      if (value) return slotFromText(value) || value;
    }
    return '';
  }

  function rowSlot(row) {
    const explicit = explicitSlot(row);
    if (explicit) return explicit;
    const direct = Array.from(row.children || []).filter(isVisible);
    const marked = direct.filter(node => matches(node, CELL_SELECTOR));
    const first = (marked.length ? marked : direct)[0];
    if (first) {
      const slot = slotFromText(first.textContent);
      if (slot) return slot;
    }
    const nested = queryAll(row, CELL_SELECTOR).filter(isVisible)[0];
    return nested ? slotFromText(nested.textContent) : '';
  }

  function rosterHeader(root) {
    const labels = queryAll(root, HEADER_SELECTOR).filter(isVisible).map(node => upper(node.textContent));
    const hasSlot = labels.some(label => label === 'SLOT' || label === 'LINEUP SLOT');
    const hasPlayer = labels.some(label => label === 'PLAYER' || label === 'PLAYERS');
    if (hasSlot && hasPlayer) return true;
    const label = upper(root.getAttribute && (root.getAttribute('aria-label') || root.getAttribute('data-testid')));
    return label.includes('ROSTER') && hasPlayer;
  }

  function findRosterRoot(identity, documentElement) {
    for (let current = identity.parentElement; current && current !== documentElement;
         current = current.parentElement) {
      if (isVisible(current) && rosterHeader(current)) return current;
      if (matches(current, TABLE_BOUNDARY_SELECTOR)) return null;
    }
    return null;
  }

  function visibleIdentities(root) {
    return queryAll(root, PLAYER_SELECTOR)
      .filter(node => isVisible(node) && playerId(node) && playerName(node));
  }

  function findRosterRow(identity, root) {
    for (let current = identity.parentElement; current && current !== root;
         current = current.parentElement) {
      if (!isVisible(current) || !rowSlot(current)) continue;
      const identities = visibleIdentities(current);
      if (identities.length === 1 && identities[0] === identity) return current;
    }
    return null;
  }

  function token(textValue, values) {
    const normalized = ` ${upper(textValue).replace(/[^A-Z0-9/]+/g, ' ')} `;
    return values.find(value => normalized.includes(` ${value} `)) || '';
  }

  function parseRow(row, identity) {
    const id = playerId(identity);
    const name = playerName(identity);
    const lineupSlot = rowSlot(row);
    if (!id || !name || !lineupSlot) return null;
    const rowText = text(row.innerText || row.textContent);
    const rawPosition = token(rowText, POSITIONS);
    const position = rawPosition === 'DST' ? 'D/ST' : rawPosition;
    const team = token(rowText, TEAMS);
    return {providerPlayerId: id, name, team, position, lineupSlot};
  }

  function discover(document) {
    const rows = [];
    const seen = new Set();
    for (const identity of queryAll(document, PLAYER_SELECTOR)) {
      if (!isVisible(identity) || !playerId(identity) || !playerName(identity)) continue;
      const root = findRosterRoot(identity, document.documentElement);
      if (!root) continue;
      const row = findRosterRow(identity, root);
      if (!row) continue;
      const parsed = parseRow(row, identity);
      if (!parsed) continue;
      const key = `${parsed.providerPlayerId}\u0000${parsed.lineupSlot}`;
      if (!seen.has(key)) {
        seen.add(key);
        rows.push(parsed);
      }
    }
    return rows;
  }

  function requireRoster(document) {
    const roster = discover(document);
    if (!roster.length) throw new Error(EMPTY_ERROR);
    return roster;
  }

  return {discover, requireRoster, EMPTY_ERROR, PLAYER_SELECTOR};
});
