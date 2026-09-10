const assert = require('node:assert/strict');
const {normalize, gameStatus} = require('../assets/score-ticker.js');
const event = (id, state, detail, completed = false) => ({id, date: '2026-09-13T17:00:00Z',
  competitions: [{status: {type: {state, shortDetail: detail, completed}}, competitors: [
    {homeAway: 'home', score: '0', team: {name: 'Home', abbreviation: 'HM', logo: 'javascript:alert(1)'}},
    {homeAway: 'away', score: '7', winner: completed, team: {name: 'Away', abbreviation: 'AW', logo: 'https://a.espncdn.com/i/teamlogos/nfl/500/buf.png'}}]}]});
const games = normalize({events: [event('1', 'post', 'Final', true), event('2', 'pre', 'Scheduled'), event('3', 'in', 'OT 2:15')]});
assert.deepEqual(games.map(g => g.id), ['3', '2', '1']);
assert.equal(games[0].away.score, 7);
assert.equal(games[0].home.score, 0);
assert.equal(games[0].home.logo, '');
assert.equal(gameStatus(games[0]), 'OT 2:15');
assert.equal(gameStatus({...games[1], detail: 'Postponed'}), 'Postponed');
assert.equal(gameStatus({...games[1], timeValid: false}), 'Time TBD');
assert.equal(gameStatus({...games[1], date: 'invalid'}), 'Time TBD');
assert.equal(games[2].away.winner, true);
assert.deepEqual(normalize({events: []}), []);
assert.deepEqual(normalize({events: [{id: '42'}, event('bad-url', 'in', 'Q1')]}), []);
assert.throws(() => normalize({error: 'unavailable'}), /Invalid scoreboard/);
console.log('Score ticker: live sorting, zero scores, home/away order, overtime, postponed/TBD, completed, empty, malformed and unsafe source cases pass.');
