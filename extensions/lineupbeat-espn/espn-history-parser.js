(function (root, factory) {
  'use strict';
  const parser = factory();
  if (typeof module === 'object' && module.exports) module.exports = parser;
  root.LineupBeatEspnHistoryParser = parser;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const MAX_SEASONS = 25;
  const NICKNAMES = {
    alex: 'alexander', andy: 'andrew', ben: 'benjamin', bill: 'william', billy: 'william',
    bob: 'robert', bobby: 'robert', chris: 'christopher', dan: 'daniel', danny: 'daniel',
    dave: 'david', drew: 'andrew', greg: 'gregory', jeff: 'jeffrey', jim: 'james',
    jimmy: 'james', joe: 'joseph', joey: 'joseph', jon: 'jonathan', josh: 'joshua',
    ken: 'kenneth', kenny: 'kenneth', matt: 'matthew', mike: 'michael', nick: 'nicholas',
    nicky: 'nicholas', pat: 'patrick', pete: 'peter', rich: 'richard', rob: 'robert',
    sam: 'samuel', steve: 'steven', tim: 'timothy', tom: 'thomas', tony: 'anthony',
    will: 'william', zach: 'zachary'
  };

  function finiteNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clean(value, fallback) {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    return text || fallback || '';
  }

  function normalizeName(value) {
    const parts = clean(value).toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, ' ')
      .split(/\s+/).filter(Boolean);
    return parts.map(part => NICKNAMES[part] || part).join(' ');
  }

  function discoverYears(raw, requestedYear, limit) {
    const cap = Math.min(MAX_SEASONS, Math.max(1, finiteNumber(limit, MAX_SEASONS)));
    const years = new Set([finiteNumber(requestedYear, new Date().getFullYear())]);
    const prior = raw && raw.status && raw.status.previousSeasons;
    if (Array.isArray(prior)) prior.forEach(year => years.add(finiteNumber(year, 0)));
    return [...years].filter(year => Number.isInteger(year) && year >= 1960)
      .sort((a, b) => a - b).slice(-cap);
  }

  function memberName(member) {
    return clean([member && member.firstName, member && member.lastName].filter(Boolean).join(' '),
      clean(member && member.displayName, 'Unknown manager'));
  }

  function teamName(team) {
    return clean(team && team.name,
      clean([team && team.location, team && team.nickname].filter(Boolean).join(' '), 'Unnamed team'));
  }

  function normalizeSeason(raw, leagueId, requestedYear) {
    if (!raw || typeof raw !== 'object') throw new Error('invalid ESPN season response');
    const year = finiteNumber(raw.seasonId, requestedYear);
    if (!Number.isInteger(year)) throw new Error('ESPN season has no valid year');
    const settings = raw.settings || {};
    const scheduleSettings = settings.scheduleSettings || {};
    const regularSeasonWeeks = finiteNumber(scheduleSettings.matchupPeriodCount, 14);
    const members = new Map((raw.members || []).filter(row => row && row.id)
      .map(row => [String(row.id), memberName(row)]));
    const teams = (raw.teams || []).filter(row => row && Number.isInteger(Number(row.id)))
      .map(team => {
        const ownerIds = (team.owners || []).map(String).filter(Boolean);
        const overall = team.record && team.record.overall || {};
        return {
          teamId: String(team.id),
          teamName: teamName(team),
          ownerIds: ownerIds.length ? ownerIds : [`legacy:${year}:${team.id}`],
          owners: (ownerIds.length ? ownerIds : [`legacy:${year}:${team.id}`]).map(id => ({
            id, displayName: members.get(id) || `Legacy manager ${team.id}`
          })),
          wins: finiteNumber(overall.wins, 0), losses: finiteNumber(overall.losses, 0),
          ties: finiteNumber(overall.ties, 0), pointsFor: finiteNumber(overall.pointsFor, 0),
          pointsAgainst: finiteNumber(overall.pointsAgainst, 0),
          playoffSeed: finiteNumber(team.playoffSeed, 0),
          finalStanding: finiteNumber(team.rankCalculatedFinal, 0),
          logo: /^https:\/\//.test(String(team.logo || '')) ? String(team.logo) : ''
        };
      });
    const validTeamIds = new Set(teams.map(team => team.teamId));
    const matchups = [];
    for (const matchup of raw.schedule || []) {
      const home = matchup && matchup.home || {};
      const away = matchup && matchup.away || {};
      const homeId = String(home.teamId == null ? '' : home.teamId);
      const awayId = String(away.teamId == null ? '' : away.teamId);
      const homeScore = finiteNumber(home.totalPoints, 0);
      const awayScore = finiteNumber(away.totalPoints, 0);
      if (!validTeamIds.has(homeId) || !validTeamIds.has(awayId) || homeId === awayId ||
          (homeScore === 0 && awayScore === 0)) continue;
      const week = finiteNumber(matchup.matchupPeriodId, 0);
      if (!Number.isInteger(week) || week < 1) continue;
      matchups.push({
        id: String(matchup.id == null ? `${year}-${week}-${matchups.length + 1}` : matchup.id),
        week, playoff: week > regularSeasonWeeks, homeTeamId: homeId, awayTeamId: awayId,
        homeScore, awayScore
      });
    }
    matchups.sort((a, b) => a.week - b.week || a.id.localeCompare(b.id));
    const status = raw.status || {};
    const finalPeriod = finiteNumber(status.finalScoringPeriod, regularSeasonWeeks);
    const currentPeriod = finiteNumber(status.currentMatchupPeriod, 0);
    const complete = matchups.length > 0 &&
      (status.isActive === false || currentPeriod >= finalPeriod);
    return {
      year, leagueName: clean(settings.name, 'ESPN league'), regularSeasonWeeks,
      complete, teams, matchups,
      source: {leagueId: String(leagueId), scoringPeriodId: finiteNumber(raw.scoringPeriodId, 0)}
    };
  }

  function buildIdentityReview(seasons) {
    const identities = new Map();
    for (const season of seasons) {
      for (const team of season.teams) {
        for (const owner of team.owners) {
          if (!identities.has(owner.id)) identities.set(owner.id, {
            identityId: owner.id, displayName: owner.displayName,
            normalizedName: normalizeName(owner.displayName), seasons: [], teamNames: []
          });
          const identity = identities.get(owner.id);
          if (!identity.seasons.includes(season.year)) identity.seasons.push(season.year);
          if (!identity.teamNames.includes(team.teamName)) identity.teamNames.push(team.teamName);
        }
      }
    }
    const rows = [...identities.values()].sort((a, b) =>
      a.displayName.localeCompare(b.displayName) || a.identityId.localeCompare(b.identityId));
    const suggestions = [];
    for (let left = 0; left < rows.length; left += 1) {
      for (let right = left + 1; right < rows.length; right += 1) {
        const a = rows[left];
        const b = rows[right];
        const aParts = a.normalizedName.split(' ');
        const bParts = b.normalizedName.split(' ');
        const sameName = a.normalizedName && a.normalizedName === b.normalizedName;
        const sameSurname = aParts.length > 1 && bParts.length > 1 &&
          aParts[aParts.length - 1] === bParts[bParts.length - 1];
        if (sameName || sameSurname) suggestions.push({
          a: a.identityId, b: b.identityId,
          reason: sameName ? 'same normalized name' : 'same surname'
        });
      }
    }
    return {identities: rows, suggestions};
  }

  function combine(seasons, incomplete, leagueId, capturedAt) {
    const available = (seasons || []).slice().sort((a, b) => a.year - b.year);
    if (!available.length) throw new Error('ESPN returned no usable seasons');
    const review = buildIdentityReview(available);
    return {
      schemaVersion: 'lineupbeat-espn-history-capture-v1',
      provider: 'espn', connectionType: 'browser_extension',
      capturedAt: capturedAt || new Date().toISOString(),
      league: {id: String(leagueId), name: available[available.length - 1].leagueName},
      seasons: available, incomplete: incomplete || [], identityReview: review,
      counts: {
        seasons: available.length,
        teams: Math.max(...available.map(season => season.teams.length)),
        matchups: available.reduce((sum, season) => sum + season.matchups.length, 0),
        identities: review.identities.length
      }
    };
  }

  return {MAX_SEASONS, discoverYears, normalizeName, normalizeSeason, buildIdentityReview, combine};
});
