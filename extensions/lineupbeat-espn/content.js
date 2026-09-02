(function (root, factory) {
  'use strict';
  const diagnostics = factory();
  if (typeof module === 'object' && module.exports) module.exports = diagnostics;
  root.LineupBeatSafeDiagnostics = diagnostics;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const SLOT_LABELS = new Set([
    'QB', 'RB', 'WR', 'TE', 'FLEX', 'RB/WR/TE', 'WR/RB/TE', 'RB/WR', 'WR/RB',
    'WR/TE', 'RB/TE', 'OP', 'SUPERFLEX', 'D/ST', 'DST', 'K', 'BE', 'BN',
    'BENCH', 'IR', 'RES', 'RESERVE'
  ]);
  const HEADER_LABELS = new Set([
    'SLOT', 'PLAYER', 'ACTION', 'OPP', 'STATUS', 'PROJ', 'SCORE', 'OPRK', '%ST', '%ROST'
  ]);
  const ROLE_ALLOWLIST = new Set([
    'table', 'row', 'cell', 'gridcell', 'rowgroup', 'columnheader', 'grid', 'presentation'
  ]);
  const SAFE_WORDS = new Set([
    'table', 'tr', 'td', 'th', 'row', 'cell', 'grid', 'roster', 'lineup', 'slot',
    'player', 'position', 'team', 'column', 'header', 'body', 'wrapper', 'container',
    'scroll', 'fixed', 'responsive', 'desktop', 'mobile', 'starter', 'bench', 'flex',
    'sm', 'md', 'lg', 'even', 'odd', 'data', 'testid', 'id', 'index', 'athlete',
    'html', 'main', 'section', 'article', 'div', 'span', 'a', 'img', 'table',
    'thead', 'tbody', 'tr', 'th', 'td', 'ul', 'li'
  ]);
  const SAFE_HOST_LABELS = new Set([
    'www', 'fantasy', 'espn', 'espncdn', 'cdn', 'images', 'secure', 'a', 'com', 'net'
  ]);
  const SAFE_PATH_SEGMENTS = new Set([
    '_', 'nfl', 'football', 'fantasy', 'player', 'players', 'playercard', 'athlete',
    'profile', 'id', 'team', 'league', 'game', 'games', 'boxscore', 'i', 'headshots',
    'full', 'combiner', 'photo', 'photos', 'images', 'image', 'cdn', 'assets', 'logos'
  ]);
  const PLAYER_ATTRIBUTES = [
    'data-playerid', 'data-player-id', 'data-athlete-id', 'data-lineup-slot',
    'data-slot', 'data-slot-id', 'data-position', 'data-team'
  ];

  function normalized(value) {
    return String(value || '').replace(/\s+/g, ' ').trim().toUpperCase();
  }

  function queryAll(root, selector) {
    return root && typeof root.querySelectorAll === 'function'
      ? Array.from(root.querySelectorAll(selector)) : [];
  }

  function visible(node) {
    for (let current = node; current && current.nodeType === 1; current = current.parentElement) {
      if (current.hidden || current.hasAttribute('hidden') || current.hasAttribute('inert') ||
          normalized(current.getAttribute('aria-hidden')) === 'TRUE') return false;
      const style = current.style || {};
      if (style.display === 'none' || style.visibility === 'hidden') return false;
      const view = current.ownerDocument && current.ownerDocument.defaultView;
      if (view && typeof view.getComputedStyle === 'function') {
        const computed = view.getComputedStyle(current);
        if (computed.display === 'none' || computed.visibility === 'hidden') return false;
      }
    }
    return true;
  }

  function safeToken(value) {
    return String(value || '').replace(/[A-Za-z]+|\d+/g, part => {
      if (/^\d+$/.test(part)) return '#';
      return SAFE_WORDS.has(part.toLowerCase()) ? part : '*';
    });
  }

  function safePathSegment(value) {
    const part = String(value || '');
    if (!part) return '';
    const extensionMatch = part.match(/(\.[A-Za-z]{1,5})$/);
    const extension = extensionMatch ? extensionMatch[1].toLowerCase() : '';
    const base = extension ? part.slice(0, -extension.length) : part;
    if (/^\d+$/.test(base)) return `#${extension}`;
    if (SAFE_PATH_SEGMENTS.has(base.toLowerCase())) return `${base.toLowerCase()}${extension}`;
    return `*${extension}`;
  }

  function safePath(value) {
    const parts = String(value || '/').split('/').map(safePathSegment);
    const result = parts.join('/');
    return result.startsWith('/') ? result : `/${result}`;
  }

  function safeHostname(value) {
    return String(value || '').split('.').map(label => {
      if (/^\d+$/.test(label)) return '#';
      return SAFE_HOST_LABELS.has(label.toLowerCase()) ? label.toLowerCase() : '*';
    }).join('.');
  }

  function safeUrl(value, baseOrigin, image) {
    try {
      const url = new URL(String(value || ''), baseOrigin);
      if (url.protocol !== 'https:' && url.protocol !== 'http:') return null;
      const pathname = safePath(url.pathname);
      if (image) return `${safeHostname(url.hostname)}${pathname}`;
      return `${url.protocol}//${safeHostname(url.hostname)}${pathname}`;
    } catch (_error) {
      return null;
    }
  }

  function classTokens(node) {
    return String(node.getAttribute('class') || '').split(/\s+/).filter(Boolean)
      .slice(0, 12).map(safeToken);
  }

  function dataAttributeNames(node) {
    if (typeof node.getAttributeNames !== 'function') return [];
    return node.getAttributeNames().filter(name => name.toLowerCase().startsWith('data-'))
      .slice(0, 12).map(safeToken);
  }

  function descendantPatterns(nodes, attribute, baseOrigin, image) {
    const patterns = [];
    for (const node of nodes) {
      for (const target of queryAll(node, image ? 'img[src]' : 'a[href]')) {
        const pattern = safeUrl(target.getAttribute(attribute), baseOrigin, image);
        if (pattern && !patterns.includes(pattern)) patterns.push(pattern);
        if (patterns.length === 5) return patterns;
      }
    }
    return patterns;
  }

  function candidateDetails(candidate, baseOrigin) {
    const ancestors = [];
    const nodes = [];
    for (let current = candidate.parentElement; current && ancestors.length < 8;
         current = current.parentElement) {
      nodes.push(current);
      const rawRole = String(current.getAttribute('role') || '').toLowerCase();
      ancestors.push({
        tag: safeToken(String(current.tagName || '').toLowerCase()).toUpperCase(),
        classes: classTokens(current),
        role: ROLE_ALLOWLIST.has(rawRole) ? rawRole : null,
        dataAttributes: dataAttributeNames(current),
        childCount: Number((current.children || []).length),
        descendantAnchor: Boolean(current.querySelector && current.querySelector('a[href]')),
        descendantImage: Boolean(current.querySelector && current.querySelector('img[src]'))
      });
    }
    return {
      slot: normalized(candidate.textContent),
      ancestors,
      anchorPatterns: descendantPatterns(nodes, 'href', baseOrigin, false),
      imagePatterns: descendantPatterns(nodes, 'src', baseOrigin, true)
    };
  }

  function safeRowDiagnostics(value) {
    const source = value && typeof value === 'object' ? value : {};
    const rejections = source.rejections && typeof source.rejections === 'object'
      ? source.rejections : {};
    const number = key => Number.isFinite(Number(source[key])) ? Number(source[key]) : 0;
    const rejected = key => Number.isFinite(Number(rejections[key])) ? Number(rejections[key]) : 0;
    return {
      tablesScanned: number('tablesScanned'),
      qualifyingTables: number('qualifyingTables'),
      rowsScanned: number('rowsScanned'),
      rowsAccepted: number('rowsAccepted'),
      legacyFallbackUsed: source.legacyFallbackUsed === true,
      legacyRowsAccepted: number('legacyRowsAccepted'),
      rejections: {
        missingMappedCells: rejected('missingMappedCells'),
        invalidSlot: rejected('invalidSlot'),
        invalidIdentityText: rejected('invalidIdentityText'),
        missingProviderId: rejected('missingProviderId'),
        unsupportedWithoutProviderId: rejected('unsupportedWithoutProviderId'),
        duplicateOrAmbiguous: rejected('duplicateOrAmbiguous')
      }
    };
  }

  function generate({document, location, version, playerSelector, rowDiagnostics}) {
    const elements = queryAll(document, '*');
    const exactSlots = elements.filter(node => visible(node) && SLOT_LABELS.has(normalized(node.textContent)));
    const leafSlots = exactSlots.filter(node => !Array.from(node.children || [])
      .some(child => visible(child) && normalized(child.textContent) === normalized(node.textContent)));
    const headers = [];
    for (const node of elements) {
      const label = visible(node) ? normalized(node.textContent) : '';
      if (HEADER_LABELS.has(label) && !headers.includes(label)) headers.push(label);
    }
    const attributePresence = {};
    for (const name of PLAYER_ATTRIBUTES) {
      attributePresence[name] = elements.some(node => node.hasAttribute && node.hasAttribute(name));
    }
    return {
      schemaVersion: 'lineupbeat-espn-safe-diagnostics-v1',
      extensionVersion: /^\d+\.\d+\.\d+$/.test(String(version || '')) ? String(version) : 'unknown',
      pathname: safePath(location && location.pathname),
      counts: {
        tr: queryAll(document, 'tr').length,
        roleRow: queryAll(document, '[role="row"]').length,
        table: queryAll(document, 'table').length,
        roleTable: queryAll(document, '[role="table"]').length,
        tableClassRow: queryAll(document, '.Table__TR').length,
        tableClassCell: queryAll(document, '.Table__TD').length,
        currentPlayerSelector: playerSelector ? queryAll(document, playerSelector).length : 0,
        knownSlotLabel: exactSlots.length
      },
      headerLabels: headers,
      likelyPlayerAttributes: attributePresence,
      rowFirst: safeRowDiagnostics(rowDiagnostics),
      slotCandidates: leafSlots.slice(0, 3).map(node =>
        candidateDetails(node, location && location.origin))
    };
  }

  function install({button, status, document, location, version, playerSelector, clipboard,
                    generateDiagnostics, inspectRoster}) {
    let enabled = false;
    const create = generateDiagnostics || generate;
    button.hidden = true;
    if (button.style) button.style.display = 'none';
    button.addEventListener('click', async () => {
      if (!enabled) return;
      try {
        const inspected = typeof inspectRoster === 'function' ? inspectRoster(document) : null;
        const payload = create({
          document, location, version, playerSelector,
          rowDiagnostics: inspected && inspected.diagnostics
        });
        await clipboard.writeText(JSON.stringify(payload, null, 2));
        status.textContent = 'Safe diagnostics copied. Paste the JSON into Codex.';
      } catch (_error) {
        status.textContent = 'Safe diagnostics could not be copied. Clipboard access was denied.';
      }
    });
    return {
      show() {
        enabled = true;
        button.hidden = false;
        if (button.style) button.style.display = 'inline-block';
      },
      hide() {
        enabled = false;
        button.hidden = true;
        if (button.style) button.style.display = 'none';
      }
    };
  }

  return {generate, install};
});

