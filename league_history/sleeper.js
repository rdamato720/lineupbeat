(function (root, factory) {
  'use strict';
  const parser = factory();
  if (typeof module === 'object' && module.exports) module.exports = parser;
  root.LineupBeatSleeperHistory = parser;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const MAX_SEASONS = 25;
  const clean = value => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  const finite = (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const matchupScore = row => row && row.custom_points != null &&
    Number.isFinite(Number(row.custom_points)) ? Number(row.custom_points) : Number(row && row.points);
  const normalizeName = value => clean(value).toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ').trim();

  function seasonPoints(settings, field) {
    return finite(settings && settings[field], 0) +
      finite(settings && settings[field + '_decimal'], 0) / 100;
  }

  function finishMap(rosters, bracket) {
    const ordered = (rosters || []).slice().sort((left, right) => {
      const a = left.settings || {};
      const b = right.settings || {};
      return finite(b.wins, 0) - finite(a.wins, 0) ||
        finite(b.ties, 0) - finite(a.ties, 0) ||
        seasonPoints(b, 'fpts') - seasonPoints(a, 'fpts') ||
        finite(left.roster_id, 0) - finite(right.roster_id, 0);
    });
    const finishes = new Map(ordered.map((row, index) => [String(row.roster_id), index + 1]));
    const placementRows = (bracket || []).filter(row => Number.isInteger(Number(row && row.p)));
    placementRows.forEach(row => {
      const place = Number(row.p);
      if (row.w != null && place > 0) finishes.set(String(row.w), place);
      if (row.l != null && place > 0) finishes.set(String(row.l), place + 1);
    });
    if (!placementRows.length && bracket && bracket.length) {
      const final = bracket.slice().filter(row => row && row.w != null && row.l != null)
        .sort((a, b) => finite(b.r, 0) - finite(a.r, 0) || finite(b.m, 0) - finite(a.m, 0))[0];
      if (final) {
        finishes.set(String(final.w), 1);
        finishes.set(String(final.l), 2);
      }
    }
    return finishes;
  }

  function normalizeSeason(input) {
    const source = input || {};
    const league = source.league || {};
    const year = Number(league.season);
    if (!league.league_id || !Number.isInteger(year) || year < 2017) {
      throw new Error('Sleeper returned an invalid league season.');
    }
    const users = new Map((source.users || []).filter(row => row && row.user_id)
      .map(row => [String(row.user_id), row]));
    const rosters = (source.rosters || []).filter(row => row && row.roster_id != null);
    if (rosters.length < 2) throw new Error('Sleeper returned fewer than two teams.');
    const finishes = finishMap(rosters,
      (source.winnersBracket || []).concat(source.losersBracket || []));
    const regularSeasonWeeks = Math.max(1, finite(league.settings && league.settings.playoff_week_start, 15) - 1);
    const teams = rosters.map(roster => {
      const ownerIds = [roster.owner_id].concat(roster.co_owners || [])
        .filter(Boolean).map(id => `sleeper:${id}`);
      const stableOwners = ownerIds.length ? [...new Set(ownerIds)] :
        [`sleeper:legacy:${league.league_id}:${roster.roster_id}`];
      const primary = users.get(String(roster.owner_id || '')) || {};
      const metadata = primary.metadata || {};
      const settings = roster.settings || {};
      return {
        teamId: String(roster.roster_id),
        teamName: clean(metadata.team_name || metadata.team_name_update ||
          (primary.display_name ? `${primary.display_name}'s Team` : ''), `Team ${roster.roster_id}`),
        ownerIds: stableOwners,
        owners: stableOwners.map(id => {
          const rawId = id.replace(/^sleeper:/, '');
          const user = users.get(rawId) || {};
          return {id, displayName: clean(user.display_name || user.username, `Manager ${roster.roster_id}`)};
        }),
        wins: finite(settings.wins, 0), losses: finite(settings.losses, 0),
        ties: finite(settings.ties, 0), pointsFor: seasonPoints(settings, 'fpts'),
        pointsAgainst: seasonPoints(settings, 'fpts_against'),
        playoffSeed: finite(settings.rank, 0), finalStanding: finishes.get(String(roster.roster_id)) || 0,
        logo: primary.avatar ? `https://sleepercdn.com/avatars/${encodeURIComponent(primary.avatar)}` : ''
      };
    });
    const validTeams = new Set(teams.map(team => team.teamId));
    const matchups = [];
    (source.matchupWeeks || []).forEach(weekRow => {
      const week = Number(weekRow.week);
      const groups = new Map();
      (weekRow.rows || []).forEach(row => {
        if (!row || row.matchup_id == null || !validTeams.has(String(row.roster_id))) return;
        const key = String(row.matchup_id);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(row);
      });
      [...groups.entries()].sort((a, b) => Number(a[0]) - Number(b[0])).forEach(([matchupId, rows]) => {
        if (rows.length !== 2 || !Number.isFinite(matchupScore(rows[0])) ||
            !Number.isFinite(matchupScore(rows[1]))) return;
        const ordered = rows.slice().sort((a, b) => Number(a.roster_id) - Number(b.roster_id));
        matchups.push({
          id: `${league.league_id}:${week}:${matchupId}`, week,
          playoff: week > regularSeasonWeeks,
          homeTeamId: String(ordered[0].roster_id), awayTeamId: String(ordered[1].roster_id),
          homeScore: matchupScore(ordered[0]), awayScore: matchupScore(ordered[1])
        });
      });
    });
    matchups.sort((a, b) => a.week - b.week || a.id.localeCompare(b.id));
    if (!matchups.length) throw new Error(`Sleeper returned no completed matchups for ${year}.`);
    return {
      year, leagueName: clean(league.name, 'Sleeper league'), regularSeasonWeeks,
      complete: league.status === 'complete', teams, matchups,
      source: {leagueId: String(league.league_id), status: clean(league.status)}
    };
  }

  function buildIdentityReview(seasons) {
    const identities = new Map();
    seasons.forEach(season => season.teams.forEach(team => team.owners.forEach(owner => {
      const current = identities.get(owner.id) || {
        identityId: owner.id, displayName: owner.displayName,
        normalizedName: normalizeName(owner.displayName), seasons: [], teamNames: []
      };
      if (!current.seasons.includes(season.year)) current.seasons.push(season.year);
      if (!current.teamNames.includes(team.teamName)) current.teamNames.push(team.teamName);
      identities.set(owner.id, current);
    })));
    const rows = [...identities.values()].sort((a, b) =>
      a.displayName.localeCompare(b.displayName) || a.identityId.localeCompare(b.identityId));
    const suggestions = [];
    for (let left = 0; left < rows.length; left += 1) {
      for (let right = left + 1; right < rows.length; right += 1) {
        if (rows[left].identityId !== rows[right].identityId && rows[left].normalizedName &&
            rows[left].normalizedName === rows[right].normalizedName) {
          suggestions.push({a: rows[left].identityId, b: rows[right].identityId,
            reason: 'same Sleeper manager name'});
        }
      }
    }
    return {identities: rows, suggestions};
  }

  function combine(seasons, rootLeague, capturedAt) {
    const available = (seasons || []).slice().sort((a, b) => a.year - b.year).slice(-MAX_SEASONS);
    if (!available.length) throw new Error('Sleeper returned no usable seasons.');
    const review = buildIdentityReview(available);
    return {
      schemaVersion: 'lineupbeat-history-capture-v1', provider: 'sleeper',
      connectionType: 'public_api', capturedAt: capturedAt || new Date().toISOString(),
      league: {id: String(rootLeague.league_id), name: clean(rootLeague.name, available[available.length - 1].leagueName)},
      seasons: available,
      incomplete: available.filter(season => !season.complete)
        .map(season => ({year: season.year, reason: 'season in progress'})),
      identityReview: review,
      counts: {seasons: available.length, teams: Math.max(...available.map(row => row.teams.length)),
        matchups: available.reduce((total, row) => total + row.matchups.length, 0),
        identities: review.identities.length}
    };
  }

  return {MAX_SEASONS, normalizeName, finishMap, normalizeSeason, buildIdentityReview, combine};
});
