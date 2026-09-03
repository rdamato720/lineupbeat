const assert = require('assert');
const parser = require('../extensions/lineupbeat-espn/espn-history-parser.js');

function season(year, owners, schedule) {
  return {
    seasonId: year,
    scoringPeriodId: 17,
    status: {previousSeasons: [2022, 2023], currentMatchupPeriod: 18, finalScoringPeriod: 17},
    settings: {name: 'Fixture League', scheduleSettings: {matchupPeriodCount: 14}},
    members: owners.map(owner => ({id: owner.id, firstName: owner.first, lastName: owner.last})),
    teams: owners.map((owner, index) => ({
      id: index + 1, name: `${owner.first} Team`, owners: [owner.id],
      record: {overall: {wins: 8 - index, losses: 6 + index, ties: 0,
        pointsFor: 1500 - index * 10, pointsAgainst: 1400 + index * 10}},
      playoffSeed: index + 1, rankCalculatedFinal: index + 1
    })),
    schedule
  };
}

const owners2023 = [
  {id: 'a', first: 'Mike', last: 'Sample'},
  {id: 'b', first: 'Jordan', last: 'Example'}
];
const owners2022 = [
  {id: 'old-a', first: 'Michael', last: 'Sample'},
  {id: 'b', first: 'Jordan', last: 'Example'}
];
const games = [
  {id: 1, matchupPeriodId: 1, home: {teamId: 1, totalPoints: 111.25}, away: {teamId: 2, totalPoints: 103.5}},
  {id: 2, matchupPeriodId: 2, home: {teamId: 1, totalPoints: 0}, away: {teamId: 2, totalPoints: 0}}
];

assert.deepEqual(parser.discoverYears({status: {previousSeasons: [2000, 2001, 2022]}}, 2023, 2), [2022, 2023]);
assert.equal(parser.normalizeName('Mike Sample'), 'michael sample');
const normalized2022 = parser.normalizeSeason(season(2022, owners2022, games), 1234, 2022);
const normalized2023 = parser.normalizeSeason(season(2023, owners2023, games), 1234, 2023);
assert.equal(normalized2023.matchups.length, 1, 'future zero-score matchup must be skipped');
assert.equal(normalized2023.complete, true);
assert.equal(normalized2023.teams[0].owners[0].displayName, 'Mike Sample');
const capture = parser.combine([normalized2023, normalized2022], [{year: 2021, reason: 'Season unavailable'}], 1234, '2026-09-03T00:00:00.000Z');
assert.equal(capture.schemaVersion, 'lineupbeat-espn-history-capture-v1');
assert.deepEqual(capture.seasons.map(row => row.year), [2022, 2023]);
assert.equal(capture.counts.seasons, 2);
assert.equal(capture.counts.matchups, 2);
assert(capture.identityReview.suggestions.some(row => row.a === 'a' && row.b === 'old-a' || row.a === 'old-a' && row.b === 'a'));
const serialized = JSON.stringify(capture).toLowerCase();
['espn_s2', 'swid', 'cookie', 'token', 'password'].forEach(secret => assert(!serialized.includes(secret)));
console.log('ESPN history parser tests passed');
