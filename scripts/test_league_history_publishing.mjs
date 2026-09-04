import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

import {
  createManageToken,
  createSlug,
  hashToken,
  onRequestDelete,
  onRequestGet,
  onRequestOptions,
  onRequestPost,
  onRequestPatch,
  onRequestPut,
  sanitizePublication,
  sharedLeagueRedirect,
  slugBase
} from '../functions/_shared/league-history-api.mjs';

const devWorkflow = readFileSync(new URL('../.github/workflows/dev-site.yml', import.meta.url),
  'utf8');
const productionWorkflow = readFileSync(
  new URL('../.github/workflows/refresh.yml', import.meta.url), 'utf8');
const devConfig = readFileSync(
  new URL('../cloudflare/lineupbeat-dev.wrangler.toml', import.meta.url), 'utf8');
const productionConfig = readFileSync(
  new URL('../cloudflare/lineupbeat-production.wrangler.toml', import.meta.url), 'utf8');
assert(devWorkflow.includes('cp cloudflare/lineupbeat-dev.wrangler.toml wrangler.toml'));
assert(devWorkflow.includes('wrangler@latest pages deploy \\'));
assert(!devWorkflow.includes('wrangler@latest pages deploy site'));
assert(!devWorkflow.includes('--project-name="$DEV_PROJECT"'));
assert(devWorkflow.includes('Verify league publishing storage'));
assert(devWorkflow.includes('/api/leagues/deployment-health-check'));
assert(productionWorkflow.includes(
  'cp cloudflare/lineupbeat-production.wrangler.toml wrangler.toml'));
assert(!productionWorkflow.includes('wrangler@latest pages deploy site'));
assert(productionConfig.includes('name = "lineupbeat"'));
assert(productionConfig.includes('binding = "LEAGUE_HISTORY_DB"'));
assert(!/^database_id\s*=/m.test(productionConfig));
assert(devConfig.includes('name = "lineupbeat-dev"'));
assert(devConfig.includes('database_id ='));
assert.notEqual(devConfig, productionConfig);

const archive = {
  schemaVersion: 'lineupbeat-espn-history-capture-v1',
  capturedAt: '2026-09-04T12:00:00.000Z',
  sourceUrl: 'https://fantasy.espn.com/private/league?leagueId=987654',
  league: {id: '987654', name: 'BG-N-Co.', logo: 'https://example.com/private.png'},
  identityReview: {
    identities: [
      {identityId: 'espn-owner-a', displayName: 'Alex Example', teamNames: ['Old Name']},
      {identityId: 'espn-owner-b', displayName: 'Blake Sample', teamNames: ['New Name']}
    ],
    suggestions: [{a: 'espn-owner-a', b: 'espn-owner-b'}]
  },
  seasons: [{
    year: 2025,
    leagueName: 'BG-N-Co.',
    regularSeasonWeeks: 14,
    complete: true,
    teams: [
      {teamId: 'espn-team-1', teamName: 'Old Name', ownerIds: ['espn-owner-a'],
        wins: 10, losses: 4, ties: 0, pointsFor: 1600.25, pointsAgainst: 1400.5,
        playoffSeed: 1, finalStanding: 1},
      {teamId: 'espn-team-2', teamName: 'New Name', ownerIds: ['espn-owner-b'],
        wins: 4, losses: 10, ties: 0, pointsFor: 1400.5, pointsAgainst: 1600.25,
        playoffSeed: 2, finalStanding: 2}
    ],
    matchups: [{id: 'espn-matchup-1', week: 1, playoff: false,
      homeTeamId: 'espn-team-1', awayTeamId: 'espn-team-2',
      homeScore: 111.25, awayScore: -1.25}]
  }],
  incomplete: [{year: 2024, reason: 'Private league'}],
  counts: {seasons: 99, teams: 99, matchups: 99, identities: 99}
};

const review = {
  schemaVersion: 'lineupbeat-history-identity-review-v1',
  capturedAt: archive.capturedAt,
  approvedAt: '2026-09-04T12:05:00.000Z',
  leagueId: '987654',
  identities: [
    {identityId: 'espn-owner-a', displayName: 'Alex Example', mergeInto: null},
    {identityId: 'espn-owner-b', displayName: 'Blake Sample', mergeInto: 'espn-owner-a'}
  ]
};

const publication = sanitizePublication(archive, review).value;
const yahooArchive = {...structuredClone(archive),
  schemaVersion: 'lineupbeat-history-capture-v1', provider: 'yahoo'};
