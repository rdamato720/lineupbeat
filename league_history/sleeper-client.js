(function () {
  'use strict';

  const API = 'https://api.sleeper.app/v1';
  const STORAGE_KEY = 'lineupBeatSleeperHistoryV1';
  const status = document.getElementById('import-status');
  const username = document.getElementById('sleeper-username');
  const season = document.getElementById('sleeper-season');
  const find = document.getElementById('find-sleeper-leagues');
  const picker = document.getElementById('sleeper-picker');
  const select = document.getElementById('sleeper-league');
  const start = document.getElementById('import-sleeper-history');
  const progress = document.getElementById('sleeper-progress');
  let leagues = [];

  function say(message) { if (status) status.textContent = message; }
  async function json(path) {
    const response = await fetch(API + path, {credentials: 'omit', cache: 'no-store'});
    if (response.status === 404) return null;
    if (!response.ok) throw new Error('Sleeper is temporarily unavailable.');
    return response.json();
  }
  async function mapLimit(values, limit, task) {
    const output = new Array(values.length);
    let cursor = 0;
    async function worker() {
      while (cursor < values.length) {
        const index = cursor++;
        output[index] = await task(values[index], index);
      }
    }
    await Promise.all(Array.from({length: Math.min(limit, values.length)}, worker));
    return output;
  }
  function stored() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
      return value && value.payload ? value : null;
    } catch (_) { return null; }
  }
  function post(record) {
    window.postMessage({type: 'LB_LEAGUE_HISTORY_CAPTURE', version: 1,
      payload: record.payload, review: record.review || null}, location.origin);
  }
  function save(record) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(record)); return true; }
    catch (_) { return false; }
  }

  async function findLeagues() {
    const handle = String(username.value || '').trim();
    const year = Number(season.value);
    if (!handle || !Number.isInteger(year) || year < 2017 || year > new Date().getFullYear() + 1) {
      say('Enter a valid Sleeper username and season.');
      return;
    }
    find.disabled = true;
    picker.hidden = true;
    say('Finding your Sleeper leagues…');
    try {
      const user = await json('/user/' + encodeURIComponent(handle));
      if (!user || !user.user_id) throw new Error('That Sleeper username was not found.');
      leagues = await json('/user/' + encodeURIComponent(user.user_id) + '/leagues/nfl/' + year) || [];
      if (!leagues.length) throw new Error(`No Sleeper football leagues were found for ${year}.`);
      leagues.sort((a, b) => String(a.name || '').localeCompare(String(b.name || '')));
      select.replaceChildren();
      leagues.forEach((league, index) => {
        const option = document.createElement('option');
        option.value = String(index);
        option.textContent = String(league.name || 'Sleeper league') + ' · ' + String(league.season || year);
        select.appendChild(option);
      });
      picker.hidden = false;
      say('Choose the Sleeper league you want to import.');
    } catch (error) { say(error.message || 'Sleeper leagues could not be loaded.'); }
    finally { find.disabled = false; }
  }

  async function leagueChain(rootLeague) {
    const chain = [];
    const seen = new Set();
    let current = rootLeague;
    while (current && current.league_id && chain.length < LineupBeatSleeperHistory.MAX_SEASONS) {
      const id = String(current.league_id);
      if (seen.has(id)) throw new Error('Sleeper returned a circular league-history chain.');
      seen.add(id);
      chain.push(current);
      const previous = String(current.previous_league_id || '0');
      if (!previous || previous === '0') break;
      current = await json('/league/' + encodeURIComponent(previous));
      if (!current) throw new Error('A previous Sleeper season is no longer available. Nothing was saved.');
    }
    return chain.reverse();
  }

  async function loadSeason(league) {
    const id = encodeURIComponent(league.league_id);
    const settings = league.settings || {};
    const lastWeek = Math.min(22, Math.max(1, Number(settings.last_scored_leg) ||
      (league.status === 'complete' ? Number(settings.playoff_week_start || 15) + 3 : 1)));
    const weeks = Array.from({length: lastWeek}, (_, index) => index + 1);
    const [users, rosters, winnersBracket, losersBracket, matchupWeeks] = await Promise.all([
      json('/league/' + id + '/users'), json('/league/' + id + '/rosters'),
      json('/league/' + id + '/winners_bracket').catch(() => []),
      json('/league/' + id + '/losers_bracket').catch(() => []),
      mapLimit(weeks, 6, async week => ({week, rows: await json('/league/' + id + '/matchups/' + week) || []}))
    ]);
    return LineupBeatSleeperHistory.normalizeSeason({league, users, rosters,
      winnersBracket: winnersBracket || [], losersBracket: losersBracket || [], matchupWeeks});
  }

  async function importHistory() {
    const rootLeague = leagues[Number(select.value)];
    if (!rootLeague) return;
    start.disabled = true;
    select.disabled = true;
    progress.hidden = false;
    try {
      progress.textContent = 'Finding every connected season…';
      const chain = await leagueChain(rootLeague);
      const seasons = [];
      for (let index = 0; index < chain.length; index += 1) {
        const message = 'Importing ' + chain[index].season + ' · ' + (index + 1) + ' of ' + chain.length;
        progress.textContent = message;
        say(message + '…');
        seasons.push(await loadSeason(chain[index]));
      }
      const payload = LineupBeatSleeperHistory.combine(seasons, rootLeague);
      const record = {payload, review: null};
      if (!save(record)) throw new Error('This browser could not save the Sleeper archive.');
      post(record);
      history.replaceState(null, '', '/league-history/');
      say(payload.counts.seasons + ' Sleeper seasons imported. Match any duplicate managers to continue.');
    } catch (error) {
      progress.textContent = 'Nothing was saved.';
      say(error.message || 'Sleeper history could not be imported.');
    } finally { start.disabled = false; select.disabled = false; }
  }

  if (find) find.addEventListener('click', findLeagues);
  if (start) start.addEventListener('click', importHistory);
  if (!new URLSearchParams(location.search).has('league')) {
    const record = stored();
    if (record) post(record);
  }
}());
