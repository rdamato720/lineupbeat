(function () {
  'use strict';

  const STARTING_ELO = 1500;
  const ELO_K = 24;
  const SEASON_REGRESSION = 0.30;
  let capture = null;
  let pendingReview = null;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function number(value, digits) {
    return new Intl.NumberFormat('en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    }).format(Number(value) || 0);
  }

  function winPct(value) {
    return (Number(value) || 0).toFixed(3).replace(/^0/, '');
  }

  function record(row) {
    return row.wins + '-' + row.losses + (row.ties ? '-' + row.ties : '');
  }

  function outcome(left, right) {
    if (left > right) return 1;
    if (left < right) return 0;
    return 0.5;
  }

  function eloUpdate(left, right, leftScore, rightScore) {
    const actual = outcome(leftScore, rightScore);
    const expected = 1 / (1 + Math.pow(10, (right - left) / 400));
    const margin = Math.abs(leftScore - rightScore);
    const multiplier = Math.min(1.8, Math.max(0.5,
      Math.log(Math.max(margin, 1) + 1) / Math.log(30)));
    const delta = ELO_K * multiplier * (actual - expected);
    return [left + delta, right - delta];
  }

  function identityResolver(payload, review) {
    const identities = new Map((payload.identityReview.identities || [])
      .map(row => [row.identityId, row]));
    const targets = new Map((review.identities || [])
      .map(row => [row.identityId, row.mergeInto || row.identityId]));

    function resolve(id) {
      let current = id;
      const seen = new Set();
      while (targets.has(current) && targets.get(current) !== current && !seen.has(current)) {
        seen.add(current);
        current = targets.get(current);
      }
      return current;
    }

    function name(id) {
      const master = identities.get(resolve(id));
      const original = identities.get(id);
      return (master || original || {}).displayName || 'Unknown manager';
    }

    return {resolve, name};
  }

  function summarize(payload, review) {
    const resolver = identityResolver(payload, review);
    const managers = new Map();
    const seasons = [];
    const games = [];

    function managerFor(team) {
      const ownerIds = Array.from(new Set((team.ownerIds || [])
        .map(resolver.resolve))).sort();
      const ids = ownerIds.length ? ownerIds : ['legacy:' + team.teamId];
      const id = ids.join('+');
      const name = ids.map(resolver.name).join(' & ');
      return {id, name};
    }

    function ensureManager(team, year) {
      const owner = managerFor(team);
      if (!managers.has(owner.id)) {
        managers.set(owner.id, {
          id: owner.id,
          manager: owner.name,
          wins: 0,
          losses: 0,
          ties: 0,
          games: 0,
          pointsFor: 0,
          pointsAgainst: 0,
          expectedWins: 0,
          titles: 0,
          runnerUps: 0,
          scoringCrowns: 0,
          elo: STARTING_ELO,
          peakElo: STARTING_ELO,
          longestWinStreak: 0,
          longestLosingStreak: 0,
          currentWinStreak: 0,
          currentLosingStreak: 0,
          seasons: new Set(),
          aliases: new Set(),
          latestYear: year,
          latestTeam: team.teamName
        });
      }
      const row = managers.get(owner.id);
      row.seasons.add(year);
      row.aliases.add(team.teamName);
      if (year >= row.latestYear) {
        row.latestYear = year;
        row.latestTeam = team.teamName;
      }
      return row;
    }

    const orderedSeasons = (payload.seasons || []).slice()
      .sort((a, b) => a.year - b.year);

    orderedSeasons.forEach((season, seasonIndex) => {
      if (seasonIndex) {
        managers.forEach(row => {
          if (row.games) {
            row.elo = STARTING_ELO +
              (row.elo - STARTING_ELO) * (1 - SEASON_REGRESSION);
          }
        });
      }

      const teams = new Map();
      (season.teams || []).forEach(team => {
        const manager = ensureManager(team, season.year);
        teams.set(String(team.teamId), {team, manager});
      });

      let champion = null;
      let runnerUp = null;
      let scoringCrown = null;
      if (season.complete) {
        champion = Array.from(teams.values()).find(row =>
          Number(row.team.finalStanding) === 1) || null;
        runnerUp = Array.from(teams.values()).find(row =>
          Number(row.team.finalStanding) === 2) || null;
        scoringCrown = Array.from(teams.values()).sort((a, b) =>
          Number(b.team.pointsFor || 0) - Number(a.team.pointsFor || 0))[0] || null;
        if (champion) champion.manager.titles += 1;
        if (runnerUp) runnerUp.manager.runnerUps += 1;
        if (scoringCrown) scoringCrown.manager.scoringCrowns += 1;
      }

      const seasonGames = (season.matchups || []).slice().sort((a, b) =>
        a.week - b.week || String(a.id).localeCompare(String(b.id)));
      const weekScores = new Map();

      seasonGames.forEach(game => {
        const home = teams.get(String(game.homeTeamId));
        const away = teams.get(String(game.awayTeamId));
        if (!home || !away || home.manager.id === away.manager.id) return;
        const homeScore = Number(game.homeScore);
        const awayScore = Number(game.awayScore);
        const result = outcome(homeScore, awayScore);

        [
          [home.manager, away.manager, homeScore, awayScore, result],
          [away.manager, home.manager, awayScore, homeScore, 1 - result]
        ].forEach(([row, opponent, scored, allowed, side]) => {
          row.games += 1;
          row.pointsFor += scored;
          row.pointsAgainst += allowed;
          if (side === 1) {
            row.wins += 1;
            row.currentWinStreak += 1;
            row.currentLosingStreak = 0;
          } else if (side === 0) {
            row.losses += 1;
            row.currentLosingStreak += 1;
            row.currentWinStreak = 0;
          } else {
            row.ties += 1;
            row.currentWinStreak = 0;
            row.currentLosingStreak = 0;
          }
          row.longestWinStreak = Math.max(row.longestWinStreak, row.currentWinStreak);
          row.longestLosingStreak = Math.max(
            row.longestLosingStreak, row.currentLosingStreak);
        });

        const ratings = eloUpdate(
          home.manager.elo, away.manager.elo, homeScore, awayScore);
        home.manager.elo = ratings[0];
        away.manager.elo = ratings[1];
        home.manager.peakElo = Math.max(home.manager.peakElo, ratings[0]);
        away.manager.peakElo = Math.max(away.manager.peakElo, ratings[1]);

        if (!weekScores.has(game.week)) weekScores.set(game.week, []);
        weekScores.get(game.week).push(
          {manager: home.manager, teamId: home.team.teamId, score: homeScore},
          {manager: away.manager, teamId: away.team.teamId, score: awayScore}
        );

        games.push({
          year: season.year,
          week: game.week,
          playoff: Boolean(game.playoff),
          homeTeam: home.team.teamName,
          awayTeam: away.team.teamName,
          homeManager: home.manager.manager,
          awayManager: away.manager.manager,
          homeScore,
          awayScore
        });
      });

      weekScores.forEach(entries => {
        entries.forEach(entry => {
          const opponents = entries.filter(other => other.teamId !== entry.teamId);
          if (!opponents.length) return;
          entry.manager.expectedWins += opponents.reduce((total, other) =>
            total + outcome(entry.score, other.score), 0) / opponents.length;
        });
      });

      seasons.push({
        year: season.year,
        complete: Boolean(season.complete),
        regularSeasonWeeks: season.regularSeasonWeeks,
        teamCount: teams.size,
        gameCount: seasonGames.length,
        champion,
        runnerUp,
        scoringCrown
      });
    });

    const rows = Array.from(managers.values()).map(row => {
      const decisions = row.wins + row.losses + row.ties;
      return Object.assign(row, {
        winPct: decisions ? (row.wins + row.ties * 0.5) / decisions : 0,
        pointsPerGame: row.games ? row.pointsFor / row.games : 0,
        luck: row.wins + row.ties * 0.5 - row.expectedWins
      });
    }).sort((a, b) =>
      b.titles - a.titles || b.wins - a.wins ||
      b.pointsFor - a.pointsFor || a.manager.localeCompare(b.manager));

    const scoringPlays = games.flatMap(game => [
      {
        score: game.homeScore,
        team: game.homeTeam,
        manager: game.homeManager,
        game
      },
      {
        score: game.awayScore,
        team: game.awayTeam,
        manager: game.awayManager,
        game
      }
    ]);
    const decidedGames = games.filter(game => game.homeScore !== game.awayScore);
    const records = games.length ? {
      highestWeek: scoringPlays.reduce((best, row) =>
        row.score > best.score ? row : best),
      lowestWeek: scoringPlays.reduce((best, row) =>
        row.score < best.score ? row : best),
      biggestBlowout: games.reduce((best, game) =>
        Math.abs(game.homeScore - game.awayScore) >
        Math.abs(best.homeScore - best.awayScore) ? game : best),
      closestGame: decidedGames.length ? decidedGames.reduce((best, game) =>
        Math.abs(game.homeScore - game.awayScore) <
        Math.abs(best.homeScore - best.awayScore) ? game : best) : games[0],
      highestCombined: games.reduce((best, game) =>
        game.homeScore + game.awayScore >
        best.homeScore + best.awayScore ? game : best)
    } : null;

    return {managers: rows, seasons: seasons.reverse(), games, records};
  }

  function setText(selector, text) {
    const node = document.querySelector(selector);
    if (node) node.textContent = text;
  }

  function renderOverview(summary) {
    const latest = summary.seasons.find(season => season.complete && season.champion);
    const championCard = document.querySelector('#overview .champ');
    championCard.replaceChildren();
    const label = element('div');
    label.appendChild(element('span', 'eyebrow',
      latest ? 'Defending champion · ' + latest.year : 'League history'));
    championCard.appendChild(label);
    championCard.appendChild(element('strong', '',
      latest ? latest.champion.team.teamName : 'Season in progress'));
    championCard.appendChild(element('p', '',
      latest ? latest.champion.manager.manager : 'No completed champion found'));
    championCard.appendChild(element('span', 'season-mark',
      latest ? String(latest.year).slice(-2) : '--'));

    const leaders = summary.managers.slice(0, 5);
    const power = document.querySelector('#overview .power');
    power.replaceChildren(
      element('span', 'eyebrow', 'All-time standings'),
      element('h2', '', 'League leaders')
    );
    const list = element('ol');
    leaders.forEach((row, index) => {
      const item = element('li');
      item.append(
        element('b', '', String(index + 1)),
        element('span', '', row.manager),
        element('strong', '', row.wins + ' W')
      );
      list.appendChild(item);
    });
    power.appendChild(list);

    let snapshot = document.querySelector('#overview .history-snapshot');
    if (!snapshot) {
      snapshot = element('div', 'history-snapshot');
      document.querySelector('#overview .notice').before(snapshot);
    }
    snapshot.replaceChildren();
    const titleLeader = summary.managers.slice().sort((a, b) =>
      b.titles - a.titles || b.wins - a.wins)[0];
    [
      ['Most wins', leaders[0] ? leaders[0].manager : '—',
        leaders[0] ? leaders[0].wins + ' career wins' : 'No results'],
      ['Most titles', titleLeader ? titleLeader.manager : '—',
        titleLeader ? titleLeader.titles + ' championships' : 'No results'],
      ['League archive', summary.seasons.length + ' seasons',
        summary.games.length.toLocaleString() + ' matchups']
    ].forEach(([label, value, detail]) => {
      const card = element('article', 'snapshot-card');
      card.append(
        element('small', '', label),
        element('strong', '', value),
        element('span', '', detail)
      );
      snapshot.appendChild(card);
    });

    const notice = document.querySelector('#overview .notice');
    notice.replaceChildren();
    notice.append(
      element('strong', '', 'Private by default. '),
      document.createTextNode('Your ESPN archive is calculated in this browser.')
    );
  }

  function renderTrophies(summary) {
    setText('#trophies .section-head p',
      'Champions from every completed season.');
    const grid = document.querySelector('#trophies .record-grid');
    grid.replaceChildren();
    summary.seasons.filter(season => season.complete && season.champion)
      .forEach(season => {
        const card = element('article', 'record-card');
        card.append(
          element('small', '', season.year + ' champion'),
          element('b', '', '🏆'),
          element('h3', '', season.champion.team.teamName),
          element('p', '', season.champion.manager.manager +
            (season.runnerUp ? ' · over ' + season.runnerUp.team.teamName : ''))
        );
        grid.appendChild(card);
      });
    if (!grid.children.length) {
      grid.appendChild(element('article', 'record-card', 'No completed seasons found.'));
    }
  }

  function renderAllTime(summary) {
    setText('#all-time .section-head p',
      'Career results across every season and team name.');
    const head = document.querySelector('#all-time thead tr');
    const labels = ['#', 'Manager', 'Record', 'Win%', 'PPG', 'Titles'];
    head.replaceChildren(...labels.map(label => element('th', '', label)));
    const body = document.querySelector('#all-time tbody');
    body.replaceChildren();
    summary.managers.forEach((row, index) => {
      const tr = element('tr');
      tr.appendChild(element('td', 'rank', String(index + 1)));
      const name = element('td');
      name.append(
        element('strong', '', row.manager),
        element('small', '', row.latestTeam + ' · ' + row.seasons.size +
          ' seasons · ' + row.aliases.size + ' team names')
      );
      tr.appendChild(name);
      [
        record(row),
        winPct(row.winPct),
        number(row.pointsPerGame, 1)
      ].forEach(value => tr.appendChild(element('td', '', value)));
      tr.appendChild(element('td', '', String(row.titles)));
      body.appendChild(tr);
    });
  }

  function renderManagers(summary) {
    setText('#managers .section-head p',
      'One career record per manager, across every team name.');
    const grid = document.querySelector('#managers .manager-grid');
    grid.replaceChildren();
    summary.managers.forEach(row => {
      const card = element('article', 'manager-card');
      const head = element('div', 'manager-card__head');
      const identity = element('div');
      identity.append(
        element('span', '', row.manager),
        element('small', '', row.latestTeam)
      );
      head.append(identity, element('b', '', record(row)));
      const dl = element('dl');
      [
        ['Seasons', String(row.seasons.size)],
        ['Win pct', winPct(row.winPct)],
        ['Titles', String(row.titles)],
        ['Best run', row.longestWinStreak + 'W']
      ].forEach(([term, value]) => {
        const group = element('div');
        group.append(element('dt', '', term), element('dd', '', value));
        dl.appendChild(group);
      });
      card.append(head, dl);
      if (row.aliases.size > 1) {
        const history = element('details', 'team-history');
        history.append(
          element('summary', '', 'View ' + row.aliases.size + ' team names'),
          element('p', '', Array.from(row.aliases).join(' · '))
        );
        card.appendChild(history);
      }
      grid.appendChild(card);
    });
  }

  function renderSeasons(summary) {
    setText('#seasons .section-head p',
      'Every season and team stays in the archive.');
    const grid = document.querySelector('#seasons .season-grid');
    grid.replaceChildren();
    summary.seasons.forEach(season => {
      const card = element('article', 'season-card');
      const year = element('div');
      year.append(
        element('small', '', season.complete ? 'Complete' : 'In progress'),
        element('h3', '', String(season.year))
      );
      const dl = element('dl');
      const champion = season.champion ?
        season.champion.team.teamName + ' · ' + season.champion.manager.manager :
        'Not awarded';
      [
        ['Champion', champion],
        ['Teams', String(season.teamCount)],
        ['Matchups', String(season.gameCount)],
        ['Regular season', season.regularSeasonWeeks + ' weeks']
      ].forEach(([term, value]) => {
        const group = element('div');
        group.append(element('dt', '', term), element('dd', '', value));
        dl.appendChild(group);
      });
      card.append(year, dl);
      grid.appendChild(card);
    });
  }

  function winner(game) {
    return game.homeScore >= game.awayScore ?
      {team: game.homeTeam, manager: game.homeManager} :
      {team: game.awayTeam, manager: game.awayManager};
  }

  function recordCard(label, value, title, detail) {
    const card = element('article', 'record-card');
    card.append(
      element('small', '', label),
      element('b', '', value),
      element('h3', '', title),
      element('p', '', detail)
    );
    return card;
  }

  function renderRecords(summary) {
    setText('#records .section-head p',
      'Single-game highs, lows, and closest finishes.');
    const grid = document.querySelector('#records .record-grid');
    grid.replaceChildren();
    if (!summary.records) {
      grid.appendChild(recordCard('Record book', '—', 'No matchups found',
        'Import a completed season to calculate records.'));
      return;
    }
    const records = summary.records;
    const high = records.highestWeek;
    const low = records.lowestWeek;
    const blowoutWinner = winner(records.biggestBlowout);
    const closestWinner = winner(records.closestGame);
    grid.append(
      recordCard('Highest week', number(high.score, 2), high.team,
        high.manager + ' · ' + high.game.year + ' · Week ' + high.game.week),
      recordCard('Lowest week', number(low.score, 2), low.team,
        low.manager + ' · ' + low.game.year + ' · Week ' + low.game.week),
      recordCard('Biggest blowout',
        number(Math.abs(records.biggestBlowout.homeScore -
          records.biggestBlowout.awayScore), 2),
        blowoutWinner.team,
        blowoutWinner.manager + ' · ' + records.biggestBlowout.year +
          ' · Week ' + records.biggestBlowout.week),
      recordCard('Closest game',
        number(Math.abs(records.closestGame.homeScore -
          records.closestGame.awayScore), 2),
        closestWinner.team,
        closestWinner.manager + ' · ' + records.closestGame.year +
          ' · Week ' + records.closestGame.week),
      recordCard('Highest combined',
        number(records.highestCombined.homeScore +
          records.highestCombined.awayScore, 2),
        records.highestCombined.homeTeam + ' vs ' +
          records.highestCombined.awayTeam,
        records.highestCombined.year + ' · Week ' +
          records.highestCombined.week)
    );
    const provenance = document.querySelector('#records .source-grid');
    if (provenance) provenance.hidden = true;
  }

  function renderDashboard(payload, review) {
    const summary = summarize(payload, review);
    renderOverview(summary);
    renderTrophies(summary);
    renderAllTime(summary);
    renderManagers(summary);
    renderSeasons(summary);
    renderRecords(summary);
    document.body.classList.add('history-ready');
    document.getElementById('import-summary').classList.remove('open');
    const edit = document.getElementById('edit-manager-matches');
    if (edit) edit.hidden = false;
    setText('#import-status', payload.counts.seasons + ' seasons · ' +
      payload.counts.matchups.toLocaleString() + ' matchups loaded');
    const footer = document.querySelector('.lh-footer');
    footer.replaceChildren(
      element('span', '', 'Private ESPN history · processed only in this browser.'),
      element('span', '', 'Imported ' + String(payload.capturedAt || '').slice(0, 10))
    );
  }

  function clearReady() {
    document.body.classList.remove('history-ready');
    const edit = document.getElementById('edit-manager-matches');
    if (edit) edit.hidden = true;
  }

  globalThis.LineupBeatLeagueHistoryDashboard = {summarize};
  if (typeof document === 'undefined') return;

  const edit = document.getElementById('edit-manager-matches');
  if (edit) {
    edit.addEventListener('click', () => {
      document.body.classList.remove('history-ready');
      document.getElementById('import-summary').classList.add('open');
      document.getElementById('review-managers').click();
      document.getElementById('import-summary').scrollIntoView({
        behavior: 'smooth',
        block: 'start'
      });
    });
  }

  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== location.origin ||
        !event.data || event.data.version !== 1) return;
    if (event.data.type === 'LB_LEAGUE_HISTORY_CAPTURE') {
      capture = event.data.payload;
      pendingReview = null;
      if (event.data.review) renderDashboard(capture, event.data.review);
      else clearReady();
    }
    if (event.data.type === 'LB_LEAGUE_HISTORY_SAVE_REVIEW_REQUEST') {
      pendingReview = event.data.review;
    }
    if (event.data.type === 'LB_LEAGUE_HISTORY_REVIEW_COMPLETE' &&
        event.data.ok && capture && pendingReview) {
      renderDashboard(capture, pendingReview);
    }
    if (event.data.type === 'LB_LEAGUE_HISTORY_CLEAR_COMPLETE') {
      clearReady();
      window.location.reload();
    }
  });
}());