assert.doesNotThrow(() => sanitizePublication(yahooArchive, review));
assert.equal(publication.archive.schemaVersion, 'lineupbeat-public-history-v1');
assert.equal(publication.review.schemaVersion, 'lineupbeat-public-history-review-v1');
assert.deepEqual(publication.archive.counts, {seasons: 1, teams: 2, matchups: 1, identities: 2});
assert.deepEqual(publication.archive.identityReview.identities.map(row => row.identityId), ['m1', 'm2']);
assert.deepEqual(publication.archive.seasons[0].teams.map(row => row.teamId), ['s1t1', 's1t2']);
assert.equal(publication.archive.seasons[0].matchups[0].awayScore, -1.25);
assert.equal(publication.review.identities[1].mergeInto, 'm1');

const largeArchive = structuredClone(archive);
largeArchive.identityReview.identities = Array.from({length: 32}, (_, index) => ({
  identityId: `owner-${index + 1}`,
  displayName: `Manager ${index + 1}`
}));
largeArchive.seasons = Array.from({length: 50}, (_, seasonIndex) => {
  const year = 2000 + seasonIndex;
  const teams = Array.from({length: 32}, (_, teamIndex) => ({
    teamId: `team-${seasonIndex + 1}-${teamIndex + 1}`,
    teamName: `Team ${teamIndex + 1}`,
    ownerIds: [`owner-${teamIndex + 1}`],
    wins: 7,
    losses: 7,
    ties: 0,
    pointsFor: 1500.25,
    pointsAgainst: 1499.75,
    playoffSeed: teamIndex + 1,
    finalStanding: teamIndex + 1
  }));
  const matchups = Array.from({length: 120}, (_, matchupIndex) => {
    const homeIndex = matchupIndex % 32;
    const awayIndex = (homeIndex + 1 + Math.floor(matchupIndex / 32)) % 32;
    return {
      id: `game-${seasonIndex + 1}-${matchupIndex + 1}`,
      week: (matchupIndex % 18) + 1,
      playoff: matchupIndex >= 96,
      homeTeamId: teams[homeIndex].teamId,
      awayTeamId: teams[awayIndex].teamId,
      homeScore: 101.25,
      awayScore: 99.75
    };
  });
  return {year, leagueName: 'Large League', regularSeasonWeeks: 14,
    complete: true, teams, matchups};
});
largeArchive.incomplete = [];
const largeReview = {
  schemaVersion: 'lineupbeat-history-identity-review-v1',
  identities: largeArchive.identityReview.identities.map(row => ({
    identityId: row.identityId,
    displayName: row.displayName,
    mergeInto: null
  }))
};
const largePublication = sanitizePublication(largeArchive, largeReview);
assert.equal(largePublication.value.archive.counts.seasons, 50);
assert.equal(largePublication.value.archive.counts.teams, 32);
assert.equal(largePublication.value.archive.counts.matchups, 6000);
assert(largePublication.encoded.length < 1_800_000);
const tooManyMatchups = structuredClone(largeArchive);
tooManyMatchups.seasons[0].matchups.push({
  id: 'one-too-many', week: 1, playoff: false,
  homeTeamId: 'team-1-1', awayTeamId: 'team-1-2',
  homeScore: 100, awayScore: 90
});
assert.throws(() => sanitizePublication(tooManyMatchups, largeReview), /too many matchups/);

const encoded = JSON.stringify(publication).toLowerCase();
for (const privateValue of [
  '987654', 'espn-owner-a', 'espn-owner-b', 'espn-team-1', 'espn-matchup-1',
  'sourceurl', 'private.png', 'leagueid', 'approvedat', 'cookie', 'password'
]) assert(!encoded.includes(privateValue), `publication leaked ${privateValue}`);

assert.equal(slugBase(' BG-N-Co. 2026! '), 'bg-n-co-2026');
for (let index = 0; index < 500; index += 1) {
  assert.match(createSlug('BG-N-Co.'), /^bg-n-co-[a-f0-9]{10}$/);
}
const token = createManageToken();
assert(token.length >= 40);
assert.equal(await hashToken(token), await hashToken(token));
assert.notEqual(await hashToken(token), await hashToken(token + 'x'));

const redirect = sharedLeagueRedirect(new Request('https://lineupbeat.dev/leagues/bg-n-co-abc12'));
assert.equal(redirect.status, 302);
assert.equal(redirect.headers.get('location'),
  'https://lineupbeat.dev/league-history/?league=bg-n-co-abc12');
assert.equal(sharedLeagueRedirect(new Request('https://lineupbeat.dev/leagues/bad_slug')).status, 404);

