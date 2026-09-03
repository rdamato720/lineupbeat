const assert = require('assert');
const fs = require('fs');

const manifest = JSON.parse(fs.readFileSync('extensions/lineupbeat-espn/manifest.json', 'utf8'));
const worker = fs.readFileSync('extensions/lineupbeat-espn/background.js', 'utf8');
const content = fs.readFileSync('extensions/lineupbeat-espn/content.js', 'utf8');

assert.equal(manifest.version, '0.3.0');
assert.deepEqual(manifest.permissions, ['storage']);
assert.deepEqual(manifest.host_permissions, ['https://lm-api-reads.fantasy.espn.com/*']);
assert(manifest.content_scripts.some(row => row.matches.includes('https://lineupbeat-dev.pages.dev/league-history/*')));
assert(manifest.content_scripts[0].js.includes('espn-history-parser.js'));
assert(worker.includes("credentials: 'include'"));
assert(worker.includes("const HISTORY_KEY = 'lineupBeatEspnHistoryV1'"));
assert(worker.includes("parser.MAX_SEASONS"));
assert(worker.includes("LB_SAVE_ESPN_HISTORY_REVIEW"));
assert(worker.includes("senderMatches(sender, MY_TEAM_ORIGIN, HISTORY_PATH)"));
assert(!manifest.permissions.includes('cookies'));
assert(!worker.includes('chrome.cookies'));
assert(content.includes('Import league history'));
assert(content.includes('LB_LEAGUE_HISTORY_CAPTURE'));
assert(content.includes('LB_LEAGUE_HISTORY_SAVE_REVIEW_REQUEST'));
console.log('ESPN history extension tests passed');
