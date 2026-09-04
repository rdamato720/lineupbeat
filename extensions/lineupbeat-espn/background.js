if (typeof importScripts === 'function') importScripts('espn-history-parser.js');

const ROSTER_KEY = 'lineupBeatEspnRosterV1';
const HISTORY_KEY = 'lineupBeatEspnHistoryV1';
const ESPN_ORIGIN = 'https://fantasy.espn.com';
const ESPN_PATH = '/football/';
const MY_TEAM_ORIGIN = 'https://lineupbeat.com';
const SITE_ORIGINS = [MY_TEAM_ORIGIN, 'https://www.lineupbeat.com',
  'https://lineupbeat-dev.pages.dev'];
const MY_TEAM_PATH = '/my-team/';
const MY_TEAM_URL = `${MY_TEAM_ORIGIN}${MY_TEAM_PATH}`;
const HISTORY_PATH = '/league-history/';
const HISTORY_URL = `${MY_TEAM_ORIGIN}${HISTORY_PATH}`;
const ESPN_API_ORIGIN = 'https://lm-api-reads.fantasy.espn.com';
const ESPN_VIEWS = ['mTeam', 'mRoster', 'mMatchup', 'mSettings', 'mStandings'];

function senderMatches(sender, origins, path) {
  try {
    const url = new URL((sender && sender.url) || '');
    const allowed = Array.isArray(origins) ? origins : [origins];
    return allowed.includes(url.origin) && url.pathname.startsWith(path);
  } catch (_error) {
    return false;
  }
}

function reject(sendResponse) {
  sendResponse({ok: false, error: 'unexpected_sender'});
  return false;
}

async function saveRoster(payload, openMyTeam) {
  await chrome.storage.local.set({[ROSTER_KEY]: payload});
  if (!openMyTeam) return {ok: true, opened: false};
  try {
    await chrome.tabs.create({url: MY_TEAM_URL});
    return {ok: true, opened: true};
  } catch (_error) {
    return {ok: true, opened: false};
  }
}

function positiveInteger(value, label) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < 1) throw new Error(`invalid_${label}`);
  return number;
}

function seasonUrl(leagueId, year, legacy) {
  const path = legacy
    ? `/apis/v3/games/ffl/leagueHistory/${leagueId}`
    : `/apis/v3/games/ffl/seasons/${year}/segments/0/leagues/${leagueId}`;
  const url = new URL(path, ESPN_API_ORIGIN);
  if (legacy) url.searchParams.set('seasonId', String(year));
  ESPN_VIEWS.forEach(view => url.searchParams.append('view', view));
  return url.href;
}

async function requestSeason(leagueId, year, legacy) {
  const response = await fetch(seasonUrl(leagueId, year, legacy), {
    credentials: 'include', headers: {'Accept': 'application/json'}
  });
  if (!response.ok) throw new Error(response.status === 401 || response.status === 403
    ? 'espn_session_required' : `espn_http_${response.status}`);
  const body = await response.json();
  const raw = Array.isArray(body) ? body[0] : body;
  if (!raw || typeof raw !== 'object') throw new Error('espn_empty_response');
  return raw;
}

async function fetchSeason(leagueId, year) {
  const preferredLegacy = year < 2018;
  try {
    return await requestSeason(leagueId, year, preferredLegacy);
  } catch (error) {
    if (error.message !== 'espn_session_required') throw error;
    return requestSeason(leagueId, year, !preferredLegacy);
  }
}

async function captureHistory(message) {
  const parser = globalThis.LineupBeatEspnHistoryParser;
  const leagueId = positiveInteger(message.leagueId, 'league_id');
  const requestedYear = positiveInteger(message.season, 'season');
  if (requestedYear < 1960 || requestedYear > 2200) throw new Error('invalid_season');
  const seed = await fetchSeason(leagueId, requestedYear);
  const years = parser.discoverYears(seed, requestedYear, parser.MAX_SEASONS);
  const seasons = [];
  const incomplete = [];
  for (const year of years) {
    try {
      const raw = year === requestedYear ? seed : await fetchSeason(leagueId, year);
      seasons.push(parser.normalizeSeason(raw, leagueId, year));
    } catch (error) {
      incomplete.push({year, reason: error.message === 'espn_session_required'
        ? 'ESPN session required' : 'Season unavailable'});
    }
  }
  const payload = parser.combine(seasons, incomplete, leagueId);
  const record = {payload, review: null};
  await chrome.storage.local.set({[HISTORY_KEY]: record});
  let opened = false;
  try {
    await chrome.tabs.create({url: HISTORY_URL});
    opened = true;
  } catch (_error) {}
  return {ok: true, opened, counts: payload.counts};
}

