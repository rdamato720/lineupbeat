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
  const EXCLUDED_PAGE_LABELS = new Set([
    'MY TEAM', 'TEAM SETTINGS', 'LEAGUE', 'OPPOSING TEAMS',
    'ESPN FANTASY FOOTBALL'
  ]);

  function normalized(value) {
    return String(value || '').replace(/\s+/g, ' ').trim().toUpperCase();
  }

  function queryAll(root, selector) {
    return root && typeof root.querySelectorAll === 'function'
      ? Array.from(root.querySelectorAll(selector)) : [];
  }

  function pageLabel(value) {
    const candidate = String(value || '').replace(/\s+/g, ' ').trim();
    const label = normalized(candidate);
    if (!candidate || candidate.length > 80 || EXCLUDED_PAGE_LABELS.has(label) ||
        /https?:\/\//i.test(candidate) || /[?&](?:leagueId|teamId)=/i.test(candidate) ||
        /^\d+(?:-\d+){1,2}$/.test(candidate)) return '';
    return /[\p{L}\p{N}]/u.test(candidate) ? candidate : '';
  }

  function visibleLabelCandidates(link) {
    const leaves = queryAll(link, '*').filter(node => visible(node) &&
      !Array.from(node.children || []).some(visible));
    const values = leaves.map(node => node.textContent);
    if (!values.length) values.push(link.textContent);
    const aria = link.getAttribute && link.getAttribute('aria-label');
    if (aria) values.push(aria);
    return values.map(pageLabel).filter(Boolean);
  }

  function managerContext(node) {
    let depth = 0;
    for (let current = node; current && current.nodeType === 1 && depth < 2;
         current = current.parentElement, depth += 1) {
      const context = ['class', 'id', 'data-testid', 'aria-label']
        .map(name => String(current.getAttribute && current.getAttribute(name) || ''))
        .join(' ');
      if (/(?:^|[-_\s])(?:manager|owner|member)(?:$|[-_\s])/i.test(context)) return true;
    }
    return false;
  }

  function fantasyUrl(node) {
    try {
      const base = node.ownerDocument && node.ownerDocument.baseURI ||
        'https://fantasy.espn.com/football/team';
      const url = new URL(String(node.getAttribute('href') || ''), base);
      return url.origin === 'https://fantasy.espn.com' && url.pathname.startsWith('/football/')
        ? url : null;
    } catch (_error) {
      return null;
    }
  }

  function oneLabel(values) {
    const labels = new Map();
    for (const value of values) {
      const candidate = pageLabel(value);
      if (candidate) labels.set(normalized(candidate), candidate);
    }
    return {value: labels.size === 1 ? [...labels.values()][0] : '', count: labels.size};
  }

  function pageLabels(document, leagueId, teamId) {
    const leagueValues = [];
    const teamValues = [];
    for (const link of queryAll(document, 'a[href]').filter(visible)) {
      const url = fantasyUrl(link);
      if (!url) continue;
      const labels = visibleLabelCandidates(link);
      if (!labels.length) continue;
      const leagueContext = url.pathname.startsWith('/football/league') ||
        !url.searchParams.has('teamId');
      if (leagueId && leagueContext &&
          url.searchParams.get('leagueId') === String(leagueId)) leagueValues.push(...labels);
      if (teamId && url.pathname.startsWith('/football/team') &&
          url.searchParams.get('teamId') === String(teamId) &&
          (!leagueId || !url.searchParams.has('leagueId') ||
           url.searchParams.get('leagueId') === String(leagueId)) && !managerContext(link)) {
        teamValues.push(...labels);
      }
    }
    const league = oneLabel(leagueValues);
    const team = oneLabel(teamValues);
    return {
      leagueName: league.value || 'ESPN league',
      teamName: team.value || 'My ESPN team',
      diagnostics: {
        leagueCandidateCount: league.count,
        leagueConflict: league.count > 1,
        teamCandidateCount: team.count,
        teamConflict: team.count > 1
      }
    };
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
    let linkedLabels = {diagnostics: {
      leagueCandidateCount: 0, leagueConflict: false,
      teamCandidateCount: 0, teamConflict: false
    }};
    try {
      const url = new URL(String(location && location.href || ''),
        String(location && location.origin || 'https://fantasy.espn.com'));
      linkedLabels = pageLabels(document, url.searchParams.get('leagueId') || '',
        url.searchParams.get('teamId') || '');
    } catch (_error) {}
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
      metadataLabels: linkedLabels.diagnostics,
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

  return {generate, install, pageLabels};
});

