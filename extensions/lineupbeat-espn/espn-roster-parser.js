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
  const AMBIGUOUS_ERROR = 'Ambiguous or duplicate ESPN roster rows were found. Capture stopped without saving; copy safe diagnostics for review.';
  const TABLE_SELECTOR = 'table,[role="table"]';
  const ROW_SELECTOR = 'tr,[role="row"],.Table__TR';
  const DIRECT_CELL_SELECTOR = [
    'td', 'th', '[role="cell"]', '[role="gridcell"]', '[role="columnheader"]',
    '.Table__TD', '.Table__TH'
  ].join(',');
  const HEADER_SLOT_LABELS = new Set(['SLOT', 'LINEUP SLOT']);
  const HEADER_PLAYER_LABELS = new Set(['PLAYER', 'PLAYERS']);
  const DESIGNATIONS = new Set([
    'Q', 'O', 'D', 'IR', 'PUP', 'SUS', 'EXE', 'NFI', 'COVID', 'NA', 'OUT',
    'DOUBTFUL', 'QUESTIONABLE', 'PROBABLE'
  ]);
  const NON_NAME_LABELS = new Set([
    'SLOT', 'PLAYER', 'PLAYERS', 'ACTION', 'OPP', 'STATUS', 'PROJ', 'SCORE',
    'OPRK', '%ST', '%ROST', 'MOVE', 'DROP', 'TRADE', 'EDIT', 'ACTIVE',
    'INACTIVE', 'SUSP', 'NEWS', 'PLAYER NEWS', 'VIEW PLAYER CARD'
  ]);
  const REJECTION_KEYS = [
    'missingMappedCells', 'invalidSlot', 'invalidIdentityText', 'missingProviderId',
    'unsupportedWithoutProviderId', 'duplicateOrAmbiguous'
  ];

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

  function strictSlot(value) {
    const candidate = upper(value);
    if (!SLOT_ALIASES.includes(candidate)) return '';
    return SLOT_CANONICAL[candidate] || candidate;
  }

  function nearestTable(node) {
    for (let current = node && node.parentElement; current; current = current.parentElement) {
      if (matches(current, TABLE_SELECTOR)) return current;
    }
    return null;
  }

  function directCells(row) {
    return Array.from(row.children || []).filter(node =>
      isVisible(node) && matches(node, DIRECT_CELL_SELECTOR));
  }

  function tableRows(table) {
    return queryAll(table, ROW_SELECTOR).filter(row => nearestTable(row) === table);
  }

  function tableHeader(table) {
    for (const row of tableRows(table)) {
      if (!isVisible(row)) continue;
      const cells = directCells(row);
      const labels = cells.map(cell => upper(cell.textContent));
      const slotIndex = labels.findIndex(label => HEADER_SLOT_LABELS.has(label));
      const playerIndex = labels.findIndex(label => HEADER_PLAYER_LABELS.has(label));
      if (slotIndex >= 0 && playerIndex >= 0 && slotIndex !== playerIndex) {
        return {row, slotIndex, playerIndex};
      }
    }
    return null;
  }

  function visibleLines(node) {
    const lines = [];
    function add(value) {
      for (const line of String(value || '').split(/[\r\n]+/).map(text).filter(Boolean)) {
        if (lines[lines.length - 1] !== line) lines.push(line);
      }
    }
    function visit(current) {
      if (!isVisible(current)) return;
      const children = Array.from(current.children || []).filter(isVisible);
      if (!children.length) {
        add(current.innerText || current.textContent);
        return;
      }
      for (const child of children) visit(child);
    }
    visit(node);
    if (!lines.length) add(node.innerText || node.textContent);
    return lines;
  }

  function teamPosition(value) {
    const candidate = upper(value).replace(/[|,\u2022\u00b7-]+/g, ' ')
      .replace(/\s+/g, ' ').trim();
    const match = candidate.match(/^([A-Z]{2,3})\s+(D\/ST|DST|QB|RB|WR|TE|K)$/);
    if (!match || !TEAMS.includes(match[1]) || !POSITIONS.includes(match[2])) return null;
    return {team: match[1], position: match[2] === 'DST' ? 'D/ST' : match[2]};
  }

  function likelyName(value) {
    const candidate = text(value);
    const label = upper(candidate);
    if (!candidate || candidate.length > 80 || NON_NAME_LABELS.has(label) ||
        DESIGNATIONS.has(label) ||
        SLOT_ALIASES.includes(label) || TEAMS.includes(label) || POSITIONS.includes(label) ||
        teamPosition(candidate) ||
        /^(?:VS\.?|@)\s+[A-Z]{2,3}$/.test(label) || /^\d+(?:\.\d+)?$/.test(label)) return false;
    return /[\p{L}]/u.test(candidate) && /^[\p{L}\p{M}0-9 ./'\u2019-]+$/u.test(candidate);
  }

  function personName(value) {
    const candidate = text(value);
    if (!likelyName(candidate)) return '';
    const words = candidate.match(/[\p{L}\p{M}]+/gu) || [];
    return words.length >= 2 ? candidate : '';
  }

  function descendants(node) {
    const nodes = [];
    function visit(current) {
      nodes.push(current);
      for (const child of Array.from(current.children || [])) visit(child);
    }
    visit(node);
    return nodes;
  }

  function oneName(values) {
    const names = new Map();
    for (const value of values) {
      const candidate = personName(value);
      if (candidate) names.set(upper(candidate), candidate);
    }
    return names.size === 1 ? [...names.values()][0] : '';
  }

  function headshotAltName(cell) {
    return oneName(queryAll(cell, 'img').filter(isVisible)
      .map(image => image.getAttribute('alt') || ''));
  }

  function boundedName(cell) {
    const values = [];
    for (const node of descendants(cell)) {
      if (!isVisible(node)) continue;
      const explicit = node.getAttribute && node.getAttribute('data-player-name');
      if (explicit) values.push(explicit);
      const classes = String(node.getAttribute && node.getAttribute('class') || '')
        .split(/\s+/).filter(Boolean);
      const named = classes.some(token => /athlete/i.test(token) ||
        (/player/i.test(token) && /name/i.test(token)));
      if (named && !Array.from(node.children || []).filter(isVisible).length) {
        values.push(node.innerText || node.textContent);
      }
    }
    return oneName(values);
  }

  function structuredName(cell) {
    return oneName(visibleLines(cell).filter(value => {
      const label = upper(value);
      return !DESIGNATIONS.has(label) && !TEAMS.includes(label) &&
        !POSITIONS.includes(label) && !teamPosition(value);
    }));
  }

  function espnStatus(cell) {
    const statuses = [...new Set(visibleLines(cell).map(upper)
      .filter(label => DESIGNATIONS.has(label)))];
    return statuses.length === 1 ? statuses[0] : '';
  }

  function splitTeamPosition(cell) {
    const candidates = new Map();
    for (const container of descendants(cell)) {
      if (!isVisible(container)) continue;
      const children = Array.from(container.children || []).filter(isVisible);
      if (children.length !== 2) continue;
      const labels = children.map(child => upper(child.textContent));
      const teams = labels.filter(label => TEAMS.includes(label));
      const positions = labels.filter(label => POSITIONS.includes(label));
      if (teams.length !== 1 || positions.length !== 1) continue;
      const candidate = {
        team: teams[0],
        position: positions[0] === 'DST' ? 'D/ST' : positions[0],
        labels
      };
      candidates.set(`${candidate.team}\u0000${candidate.position}`, candidate);
    }
    return candidates.size === 1 ? [...candidates.values()][0] : null;
  }

  function mappedIdentity(cell) {
    const combined = visibleLines(cell).map(teamPosition).filter(Boolean);
    const metadata = combined.length === 1 ? combined[0] : splitTeamPosition(cell);
    if (!metadata) return null;
    const name = headshotAltName(cell) || boundedName(cell) || structuredName(cell);
    return name ? {...metadata, name, espnStatus: espnStatus(cell)} : null;
  }

  function headshotPathId(value) {
    const candidate = String(value || '');
    const match = candidate.match(/(?:^|\/)(?:i\/)?headshots\/nfl\/players\/(?:full\/)?(\d+)(?:\.[A-Za-z0-9]+)?(?:$|[/?#])/);
    return match ? match[1] : '';
  }

  function decodedHeadshotId(value) {
    let candidate = String(value || '');
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const id = headshotPathId(candidate);
      if (id) return id;
      try {
        const decoded = decodeURIComponent(candidate);
        if (decoded === candidate) break;
        candidate = decoded;
      } catch (_error) {
        break;
      }
    }
    return headshotPathId(candidate);
  }

  function headshotIdFromUrl(value) {
    try {
      const url = new URL(String(value || ''), 'https://fantasy.espn.com');
      if (url.protocol !== 'https:' ||
          !(url.hostname === 'espncdn.com' || url.hostname.endsWith('.espncdn.com'))) return '';
      const direct = headshotPathId(url.pathname);
      if (direct) return direct;
      if (url.pathname !== '/combiner/i') return '';
      return decodedHeadshotId(url.searchParams.get('img'));
    } catch (_error) {
      return '';
    }
  }

  function playerHeadshotId(cell) {
    for (const image of queryAll(cell, 'img')) {
      if (!isVisible(image)) continue;
      const candidates = [image.getAttribute('src') || ''];
      for (const entry of String(image.getAttribute('srcset') || '').split(',')) {
        candidates.push(entry.trim().split(/\s+/)[0] || '');
      }
      for (const candidate of candidates) {
        const id = headshotIdFromUrl(candidate);
        if (id) return id;
      }
    }
    return '';
  }

  function emptyDiagnostics() {
    return {
      tablesScanned: 0,
      qualifyingTables: 0,
      rowsScanned: 0,
      rowsAccepted: 0,
      legacyFallbackUsed: false,
      legacyRowsAccepted: 0,
      rejections: Object.fromEntries(REJECTION_KEYS.map(key => [key, 0]))
    };
  }

  function finalizePrimary(candidates, diagnostics) {
    const ambiguous = new Set();
    const byProvider = new Map();
    const byIdentity = new Map();
    candidates.forEach((candidate, index) => {
      const providerKey = candidate.providerPlayerId;
      const identityKey = `${upper(candidate.name)}\u0000${candidate.team}\u0000${candidate.position}`;
      if (providerKey && !byProvider.has(providerKey)) byProvider.set(providerKey, []);
      if (!byIdentity.has(identityKey)) byIdentity.set(identityKey, []);
      if (providerKey) byProvider.get(providerKey).push(index);
      byIdentity.get(identityKey).push(index);
    });
    for (const indexes of [...byProvider.values(), ...byIdentity.values()]) {
      if (indexes.length > 1) indexes.forEach(index => ambiguous.add(index));
    }
    diagnostics.rejections.duplicateOrAmbiguous += ambiguous.size;
    const roster = candidates.filter((_candidate, index) => !ambiguous.has(index));
    diagnostics.rowsAccepted = roster.length;
    return roster;
  }

  function discoverPrimary(document) {
    const diagnostics = emptyDiagnostics();
    const candidates = [];
    for (const table of queryAll(document, TABLE_SELECTOR)) {
      diagnostics.tablesScanned += 1;
      if (!isVisible(table)) continue;
      const header = tableHeader(table);
      if (!header) continue;
      diagnostics.qualifyingTables += 1;
      for (const row of tableRows(table)) {
        if (!isVisible(row) || row === header.row) continue;
        diagnostics.rowsScanned += 1;
        const cells = directCells(row);
        if (cells.length <= Math.max(header.slotIndex, header.playerIndex)) {
          diagnostics.rejections.missingMappedCells += 1;
          continue;
        }
        const lineupSlot = strictSlot(cells[header.slotIndex].textContent);
        if (!lineupSlot) {
          diagnostics.rejections.invalidSlot += 1;
          continue;
        }
        const identity = mappedIdentity(cells[header.playerIndex]);
        if (!identity) {
          diagnostics.rejections.invalidIdentityText += 1;
          continue;
        }
        const providerPlayerId = playerHeadshotId(cells[header.playerIndex]);
        if (!providerPlayerId) {
          if (identity.position === 'D/ST' || identity.position === 'K') {
            diagnostics.rejections.unsupportedWithoutProviderId += 1;
          } else {
            diagnostics.rejections.missingProviderId += 1;
            continue;
          }
        }
        candidates.push({...identity, providerPlayerId, lineupSlot});
      }
    }
    return {roster: finalizePrimary(candidates, diagnostics), diagnostics};
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

  function discoverLegacy(document) {
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

  function inspect(document) {
    const primary = discoverPrimary(document);
    if (primary.diagnostics.qualifyingTables > 0) return primary;
    const roster = discoverLegacy(document);
    primary.diagnostics.legacyFallbackUsed = true;
    primary.diagnostics.legacyRowsAccepted = roster.length;
    primary.diagnostics.rowsAccepted = roster.length;
    return {roster, diagnostics: primary.diagnostics};
  }

  function discover(document) {
    return inspect(document).roster;
  }

  function requireRoster(document) {
    const inspected = inspect(document);
    if (inspected.diagnostics.rejections.duplicateOrAmbiguous) {
      throw new Error(AMBIGUOUS_ERROR);
    }
    if (!inspected.roster.length) throw new Error(EMPTY_ERROR);
    return inspected.roster;
  }

  return {discover, inspect, requireRoster, EMPTY_ERROR, AMBIGUOUS_ERROR, PLAYER_SELECTOR};
});
