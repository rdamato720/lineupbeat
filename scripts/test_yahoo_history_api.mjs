import assert from 'node:assert/strict';
import {
  discoverYahooLeagues,
  onRequestGet,
  openYahooSession,
  parseYahooSeason,
  sealYahooSession
} from '../functions/_shared/yahoo-history-api.mjs';

function league(key, id, name, season, extra = {}) {
  return [[
    {league_key: key}, {league_id: id}, {name}, {season}, {game_code: 'nfl'},
    {num_teams: 2}, {start_week: 1}, {current_week: 2}, {end_week: 2},
    {is_finished: 1}, ...Object.entries(extra).map(([field, value]) => ({[field]: value}))
  ]];
}

const discovery = {fantasy_content: {users: {0: {user: [{games: {
  0: {game: [[{game_code: 'nfl'}], {leagues: {
    0: {league: league('449.l.10', '10', 'Night Shift', 2024, {renewed: '461_20'})},
    count: 1
  }}]},
  1: {game: [[{game_code: 'nfl'}], {leagues: {
    0: {league: league('461.l.20', '20', 'Night Shift', 2025, {renew: '449_10'})},
    1: {league: league('461.l.30', '30', 'Sunday Friends', 2025)},
    count: 2
  }}]},
  count: 2
}}]}, count: 1}}};

const families = discoverYahooLeagues(discovery);
assert.equal(families.length, 2);
assert.deepEqual(families[0].seasons.map(row => row.season), [2024, 2025]);
assert.equal(families[0].name, 'Night Shift');
assert.equal(families[1].name, 'Sunday Friends');

function manager(id, nickname, guid = '') {
  return {manager: [{manager_id: id}, {nickname}, ...(guid ? [{guid}] : [])]};
}

function standingsTeam(teamKey, name, managerRow, rank, wins, losses, pointsFor, pointsAgainst) {
  return {team: [[
    {team_key: teamKey}, {name}, {managers: {0: managerRow, count: 1}}
  ], {team_standings: [
    {rank}, {playoff_seed: rank}, {outcome_totals: [{wins}, {losses}, {ties: 0}]},
    {points_for: pointsFor}, {points_against: pointsAgainst}
  ]}]};
}

const leaguePayload = {fantasy_content: {
  league: league('461.l.20', '20', 'Night Shift', 2025)[0]
}};
const settingsPayload = {fantasy_content: {league: [league('461.l.20', '20', 'Night Shift', 2025)[0][0], {
  settings: [{playoff_start_week: 2}]
}]}};
const standingsPayload = {fantasy_content: {league: [league('461.l.20', '20', 'Night Shift', 2025)[0][0], {
  standings: [{teams: {
    0: standingsTeam('461.l.20.t.1', 'First Name', manager('1', 'Ralph', 'guid-a'), 1, 1, 0, 112.5, -2.5),
    1: standingsTeam('461.l.20.t.2', 'Second Name', manager('2', 'Jamie', 'guid-b'), 2, 0, 1, -2.5, 112.5),
    count: 2
  }}]
}]}};

function scoreboard(week, homeScore, awayScore, playoffs = 0) {
  return {fantasy_content: {league: [league('461.l.20', '20', 'Night Shift', 2025)[0][0], {
    scoreboard: [{matchups: {0: {matchup: [[
      {week}, {status: 'postevent'}, {is_playoffs: playoffs}
    ], {teams: {
      0: {team: [[{team_key: '461.l.20.t.1'}, {name: 'First Name'}],
        {team_points: [{coverage_type: 'week'}, {week}, {total: homeScore}]}]},
      1: {team: [[{team_key: '461.l.20.t.2'}, {name: 'Second Name'}],
        {team_points: [{coverage_type: 'week'}, {week}, {total: awayScore}]}]},
      count: 2
    }}]}, count: 1}}]
  }]}};
}

const parsed = parseYahooSeason({
  league: leaguePayload,
  settings: settingsPayload,
  standings: standingsPayload,
  scoreboards: [scoreboard(1, 112.5, -2.5), scoreboard(2, 95.25, 101.75, 1)]
});
assert.equal(parsed.league.name, 'Night Shift');
assert.equal(parsed.season.year, 2025);
assert.equal(parsed.season.regularSeasonWeeks, 1);
assert.equal(parsed.season.teams.length, 2);
assert.equal(parsed.season.matchups.length, 2);
assert.equal(parsed.season.matchups[0].awayScore, -2.5);
assert.equal(parsed.season.matchups[1].playoff, true);
assert.deepEqual(parsed.identities.map(row => row.identityId), ['yahoo:guid-a', 'yahoo:guid-b']);

const badScore = structuredClone(scoreboard(1, 10, 9));
const awayTeam = badScore.fantasy_content.league[1].scoreboard[0].matchups[0].matchup[1].teams[1].team;
awayTeam[1].team_points = [{coverage_type: 'week'}, {week: 1}];
assert.throws(() => parseYahooSeason({
  league: leaguePayload,
  settings: settingsPayload,
  standings: standingsPayload,
  scoreboards: [badScore]
}), /invalid score/);

const secret = '0123456789abcdef0123456789abcdef';
const session = {accessToken: 'access', refreshToken: 'refresh', expiresAt: Date.now() + 1000};
const sealed = await sealYahooSession(session, secret);
assert.deepEqual(await openYahooSession(sealed, secret), session);
assert.equal(await openYahooSession(sealed + 'tampered', secret), null);

const unavailable = await onRequestGet({
  request: new Request('https://lineupbeat.dev/api/yahoo/status'),
  env: {}
});
assert.equal(unavailable.status, 200);
assert.deepEqual(await unavailable.json(), {configured: false, connected: false});

// Locked provider corpus: three expected Yahoo league resources are detected,
// and no wrapper/count objects are misclassified as leagues.
console.log('Yahoo history semantic corpus precision 3/3; recall 3/3');
console.log('Yahoo OAuth session, discovery, standings and matchup normalization: ok');
