(function () {
  'use strict';

  const STORAGE_KEY = 'lineupBeatYahooHistoryV1';
  const status = document.getElementById('import-status');
  const connect = document.getElementById('connect-yahoo');
  const disconnect = document.getElementById('disconnect-yahoo');
  const picker = document.getElementById('yahoo-picker');
  const select = document.getElementById('yahoo-family');
  const start = document.getElementById('import-yahoo-history');
  const progress = document.getElementById('yahoo-progress');
  const sourceTabs = Array.from(document.querySelectorAll('[data-history-source]'));
  const sourcePanels = Array.from(document.querySelectorAll('[data-source-panel]'));
  let families = [];

  function say(message) {
    if (status) status.textContent = message;
  }

  function setSource(provider) {
    sourceTabs.forEach(button => {
      const active = button.dataset.historySource === provider;
      button.setAttribute('aria-selected', String(active));
    });
    sourcePanels.forEach(panel => {
      panel.hidden = panel.dataset.sourcePanel !== provider;
    });
  }

  function storedRecord() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
      return value && value.payload ? value : null;
    } catch (_) {
      return null;
    }
  }

  function storeRecord(record) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(record));
      return true;
    } catch (_) {
      return false;
    }
  }

  function postRecord(record) {
    window.postMessage({
      type: 'LB_LEAGUE_HISTORY_CAPTURE',
      version: 1,
      payload: record.payload,
      review: record.review || null
    }, location.origin);
  }

  function normalizeName(value) {
    return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }

  function combine(records, family) {
    const seasons = records.map(row => row.season).sort((a, b) => a.year - b.year);
    const identities = new Map();
    records.forEach(record => {
      record.identities.forEach(identity => {
        const current = identities.get(identity.identityId) || {
          identityId: identity.identityId,
          displayName: identity.displayName,
          seasons: [],
          teamNames: []
        };
        record.season.teams.filter(team => team.ownerIds.includes(identity.identityId))
          .forEach(team => {
            if (!current.seasons.includes(record.season.year)) current.seasons.push(record.season.year);
            if (!current.teamNames.includes(team.teamName)) current.teamNames.push(team.teamName);
          });
        identities.set(identity.identityId, current);
      });
    });
    const identityRows = [...identities.values()].sort((a, b) =>
      Math.min.apply(null, a.seasons) - Math.min.apply(null, b.seasons) ||
      a.displayName.localeCompare(b.displayName));
    const suggestions = [];
    for (let left = 0; left < identityRows.length; left += 1) {
      for (let right = left + 1; right < identityRows.length; right += 1) {
        const a = identityRows[left];
        const b = identityRows[right];
        if (normalizeName(a.displayName) && normalizeName(a.displayName) === normalizeName(b.displayName)) {
          suggestions.push({a: a.identityId, b: b.identityId, reason: 'same Yahoo manager name'});
        }
      }
    }
    return {
      schemaVersion: 'lineupbeat-history-capture-v1',
      provider: 'yahoo',
      connectionType: 'oauth2',
      capturedAt: new Date().toISOString(),
      league: {id: family.id, name: family.name},
      seasons,
      incomplete: [],
      identityReview: {identities: identityRows, suggestions},
      counts: {
        seasons: seasons.length,
        teams: Math.max.apply(null, seasons.map(row => row.teams.length)),
        matchups: seasons.reduce((total, row) => total + row.matchups.length, 0),
        identities: identityRows.length
      }
    };
  }

  async function responseJson(url, options) {
    const response = await fetch(url, options);
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(value.error || 'Yahoo connection is temporarily unavailable.');
    return value;
  }

  function showFamilies(value) {
    families = value;
    select.replaceChildren();
    value.forEach((family, index) => {
      const option = document.createElement('option');
      option.value = String(index);
      const years = family.firstSeason === family.lastSeason ? String(family.lastSeason) :
        family.firstSeason + '–' + family.lastSeason;
      option.textContent = family.name + ' · ' + years + ' · ' + family.seasons.length +
        ' season' + (family.seasons.length === 1 ? '' : 's');
      select.appendChild(option);
    });
    picker.hidden = false;
    connect.hidden = true;
    disconnect.hidden = false;
    say('Choose the Yahoo league history you want to import.');
  }

  async function loadFamilies() {
    say('Loading your Yahoo Fantasy Football leagues…');
    const value = await responseJson('/api/yahoo/leagues');
    showFamilies(value.families || []);
  }

  async function importHistory() {
    const family = families[Number(select.value)];
    if (!family || !family.seasons.length) return;
    start.disabled = true;
    select.disabled = true;
    progress.hidden = false;
    const records = [];
    try {
      for (let index = 0; index < family.seasons.length; index += 1) {
        const season = family.seasons[index];
        const message = 'Importing ' + season.season + ' · ' + (index + 1) +
          ' of ' + family.seasons.length;
        progress.textContent = message;
        say(message + '…');
        records.push(await responseJson('/api/yahoo/season?league_key=' +
          encodeURIComponent(season.leagueKey)));
      }
      const payload = combine(records, family);
      const record = {payload, review: null};
      if (!storeRecord(record)) throw new Error('This browser could not save the Yahoo archive.');
      postRecord(record);
      history.replaceState(null, '', '/league-history/');
      say(payload.counts.seasons + ' Yahoo seasons imported. Match any duplicate managers to continue.');
    } catch (error) {
      progress.textContent = 'Nothing was saved.';
      say(error && error.message ? error.message : 'Yahoo history could not be imported.');
    } finally {
      start.disabled = false;
      select.disabled = false;
    }
  }

  async function initialize() {
    const shared = new URLSearchParams(location.search).has('league');
    if (!shared) {
      const saved = storedRecord();
      if (saved) postRecord(saved);
    }
    try {
      const value = await responseJson('/api/yahoo/status');
      if (!value.configured) {
        connect.disabled = true;
        connect.textContent = 'Yahoo setup pending';
        return;
      }
      if (value.connected || new URLSearchParams(location.search).get('yahoo') === 'connected') {
        setSource('yahoo');
        await loadFamilies();
      }
    } catch (_) {
      connect.disabled = true;
      connect.textContent = 'Yahoo unavailable';
    }
  }

  sourceTabs.forEach(button => button.addEventListener('click', () =>
    setSource(button.dataset.historySource)));
  if (connect) connect.addEventListener('click', () => {
    location.href = '/api/yahoo/connect';
  });
  if (disconnect) disconnect.addEventListener('click', async () => {
    disconnect.disabled = true;
    try {
      await responseJson('/api/yahoo/disconnect', {method: 'POST'});
      picker.hidden = true;
      connect.hidden = false;
      connect.disabled = false;
      disconnect.hidden = true;
      say('Yahoo disconnected. Your imported archive remains on this device.');
    } catch (error) {
      say(error.message);
    } finally {
      disconnect.disabled = false;
    }
  });
  if (start) start.addEventListener('click', importHistory);
  initialize();
}());