(function () {
  'use strict';

  if (typeof location === 'undefined' || typeof document === 'undefined') return;

  const ESPN_ORIGIN = 'https://fantasy.espn.com';
  const ESPN_PATH = '/football/';
  const MY_TEAM_ORIGIN = 'https://lineupbeat-dev.pages.dev';
  const MY_TEAM_PATH = '/my-team/';
  const MY_TEAM_URL = `${MY_TEAM_ORIGIN}${MY_TEAM_PATH}`;
  const PRIVACY_URL = `${MY_TEAM_URL}extension/privacy/`;

  function onExpectedPage(origin, path) {
    return location.origin === origin && location.pathname.startsWith(path);
  }

  function queryValue(key) {
    return new URL(location.href).searchParams.get(key) || '';
  }

  function firstText(selectors, fallback) {
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      if (node && node.textContent.trim()) return node.textContent.trim();
    }
    return fallback;
  }

  function capture(receptionPoints) {
    const parser = globalThis.LineupBeatEspnRosterParser;
    if (!parser) throw new Error('The ESPN roster parser did not load. Reload the extension and try again.');
    const roster = parser.requireRoster(document);
    return {
      provider: 'espn',
      connectionType: 'browser_extension',
      league: {
        id: queryValue('leagueId') || 'unknown',
        name: firstText(['[data-testid="league-name"]', '.league-name', 'header h1'], 'ESPN league'),
        season: Number(queryValue('seasonId') || new Date().getFullYear()),
        scoringSettings: {receptionPoints: Number(receptionPoints)}
      },
      team: {
        id: queryValue('teamId') || 'unknown',
        name: firstText(['[data-testid="team-name"]', '.team-name', 'main h1'], 'My ESPN team')
      },
      roster
    };
  }

  function stylePanel(panel, select, save, open, diagnostics) {
    Object.assign(panel.style, {
      position: 'fixed', right: '18px', bottom: '18px', zIndex: '2147483647',
      width: 'min(380px, calc(100vw - 36px))', padding: '14px', borderRadius: '8px',
      background: '#0b100f', color: '#f7f5ef', boxShadow: '0 8px 30px rgba(0,0,0,.4)',
      font: '14px/1.35 Arial,sans-serif'
    });
    Object.assign(select.style, {padding: '10px', background: '#fff', color: '#0b100f'});
    for (const node of [save, open, diagnostics]) {
      Object.assign(node.style, {
        display: 'inline-block', padding: '12px 16px', border: '0', borderRadius: '4px',
        background: '#c6f53c', color: '#0b100f', fontWeight: '800', cursor: 'pointer',
        textDecoration: 'none'
      });
    }
  }

  function installEspnCapture() {
    const panel = document.createElement('section');
    const heading = document.createElement('strong');
    const disclosure = document.createElement('p');
    const privacy = document.createElement('a');
    const controls = document.createElement('div');
    const select = document.createElement('select');
    const save = document.createElement('button');
    const open = document.createElement('a');
    const diagnostics = document.createElement('button');
    const status = document.createElement('p');

    heading.textContent = 'Lineup Beat My Team BETA';
    disclosure.textContent = 'Reads visible roster and league labels and saves them only in this browser. No passwords, cookies, tokens, or server upload.';
    privacy.href = PRIVACY_URL;
    privacy.target = '_blank';
    privacy.rel = 'noopener';
    privacy.textContent = 'Privacy details';
    privacy.style.color = '#c6f53c';
    select.setAttribute('aria-label', 'Reception scoring');
    select.innerHTML = '<option value="">Choose scoring</option><option value="1">PPR</option><option value="0.5">Half-PPR</option><option value="0">Non-PPR</option>';
    save.type = 'button';
    save.textContent = 'Save roster locally for My Team';
    save.setAttribute('aria-label', 'Save visible ESPN roster locally for My Team');
    open.href = MY_TEAM_URL;
    open.target = '_blank';
    open.rel = 'noopener';
    open.textContent = 'Open My Team';
    open.hidden = true;
    diagnostics.type = 'button';
    diagnostics.textContent = 'Copy safe diagnostics';
    diagnostics.setAttribute('aria-label', 'Copy safe roster-discovery diagnostics');
    status.setAttribute('role', 'status');
    status.style.margin = '10px 0 0';
    controls.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;margin-top:12px';
    disclosure.style.margin = '8px 0 4px';

    stylePanel(panel, select, save, open, diagnostics);
    const diagnosticController = globalThis.LineupBeatSafeDiagnostics.install({
      button: diagnostics,
      status,
      document,
      location,
      version: chrome.runtime.getManifest().version,
      playerSelector: globalThis.LineupBeatEspnRosterParser.PLAYER_SELECTOR,
      inspectRoster: globalThis.LineupBeatEspnRosterParser.inspect,
      clipboard: navigator.clipboard
    });
    save.addEventListener('click', () => {
      diagnosticController.hide();
      if (select.value === '') {
        status.textContent = 'Choose scoring before saving.';
        return;
      }
      try {
        const payload = capture(select.value);
        chrome.runtime.sendMessage(
          {type: 'LB_CAPTURE_ESPN_ROSTER', version: 1, payload},
          response => {
            if (!response || !response.ok) {
              status.textContent = 'Roster could not be saved locally. Try again.';
              return;
            }
            open.hidden = false;
            status.textContent = response.opened
              ? 'Roster saved locally. My Team opened in a new tab.'
              : 'Roster saved locally. Use Open My Team to continue.';
          }
        );
      } catch (error) {
        status.textContent = error.message;
        if (error.message === globalThis.LineupBeatEspnRosterParser.EMPTY_ERROR ||
            error.message === globalThis.LineupBeatEspnRosterParser.AMBIGUOUS_ERROR) {
          diagnosticController.show();
        }
      }
    });

    controls.append(select, save, open, diagnostics);
    panel.append(heading, disclosure, privacy, controls, status);
    document.documentElement.appendChild(panel);
  }

  function postRoster(response) {
    if (response && response.payload) {
      window.postMessage({
        type: 'LB_MY_TEAM_ESPN_ROSTER', version: 1, payload: response.payload
      }, location.origin);
    }
  }

  function ready() {
    chrome.runtime.sendMessage({type: 'LB_GET_ESPN_ROSTER', version: 1}, response => {
      window.postMessage({
        type: 'LB_MY_TEAM_EXTENSION_READY',
        version: 1,
        hasRoster: Boolean(response && response.payload)
      }, location.origin);
    });
  }

  function installMyTeamBridge() {
    window.addEventListener('message', event => {
      if (event.source !== window || event.origin !== location.origin ||
          !event.data || event.data.version !== 1) return;
      if (event.data.type === 'LB_MY_TEAM_CONNECT_REQUEST') {
        chrome.runtime.sendMessage({type: 'LB_GET_ESPN_ROSTER', version: 1}, postRoster);
      }
      if (event.data.type === 'LB_MY_TEAM_REVIEW_DEMO_REQUEST') {
        chrome.runtime.sendMessage({
          type: 'LB_SAVE_REVIEW_DEMO_ROSTER', version: 1, payload: event.data.payload
        }, response => {
          if (response && response.ok) postRoster({payload: event.data.payload});
        });
      }
      if (event.data.type === 'LB_MY_TEAM_CLEAR_REQUEST') {
        chrome.runtime.sendMessage({type: 'LB_CLEAR_ESPN_ROSTER', version: 1}, response => {
          if (response && response.ok) {
            window.postMessage({type: 'LB_MY_TEAM_CLEAR_COMPLETE', version: 1}, location.origin);
          }
        });
      }
    });
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', ready, {once: true});
    } else {
      ready();
    }
  }

  if (onExpectedPage(ESPN_ORIGIN, ESPN_PATH)) installEspnCapture();
  if (onExpectedPage(MY_TEAM_ORIGIN, MY_TEAM_PATH)) installMyTeamBridge();
})();
