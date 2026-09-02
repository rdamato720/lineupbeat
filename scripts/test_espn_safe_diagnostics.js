const assert = require('assert');
const diagnostics = require('../extensions/lineupbeat-espn/content.js');
const parser = require('../extensions/lineupbeat-espn/espn-roster-parser.js');

function selectorMatch(node, selector) {
  return selector.split(',').some(raw => {
    const value = raw.trim();
    if (value === '*') return true;
    if (value.startsWith('.')) return String(node.getAttribute('class') || '')
      .split(/\s+/).includes(value.slice(1));
    const attribute = value.match(/^([a-z]+)?\[([a-zA-Z0-9_-]+)(?:\*="([^"]*)"|="([^"]*)")?\]$/);
    if (attribute) {
      if (attribute[1] && node.tagName !== attribute[1].toUpperCase()) return false;
      if (!node.hasAttribute(attribute[2])) return false;
      const actual = String(node.getAttribute(attribute[2]));
      if (attribute[3] !== undefined) return actual.includes(attribute[3]);
      if (attribute[4] !== undefined) return actual === attribute[4];
      return true;
    }
    return node.tagName === value.toUpperCase();
  });
}

class Element {
  constructor(tag, text = '', attributes = {}) {
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    this.ownText = text;
    this.attributes = attributes;
    this.children = [];
    this.parentElement = null;
    this.ownerDocument = null;
    this.style = {};
    this.hidden = Object.prototype.hasOwnProperty.call(attributes, 'hidden');
  }
  append(...nodes) {
    for (const node of nodes) {
      node.parentElement = this;
      node.ownerDocument = this.ownerDocument;
      this.children.push(node);
      node.walk(child => { child.ownerDocument = this.ownerDocument; });
    }
    return this;
  }
  walk(callback) { for (const child of this.children) { callback(child); child.walk(callback); } }
  get textContent() { return [this.ownText, ...this.children.map(child => child.textContent)].filter(Boolean).join(' '); }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  getAttributeNames() { return Object.keys(this.attributes); }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }
  querySelectorAll(selector) {
    const found = [];
    this.walk(node => { if (selectorMatch(node, selector)) found.push(node); });
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

class FixtureDocument {
  constructor(root) {
    this.documentElement = root;
    this.defaultView = {getComputedStyle: node => node.style};
    root.ownerDocument = this;
    root.walk(node => { node.ownerDocument = this; });
  }
  querySelectorAll(selector) {
    const found = selectorMatch(this.documentElement, selector) ? [this.documentElement] : [];
    return found.concat(this.documentElement.querySelectorAll(selector));
  }
}

function fixture() {
  const html = new Element('html');
  const body = new Element('body', '', {class: 'manager-Ralph private-league-Secret-Champions'});
  const main = new Element('main', '', {class: 'page manager-Ralph', 'data-league-name': 'Secret Champions'});
  const roster = new Element('section', '', {
    class: 'Roster private-team-Blue-Bombers', role: 'table',
    'data-roster-id': '998877', 'data-team-name': 'Blue Bombers'
  });
  const metadata = new Element('nav', '', {class: 'league-team-context'}).append(
    new Element('a', 'BG-N-Co.', {href: '/football/league?leagueId=998877&teamId=3'}),
    new Element('a', 'Some Pulp', {href: '/football/team?leagueId=998877&teamId=3'}),
    new Element('a', 'Some Pulp', {href: '/football/team?leagueId=998877&teamId=3&view=roster'}),
    new Element('a', 'Ralph Manager', {class: 'manager-link', href: '/football/team?leagueId=998877&teamId=3'}),
    new Element('a', 'My Team', {href: '/football/team?leagueId=998877&teamId=3'}),
    new Element('a', 'Team Settings', {href: '/football/team?leagueId=998877&teamId=3&view=settings'}),
    new Element('a', 'Opposing Teams', {href: '/football/team?leagueId=998877&teamId=3&view=opponents'}),
    new Element('a', 'ESPN Fantasy Football', {href: '/football/team?leagueId=998877&teamId=3&view=home'})
  );
  const header = new Element('div', '', {class: 'Table__TR header'}).append(
    new Element('div', 'SLOT', {class: 'Table__TH'}),
    new Element('div', 'PLAYER', {class: 'Table__TH'}),
    new Element('div', 'PRIVATE LEAGUE', {class: 'Table__TH'})
  );
  const row = new Element('div', '', {
    class: 'Table__TR player-Jane-Doe id-12345', role: 'row',
    'data-player-id': '12345678', 'data-private-name': 'Jane Doe'
  });
  const slot = new Element('div', 'QB', {class: 'Table__TD slot', 'data-slot-value': 'QB'});
  const playerCell = new Element('div', 'Jane Doe BUF QB Ralph Blue Bombers', {class: 'Table__TD player'}).append(
    new Element('a', '', {
      href: 'https://www.espn.com/nfl/player/_/id/12345678/jane-doe?leagueId=998877&teamId=3&playerId=12345678'
    }),
    new Element('img', '', {
      src: 'https://a.espncdn.com/i/headshots/nfl/players/full/12345678.png?width=96&token=SecretImageToken'
    })
  );
  row.append(slot, playerCell, new Element('div', '@ NYJ', {class: 'Table__TD'}));
  const rb = new Element('div', '', {class: 'Table__TR'}).append(
    new Element('div', 'RB', {class: 'Table__TD'}),
    new Element('div', 'Another Private Player ATL RB', {class: 'Table__TD'})
  );
  const wr = new Element('div', '', {class: 'Table__TR'}).append(
    new Element('div', 'WR', {class: 'Table__TD'}),
    new Element('div', 'Third Private Player CIN WR', {class: 'Table__TD'})
  );
  const hidden = new Element('div', '', {hidden: ''}).append(new Element('div', 'TE', {class: 'Table__TD'}));
  roster.append(header, row, rb, wr, hidden);
  main.append(metadata, roster, new Element('div', 'Arbitrary private paragraph about the manager and league.'));
  body.append(main);
  html.append(body);
  return new FixtureDocument(html);
}

class Button {
  constructor() { this.hidden = false; this.style = {}; this.listeners = {}; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  click() { return this.listeners.click(); }
}

async function main() {
  const document = fixture();
  const location = {
    origin: 'https://fantasy.espn.com',
    href: 'https://fantasy.espn.com/football/team?leagueId=998877&teamId=3#Jane-Doe',
    pathname: '/football/team',
    search: '?leagueId=998877&teamId=3',
    hash: '#Jane-Doe'
  };
  let networkCalls = 0;
  const originalFetch = global.fetch;
  const originalXmlHttpRequest = global.XMLHttpRequest;
  const originalWebSocket = global.WebSocket;
  const originalNavigator = global.navigator;
  global.fetch = () => { networkCalls += 1; throw new Error('network forbidden'); };
  global.XMLHttpRequest = class {
    constructor() { networkCalls += 1; throw new Error('network forbidden'); }
  };
  global.WebSocket = class {
    constructor() { networkCalls += 1; throw new Error('network forbidden'); }
  };
  Object.defineProperty(global, 'navigator', {
    configurable: true,
    value: {sendBeacon() { networkCalls += 1; throw new Error('network forbidden'); }}
  });
  const payload = diagnostics.generate({
    document, location, version: '0.2.5', playerSelector: parser.PLAYER_SELECTOR,
    rowDiagnostics: {
      tablesScanned: 2, qualifyingTables: 1, rowsScanned: 10, rowsAccepted: 0,
      legacyFallbackUsed: false, legacyRowsAccepted: 0,
      rejections: {missingMappedCells: 0, invalidSlot: 1, invalidIdentityText: 2,
        missingProviderId: 7, unsupportedWithoutProviderId: 2, duplicateOrAmbiguous: 0},
      privatePlayerName: 'Jane Doe', privateAttributeValue: '998877'
    }
  });
  const encoded = JSON.stringify(payload);
  const labels = diagnostics.pageLabels(document, '998877', '3');

  assert.equal(payload.extensionVersion, '0.2.5');
  assert.equal(payload.pathname, '/football/team');
  assert.equal(payload.counts.roleRow, 1);
  assert.equal(payload.counts.roleTable, 1);
  assert.equal(payload.counts.tableClassRow, 4);
  assert.equal(payload.counts.tableClassCell, 8);
  assert.equal(payload.counts.currentPlayerSelector, 2);
  assert.deepEqual(payload.headerLabels, ['SLOT', 'PLAYER']);
  assert.deepEqual(payload.slotCandidates.map(row => row.slot), ['QB', 'RB', 'WR']);
  assert(payload.likelyPlayerAttributes['data-player-id']);
  assert(payload.slotCandidates[0].anchorPatterns.includes('https://www.espn.com/nfl/player/_/id/#/*'));
  assert(payload.slotCandidates[0].imagePatterns.includes('a.espncdn.com/i/headshots/nfl/players/full/#.png'));
  assert.equal(labels.leagueName, 'BG-N-Co.');
  assert.equal(labels.teamName, 'Some Pulp');
  assert.deepEqual(payload.metadataLabels, {
    leagueCandidateCount: 1, leagueConflict: false,
    teamCandidateCount: 1, teamConflict: false
  });
  assert.deepEqual(payload.rowFirst, {
    tablesScanned: 2, qualifyingTables: 1, rowsScanned: 10, rowsAccepted: 0,
    legacyFallbackUsed: false, legacyRowsAccepted: 0,
    rejections: {missingMappedCells: 0, invalidSlot: 1, invalidIdentityText: 2,
      missingProviderId: 7, unsupportedWithoutProviderId: 2, duplicateOrAmbiguous: 0}
  });
  assert.equal(networkCalls, 0);

  for (const forbidden of [
    '?', 'leagueId', 'teamId', 'playerId', '12345678', '998877', 'Jane Doe',
    'Jane-Doe', 'Ralph', 'Blue Bombers', 'Blue-Bombers', 'Secret Champions',
    'Secret-Champions', 'Another Private Player', 'Third Private Player',
    'Arbitrary private paragraph', 'SecretImageToken', '@ NYJ'
  ]) assert(!encoded.includes(forbidden), `diagnostic leaked ${forbidden}`);
  assert(!encoded.includes('data-roster-id\":\"998877'));
  assert(!encoded.includes('data-private-name\":\"Jane Doe'));

  const conflictRoot = new Element('html').append(new Element('body').append(
    new Element('main').append(
      new Element('a', 'League Alpha', {href: '/football/league?leagueId=77'}),
      new Element('a', 'League Beta', {href: '/football/league?leagueId=77'}),
      new Element('a', 'Team Alpha', {href: '/football/team?leagueId=77&teamId=8'}),
      new Element('a', 'Team Alpha', {href: '/football/team?leagueId=77&teamId=8&view=one'}),
      new Element('a', 'Team Beta', {href: '/football/team?leagueId=77&teamId=8&view=two'}),
      new Element('a', 'Team Beta', {href: '/football/team?leagueId=77&teamId=8&view=three'})
    )
  ));
  const conflict = diagnostics.pageLabels(new FixtureDocument(conflictRoot), '77', '8');
  assert.equal(conflict.leagueName, 'ESPN league');
  assert.equal(conflict.teamName, 'My ESPN team');
  assert.deepEqual(conflict.diagnostics, {
    leagueCandidateCount: 2, leagueConflict: true,
    teamCandidateCount: 2, teamConflict: true
  });

  let generated = 0;
  let inspected = 0;
  let copied = 0;
  const button = new Button();
  const status = {textContent: ''};
  const controller = diagnostics.install({
    button, status, document, location, version: '0.2.5', playerSelector: parser.PLAYER_SELECTOR,
    clipboard: {writeText(value) { copied += 1; assert.deepEqual(JSON.parse(value), {safe: true}); return Promise.resolve(); }},
    generateDiagnostics() { generated += 1; return {safe: true}; },
    inspectRoster() { inspected += 1; return {diagnostics: {rowsAccepted: 0}}; }
  });
  assert.equal(button.hidden, true);
  assert.equal(button.style.display, 'none');
  assert.equal(generated, 0);
  assert.equal(inspected, 0);
  assert.equal(copied, 0);
  controller.show();
  assert.equal(button.hidden, false);
  assert.equal(button.style.display, 'inline-block');
  assert.equal(generated, 0);
  assert.equal(inspected, 0);
  assert.equal(copied, 0);
  await button.click();
  assert.equal(generated, 1);
  assert.equal(inspected, 1);
  assert.equal(copied, 1);
  assert.equal(status.textContent, 'Safe diagnostics copied. Paste the JSON into Codex.');
  controller.hide();
  await button.click();
  assert.equal(generated, 1);
  assert.equal(inspected, 1);
  assert.equal(copied, 1);

  const failedButton = new Button();
  const failedStatus = {textContent: ''};
  const failed = diagnostics.install({
    button: failedButton, status: failedStatus, document, location, version: '0.2.5',
    playerSelector: parser.PLAYER_SELECTOR,
    clipboard: {writeText() { return Promise.reject(new Error('denied')); }}
  });
  failed.show();
  await failedButton.click();
  assert.equal(failedStatus.textContent, 'Safe diagnostics could not be copied. Clipboard access was denied.');
  assert.equal(networkCalls, 0);

  if (originalFetch === undefined) delete global.fetch; else global.fetch = originalFetch;
  if (originalXmlHttpRequest === undefined) delete global.XMLHttpRequest;
  else global.XMLHttpRequest = originalXmlHttpRequest;
  if (originalWebSocket === undefined) delete global.WebSocket;
  else global.WebSocket = originalWebSocket;
  if (originalNavigator === undefined) delete global.navigator;
  else Object.defineProperty(global, 'navigator', {configurable: true, value: originalNavigator});

  console.log('ESPN safe diagnostic privacy and explicit-action tests passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
