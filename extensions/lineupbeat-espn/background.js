const KEY = 'lineupBeatEspnRosterV1';
const ESPN_ORIGIN = 'https://fantasy.espn.com';
const ESPN_PATH = '/football/';
const MY_TEAM_ORIGIN = 'https://lineupbeat-dev.pages.dev';
const MY_TEAM_PATH = '/my-team/';
const MY_TEAM_URL = `${MY_TEAM_ORIGIN}${MY_TEAM_PATH}`;

function senderMatches(sender, origin, path) {
  try {
    const url = new URL((sender && sender.url) || '');
    return url.origin === origin && url.pathname.startsWith(path);
  } catch (_error) {
    return false;
  }
}

function reject(sendResponse) {
  sendResponse({ok: false, error: 'unexpected_sender'});
  return false;
}

async function saveRoster(payload, openMyTeam) {
  await chrome.storage.local.set({[KEY]: payload});
  if (!openMyTeam) return {ok: true, opened: false};
  try {
    await chrome.tabs.create({url: MY_TEAM_URL});
    return {ok: true, opened: true};
  } catch (_error) {
    return {ok: true, opened: false};
  }
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
    if (!senderMatches(sender, MY_TEAM_ORIGIN, MY_TEAM_PATH)) return reject(sendResponse);
    saveRoster(message.payload, false)
      .then(sendResponse)
      .catch(() => sendResponse({ok: false, error: 'local_storage_failed'}));
    return true;
  }

  if (message.type === 'LB_GET_ESPN_ROSTER') {
    if (!senderMatches(sender, MY_TEAM_ORIGIN, MY_TEAM_PATH)) return reject(sendResponse);
    chrome.storage.local.get(KEY)
      .then(result => sendResponse({ok: true, payload: result[KEY] || null}))
      .catch(() => sendResponse({ok: false, error: 'local_storage_failed'}));
    return true;
  }

  if (message.type === 'LB_CLEAR_ESPN_ROSTER') {
    if (!senderMatches(sender, MY_TEAM_ORIGIN, MY_TEAM_PATH)) return reject(sendResponse);
    chrome.storage.local.remove(KEY)
      .then(() => sendResponse({ok: true}))
      .catch(() => sendResponse({ok: false, error: 'local_storage_failed'}));
    return true;
  }
});
