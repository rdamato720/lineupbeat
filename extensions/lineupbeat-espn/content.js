(function () {
  'use strict';

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

  function stylePanel(panel, select, save, open) {
    Object.assign(panel.style, {
      position: 'fixed', right: '18px', bottom: '18px', zIndex: '2147483647',
      width: 'min(380px, calc(100vw - 36px))', padding: '14px', borderRadius: '8px',
      background: '#0b100f', color: '#f7f5ef', boxShadow: '0 8px 30px rgba(0,0,0,.4)',
      font: '14px/1.35 Arial,sans-serif'
    });
    Object.assign(select.style, {padding: '10px', background: '#fff', color: '#0b100f'});
    for (const node of [save, open]) {
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
    status.setAttribute('role', 'status');
    status.style.margin = '10px 0 0';
    controls.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;margin-top:12px';
    disclosure.style.margin = '8px 0 4px';

    stylePanel(panel, select, save, open);
    save.addEventListener('click', () => {
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
      }
    });

    controls.append(select, save, open);
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
