'use strict';

const assert = require('assert');
const parser = require('../extensions/lineupbeat-espn/cbs-history-parser.js');

const season = parser.parseSnapshot({
  year: 2025,
  leagueId: 'fixture-league',
  leagueName: 'Fixture League',
  regularSeasonWeeks: 14,
  teams: [
    {teamName: 'Alpha Club', ownerName: 'Alex Example', wins: 10, losses: 4, pointsFor: 1601.2, finalStanding: 1},
    {teamName: 'Beta Club', ownerName: 'Blake Sample', wins: 8, losses: 6, pointsFor: 1510.4, finalStanding: 2}
  ],
  matchups: [
    {week: 1, homeTeamName: 'Alpha Club', awayTeamName: 'Beta Club', homeScore: 121.4, awayScore: 110.2}
  ]
});

assert.equal(season.year, 2025);
assert.equal(season.teams.length, 2);
assert.equal(season.matchups.length, 1);
assert.equal(season.matchups[0].homeTeamId, 'cbs:alpha club');
assert.equal(season.matchups[0].awayScore, 110.2);
assert.deepEqual(season.source, {leagueId: 'fixture-league'});

const first = parser.combine(null, season, 'fixture-league', '2026-09-04T00:00:00.000Z');
assert.equal(first.provider, 'cbs');
assert.equal(first.counts.seasons, 1);
assert.equal(first.counts.identities, 2);

const earlier = parser.parseSnapshot({
  year: 2024,
  leagueName: 'Fixture League',
  teams: [
    {teamName: 'Old Alpha Name', ownerName: 'Alex Example'},
    {teamName: 'Beta Club', ownerName: 'Blake Sample'}
  ],
  matchups: [
    {week: 1, homeTeamName: 'Old Alpha Name', awayTeamName: 'Beta Club', homeScore: 99, awayScore: 101}
  ]
});
const combined = parser.combine(first, earlier, 'fixture-league', '2026-09-04T00:00:00.000Z');
assert.deepEqual(combined.seasons.map(row => row.year), [2024, 2025]);
assert.equal(combined.counts.matchups, 2);
assert.equal(combined.identityReview.identities.find(row => row.displayName === 'Alex Example').teamNames.length, 2);
assert.throws(() => parser.parseSnapshot({year: 2025, teams: [], matchups: []}),
  new RegExp(parser.EMPTY_ERROR.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));

const largeTeams = Array.from({length: 32}, (_, index) => ({
  teamName: `Team ${index + 1}`, ownerName: `Manager ${index + 1}`,
  wins: 7, losses: 7, pointsFor: 1500 + index
}));
function largeSeason(year, scoreOffset = 0) {
  return parser.parseSnapshot({year, leagueId: 'fixture-league', leagueName: 'Large League',
    regularSeasonWeeks: 14, teams: largeTeams,
    matchups: Array.from({length: 120}, (_, gameIndex) => {
      const home = gameIndex % 32;
      const away = (home + 1 + Math.floor(gameIndex / 32)) % 32;
      return {week: gameIndex % 18 + 1, homeTeamName: `Team ${home + 1}`,
        awayTeamName: `Team ${away + 1}`, homeScore: 101.25 + scoreOffset,
        awayScore: 99.75};
    })});
}
let scaleCapture = null;
for (let year = 1995; year <= 2025; year += 1) {
  scaleCapture = parser.combine(scaleCapture, largeSeason(year), 'fixture-league',
    '2026-09-05T00:00:00.000Z');
}
assert.equal(scaleCapture.counts.seasons, parser.MAX_SEASONS);
assert.deepEqual(scaleCapture.seasons.map(row => row.year),
  Array.from({length: 25}, (_, index) => 2001 + index));
assert.equal(scaleCapture.counts.teams, 32);
assert.equal(scaleCapture.counts.matchups, 3000);
assert.equal(scaleCapture.counts.identities, 32);
const refreshed = parser.combine(scaleCapture, largeSeason(2025, 10), 'fixture-league',
  '2026-09-05T01:00:00.000Z');
assert.equal(refreshed.seasons.length, 25);
assert.equal(refreshed.seasons.find(row => row.year === 2025).matchups[0].homeScore, 111.25);
assert.equal(refreshed.counts.matchups, 3000);

console.log('CBS history parser and local multi-season merge tests passed.');