assert.throws(() => sanitizePublication({...archive, schemaVersion: 'old'}, review),
  /not supported/);
assert.throws(() => sanitizePublication(archive, {...review, identities: review.identities.slice(0, 1)}),
  /Every manager/);

class MemoryD1 {
  constructor() { this.rows = new Map(); this.limits = new Map(); this.schemaRuns = 0; }
  prepare(sql) {
    const db = this;
    return {
      values: [],
      bind(...values) { this.values = values; return this; },
      async first() {
        if (sql.includes('INSERT INTO league_history_rate_limits')) {
          const [scope, windowStart, expiresAt] = this.values;
          const current = db.limits.get(scope);
          const requests = current && current.windowStart === windowStart
            ? current.requests + 1 : 1;
          db.limits.set(scope, {windowStart, requests, expiresAt});
          return {requests};
        }
        const slug = this.values[0];
        const row = db.rows.get(slug);
        if (!row) return null;
        if (sql.includes('SELECT manage_token_hash')) {
          return {manage_token_hash: row.manage_token_hash};
        }
        if (sql.includes('SELECT slug FROM')) return {slug: row.slug};
        return {...row};
      },
      async run() {
        if (sql.includes('CREATE TABLE') || sql.includes('CREATE INDEX')) {
          db.schemaRuns += 1;
        } else if (sql.includes('DELETE FROM league_history_rate_limits')) {
          const now = this.values[0];
          for (const [scope, row] of db.limits) {
            if (row.expiresAt < now) db.limits.delete(scope);
          }
        } else if (sql.includes('INSERT INTO')) {
          const [slug, league_name, visibility, archive_json, manage_token_hash,
            created_at, updated_at] = this.values;
          db.rows.set(slug, {slug, league_name, visibility, archive_json,
            manage_token_hash, created_at, updated_at});
        } else if (sql.includes('UPDATE league_history_publications')) {
          const [league_name, visibility, archive_json, updated_at, slug] = this.values;
          Object.assign(db.rows.get(slug), {league_name, visibility, archive_json, updated_at});
        } else if (sql.includes('DELETE FROM league_history_publications')) {
          db.rows.delete(this.values[0]);
        }
        return {success: true};
      }
    };
  }
}

const db = new MemoryD1();
const origin = 'https://lineupbeat.dev';
const context = request => ({request, env: {LEAGUE_HISTORY_DB: db}});
const post = await onRequestPost(context(new Request(origin + '/api/leagues', {
  method: 'POST',
  headers: {'Content-Type': 'application/json', Origin: origin},
  body: JSON.stringify({visibility: 'unlisted', archive, review})
})));
assert.equal(post.status, 201);
const created = await post.json();
assert(created.slug && created.manageToken && created.url.endsWith('/leagues/' + created.slug));
assert.equal(db.rows.size, 1);
assert.equal(db.schemaRuns, 4);
assert(!db.rows.values().next().value.archive_json.includes('987654'));

const read = await onRequestGet(context(new Request(
  origin + '/api/leagues/' + created.slug)));
assert.equal(read.status, 200);
const shared = await read.json();
assert.equal(shared.archive.league.name, 'BG-N-Co.');
assert(!('manageToken' in shared));
assert.equal(read.headers.get('Cache-Control'), 'no-store');

const invalidRecovery = await onRequestPatch(context(new Request(
  origin + '/api/leagues/' + created.slug, {
    method: 'PATCH',
    headers: {Origin: origin, Authorization: 'Bearer wrong'}
  })));
assert.equal(invalidRecovery.status, 403);

const recovery = await onRequestPatch(context(new Request(
  origin + '/api/leagues/' + created.slug, {
    method: 'PATCH',
    headers: {Origin: origin, Authorization: 'Bearer ' + created.manageToken}
  })));
assert.equal(recovery.status, 200);
const recovered = await recovery.json();
assert.deepEqual({slug: recovered.slug, name: recovered.name,
  visibility: recovered.visibility}, {
  slug: created.slug, name: 'BG-N-Co.', visibility: 'unlisted'
});
assert(!('manageToken' in recovered));
assert.equal(recovered.comparison, null);

const recaptured = structuredClone(archive);
recaptured.capturedAt = '2026-09-04T13:00:00.000Z';
const unchangedCheck = await onRequestPatch(context(new Request(
  origin + '/api/leagues/' + created.slug, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json', Origin: origin,
      Authorization: 'Bearer ' + created.manageToken},
    body: JSON.stringify({archive: recaptured, review})
  })));
