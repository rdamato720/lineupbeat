(function () {
  'use strict';

  const STARTING_ELO = 1500;
  const ELO_K = 24;
  const SEASON_REGRESSION = 0.30;
  let capture = null;
  let pendingReview = null;
  let activeReview = null;
  let activeManagerId = null;
  let sharedPublication = null;

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
    const headToHead = new Map();

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
          longestWinFrom: null,
          longestLossFrom: null,
          seasons: new Set(),
          aliases: new Set(),
          titleYears: [],
          runnerUpYears: [],
          crownYears: [],
          seasonStats: new Map(),
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
      if (!row.seasonStats.has(year)) {
        row.seasonStats.set(year, {
          year,
          teamNames: new Set(),
          wins: 0,
          losses: 0,
          ties: 0,
          regWins: 0,
          regLosses: 0,
          regTies: 0,
          regPointsFor: 0,
          pointsFor: 0,
          pointsAgainst: 0,
          expectedWins: 0,
          finish: Number(team.finalStanding) || null
        });
      }
      const seasonRow = row.seasonStats.get(year);
      seasonRow.teamNames.add(team.teamName);
      const finish = Number(team.finalStanding) || null;
      if (finish && (!seasonRow.finish || finish < seasonRow.finish)) {
        seasonRow.finish = finish;
      }
      return row;
    }

    function h2hRow(left, right) {
      if (!headToHead.has(left)) headToHead.set(left, new Map());
      const opponents = headToHead.get(left);
      if (!opponents.has(right)) {
        opponents.set(right, {wins: 0, losses: 0, ties: 0, pointsFor: 0, pointsAgainst: 0});
      }
      return opponents.get(right);
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
        if (champion) {
          champion.manager.titles += 1;
          champion.manager.titleYears.push(season.year);
        }
        if (runnerUp) {
          runnerUp.manager.runnerUps += 1;
          runnerUp.manager.runnerUpYears.push(season.year);
        }
        if (scoringCrown) {
          scoringCrown.manager.scoringCrowns += 1;
          scoringCrown.manager.crownYears.push(season.year);
        }
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
          const seasonRow = row.seasonStats.get(season.year);
          row.games += 1;
          row.pointsFor += scored;
          row.pointsAgainst += allowed;
          seasonRow.pointsFor += scored;
          seasonRow.pointsAgainst += allowed;
          if (side === 1) {
            row.wins += 1;
            seasonRow.wins += 1;
            if (!game.playoff) seasonRow.regWins += 1;
            if (!row.currentWinStreak) {
              row.currentWinFrom = {year: season.year, week: game.week};
            }
            row.currentWinStreak += 1;
            row.currentLosingStreak = 0;
          } else if (side === 0) {
            row.losses += 1;
            seasonRow.losses += 1;
            if (!game.playoff) seasonRow.regLosses += 1;
            if (!row.currentLosingStreak) {
              row.currentLossFrom = {year: season.year, week: game.week};
            }
            row.currentLosingStreak += 1;
            row.currentWinStreak = 0;
          } else {
            row.ties += 1;
            seasonRow.ties += 1;
            if (!game.playoff) seasonRow.regTies += 1;
            row.currentWinStreak = 0;
            row.currentLosingStreak = 0;
          }
          if (!game.playoff) seasonRow.regPointsFor += scored;
          if (row.currentWinStreak > row.longestWinStreak) {
            row.longestWinStreak = row.currentWinStreak;
            row.longestWinFrom = row.currentWinFrom;
          }
          if (row.currentLosingStreak > row.longestLosingStreak) {
            row.longestLosingStreak = row.currentLosingStreak;
            row.longestLossFrom = row.currentLossFrom;
          }
        });

        const homeSeries = h2hRow(home.manager.id, away.manager.id);
        const awaySeries = h2hRow(away.manager.id, home.manager.id);
        homeSeries.pointsFor += homeScore;
        homeSeries.pointsAgainst += awayScore;
        awaySeries.pointsFor += awayScore;
        awaySeries.pointsAgainst += homeScore;
        if (result === 1) {
          homeSeries.wins += 1;
          awaySeries.losses += 1;
        } else if (result === 0) {
          homeSeries.losses += 1;
          awaySeries.wins += 1;
        } else {
          homeSeries.ties += 1;
          awaySeries.ties += 1;
        }

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
          homeId: home.manager.id,
          awayId: away.manager.id,
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
          const expected = opponents.reduce((total, other) =>
            total + outcome(entry.score, other.score), 0) / opponents.length;
          entry.manager.expectedWins += expected;
          entry.manager.seasonStats.get(season.year).expectedWins += expected;
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
        scoringCrown,
        standings: Array.from(new Map(Array.from(teams.values()).map(row =>
          [row.manager.id, row.manager.seasonStats.get(season.year)])).values())
          .sort((a, b) => (a.finish || 999) - (b.finish || 999))
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
    const seasonRows = rows.flatMap(row => Array.from(row.seasonStats.values())
      .map(seasonRow => ({manager: row, season: seasonRow})));
    const bestRegularSeason = seasonRows.slice().sort((a, b) =>
      b.season.regWins - a.season.regWins ||
      a.season.regLosses - b.season.regLosses ||
      b.season.regPointsFor - a.season.regPointsFor)[0] || null;
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
        best.homeScore + best.awayScore ? game : best),
      bestRegularSeason
    } : null;

    const series = {};
    headToHead.forEach((opponents, id) => {
      series[id] = {};
      opponents.forEach((value, opponentId) => {
        series[id][opponentId] = value;
      });
    });
    return {managers: rows, seasons: seasons.reverse(), games, records, headToHead: series};
  }

  function setText(selector, text) {
    const node = document.querySelector(selector);
    if (node) node.textContent = text;
  }

  function managerById(summary, id) {
    return summary.managers.find(row => row.id === id) || null;
  }

  function appendTableHead(table, labels) {
    const thead = element('thead');
    const tr = element('tr');
    labels.forEach(label => tr.appendChild(element('th', '', label)));
    thead.appendChild(tr);
    table.appendChild(thead);
  }

  function appendDefinition(list, term, value) {
    const group = element('div');
    group.append(element('dt', '', term), element('dd', '', value));
    list.appendChild(group);
  }

  function seriesRecord(row) {
    return row.wins + '-' + row.losses + (row.ties ? '-' + row.ties : '');
  }

  function signed(value) {
    const amount = Number(value) || 0;
    return (amount > 0 ? '+' : '') + number(amount, 1);
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
    if (sharedPublication) {
      notice.append(
        element('strong', '', 'Shared by your commissioner. '),
        document.createTextNode('ESPN credentials and private league access are not included.')
      );
    } else {
      notice.append(
        element('strong', '', 'Private by default. '),
        document.createTextNode('Your ESPN archive is calculated in this browser.')
      );
    }
  }

  function renderTrophies(summary) {
    setText('#trophies .section-head p',
      'Career hardware by manager.');
    const grid = document.getElementById('trophy-cabinet');
    grid.replaceChildren();
    summary.managers.slice().sort((a, b) =>
      b.titles - a.titles || b.scoringCrowns - a.scoringCrowns ||
      b.runnerUps - a.runnerUps || a.manager.localeCompare(b.manager))
      .forEach(row => {
        const card = element('article', 'record-card trophy-card');
        const identity = element('div', 'trophy-card__identity');
        identity.append(
          element('h3', '', row.manager),
          element('p', '', row.latestTeam)
        );
        const stats = element('div', 'trophy-stats');
        [
          ['Championships', row.titles, 'is-title'],
          ['Scoring crowns', row.scoringCrowns, ''],
          ['Runner-up', row.runnerUps, '']
        ].forEach(([label, value, className]) => {
          const stat = element('div', 'trophy-stat ' + className);
          stat.append(
            element('strong', '', String(value)),
            element('span', '', label)
          );
          stats.appendChild(stat);
        });
        card.append(identity, stats);
        grid.appendChild(card);
      });
    if (!grid.children.length) {
      grid.appendChild(element('article', 'record-card', 'No completed seasons found.'));
    }

    const ledger = document.getElementById('trophy-ledger');
    ledger.replaceChildren();
    appendTableHead(ledger, ['Season', 'Champion', 'Runner-up', 'Scoring crown']);
    const body = element('tbody');
    summary.seasons.filter(season => season.complete).forEach(season => {
      const tr = element('tr');
      [
        String(season.year),
        season.champion ? season.champion.manager.manager : '—',
        season.runnerUp ? season.runnerUp.manager.manager : '—',
        season.scoringCrown ? season.scoringCrown.manager.manager : '—'
      ].forEach(value => tr.appendChild(element('td', '', value)));
      body.appendChild(tr);
    });
    ledger.appendChild(body);
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
    renderHeadToHead(summary);
  }

  function headToHeadColor(series) {
    const games = series.wins + series.losses + series.ties;
    const rate = games ? (series.wins + series.ties * 0.5) / games : 0.5;
    const strength = Math.min(0.46, Math.abs(rate - 0.5) *
      Math.min(games / 8, 1) + 0.08);
    if (rate > 0.5) return 'rgba(82, 190, 121, ' + strength.toFixed(2) + ')';
    if (rate < 0.5) return 'rgba(224, 99, 88, ' + strength.toFixed(2) + ')';
    return 'rgba(125, 139, 131, .12)';
  }

  function renderHeadToHead(summary) {
    const table = document.getElementById('head-to-head');
    if (!table) return;
    table.replaceChildren();
    const thead = element('thead');
    const head = element('tr');
    head.appendChild(element('th', '', 'Manager'));
    summary.managers.forEach(row => {
      const th = element('th', '', row.manager.slice(0, 4).toUpperCase());
      th.title = row.manager;
      head.appendChild(th);
    });
    thead.appendChild(head);
    table.appendChild(thead);
    const body = element('tbody');
    summary.managers.forEach(row => {
      const tr = element('tr');
      tr.appendChild(element('td', '', row.manager));
      summary.managers.forEach(opponent => {
        if (row.id === opponent.id) {
          tr.appendChild(element('td', 'self', ''));
          return;
        }
        const series = (summary.headToHead[row.id] || {})[opponent.id];
        if (!series) {
          tr.appendChild(element('td', 'empty', '—'));
          return;
        }
        const td = element('td', '', seriesRecord(series));
        td.style.backgroundColor = headToHeadColor(series);
        td.title = row.manager + ' vs ' + opponent.manager + ': ' +
          seriesRecord(series);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);

    const pairs = [];
    summary.managers.forEach((row, index) => {
      summary.managers.slice(index + 1).forEach(opponent => {
        const series = (summary.headToHead[row.id] || {})[opponent.id];
        if (!series) return;
        const games = series.wins + series.losses + series.ties;
        pairs.push({row, opponent, series, games,
          margin: Math.abs(series.wins - series.losses)});
      });
    });
    const deadEven = pairs.slice().sort((a, b) =>
      a.margin - b.margin || b.games - a.games)[0];
    const mostPlayed = pairs.slice().sort((a, b) => b.games - a.games)[0];
    const mostOwned = pairs.slice().sort((a, b) =>
      b.margin - a.margin || b.games - a.games)[0];
    const rivalries = document.getElementById('rivalries');
    rivalries.replaceChildren();
    [
      ['Closest rivalry', deadEven],
      ['Most played', mostPlayed],
      ['Most one-sided', mostOwned]
    ].forEach(([label, pair]) => {
      if (!pair) return;
      const card = element('article', 'rivalry-card');
      card.append(
        element('small', '', label),
        element('strong', '', pair.row.manager + ' vs ' + pair.opponent.manager),
        element('span', '', seriesRecord(pair.series) + ' · ' + pair.games + ' games')
      );
      rivalries.appendChild(card);
    });
  }

  function renderManagers(summary) {
    setText('#managers .section-head p',
      'Career totals, season results, team names, and every matchup.');
    if (!managerById(summary, activeManagerId)) {
      activeManagerId = summary.managers[0] ? summary.managers[0].id : null;
    }
    const list = document.getElementById('manager-list');
    list.replaceChildren();
    summary.managers.forEach(row => {
      const button = element('button');
      button.type = 'button';
      button.setAttribute('aria-selected', String(row.id === activeManagerId));
      button.append(
        element('strong', '', row.manager),
        element('span', '', row.seasons.size + ' seasons' +
          (row.titles ? ' · ' + row.titles + ' titles' : ''))
      );
      button.addEventListener('click', () => {
        activeManagerId = row.id;
        Array.from(list.children).forEach(node => node.setAttribute(
          'aria-selected', String(node === button)));
        renderManagerDetail(summary, row);
      });
      list.appendChild(button);
    });
    const selected = managerById(summary, activeManagerId);
    if (selected) renderManagerDetail(summary, selected);
  }

  function appendCareerStat(container, value, label) {
    const card = element('div', 'career-stat');
    card.append(element('strong', '', value), element('span', '', label));
    container.appendChild(card);
  }

  function renderManagerDetail(summary, row) {
    const detail = document.getElementById('manager-detail');
    detail.replaceChildren();
    const years = Array.from(row.seasons).sort((a, b) => a - b);
    const head = element('header', 'career-head');
    head.append(
      element('span', 'eyebrow', years[0] + '–' + years[years.length - 1] +
        ' · ' + years.length + ' seasons'),
      element('h3', '', row.manager),
      element('p', '', row.latestTeam)
    );
    detail.appendChild(head);

    if (row.aliases.size > 1) {
      const aliases = element('details', 'team-history career-aliases');
      aliases.append(
        element('summary', '', 'View all ' + row.aliases.size + ' team names'),
        element('p', '', Array.from(row.aliases).join(' · '))
      );
      detail.appendChild(aliases);
    }

    const stats = element('div', 'career-stats');
    appendCareerStat(stats, record(row), 'Career record');
    appendCareerStat(stats, winPct(row.winPct), 'Win percentage');
    appendCareerStat(stats, String(row.titles), 'Championships');
    appendCareerStat(stats, signed(row.luck), 'Career luck');
    appendCareerStat(stats, number(row.elo, 0), 'Rating');
    appendCareerStat(stats, number(row.peakElo, 0), 'Peak rating');
    appendCareerStat(stats, number(row.pointsPerGame, 1), 'Points per game');
    appendCareerStat(stats, row.longestWinStreak + 'W', 'Longest win streak');
    detail.appendChild(stats);

    const grid = element('div', 'career-grid');
    const seasonPanel = element('section', 'career-panel');
    seasonPanel.appendChild(element('h4', '', 'Season by season'));
    const seasonWrap = element('div', 'table-wrap');
    const seasonTable = element('table', 'history-table career-table');
    appendTableHead(seasonTable, ['Season', 'Team', 'Record', 'PF', 'Luck', 'Finish']);
    const seasonBody = element('tbody');
    Array.from(row.seasonStats.values()).sort((a, b) => b.year - a.year)
      .forEach(season => {
        const tr = element('tr');
        const games = season.wins + season.losses + season.ties;
        const luck = season.wins + season.ties * 0.5 - season.expectedWins;
        [
          String(season.year),
          Array.from(season.teamNames).join(' / '),
          games ? seriesRecord(season) : '0-0',
          number(season.pointsFor, 0),
          games ? signed(luck) : '—',
          season.finish ? String(season.finish) : '—'
        ].forEach(value => tr.appendChild(element('td', '', value)));
        seasonBody.appendChild(tr);
      });
    seasonTable.appendChild(seasonBody);
    seasonWrap.appendChild(seasonTable);
    seasonPanel.appendChild(seasonWrap);
    grid.appendChild(seasonPanel);

    const side = element('div');
    const weeks = summary.games.flatMap(game => {
      if (game.homeId === row.id) return [{score: game.homeScore,
        opponent: game.awayManager, year: game.year, week: game.week}];
      if (game.awayId === row.id) return [{score: game.awayScore,
        opponent: game.homeManager, year: game.year, week: game.week}];
      return [];
    }).sort((a, b) => b.score - a.score);
    const highLow = element('section', 'career-panel');
    highLow.appendChild(element('h4', '', 'Career highs and lows'));
    const notes = [];
    if (weeks.length) {
      notes.push(['Best week', number(weeks[0].score, 1) + ' · ' +
        weeks[0].year + ' W' + weeks[0].week + ' vs ' + weeks[0].opponent]);
      const low = weeks[weeks.length - 1];
      notes.push(['Lowest week', number(low.score, 1) + ' · ' +
        low.year + ' W' + low.week + ' vs ' + low.opponent]);
    }
    notes.push(
      ['Longest win streak', row.longestWinStreak + ' games' +
        (row.longestWinFrom ? ' · from ' + row.longestWinFrom.year +
          ' W' + row.longestWinFrom.week : '')],
      ['Longest losing streak', row.longestLosingStreak + ' games' +
        (row.longestLossFrom ? ' · from ' + row.longestLossFrom.year +
          ' W' + row.longestLossFrom.week : '')]
    );
    notes.forEach(([label, value]) => {
      const note = element('div', 'career-note');
      note.append(element('strong', '', label), element('span', '', value));
      highLow.appendChild(note);
    });
    side.appendChild(highLow);

    const matchupPanel = element('section', 'career-panel');
    matchupPanel.appendChild(element('h4', '', 'Head-to-head'));
    const opponents = Object.entries(summary.headToHead[row.id] || {})
      .map(([id, series]) => ({manager: managerById(summary, id), series,
        games: series.wins + series.losses + series.ties}))
      .filter(item => item.manager)
      .sort((a, b) => b.games - a.games);
    opponents.forEach(item => {
      const note = element('div', 'career-note');
      note.append(
        element('strong', '', item.manager.manager),
        element('span', '', seriesRecord(item.series) + ' · ' + item.games + ' games')
      );
      matchupPanel.appendChild(note);
    });
    if (!opponents.length) {
      matchupPanel.appendChild(element('p', '', 'No matchup history found.'));
    }
    side.appendChild(matchupPanel);
    grid.appendChild(side);
    detail.appendChild(grid);
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
      'Career, season, and single-game records.');
    const grid = document.getElementById('record-book');
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
    const titleLeader = summary.managers.slice().sort((a, b) =>
      b.titles - a.titles || b.wins - a.wins)[0];
    const peakLeader = summary.managers.slice().sort((a, b) =>
      b.peakElo - a.peakElo)[0];
    const lucky = summary.managers.slice().sort((a, b) => b.luck - a.luck)[0];
    const unlucky = summary.managers.slice().sort((a, b) => a.luck - b.luck)[0];
    const winStreak = summary.managers.slice().sort((a, b) =>
      b.longestWinStreak - a.longestWinStreak)[0];
    const lossStreak = summary.managers.slice().sort((a, b) =>
      b.longestLosingStreak - a.longestLosingStreak)[0];
    const best = records.bestRegularSeason;
    grid.append(
      recordCard('Highest single week', number(high.score, 1), high.manager,
        high.game.year + ' · Week ' + high.game.week + ' · ' + high.team),
      recordCard('Lowest single week', number(low.score, 1), low.manager,
        low.game.year + ' · Week ' + low.game.week + ' · ' + low.team),
      recordCard('Biggest blowout',
        '+' + number(Math.abs(records.biggestBlowout.homeScore -
          records.biggestBlowout.awayScore), 1),
        blowoutWinner.manager,
        records.biggestBlowout.year + ' · Week ' +
          records.biggestBlowout.week + ' · ' + blowoutWinner.team),
      recordCard('Closest game',
        number(Math.abs(records.closestGame.homeScore -
          records.closestGame.awayScore), 2),
        closestWinner.manager,
        records.closestGame.year + ' · Week ' +
          records.closestGame.week + ' · ' + closestWinner.team),
      recordCard('Highest scoring game',
        number(records.highestCombined.homeScore +
          records.highestCombined.awayScore, 1),
        records.highestCombined.homeManager + ' vs ' +
          records.highestCombined.awayManager,
        records.highestCombined.year + ' · Week ' +
          records.highestCombined.week),
      recordCard('Best regular season', best ? seriesRecord({
        wins: best.season.regWins,
        losses: best.season.regLosses,
        ties: best.season.regTies
      }) : '—', best ? best.manager.manager : 'No result',
        best ? best.season.year + ' · ' + number(best.season.regPointsFor, 0) +
          ' points' : ''),
      recordCard('Most championships', titleLeader ? String(titleLeader.titles) : '—',
        titleLeader ? titleLeader.manager : 'No champion',
        titleLeader && titleLeader.titleYears.length ?
          titleLeader.titleYears.join(', ') : 'No titles yet'),
      recordCard('Highest peak rating', peakLeader ? number(peakLeader.peakElo, 0) : '—',
        peakLeader ? peakLeader.manager : 'No result',
        peakLeader ? 'Current rating ' + number(peakLeader.elo, 0) : ''),
      recordCard('Luckiest career', lucky ? signed(lucky.luck) : '—',
        lucky ? lucky.manager : 'No result', lucky ? lucky.wins +
          ' wins · ' + number(lucky.expectedWins, 1) + ' expected' : ''),
      recordCard('Most schedule pain', unlucky ? signed(unlucky.luck) : '—',
        unlucky ? unlucky.manager : 'No result', unlucky ? unlucky.wins +
          ' wins · ' + number(unlucky.expectedWins, 1) + ' expected' : ''),
      recordCard('Longest win streak', winStreak ? String(winStreak.longestWinStreak) : '—',
        winStreak ? winStreak.manager : 'No result', winStreak && winStreak.longestWinFrom ?
          'From ' + winStreak.longestWinFrom.year + ' · Week ' +
          winStreak.longestWinFrom.week : ''),
      recordCard('Longest losing streak', lossStreak ?
        String(lossStreak.longestLosingStreak) : '—',
        lossStreak ? lossStreak.manager : 'No result',
        lossStreak && lossStreak.longestLossFrom ? 'From ' +
          lossStreak.longestLossFrom.year + ' · Week ' +
          lossStreak.longestLossFrom.week : '')
    );
    renderTopWeeks(summary, 'best');
    const toggle = document.getElementById('weeks-toggle');
    Array.from(toggle.querySelectorAll('button')).forEach(button => {
      button.onclick = () => {
        Array.from(toggle.querySelectorAll('button')).forEach(item =>
          item.setAttribute('aria-pressed', String(item === button)));
        const kind = button.dataset.kind;
        setText('#weeks-title', kind === 'best' ?
          'Biggest weeks ever' : 'Weeks they want forgotten');
        renderTopWeeks(summary, kind);
      };
    });
    const provenance = document.querySelector('#records .source-grid');
    if (provenance) provenance.hidden = true;
  }

  function renderTopWeeks(summary, kind) {
    const weeks = summary.games.flatMap(game => [
      {manager: game.homeManager, score: game.homeScore,
        opponent: game.awayManager, opponentScore: game.awayScore,
        year: game.year, week: game.week, playoff: game.playoff},
      {manager: game.awayManager, score: game.awayScore,
        opponent: game.homeManager, opponentScore: game.homeScore,
        year: game.year, week: game.week, playoff: game.playoff}
    ]).sort((a, b) => kind === 'best' ? b.score - a.score : a.score - b.score)
      .slice(0, 15);
    const table = document.getElementById('top-weeks');
    table.replaceChildren();
    appendTableHead(table, ['#', 'Manager', 'Score', 'Opponent', 'Their score', 'When']);
    const body = element('tbody');
    weeks.forEach((week, index) => {
      const tr = element('tr');
      [
        String(index + 1), week.manager, number(week.score, 1), week.opponent,
        number(week.opponentScore, 1), week.year + ' · W' + week.week +
          (week.playoff ? ' · Playoffs' : '')
      ].forEach(value => tr.appendChild(element('td', '', value)));
      body.appendChild(tr);
    });
    table.appendChild(body);
  }

  function updateHeader(payload) {
    const counts = payload.counts || {};
    const leagueName = payload.league && payload.league.name || 'League history';
    setText('#league-title', leagueName);
    setText('#header-seasons', String(counts.seasons || 0));
    setText('#header-games', Number(counts.matchups || 0).toLocaleString());
    setText('#header-teams', String(counts.teams || 0));
    setText('#ambient-seasons', String(counts.seasons || 0));
    setText('#ambient-games', Number(counts.matchups || 0).toLocaleString());
    document.title = leagueName + ' League History | LineupBeat';
  }

  function publicationKey() {
    const league = capture && capture.league;
    return league && league.id ? 'lb-league-publication:' + league.id : '';
  }

  function savedPublication() {
    const key = publicationKey();
    if (!key) return null;
    try {
      const value = JSON.parse(localStorage.getItem(key) || 'null');
      return value && value.slug && value.manageToken ? value : null;
    } catch (_) {
      return null;
    }
  }

  function storePublication(value) {
    const key = publicationKey();
    if (!key) return false;
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (_) {
      return false;
    }
  }

  function showPublication(value) {
    const result = document.getElementById('publish-result');
    const link = document.getElementById('published-url');
    const button = document.getElementById('publish-league');
    const key = document.getElementById('recovery-key-value');
    const restore = document.getElementById('restore-publication');
    if (!result || !link || !button || !value || !value.slug) return;
    const url = new URL('/leagues/' + value.slug, location.origin).toString();
    link.href = url;
    link.textContent = url;
    if (key) key.textContent = value.manageToken || '';
    if (restore) restore.hidden = true;
    result.hidden = false;
    button.textContent = 'Update shared page';
  }

  function clearPublication() {
    const result = document.getElementById('publish-result');
    const link = document.getElementById('published-url');
    const button = document.getElementById('publish-league');
    const key = document.getElementById('recovery-key-value');
    const restore = document.getElementById('restore-publication');
    if (result) result.hidden = true;
    if (link) { link.href = '#'; link.textContent = ''; }
    if (key) key.textContent = '';
    if (restore) { restore.hidden = false; restore.open = false; }
    if (button) button.textContent = 'Create share link';
  }

  function publicationSlug(value) {
    const cleaned = String(value || '').trim();
    const valid = candidate =>
      /^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$/.test(candidate) ? candidate : '';
    if (valid(cleaned)) return cleaned;
    try {
      const url = new URL(cleaned, location.origin);
      if (url.origin !== location.origin) return '';
      const match = url.pathname.match(/^\/leagues\/([^/]+)\/?$/);
      if (match) return valid(decodeURIComponent(match[1]));
      if (/^\/league-history\/?$/.test(url.pathname)) {
        return valid(url.searchParams.get('league') || '');
      }
      return '';
    } catch (_) {
      return '';
    }
  }

  function comparisonText(comparison) {
    if (!comparison) return 'Commissioner access connected.';
    if (!comparison.changed) return 'Shared page is up to date.';
    const labels = {seasons: 'season', matchups: 'matchup', teams: 'team',
      identities: 'manager'};
    const changes = [];
    Object.keys(labels).forEach(key => {
      const row = comparison.counts && comparison.counts[key];
      if (!row || row.before === row.after) return;
      if (key === 'seasons' && comparison.seasonYears) {
        (comparison.seasonYears.added || []).forEach(year =>
          changes.push(year + ' season added'));
        (comparison.seasonYears.removed || []).forEach(year =>
          changes.push(year + ' season removed'));
        if ((comparison.seasonYears.added || []).length ||
            (comparison.seasonYears.removed || []).length) return;
      }
      const delta = row.after - row.before;
      if (delta > 0) {
        changes.push('+' + delta + ' ' + labels[key] + (delta === 1 ? '' : 's'));
      } else {
        changes.push(String(delta) + ' ' + labels[key] + (delta === -1 ? '' : 's'));
      }
    });
    if (comparison.managerMatchesChanged) changes.push('manager matches changed');
    if (comparison.detailsChanged) changes.push('scores or season details changed');
    return 'New ESPN data: ' + (changes.length ? changes.join(' · ') :
      'league details changed') + '. Update the shared page when ready.';
  }

  async function checkPublication(saved) {
    const status = document.getElementById('publish-status');
    if (!status || !capture || !activeReview) return;
    status.textContent = 'Checking the shared page…';
    try {
      const response = await fetch('/api/leagues/' + encodeURIComponent(saved.slug), {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json',
          Authorization: 'Bearer ' + saved.manageToken},
        body: JSON.stringify({archive: capture, review: activeReview})
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'Shared page status is unavailable.');
      saved.visibility = result.visibility;
      storePublication(saved);
      const option = document.querySelector(
        'input[name="league-visibility"][value="' + saved.visibility + '"]');
      if (option) option.checked = true;
      status.textContent = comparisonText(result.comparison);
    } catch (error) {
      status.textContent = error && error.message ? error.message :
        'Shared page status is unavailable.';
    }
  }

  function restorePublication() {
    const saved = savedPublication();
    if (!saved) return;
    const option = document.querySelector(
      'input[name="league-visibility"][value="' + saved.visibility + '"]');
    if (option) option.checked = true;
    showPublication(saved);
    checkPublication(saved);
  }

  async function publishLeague() {
    const button = document.getElementById('publish-league');
    const status = document.getElementById('publish-status');
    const selected = document.querySelector('input[name="league-visibility"]:checked');
    if (!button || !status || !capture || !activeReview || !selected) return;
    const saved = savedPublication();
    const updating = Boolean(saved);
    const endpoint = updating ? '/api/leagues/' + saved.slug : '/api/leagues';
    button.disabled = true;
    status.textContent = updating ? 'Updating your share link…' : 'Creating your share link…';
    try {
      const headers = {'Content-Type': 'application/json'};
      if (updating) headers.Authorization = 'Bearer ' + saved.manageToken;
      const response = await fetch(endpoint, {
        method: updating ? 'PUT' : 'POST',
        headers,
        body: JSON.stringify({
          visibility: selected.value,
          archive: capture,
          review: activeReview
        })
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'Publishing failed. Try again.');
      const stored = {
        slug: result.slug,
        manageToken: updating ? saved.manageToken : result.manageToken,
        visibility: result.visibility
      };
      showPublication(stored);
      const storedLocally = storePublication(stored);
      status.textContent = updating ?
        'Shared page updated: ' + capture.seasons.length + ' seasons · ' +
          capture.seasons.reduce((total, season) =>
            total + (season.matchups || []).length, 0).toLocaleString() +
          ' matchups. The link stays the same.' : storedLocally ?
        'Share link ready. Save the key under Recovery key.' :
        'Share link ready. This browser could not save access, so copy the recovery key now.';
    } catch (error) {
      status.textContent = error && error.message ? error.message :
        'Publishing is temporarily unavailable.';
    } finally {
      button.disabled = false;
    }
  }

  async function recoverPublicationAccess() {
    const share = document.getElementById('restore-share-link');
    const key = document.getElementById('restore-recovery-key');
    const button = document.getElementById('restore-publication-access');
    const status = document.getElementById('publish-status');
    if (!share || !key || !button || !status || !capture) return;
    const slug = publicationSlug(share.value);
    const manageToken = key.value.trim();
    if (!slug || !manageToken) {
      status.textContent = 'Enter the share link and recovery key.';
      return;
    }
    button.disabled = true;
    status.textContent = 'Checking commissioner access…';
    try {
      const response = await fetch('/api/leagues/' + encodeURIComponent(slug), {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json',
          Authorization: 'Bearer ' + manageToken},
        body: JSON.stringify({archive: capture, review: activeReview})
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'Access could not be restored.');
      const stored = {slug: result.slug, manageToken, visibility: result.visibility};
      const storedLocally = storePublication(stored);
      const option = document.querySelector(
        'input[name="league-visibility"][value="' + stored.visibility + '"]');
      if (option) option.checked = true;
      key.value = '';
      share.value = '';
      showPublication(stored);
      status.textContent = storedLocally ? 'Commissioner access restored. ' +
        comparisonText(result.comparison) :
        'Access works, but this browser could not save it. Keep the recovery key.';
    } catch (error) {
      status.textContent = error && error.message ? error.message :
        'Access could not be restored.';
    } finally {
      button.disabled = false;
    }
  }

  async function unpublishLeague() {
    const saved = savedPublication();
    const button = document.getElementById('unpublish-league');
    const status = document.getElementById('publish-status');
    if (!saved || !button || !status) return;
    if (!window.confirm('Unpublish this league history? The shared link will stop working.')) {
      return;
    }
    button.disabled = true;
    status.textContent = 'Removing shared league…';
    try {
      const response = await fetch('/api/leagues/' + encodeURIComponent(saved.slug), {
        method: 'DELETE',
        headers: {Authorization: 'Bearer ' + saved.manageToken}
      });
      const result = response.status === 204 ? {} :
        await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'The shared league could not be removed.');
      try { localStorage.removeItem(publicationKey()); } catch (_) {}
      clearPublication();
      status.textContent = 'Shared page removed. Your local league history is unchanged.';
    } catch (error) {
      status.textContent = error && error.message ? error.message :
        'The shared league could not be removed.';
    } finally {
      button.disabled = false;
    }
  }

  async function loadSharedLeague(slug) {
    document.body.classList.add('shared-history');
    const statusLine = document.querySelector('.lh-status');
    const retry = document.getElementById('retry-shared-league');
    if (retry) retry.hidden = true;
    if (statusLine) statusLine.lastChild.textContent = ' loading shared league';
    setText('#import-status', 'Loading shared league history…');
    try {
      const response = await fetch('/api/leagues/' + encodeURIComponent(slug), {
        headers: {Accept: 'application/json'}
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'League not found.');
      sharedPublication = result;
      capture = result.archive;
      activeReview = result.review;
      updateHeader(capture);
      renderDashboard(capture, activeReview);
      if (statusLine) statusLine.lastChild.textContent = result.visibility === 'public' ?
        ' public league history' : ' shared league history';
    } catch (error) {
      document.body.classList.add('shared-history-error');
      setText('#import-status', error && error.message ? error.message :
        'League history is temporarily unavailable.');
      if (statusLine) statusLine.lastChild.textContent = ' shared league unavailable';
      if (retry) retry.hidden = false;
    }
  }

  function renderDashboard(payload, review) {
    updateHeader(payload);
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
    if (edit) edit.hidden = Boolean(sharedPublication);
    const publishPanel = document.getElementById('publish-panel');
    if (publishPanel) publishPanel.hidden = Boolean(sharedPublication);
    setText('#import-status', payload.counts.seasons + ' seasons · ' +
      payload.counts.matchups.toLocaleString() + ' matchups loaded');
    const footer = document.querySelector('.lh-footer');
    if (sharedPublication) {
      footer.replaceChildren(
        element('span', '', 'Shared league history · view only.'),
        element('span', '', 'Updated ' + String(sharedPublication.updatedAt || '').slice(0, 10))
      );
    } else {
      footer.replaceChildren(
        element('span', '', 'Private ESPN history · processed only in this browser.'),
        element('span', '', 'Imported ' + String(payload.capturedAt || '').slice(0, 10))
      );
      restorePublication();
    }
  }

  function clearReady() {
    document.body.classList.remove('history-ready');
    const edit = document.getElementById('edit-manager-matches');
    if (edit) edit.hidden = true;
    const publishPanel = document.getElementById('publish-panel');
    if (publishPanel) publishPanel.hidden = true;
  }

  globalThis.LineupBeatLeagueHistoryDashboard = {summarize, comparisonText};
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

  const publish = document.getElementById('publish-league');
  if (publish) publish.addEventListener('click', publishLeague);
  const copy = document.getElementById('copy-published-url');
  if (copy) {
    copy.addEventListener('click', async () => {
      const link = document.getElementById('published-url');
      const status = document.getElementById('publish-status');
      if (!link) return;
      try {
        await navigator.clipboard.writeText(link.href);
        copy.textContent = 'Copied';
        if (status) status.textContent = 'Share link copied.';
      } catch (_) {
        if (status) status.textContent = 'Copy the link above to share it.';
      }
    });
  }
  const copyRecovery = document.getElementById('copy-recovery-key');
  if (copyRecovery) {
    copyRecovery.addEventListener('click', async () => {
      const saved = savedPublication();
      const status = document.getElementById('publish-status');
      if (!saved) return;
      try {
        await navigator.clipboard.writeText(saved.manageToken);
        copyRecovery.textContent = 'Copied';
        if (status) status.textContent = 'Recovery key copied. Keep it private.';
      } catch (_) {
        if (status) status.textContent = 'Copy the recovery key above and keep it private.';
      }
    });
  }
  const recover = document.getElementById('restore-publication-access');
  if (recover) recover.addEventListener('click', recoverPublicationAccess);
  const unpublish = document.getElementById('unpublish-league');
  if (unpublish) unpublish.addEventListener('click', unpublishLeague);

  const sharedSlug = new URLSearchParams(location.search).get('league');
  const retryShared = document.getElementById('retry-shared-league');
  if (retryShared) retryShared.addEventListener('click', () => loadSharedLeague(sharedSlug));
  if (sharedSlug) loadSharedLeague(sharedSlug);

  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== location.origin ||
        !event.data || event.data.version !== 1) return;
    if (event.data.type === 'LB_LEAGUE_HISTORY_CAPTURE') {
      capture = event.data.payload;
      pendingReview = null;
      activeReview = event.data.review || null;
      sharedPublication = null;
      if (activeReview) renderDashboard(capture, activeReview);
      else clearReady();
    }
    if (event.data.type === 'LB_LEAGUE_HISTORY_SAVE_REVIEW_REQUEST') {
      pendingReview = event.data.review;
    }
    if (event.data.type === 'LB_LEAGUE_HISTORY_REVIEW_COMPLETE' &&
        event.data.ok && capture && pendingReview) {
      activeReview = pendingReview;
      renderDashboard(capture, activeReview);
    }
    if (event.data.type === 'LB_LEAGUE_HISTORY_CLEAR_COMPLETE') {
      clearReady();
      window.location.reload();
    }
  });
}());