(function () {
  'use strict';

  if (typeof location === 'undefined' || typeof document === 'undefined') return;

  const ESPN_ORIGIN = 'https://fantasy.espn.com';
  const ESPN_PATH = '/football/';
  const YAHOO_ORIGIN = 'https://football.fantasysports.yahoo.com';
  const CBS_HOST = /(?:^|\.)football\.cbssports\.com$/;
  const SITE_ORIGINS = ['https://lineupbeat.com', 'https://www.lineupbeat.com',
    'https://lineupbeat-dev.pages.dev'];
  const MY_TEAM_ORIGIN = location.origin === 'https://lineupbeat-dev.pages.dev'
    ? location.origin : 'https://lineupbeat.com';
  const MY_TEAM_PATH = '/my-team/';
  const MY_TEAM_URL = `${MY_TEAM_ORIGIN}${MY_TEAM_PATH}`;
  const HISTORY_PATH = '/league-history/';
  const HISTORY_URL = `${MY_TEAM_ORIGIN}${HISTORY_PATH}`;
  const PRIVACY_URL = `${MY_TEAM_URL}extension/privacy/`;

  function onExpectedPage(origin, path) {
    return location.origin === origin && location.pathname.startsWith(path);
  }

  function queryValue(key) {
    return new URL(location.href).searchParams.get(key) || '';
  }

  function providerConfig() {
    if (onExpectedPage(ESPN_ORIGIN, ESPN_PATH)) return {
      id:'espn', label:'ESPN', parser:globalThis.LineupBeatEspnRosterParser,
      leagueId:queryValue('leagueId') || 'unknown', teamId:queryValue('teamId') || 'unknown',
      season:Number(queryValue('seasonId') || new Date().getFullYear()), history:true
    };
    if (location.origin === YAHOO_ORIGIN && location.pathname.startsWith('/f1/')) {
      const ids=location.pathname.match(/^\/f1\/(\d+)(?:\/(\d+))?/);
      return {id:'yahoo',label:'Yahoo',parser:globalThis.LineupBeatYahooRosterParser,
        leagueId:ids&&ids[1]||'unknown',teamId:ids&&ids[2]||'unknown',
        season:Number(queryValue('season') || new Date().getFullYear()),history:false};
    }
    if (CBS_HOST.test(location.hostname) ||
        (location.hostname === 'www.cbssports.com' && location.pathname.startsWith('/fantasy/football/'))) {
      const cbsLeagueId=queryValue('leagueId')||queryValue('league_id')||
        `${location.hostname}:${location.pathname.split('/').filter(Boolean).slice(0,3).join('/')}`;
      return {id:'cbs',label:'CBS',parser:globalThis.LineupBeatCbsRosterParser,
        leagueId:cbsLeagueId||'cbs-league',teamId:queryValue('teamId')||'my-team',
        season:Number(queryValue('season')||new Date().getFullYear()),history:true};
    }
    return null;
  }

  function pageLabels(config) {
    if (config.id === 'yahoo') {
      const title=String(document.title||'').split('|')[0].trim(),parts=title.split(/\s+-\s+/);
      return {leagueName:parts[0]||'Yahoo league',teamName:parts[1]||'My Yahoo team'};
    }
    return globalThis.LineupBeatSafeDiagnostics.pageLabels(document, config.leagueId, config.teamId);
  }

  function capture(config, receptionPoints) {
    const parser = config.parser;
    if (!parser) throw new Error(`The ${config.label} roster parser did not load. Reload the extension and try again.`);
    const roster = parser.requireRoster(document);
    const labels = pageLabels(config);
    return {
      provider: config.id,
      connectionType: 'browser_extension',
      league: {
        id: config.leagueId,
        name: labels.leagueName,
        season: config.season,
        scoringSettings: {receptionPoints: Number(receptionPoints)}
      },
      team: {
        id: config.teamId,
        name: labels.teamName
      },
      roster
    };
  }

  function stylePanel(panel, select, buttons) {
    Object.assign(panel.style, {
      position: 'fixed', right: '18px', bottom: '18px', zIndex: '2147483647',
      width: 'min(380px, calc(100vw - 36px))', padding: '14px', borderRadius: '8px',
      background: '#0b100f', color: '#f7f5ef', boxShadow: '0 8px 30px rgba(0,0,0,.4)',
      font: '14px/1.35 Arial,sans-serif'
    });
    Object.assign(select.style, {padding: '10px', background: '#fff', color: '#0b100f'});
    for (const node of buttons) {
      Object.assign(node.style, {
        display: 'inline-block', padding: '12px 16px', border: '0', borderRadius: '4px',
        background: '#c6f53c', color: '#0b100f', fontWeight: '800', cursor: 'pointer',
        textDecoration: 'none'
      });
    }
  }

  function installProviderCapture(config) {
    const panel = document.createElement('section');
    const heading = document.createElement('strong');
    const disclosure = document.createElement('p');
    const privacy = document.createElement('a');
    const controls = document.createElement('div');
    const select = document.createElement('select');
    const save = document.createElement('button');
    const open = document.createElement('a');
    const history = document.createElement('button');
    const openHistory = document.createElement('a');
    const diagnostics = document.createElement('button');
    const status = document.createElement('p');

    heading.textContent = `Lineup Beat ${config.label} Connector`;
    disclosure.textContent = config.history
      ? (config.id === 'cbs'
        ? 'Save your roster or add the CBS history season visible on this page. Repeat for each season; data stays in this browser.'
        : 'Save your roster or import league history. Data stays in this browser for review; passwords and session values are never read or stored.')
      : 'Save the visible roster for My Team. Data stays in this browser; passwords, cookies, and session values are never read or stored.';
    privacy.href = PRIVACY_URL;
    privacy.target = '_blank';
    privacy.rel = 'noopener';
    privacy.textContent = 'Privacy details';
    privacy.style.color = '#c6f53c';
    select.setAttribute('aria-label', 'Reception scoring');
    select.innerHTML = '<option value="">Choose scoring</option><option value="1">PPR</option><option value="0.5">Half-PPR</option><option value="0">Non-PPR</option>';
    save.type = 'button';
    save.textContent = 'Save roster locally for My Team';
    save.setAttribute('aria-label', `Save visible ${config.label} roster locally for My Team`);
    open.href = MY_TEAM_URL;
    open.target = '_blank';
    open.rel = 'noopener';
    open.textContent = 'Open My Team';
    open.hidden = true;
    history.type = 'button';
    history.textContent = config.id === 'cbs' ? 'Add this history season' : 'Import league history';
    history.setAttribute('aria-label', `Import ${config.label} league history locally for commissioner review`);
    history.hidden = !config.history;
    openHistory.href = HISTORY_URL;
    openHistory.target = '_blank';
    openHistory.rel = 'noopener';
    openHistory.textContent = 'Review history';
    openHistory.hidden = true;
    diagnostics.type = 'button';
    diagnostics.textContent = 'Copy safe diagnostics';
    diagnostics.setAttribute('aria-label', 'Copy safe roster-discovery diagnostics');
    status.setAttribute('role', 'status');
    status.style.margin = '10px 0 0';
    controls.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;margin-top:12px';
    disclosure.style.margin = '8px 0 4px';

    stylePanel(panel, select, [save, open, history, openHistory, diagnostics]);
    const diagnosticController = globalThis.LineupBeatSafeDiagnostics.install({
      button: diagnostics,
      status,
      document,
      location,
      version: chrome.runtime.getManifest().version,
      playerSelector: config.parser.PLAYER_SELECTOR,
      inspectRoster: config.parser.inspect,
      clipboard: navigator.clipboard
    });
    save.addEventListener('click', () => {
      diagnosticController.hide();
      if (select.value === '') {
        status.textContent = 'Choose scoring before saving.';
        return;
      }
      try {
        const payload = capture(config, select.value);
        chrome.runtime.sendMessage(
          {type: 'LB_CAPTURE_ROSTER', version: 1, provider:config.id, payload},
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
        if (error.message === config.parser.EMPTY_ERROR ||
            error.message === config.parser.AMBIGUOUS_ERROR) {
          diagnosticController.show();
        }
      }
    });

    history.addEventListener('click', () => {
      if (config.id === 'cbs') {
        diagnosticController.hide();
        history.disabled = true;
        status.textContent = 'Reading the visible CBS history season…';
        try {
          const parser = globalThis.LineupBeatCbsHistoryParser;
          const season = parser.fromDocument(document, {leagueId: config.leagueId});
          const snapshot = {
            year: season.year, leagueId: config.leagueId, leagueName: season.leagueName,
            regularSeasonWeeks: season.regularSeasonWeeks, complete: season.complete,
            teams: season.teams, matchups: season.matchups
          };
          chrome.runtime.sendMessage({
            type: 'LB_CAPTURE_CBS_HISTORY', version: 1, leagueId: config.leagueId, snapshot
          }, response => {
            history.disabled = false;
            if (!response || !response.ok) {
              status.textContent = response && response.error || parser.EMPTY_ERROR;
              return;
            }
            openHistory.hidden = false;
            const count = response.counts && response.counts.seasons || 0;
            status.textContent = `CBS season saved. ${count} season${count === 1 ? '' : 's'} now in the local archive.`;
          });
        } catch (error) {
          history.disabled = false;
          status.textContent = error.message;
        }
        return;
      }
      const leagueId = queryValue('leagueId');
      const season = Number(queryValue('seasonId') || new Date().getFullYear());
      if (!/^\d+$/.test(leagueId)) {
        status.textContent = 'Open an ESPN league page before importing history.';
        return;
      }
      diagnosticController.hide();
      history.disabled = true;
      status.textContent = 'Importing available seasons from ESPN…';
      chrome.runtime.sendMessage({
        type: 'LB_CAPTURE_ESPN_HISTORY', version: 1, leagueId, season
      }, response => {
        history.disabled = false;
        if (!response || !response.ok) {
          status.textContent = response && response.error === 'espn_session_required'
            ? 'ESPN did not authorize the import. Sign in to ESPN, reload, and try again.'
            : 'League history could not be imported. Try again from this league page.';
          return;
        }
        openHistory.hidden = false;
        const count = response.counts && response.counts.seasons || 0;
        status.textContent = `${count} season${count === 1 ? '' : 's'} saved locally for review.`;
      });
    });

    controls.append(select, save, open, history, openHistory, diagnostics);
    panel.append(heading, disclosure, privacy, controls, status);
    document.documentElement.appendChild(panel);
  }

  function postRoster(response) {
    if (response && response.payload) {
      window.postMessage({
        type: 'LB_MY_TEAM_ROSTER', version: 1, payload: response.payload
      }, location.origin);
    }
  }

  function ready() {
    chrome.runtime.sendMessage({type: 'LB_GET_ROSTER', version: 1}, response => {
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
        chrome.runtime.sendMessage({type: 'LB_GET_ROSTER', version: 1}, postRoster);
      }
      if (event.data.type === 'LB_MY_TEAM_REVIEW_DEMO_REQUEST') {
        chrome.runtime.sendMessage({
          type: 'LB_SAVE_REVIEW_DEMO_ROSTER', version: 1, payload: event.data.payload
        }, response => {
          if (response && response.ok) postRoster({payload: event.data.payload});
        });
      }
      if (event.data.type === 'LB_MY_TEAM_CLEAR_REQUEST') {
        chrome.runtime.sendMessage({type: 'LB_CLEAR_ROSTER', version: 1}, response => {
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

  function postHistory(response) {
    if (response && response.record) {
      window.postMessage({
        type: 'LB_LEAGUE_HISTORY_CAPTURE', version: 1,
        payload: response.record.payload, review: response.record.review || null
      }, location.origin);
    }
  }

  function requestHistory(provider, callback) {
    chrome.runtime.sendMessage({type: 'LB_GET_HISTORY', version: 1, provider}, callback);
  }

  function historyReady() {
    requestHistory(null, response => {
      window.postMessage({
        type: 'LB_LEAGUE_HISTORY_EXTENSION_READY', version: 1,
        provider: response && response.provider,
        hasHistory: Boolean(response && response.record)
      }, location.origin);
      postHistory(response);
    });
  }

  function installHistoryBridge() {
    window.addEventListener('message', event => {
      if (event.source !== window || event.origin !== location.origin ||
          !event.data || event.data.version !== 1) return;
      if (event.data.type === 'LB_LEAGUE_HISTORY_CONNECT_REQUEST') {
        requestHistory(event.data.provider, postHistory);
      }
      if (event.data.type === 'LB_LEAGUE_HISTORY_SAVE_REVIEW_REQUEST') {
        chrome.runtime.sendMessage({
          type: 'LB_SAVE_HISTORY_REVIEW', version: 1, provider:event.data.provider,
          review: event.data.review
        }, response => {
          window.postMessage({
            type: 'LB_LEAGUE_HISTORY_REVIEW_COMPLETE', version: 1,
            ok: Boolean(response && response.ok)
          }, location.origin);
        });
      }
      if (event.data.type === 'LB_LEAGUE_HISTORY_CLEAR_REQUEST') {
        chrome.runtime.sendMessage({type: 'LB_CLEAR_HISTORY', version: 1,
          provider:event.data.provider}, response => {
          if (response && response.ok) {
            window.postMessage({type: 'LB_LEAGUE_HISTORY_CLEAR_COMPLETE', version: 1}, location.origin);
          }
        });
      }
    });
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', historyReady, {once: true});
    } else {
      historyReady();
    }
  }

  const config=providerConfig();
  if (config) installProviderCapture(config);
  if (SITE_ORIGINS.includes(location.origin) && location.pathname.startsWith(MY_TEAM_PATH)) installMyTeamBridge();
  if (SITE_ORIGINS.includes(location.origin) && location.pathname.startsWith(HISTORY_PATH)) installHistoryBridge();
})();
