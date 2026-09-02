const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const parser = require('../extensions/lineupbeat-espn/espn-roster-parser.js');

function parseStyle(value) {
  const style = {};
  String(value || '').split(';').forEach(rule => {
    const split = rule.indexOf(':');
    if (split > 0) style[rule.slice(0, split).trim()] = rule.slice(split + 1).trim();
  });
  return style;
}

function selectorPart(value) {
  const match = value.trim().match(/^([a-zA-Z0-9_-]+)?(?:\.([a-zA-Z0-9_-]+))?(?:\[([a-zA-Z0-9_-]+)(\*=|=)?(?:"([^"]*)")?\])?$/);
  if (!match) throw new Error(`Unsupported fixture selector: ${value}`);
  return {tag: match[1], className: match[2], attribute: match[3], operator: match[4], value: match[5]};
}

class FixtureElement {
  constructor(tagName, attributes, ownerDocument) {
    this.nodeType = 1;
    this.tagName = tagName.toUpperCase();
    this.attributes = attributes;
    this.ownerDocument = ownerDocument;
    this.parentElement = null;
    this.children = [];
    this.textNodes = [];
    this.style = parseStyle(attributes.style);
    this.hidden = Object.prototype.hasOwnProperty.call(attributes, 'hidden');
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }
  get href() { return this.getAttribute('href') || ''; }
  get textContent() {
    const parts = [...this.textNodes, ...this.children.map(child => child.textContent)];
    return parts.join(' ').replace(/\s+/g, ' ').trim();
  }
  get innerText() { return this.textContent; }
  matches(selector) {
    return selector.split(',').some(raw => {
      const part = selectorPart(raw);
      if (part.tag && this.tagName !== part.tag.toUpperCase()) return false;
      if (part.className && !String(this.getAttribute('class') || '').split(/\s+/).includes(part.className)) return false;
      if (!part.attribute) return true;
      if (!this.hasAttribute(part.attribute)) return false;
      const actual = String(this.getAttribute(part.attribute));
      if (!part.operator) return true;
      if (part.operator === '=') return actual === part.value;
      return actual.includes(part.value);
    });
  }
  querySelectorAll(selector) {
    const found = [];
    const visit = node => node.children.forEach(child => {
      if (child.matches(selector)) found.push(child);
      visit(child);
    });
    visit(this);
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function parseFixture(html) {
  const document = {
    nodeType: 9,
    documentElement: null,
    defaultView: {getComputedStyle: node => node.style},
    querySelectorAll(selector) { return this.documentElement.querySelectorAll(selector); },
    querySelector(selector) { return this.documentElement.querySelector(selector); },
  };
  const root = new FixtureElement('document-root', {}, document);
  const stack = [root];
  const voidTags = new Set(['meta', 'link', 'img', 'input', 'br', 'hr']);
  const tokens = html.match(/<!--[\s\S]*?-->|<![^>]*>|<[^>]+>|[^<]+/g) || [];
  for (const token of tokens) {
    if (token.startsWith('<!--') || token.startsWith('<!')) continue;
    if (token.startsWith('</')) { stack.pop(); continue; }
    if (token.startsWith('<')) {
      const tag = (token.match(/^<\s*([a-zA-Z0-9-]+)/) || [])[1];
      if (!tag) continue;
      const attributes = {};
      const body = token.replace(/^<\s*[a-zA-Z0-9-]+/, '').replace(/\/?\s*>$/, '');
      const attributePattern = /([a-zA-Z_:][a-zA-Z0-9_:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
      let match;
      while ((match = attributePattern.exec(body))) attributes[match[1]] = match[2] ?? match[3] ?? match[4] ?? '';
      const element = new FixtureElement(tag, attributes, document);
      element.parentElement = stack[stack.length - 1];
      stack[stack.length - 1].children.push(element);
      if (!voidTags.has(tag.toLowerCase()) && !token.endsWith('/>')) stack.push(element);
      continue;
    }
    const value = token.replace(/\s+/g, ' ').trim();
    if (value) stack[stack.length - 1].textNodes.push(value);
  }
  document.documentElement = root.children.find(node => node.tagName === 'HTML') || root;
  return document;
}

function backgroundHarness() {
  let listener;
  const store = {};
  const opened = [];
  const chrome = {
    runtime: {onMessage: {addListener(fn) { listener = fn; }}},
    tabs: {create({url}) { opened.push(url); return Promise.resolve({id: 1, url}); }},
    storage: {local: {
      set(value) { Object.assign(store, value); return Promise.resolve(); },
      get(key) { return Promise.resolve({[key]: store[key]}); },
      remove(key) { delete store[key]; return Promise.resolve(); },
    }},
  };
  vm.runInNewContext(fs.readFileSync('extensions/lineupbeat-espn/background.js', 'utf8'), {chrome, URL});
  const send = (type, url, payload) => new Promise(resolve => listener({type, version: 1, payload}, {url}, resolve));
  return {send, store, opened};
}

async function main() {
  const fixture = parseFixture(fs.readFileSync('scripts/fixtures/espn_roster_table_current.html', 'utf8'));
  assert.equal(fixture.querySelectorAll(parser.PLAYER_SELECTOR).length, 0,
    'the live-shape fixture must contain zero legacy player-selector matches');
  const rosterTable = fixture.querySelector('[aria-label="Sanitized fantasy roster"]');
  assert.equal(rosterTable.querySelectorAll('tr').length, 12);
  assert(rosterTable.querySelectorAll('tr')[1].children.length === 14,
    'the live-shape fixture must retain the observed 14-cell row');
  const splitMetadata = rosterTable.querySelectorAll('.player-column__position');
  assert.equal(splitMetadata.length, 9);
  assert(splitMetadata.every(node => node.children.length === 2),
    'the live-shape fixture must keep team and position in separate sibling nodes');
  const inspection = parser.inspect(fixture);
  const roster = parser.requireRoster(fixture);
  assert.equal(roster.length, 9);
  assert.deepEqual(roster.map(row => row.lineupSlot), ['QB', 'RB', 'WR', 'TE', 'FLEX', 'D/ST', 'K', 'BE', 'BE']);
  assert.deepEqual(roster.map(row => row.position), ['QB', 'RB', 'WR', 'TE', 'WR', 'D/ST', 'K', 'RB', 'WR']);
  assert.equal(roster[0].name, "Sanitized O'Brien-Jones II");
  assert.equal(roster[1].name, 'Sanitized Runner Jr.');
  assert.equal(roster[3].name, 'Sanitized Tight End III');
  assert.equal(roster[4].name, 'Sanitized Flex-Wideout IV');
  assert.deepEqual(roster.map(row => row.team), ['BUF', 'ATL', 'CIN', 'DET', 'LAR', 'SEA', 'TB', 'NO', 'WAS']);
  assert.deepEqual(roster.map(row => row.providerPlayerId),
    ['920001', '920002', '920003', '920004', '920005', '', '', '920008', '920009']);
  assert.equal(inspection.diagnostics.tablesScanned, 2);
  assert.equal(inspection.diagnostics.qualifyingTables, 1);
  assert.equal(inspection.diagnostics.legacyFallbackUsed, false);
  assert.equal(inspection.diagnostics.rejections.invalidSlot, 1);
  assert.equal(inspection.diagnostics.rejections.unsupportedWithoutProviderId, 2);
  for (const ignored of ['700001', '800001', '929991', '929992', '939991']) {
    assert(!roster.some(row => row.providerPlayerId === ignored));
  }

  const untrustedNumeric = parseFixture(`
    <html><body><table><thead><tr><th>SLOT</th><th>PLAYER</th><th>LINK</th></tr></thead><tbody>
      <tr><td>RB</td><td><img src="https://a.espncdn.com/i/teamlogos/nfl/500/950001.png"><div>Sanitized No Id</div><div>BUF RB</div></td><td><a href="/nfl/player/_/id/950002/not-in-player-cell">Game link</a></td></tr>
    </tbody></table></body></html>`);
  const untrustedInspection = parser.inspect(untrustedNumeric);
  assert.deepEqual(untrustedInspection.roster, []);
  assert.equal(untrustedInspection.diagnostics.rejections.missingProviderId, 1,
    'team logos and numeric links outside the mapped player cell cannot provide identity');

  const unboundedSplit = parseFixture(`
    <html><body><table><thead><tr><th>SLOT</th><th>PLAYER</th></tr></thead><tbody>
      <tr><td>QB</td><td><img src="https://a.espncdn.com/i/headshots/nfl/players/full/950003.png"><div>Sanitized Unbounded</div><div>BUF</div><div>QB</div></td></tr>
    </tbody></table></body></html>`);
  const unboundedInspection = parser.inspect(unboundedSplit);
  assert.deepEqual(unboundedInspection.roster, []);
  assert.equal(unboundedInspection.diagnostics.rejections.invalidIdentityText, 1,
    'separate team and position tokens require one exact two-child metadata container');

  const ambiguous = parseFixture(`
    <html><body><table><thead><tr><th>PLAYER</th><th>SLOT</th></tr></thead><tbody>
      <tr><td><img src="https://a.espncdn.com/i/headshots/nfl/players/full/960001.png"><div>Sanitized First</div><div>BUF RB</div></td><td>RB</td></tr>
      <tr><td><img src="https://a.espncdn.com/i/headshots/nfl/players/full/960001.png"><div>Sanitized Conflict</div><div>ATL RB</div></td><td>BE</td></tr>
      <tr><td><img src="https://a.espncdn.com/i/headshots/nfl/players/full/960002.png"><div>Sanitized Duplicate</div><div>CIN WR</div></td><td>WR</td></tr>
      <tr><td><img src="https://a.espncdn.com/i/headshots/nfl/players/full/960003.png"><div>Sanitized Duplicate</div><div>CIN WR</div></td><td>BE</td></tr>
    </tbody></table></body></html>`);
  const ambiguousInspection = parser.inspect(ambiguous);
  assert.deepEqual(ambiguousInspection.roster, []);
  assert.equal(ambiguousInspection.diagnostics.rejections.duplicateOrAmbiguous, 4);
  assert.throws(() => parser.requireRoster(ambiguous), error => error.message === parser.AMBIGUOUS_ERROR);

  const legacyFixture = parseFixture(fs.readFileSync('scripts/fixtures/espn_roster_current.html', 'utf8'));
  const legacyInspection = parser.inspect(legacyFixture);
  assert.equal(legacyInspection.diagnostics.legacyFallbackUsed, true);
  assert.equal(legacyInspection.roster.length, 9);
  assert.equal(legacyInspection.roster[1].name, 'Sanitized Runner Jr.');

  const empty = parseFixture('<html><body><table><tr><th>PLAYER</th></tr><tr><td>No roster here</td></tr></table></body></html>');
  assert.deepEqual(parser.discover(empty), []);
  assert.throws(() => parser.requireRoster(empty), error => error.message === parser.EMPTY_ERROR);

  vm.runInThisContext(fs.readFileSync('my-team/league-adapter.js', 'utf8'));
  vm.runInThisContext(fs.readFileSync('my-team/espn-adapter.js', 'utf8'));
  const raw = {provider: 'espn', connectionType: 'browser_extension', league: {id: 'sanitized', name: 'Sanitized League', season: 2026, scoringSettings: {receptionPoints: 0.5}}, team: {id: '3', name: 'Sanitized Team'}, roster};
  const normalized = LineupBeatEspnAdapter.adapt(raw);
  assert.equal(normalized.roster.starters.length, 7);
  assert.equal(normalized.roster.bench.length, 2);
  assert.equal(normalized.roster.reserve.length, 0);
  assert.equal(normalized.roster.starters.filter(row => row.matchStatus === 'unsupported_position').length, 2);
  assert(normalized.roster.starters.find(row => row.position === 'D/ST').unresolvedReason.includes('not supported'));
  assert(normalized.roster.starters.find(row => row.position === 'K').unresolvedReason.includes('not supported'));
  assert.deepEqual(normalized.startingLineupSlots.find(row => row.slotId === 'FLEX').allowedPositions, ['RB', 'WR', 'TE']);

  const harness = backgroundHarness();
  const capture = await harness.send('LB_CAPTURE_ESPN_ROSTER', 'https://fantasy.espn.com/football/team?leagueId=sanitized&teamId=3', raw);
  assert.equal(capture.ok, true);
  assert.equal(capture.opened, true);
  assert.deepEqual(harness.opened, ['https://lineupbeat-dev.pages.dev/my-team/']);
  const retrieval = await harness.send('LB_GET_ESPN_ROSTER', 'https://lineupbeat-dev.pages.dev/my-team/');
  assert.deepEqual(retrieval.payload, raw);
  assert.equal((await harness.send('LB_CLEAR_ESPN_ROSTER', 'https://lineupbeat-dev.pages.dev/my-team/')).ok, true);
  assert.equal((await harness.send('LB_GET_ESPN_ROSTER', 'https://lineupbeat-dev.pages.dev/my-team/')).payload, null);

  console.log('ESPN structural roster parser tests passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
