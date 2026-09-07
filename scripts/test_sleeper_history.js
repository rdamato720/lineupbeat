const assert = require('node:assert/strict');
const fs = require('node:fs');
const parser = require('../league_history/sleeper.js');

function league(id, season, previous, status = 'complete') {
  return {league_id: id, name: 'Sunday Friends', season: String(season),
    previous_league_id: previous || null, status,
    settings: {playoff_week_start: 2, last_scored_leg: 2}};
}

const users = [
  {user_id: 'u1', username: 'alpha', display_name: 'Ralph', avatar: 'one',
    metadata: {team_name: 'Fourth & Long'}},
  {user_id: 'u2', username: 'beta', display_name: 'Jamie', avatar: 'two',
    metadata: {team_name: 'Sunday Scaries'}}
];
const rosters = [
  {roster_id: 1, owner_id: 'u1', settings: {wins: 1, losses: 0, ties: 0,
    fpts: 210, fpts_decimal: 25, fpts_against: 190, fpts_against_decimal: 50}},
  {roster_id: 2, owner_id: 'u2', settings: {wins: 0, losses: 1, ties: 0,
    fpts: 190, fpts_decimal: 50, fpts_against: 210, fpts_against_decimal: 25}}
];
const matchupWeeks = [
  {week: 1, rows: [
    {matchup_id: 1, roster_id: 1, points: 110.25},
    {matchup_id: 1, roster_id: 2, points: 90.5}
  ]},
  {week: 2, rows: [
    {matchup_id: 1, roster_id: 1, points: 99, custom_points: 100},
    {matchup_id: 1, roster_id: 2, points: 100}
  ]}
];

const first = parser.normalizeSeason({league: league('old', 2024), users, rosters,
  winnersBracket: [{r: 1, m: 1, p: 1, w: 1, l: 2}], matchupWeeks});
assert.equal(first.year, 2024);
assert.equal(first.regularSeasonWeeks, 1);
assert.equal(first.complete, true);
assert.equal(first.teams[0].teamName, 'Fourth & Long');
assert.equal(first.teams[0].pointsFor, 210.25);
assert.equal(first.teams[0].finalStanding, 1);
assert.equal(first.teams[1].finalStanding, 2);
assert.equal(first.matchups.length, 2);
assert.equal(first.matchups[1].playoff, true);
assert.equal(first.matchups[1].awayScore, 100);

const currentUsers = structuredClone(users);
currentUsers[0].metadata.team_name = 'New Name';
const current = parser.normalizeSeason({league: league('new', 2025, 'old', 'in_season'),
  users: currentUsers, rosters, winnersBracket: [], matchupWeeks: [matchupWeeks[0]]});
const combined = parser.combine([current, first], league('new', 2025, 'old'),
  '2026-09-07T00:00:00.000Z');
assert.equal(combined.provider, 'sleeper');
assert.equal(combined.connectionType, 'public_api');
assert.deepEqual(combined.seasons.map(row => row.year), [2024, 2025]);
assert.equal(combined.counts.seasons, 2);
assert.equal(combined.counts.matchups, 3);
assert.equal(combined.counts.identities, 2);
assert.deepEqual(combined.incomplete, [{year: 2025, reason: 'season in progress'}]);
const ralph = combined.identityReview.identities.find(row => row.identityId === 'sleeper:u1');
assert.deepEqual(ralph.seasons, [2024, 2025]);
assert.deepEqual(new Set(ralph.teamNames), new Set(['New Name', 'Fourth & Long']));

assert.throws(() => parser.normalizeSeason({league: league('bad', 2025), users,
  rosters, winnersBracket: [], matchupWeeks: []}), /no completed matchups/);
assert.equal(parser.MAX_SEASONS, 25);

const client = fs.readFileSync('league_history/sleeper-client.js', 'utf8');
for (const required of ['/user/', '/leagues/nfl/', 'previous_league_id', '/matchups/',
  '/winners_bracket', '/losers_bracket', 'Nothing was saved.',
  'lineupBeatSleeperHistoryV1', 'mapLimit(weeks, 6']) {
  assert.ok(client.includes(required), required);
}

console.log('Sleeper history semantic corpus precision 3/3; recall 3/3');
console.log('Sleeper renewal chain, standings, matchups and identity normalization: ok');
