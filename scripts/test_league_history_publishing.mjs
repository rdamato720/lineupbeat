import assert from 'node:assert/strict';

import {
  createManageToken,
  createSlug,
  hashToken,
  onRequestGet,
  onRequestPost,
  onRequestPut,
  sanitizePublication,
  sharedLeagueRedirect,
  slugBase
} from '../functions/_shared/league-history-api.mjs';

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
      homeScore: 111.25, awayScore: 103.5}]
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
assert.equal(publication.archive.schemaVersion, 'lineupbeat-public-history-v1');
assert.equal(publication.review.schemaVersion, 'lineupbeat-public-history-review-v1');
assert.deepEqual(publication.archive.counts, {seasons: 1, teams: 2, matchups: 1, identities: 2});
assert.deepEqual(publication.archive.identityReview.identities.map(row => row.identityId), ['m1', 'm2']);
assert.deepEqual(publication.archive.seasons[0].teams.map(row => row.teamId), ['s1t1', 's1t2']);
assert.equal(publication.review.identities[1].mergeInto, 'm1');

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
  constructor() { this.rows = new Map(); }
  prepare(sql) {
    const db = this;
    return {
      values: [],
      bind(...values) { this.values = values; return this; },
      async first() {
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
        if (sql.includes('INSERT INTO')) {
          const [slug, league_name, visibility, archive_json, manage_token_hash,
            created_at, updated_at] = this.values;
          db.rows.set(slug, {slug, league_name, visibility, archive_json,
            manage_token_hash, created_at, updated_at});
        } else if (sql.includes('UPDATE league_history_publications')) {
          const [league_name, visibility, archive_json, updated_at, slug] = this.values;
          Object.assign(db.rows.get(slug), {league_name, visibility, archive_json, updated_at});
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
assert(!db.rows.values().next().value.archive_json.includes('987654'));

const read = await onRequestGet(context(new Request(
  origin + '/api/leagues/' + created.slug)));
assert.equal(read.status, 200);
const shared = await read.json();
assert.equal(shared.archive.league.name, 'BG-N-Co.');
assert(!('manageToken' in shared));

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

const crossOrigin = await onRequestPost(context(new Request(origin + '/api/leagues', {
  method: 'POST',
  headers: {'Content-Type': 'application/json', Origin: 'https://attacker.example'},
  body: JSON.stringify({visibility: 'unlisted', archive, review})
})));
assert.equal(crossOrigin.status, 403);

console.log('league history publishing privacy, tokens and routing passed');