assert.equal(unchangedCheck.status, 200);
assert.equal((await unchangedCheck.json()).comparison.changed, false);

const corrected = structuredClone(recaptured);
corrected.seasons[0].matchups[0].awayScore = -2.5;
const detailCheck = await onRequestPatch(context(new Request(
  origin + '/api/leagues/' + created.slug, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json', Origin: origin,
      Authorization: 'Bearer ' + created.manageToken},
    body: JSON.stringify({archive: corrected, review})
  })));
const detailComparison = (await detailCheck.json()).comparison;
assert.equal(detailComparison.changed, true);
assert.equal(detailComparison.detailsChanged, true);
assert.equal(detailComparison.counts.matchups.before, 1);
assert.equal(detailComparison.counts.matchups.after, 1);

const expanded = structuredClone(recaptured);
expanded.seasons[0].matchups.push({id: 'espn-matchup-2', week: 2, playoff: false,
  homeTeamId: 'espn-team-2', awayTeamId: 'espn-team-1',
  homeScore: 95.5, awayScore: 101.25});
const countCheck = await onRequestPatch(context(new Request(
  origin + '/api/leagues/' + created.slug, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json', Origin: origin,
      Authorization: 'Bearer ' + created.manageToken},
    body: JSON.stringify({archive: expanded, review})
  })));
const countComparison = (await countCheck.json()).comparison;
assert.equal(countComparison.changed, true);
assert.equal(countComparison.detailsChanged, false);
assert.deepEqual(countComparison.counts.matchups, {before: 1, after: 2});

const forbidden = await onRequestPut(context(new Request(
  origin + '/api/leagues/' + created.slug, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json', Origin: origin,
      Authorization: 'Bearer wrong'},
    body: JSON.stringify({visibility: 'public', archive, review})
  })));
assert.equal(forbidden.status, 403);

const updated = await onRequestPut(context(new Request(
  origin + '/api/leagues/' + created.slug, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json', Origin: origin,
      Authorization: 'Bearer ' + created.manageToken},
    body: JSON.stringify({visibility: 'public', archive, review})
  })));
assert.equal(updated.status, 200);
assert.equal((await updated.json()).slug, created.slug);
assert.equal(db.rows.get(created.slug).visibility, 'public');

const forbiddenDelete = await onRequestDelete(context(new Request(
  origin + '/api/leagues/' + created.slug, {
    method: 'DELETE',
    headers: {Origin: origin, Authorization: 'Bearer wrong'}
  })));
assert.equal(forbiddenDelete.status, 403);
assert.equal(db.rows.size, 1);

const crossOrigin = await onRequestPost(context(new Request(origin + '/api/leagues', {
  method: 'POST',
  headers: {'Content-Type': 'application/json', Origin: 'https://attacker.example'},
  body: JSON.stringify({visibility: 'unlisted', archive, review})
})));
assert.equal(crossOrigin.status, 403);

const deleted = await onRequestDelete(context(new Request(
  origin + '/api/leagues/' + created.slug, {
    method: 'DELETE',
    headers: {Origin: origin, Authorization: 'Bearer ' + created.manageToken}
  })));
assert.equal(deleted.status, 204);
assert.equal(db.rows.size, 0);
const missing = await onRequestGet(context(new Request(
  origin + '/api/leagues/' + created.slug)));
assert.equal(missing.status, 404);

const options = onRequestOptions();
assert.equal(options.headers.get('Allow'), 'GET, POST, PUT, PATCH, DELETE, OPTIONS');

const limitedDb = new MemoryD1();
const limitedContext = request => ({request, env: {LEAGUE_HISTORY_DB: limitedDb}});
let rateLimited;
for (let index = 0; index < 11; index += 1) {
  rateLimited = await onRequestPost(limitedContext(new Request(origin + '/api/leagues', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', Origin: origin,
      'CF-Connecting-IP': '203.0.113.10'},
    body: JSON.stringify({visibility: 'unlisted', archive, review})
  })));
}
assert.equal(rateLimited.status, 429);
assert.match(rateLimited.headers.get('Retry-After'), /^\d+$/);
assert.equal((await rateLimited.json()).error, 'Too many requests. Try again shortly.');

const otherClient = await onRequestPost(limitedContext(new Request(origin + '/api/leagues', {
  method: 'POST',
  headers: {'Content-Type': 'application/json', Origin: origin,
    'CF-Connecting-IP': '203.0.113.11'},
  body: JSON.stringify({visibility: 'unlisted', archive, review})
})));
assert.equal(otherClient.status, 201);

console.log('league history publishing privacy, scale, limits, tokens and routing passed');
