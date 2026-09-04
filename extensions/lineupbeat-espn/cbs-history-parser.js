(function (root, factory) {
  'use strict';
  const parser = factory();
  if (typeof module === 'object' && module.exports) module.exports = parser;
  root.LineupBeatCbsHistoryParser = parser;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const MAX_SEASONS = 25;
  const EMPTY_ERROR = 'No complete CBS season was found on this page. Open My League, History, then a season scoreboard or schedule and try again.';
  const clean = value => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  const key = value => clean(value).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const number = value => {
    const match = clean(value).replace(/,/g, '').match(/-?\d+(?:\.\d+)?/);
    return match ? Number(match[0]) : NaN;
  };

  function seasonYear(value, fallback) {
    const matches = clean(value).match(/(?:19|20)\d{2}/g) || [];
    const years = matches.map(Number).filter(year => year >= 1960 && year <= 2200);
    return years.length ? years[years.length - 1] : Number(fallback);
  }

  function column(headers, patterns) {
    return headers.findIndex(header => patterns.some(pattern => pattern.test(key(header))));
  }

  function normalizeTeams(rows) {
    const teams = [];
    const seen = new Set();
    for (const row of rows || []) {
      const name = clean(row.teamName);
      const owner = clean(row.ownerName || row.managerName || name);
      if (!name || !owner) continue;
      const id = clean(row.teamId || `cbs:${key(name)}`);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      teams.push({
        teamId: id,
        teamName: name,
        ownerIds: [`cbs-owner:${key(owner)}`],
        owners: [{id: `cbs-owner:${key(owner)}`, displayName: owner}],
        wins: Number.isFinite(Number(row.wins)) ? Number(row.wins) : 0,
        losses: Number.isFinite(Number(row.losses)) ? Number(row.losses) : 0,
        ties: Number.isFinite(Number(row.ties)) ? Number(row.ties) : 0,
        pointsFor: Number.isFinite(Number(row.pointsFor)) ? Number(row.pointsFor) : 0,
        pointsAgainst: Number.isFinite(Number(row.pointsAgainst)) ? Number(row.pointsAgainst) : 0,
        playoffSeed: Number.isFinite(Number(row.playoffSeed)) ? Number(row.playoffSeed) : 0,
        finalStanding: Number.isFinite(Number(row.finalStanding)) ? Number(row.finalStanding) : 0,
        logo: ''
      });
    }
    return teams;
  }

  function normalizeMatchups(rows, teams, year, regularSeasonWeeks) {
    const byName = new Map(teams.map(team => [key(team.teamName), team.teamId]));
    const matchups = [];
    const seen = new Set();
    for (const row of rows || []) {
      const homeTeamId = clean(row.homeTeamId || byName.get(key(row.homeTeamName)) || '');
      const awayTeamId = clean(row.awayTeamId || byName.get(key(row.awayTeamName)) || '');
      const week = Number(row.week);
      const homeScore = Number(row.homeScore);
      const awayScore = Number(row.awayScore);
      if (!homeTeamId || !awayTeamId || homeTeamId === awayTeamId || !Number.isInteger(week) ||
          week < 1 || !Number.isFinite(homeScore) || !Number.isFinite(awayScore)) continue;
      const identity = `${week}|${homeTeamId}|${awayTeamId}|${homeScore}|${awayScore}`;
      if (seen.has(identity)) continue;
      seen.add(identity);
      matchups.push({
        id: clean(row.id || `${year}-${week}-${matchups.length + 1}`), week,
        playoff: row.playoff === true || week > regularSeasonWeeks,
        homeTeamId, awayTeamId, homeScore, awayScore
      });
    }
    return matchups.sort((a, b) => a.week - b.week || a.id.localeCompare(b.id));
  }

  function tableData(table) {
    const headers = Array.from(table.querySelectorAll('thead th, thead td')).map(cell => clean(cell.textContent));
    const rows = Array.from(table.querySelectorAll('tbody tr')).map(row =>
      Array.from(row.querySelectorAll(':scope > th, :scope > td')).map(cell => clean(cell.textContent)));
    return {headers, rows};
  }

  function standingsFrom(document) {
    const output = [];
    for (const table of Array.from(document.querySelectorAll('table'))) {
      const data = tableData(table);
      const team = column(data.headers, [/^team$/, /franchise/]);
      const owner = column(data.headers, [/owner/, /manager/]);
      const wins = column(data.headers, [/^w$/, /^wins?$/]);
      const losses = column(data.headers, [/^l$/, /^loss(?:es)?$/]);
      const points = column(data.headers, [/points for/, /^pf$/, /^points$/]);
      if (team < 0 || wins < 0 || losses < 0) continue;
      data.rows.forEach((cells, index) => output.push({
        teamName: cells[team], ownerName: owner >= 0 ? cells[owner] : cells[team],
        teamId: `cbs:${key(cells[team])}`, wins: number(cells[wins]), losses: number(cells[losses]),
        ties: 0, pointsFor: points >= 0 ? number(cells[points]) : 0, finalStanding: index + 1
      }));
    }
    return output;
  }

  function matchupsFrom(document) {
    const output = [];
    for (const table of Array.from(document.querySelectorAll('table'))) {
      const data = tableData(table);
      const week = column(data.headers, [/^week$/, /^wk$/]);
      const home = column(data.headers, [/home/, /^team 1$/, /^winner$/]);
      const away = column(data.headers, [/away/, /^team 2$/, /^loser$/]);
      const homeScore = column(data.headers, [/home score/, /team 1 score/, /winner score/]);
      const awayScore = column(data.headers, [/away score/, /team 2 score/, /loser score/]);
      if ([week, home, away, homeScore, awayScore].some(index => index < 0)) continue;
      data.rows.forEach((cells, index) => output.push({
        id: `row-${index + 1}`, week: number(cells[week]), homeTeamName: cells[home],
        awayTeamName: cells[away], homeScore: number(cells[homeScore]), awayScore: number(cells[awayScore])
      }));
    }
    return output;
  }

  function parseSnapshot(input) {
    const source = input || {};
    const year = seasonYear(source.year || source.pageText || source.url, new Date().getFullYear());
    const regularSeasonWeeks = Number.isInteger(Number(source.regularSeasonWeeks))
      ? Number(source.regularSeasonWeeks) : 14;
    const teams = normalizeTeams(source.teams);
    const matchups = normalizeMatchups(source.matchups, teams, year, regularSeasonWeeks);
    if (teams.length < 2 || !matchups.length) throw new Error(EMPTY_ERROR);
    return {
      year, leagueName: clean(source.leagueName, 'CBS league'), regularSeasonWeeks,
      complete: source.complete !== false, teams, matchups,
      source: {leagueId: clean(source.leagueId, 'cbs-league')}
    };
  }

  function fromDocument(document, options) {
    const title = clean(document.title);
    const heading = clean((document.querySelector('h1') || {}).textContent);
    const teams = standingsFrom(document);
    const matchups = matchupsFrom(document);
    return parseSnapshot({
      year: seasonYear(`${document.location && document.location.href || ''} ${title} ${heading}`),
      leagueId: options && options.leagueId,
      leagueName: heading || title.split('|')[0] || 'CBS league',
      url: document.location && document.location.href,
      teams, matchups,
      complete: !/current|live|in progress/i.test(`${title} ${heading}`)
    });
  }

  function buildIdentityReview(seasons) {
    const identities = new Map();
    seasons.forEach(season => season.teams.forEach(team => team.owners.forEach(owner => {
      const current = identities.get(owner.id) || {
        identityId: owner.id, displayName: owner.displayName, normalizedName: key(owner.displayName),
        seasons: [], teamNames: []
      };
      if (!current.seasons.includes(season.year)) current.seasons.push(season.year);
      if (!current.teamNames.includes(team.teamName)) current.teamNames.push(team.teamName);
      identities.set(owner.id, current);
    })));
    const rows = [...identities.values()].sort((a, b) => a.displayName.localeCompare(b.displayName));
    const suggestions = [];
    for (let left = 0; left < rows.length; left += 1) for (let right = left + 1; right < rows.length; right += 1) {
      if (rows[left].normalizedName && rows[left].normalizedName === rows[right].normalizedName) {
        suggestions.push({a: rows[left].identityId, b: rows[right].identityId, reason: 'same CBS manager name'});
      }
    }
    return {identities: rows, suggestions};
  }

  function combine(existing, season, leagueId, capturedAt) {
    const prior = existing && Array.isArray(existing.seasons) ? existing.seasons : [];
    const seasons = prior.filter(row => row.year !== season.year).concat([season])
      .sort((a, b) => a.year - b.year).slice(-MAX_SEASONS);
    const review = buildIdentityReview(seasons);
    return {
      schemaVersion: 'lineupbeat-history-capture-v1', provider: 'cbs',
      connectionType: 'browser_extension', capturedAt: capturedAt || new Date().toISOString(),
      league: {id: clean(leagueId, 'cbs-league'), name: season.leagueName},
      seasons, incomplete: [], identityReview: review,
      counts: {seasons: seasons.length, teams: Math.max(...seasons.map(row => row.teams.length)),
        matchups: seasons.reduce((total, row) => total + row.matchups.length, 0), identities: review.identities.length}
    };
  }

  return {MAX_SEASONS, EMPTY_ERROR, seasonYear, normalizeTeams, normalizeMatchups,
    standingsFrom, matchupsFrom, parseSnapshot, fromDocument, buildIdentityReview, combine};
});