function validReview(review, record) {
  if (!(review && review.schemaVersion === 'lineupbeat-history-identity-review-v1' &&
    String(review.leagueId) === String(record.payload.league.id) &&
    review.capturedAt === record.payload.capturedAt && Array.isArray(review.identities))) return false;
  const sourceIds = new Set(record.payload.identityReview.identities.map(row => row.identityId));
  if (review.identities.length !== sourceIds.size || sourceIds.size > 500) return false;
  const seen = new Set();
  for (const identity of review.identities) {
    const id = identity && String(identity.identityId || '');
    const name = identity && String(identity.displayName || '').trim();
    const merge = identity && identity.mergeInto ? String(identity.mergeInto) : '';
    if (!sourceIds.has(id) || seen.has(id) || !name || name.length > 120 ||
        (merge && (!sourceIds.has(merge) || merge === id))) return false;
    seen.add(id);
  }
  return true;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.version !== 1) return;

  if (message.type === 'LB_CAPTURE_ESPN_ROSTER') {
    if (!senderMatches(sender, ESPN_ORIGIN, ESPN_PATH)) return reject(sendResponse);
    saveRoster(message.payload, true)
      .then(sendResponse)
      .catch(() => sendResponse({ok: false, error: 'local_storage_failed'}));
    return true;
  }

  if (message.type === 'LB_SAVE_REVIEW_DEMO_ROSTER') {
    if (!senderMatches(sender, SITE_ORIGINS, MY_TEAM_PATH)) return reject(sendResponse);
    saveRoster(message.payload, false)
      .then(sendResponse)
      .catch(() => sendResponse({ok: false, error: 'local_storage_failed'}));
    return true;
  }

  if (message.type === 'LB_GET_ESPN_ROSTER') {
    if (!senderMatches(sender, SITE_ORIGINS, MY_TEAM_PATH)) return reject(sendResponse);
    chrome.storage.local.get(ROSTER_KEY)
      .then(result => sendResponse({ok: true, payload: result[ROSTER_KEY] || null}))
      .catch(() => sendResponse({ok: false, error: 'local_storage_failed'}));
    return true;
  }

  if (message.type === 'LB_CLEAR_ESPN_ROSTER') {
    if (!senderMatches(sender, SITE_ORIGINS, MY_TEAM_PATH)) return reject(sendResponse);
    chrome.storage.local.remove(ROSTER_KEY)
      .then(() => sendResponse({ok: true}))
      .catch(() => sendResponse({ok: false, error: 'local_storage_failed'}));
    return true;
  }

  if (message.type === 'LB_CAPTURE_ESPN_HISTORY') {
    if (!senderMatches(sender, ESPN_ORIGIN, ESPN_PATH)) return reject(sendResponse);
    captureHistory(message)
      .then(sendResponse)
      .catch(error => sendResponse({ok: false, error: error.message || 'history_capture_failed'}));
    return true;
  }

  if (message.type === 'LB_GET_ESPN_HISTORY') {
    if (!senderMatches(sender, SITE_ORIGINS, HISTORY_PATH)) return reject(sendResponse);
    chrome.storage.local.get(HISTORY_KEY)
      .then(result => sendResponse({ok: true, record: result[HISTORY_KEY] || null}))
      .catch(() => sendResponse({ok: false, error: 'local_storage_failed'}));
    return true;
  }

  if (message.type === 'LB_SAVE_ESPN_HISTORY_REVIEW') {
    if (!senderMatches(sender, SITE_ORIGINS, HISTORY_PATH)) return reject(sendResponse);
    chrome.storage.local.get(HISTORY_KEY).then(async result => {
      const record = result[HISTORY_KEY];
      if (!record || !validReview(message.review, record)) {
        sendResponse({ok: false, error: 'invalid_review'});
        return;
      }
      record.review = message.review;
      await chrome.storage.local.set({[HISTORY_KEY]: record});
      sendResponse({ok: true});
    }).catch(() => sendResponse({ok: false, error: 'local_storage_failed'}));
    return true;
  }

  if (message.type === 'LB_CLEAR_ESPN_HISTORY') {
    if (!senderMatches(sender, SITE_ORIGINS, HISTORY_PATH)) return reject(sendResponse);
    chrome.storage.local.remove(HISTORY_KEY)
      .then(() => sendResponse({ok: true}))
      .catch(() => sendResponse({ok: false, error: 'local_storage_failed'}));
    return true;
  }
});
