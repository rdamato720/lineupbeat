'use strict';

const assert = require('assert');
require('../league_history/dashboard.js');

const dashboard = globalThis.LineupBeatLeagueHistoryDashboard;
assert(dashboard && typeof dashboard.summarize === 'function');

function team(teamId, teamName, ownerId, finalStanding, pointsFor) {
  return {
    teamId,
    teamName,
    ownerIds: [ownerId],
    finalStanding,
    pointsFor
  };
}

const payload = {
  identityReview: {
    identities: [
      {identityId: 'old-a', displayName: 'Alex Example'},
      {identityId: 'a', displayName: 'Alex Example'},
      {identityId: 'b', displayName: 'Blake Sample'},
      {identityId: 'c', displayName: 'Casey Fixture'}
    ]
  },
  seasons: [
    {
      year: 2024,
      complete: true,
      regularSeasonWeeks: 1,
      teams: [
        team('1', 'Alpha Team', 'old-a', 1, 100),
        team('2', 'Beta Team', 'b', 2, 90)
      ],
      matchups: [{
        id: '2024-1',
        week: 1,
        playoff: false,
        homeTeamId: '1',
        awayTeamId: '2',
        homeScore: 100,
        awayScore: 90
      }]
    },
    {
      year: 2025,
      complete: true,
      regularSeasonWeeks: 1,
      teams: [
        team('1', 'Renamed Team', 'a', 2, 80),
        team('2', 'New Team', 'c', 1, 120)
      ],
      matchups: [{
        id: '2025-1',
        week: 1,
        playoff: false,
        homeTeamId: '1',
        awayTeamId: '2',
        homeScore: 80,
        awayScore: 120
      }]
    }
  ]
};

const review = {
  identities: [
    {identityId: 'old-a', displayName: 'Alex Example', mergeInto: null},
    {identityId: 'a', displayName: 'Alex Example', mergeInto: 'old-a'},
    {identityId: 'b', displayName: 'Blake Sample', mergeInto: null},
    {identityId: 'c', displayName: 'Casey Fixture', mergeInto: null}
  ]
};

const summary = dashboard.summarize(payload, review);
assert.equal(summary.seasons.length, 2);
assert.equal(summary.games.length, 2);
assert.equal(summary.managers.length, 3);
const alex = summary.managers.find(row => row.id === 'old-a');
assert(alex);
assert.equal(alex.wins, 1);
assert.equal(alex.losses, 1);
assert.equal(alex.titles, 1);
assert.equal(alex.seasons.size, 2);
assert.deepEqual(Array.from(alex.aliases), ['Alpha Team', 'Renamed Team']);
assert.equal(summary.seasons[0].champion.team.teamName, 'New Team');
assert.equal(summary.records.highestWeek.score, 120);
assert.equal(summary.records.lowestWeek.score, 80);
assert.equal(summary.records.bestRegularSeason.manager.manager, 'Casey Fixture');
assert.equal(summary.records.bestRegularSeason.season.regWins, 1);
assert.deepEqual(summary.headToHead['old-a']['b'], {
  wins: 1,
  losses: 0,
  ties: 0,
  pointsFor: 100,
  pointsAgainst: 90
});
assert.equal(alex.seasonStats.get(2024).wins, 1);
assert.equal(alex.seasonStats.get(2025).losses, 1);
assert.deepEqual(alex.titleYears, [2024]);
assert.equal(alex.longestWinStreak, 1);

console.log('league history dashboard calculations passed');
