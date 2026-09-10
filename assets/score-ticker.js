/* Independent display feed. Never writes fantasy projections or rankings. */
(() => {
  'use strict';
  const SPORTS = {
    nfl: {label: 'NFL', path: 'nfl', query: ''},
    college: {label: 'College', path: 'college-football', query: '&groups=80'}
  };
  const REFRESH_MS = 60000;
  const score = value => value !== '' && value != null && Number.isFinite(Number(value)) ? Number(value) : null;
  function normalize(payload) {
    if (!Array.isArray(payload.events)) throw new Error('Invalid scoreboard');
    return payload.events.flatMap(event => {
      const competition = event.competitions?.[0];
      const type = competition?.status?.type || event.status?.type || {};
      const away = competition?.competitors?.find(team => team.homeAway === 'away');
      const home = competition?.competitors?.find(team => team.homeAway === 'home');
      if (!away?.team || !home?.team || !/^\d+$/.test(String(event.id))) return [];
      const mapTeam = entry => ({name: entry.team.displayName || entry.team.name || 'Team',
        abbreviation: entry.team.abbreviation || entry.team.shortDisplayName || entry.team.name,
        logo: /^https:\/\/(?:a|a1|a2|a3|a4)\.espncdn\.com\//.test(entry.team.logo || '') ? entry.team.logo : '',
        score: score(entry.score), winner: !!entry.winner});
      return [{id: String(event.id), date: event.date, timeValid: competition.timeValid !== false,
        state: type.state || 'pre', completed: !!type.completed,
        detail: type.shortDetail || type.detail || type.description || 'Scheduled',
        name: event.name || `${away.team.displayName} at ${home.team.displayName}`,
        away: mapTeam(away), home: mapTeam(home)}];
    }).sort((a, b) => {
      const priority = game => game.state === 'in' ? 0 : game.completed ? 2 : 1;
      const delta = priority(a) - priority(b);
      const dateDelta = (Date.parse(a.date) || 0) - (Date.parse(b.date) || 0);
      return delta || (a.completed && b.completed ? -dateDelta : dateDelta);
    });
  }
  function gameStatus(game) {
    if (game.state !== 'pre' || /postpon|cancel|delay|suspend/i.test(game.detail)) return game.detail;
    if (!game.timeValid || !Number.isFinite(Date.parse(game.date))) return 'Time TBD';
    return new Intl.DateTimeFormat(undefined, {weekday: 'short', hour: 'numeric', minute: '2-digit', timeZoneName: 'short'}).format(new Date(game.date));
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {normalize, gameStatus};
  if (typeof document === 'undefined') return;
  const root = document.getElementById('lb-scores');
  if (!root) return;
  const track = document.getElementById('lb-score-track');
  const status = document.getElementById('lb-score-status');
  const source = document.getElementById('lb-score-source');
  const cache = new Map();
  let selected = 'nfl', activeRequest = null;
  function el(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }
  function navigation() {
    const end = track.scrollWidth - track.clientWidth;
    root.querySelector('[data-score-direction="-1"]').disabled = track.scrollLeft < 2;
    root.querySelector('[data-score-direction="1"]').disabled = track.scrollLeft >= end - 2;
  }
  function render(sport, stale = false) {
    const data = cache.get(sport);
    if (!data) return;
    const fragment = document.createDocumentFragment();
    for (const game of data.games) {
      const card = el('a', 'lb-score-card' + (game.state === 'in' && !stale ? ' is-live' : ''));
      card.href = `https://www.espn.com/${SPORTS[sport].path}/game/_/gameId/${game.id}`;
      card.target = '_blank'; card.rel = 'noopener noreferrer';
      card.setAttribute('aria-label', `${game.name}: ${gameStatus(game)}. ${game.away.name} ${game.away.score ?? ''}, ${game.home.name} ${game.home.score ?? ''}. View game on ESPN`);
      card.append(el('span', 'lb-score-state', (stale && game.state === 'in' ? 'Last: ' : '') + gameStatus(game)));
      for (const team of [game.away, game.home]) {
        const row = el('span', 'lb-score-team' + (game.completed && team.winner ? ' is-winner' : ''));
        if (team.logo) {
          const image = el('img', ''); image.src = team.logo; image.alt = ''; image.loading = 'lazy';
          image.addEventListener('error', () => image.remove(), {once: true}); row.append(image);
        }
        row.append(el('span', '', team.abbreviation), el('b', '', game.state === 'pre' ? '–' : team.score ?? '–'));
        card.append(row);
      }
      fragment.append(card);
    }
    if (!data.games.length) fragment.append(el('p', 'lb-score-message', 'No games scheduled. Check back for the next slate.'));
    // Preserve a focused game link and horizontal position across refreshes.
    const focusedHref = track.contains(document.activeElement) ? document.activeElement.href : null;
    const scrollLeft = track.scrollLeft;
    track.replaceChildren(fragment); track.scrollLeft = scrollLeft;
    if (focusedHref) [...track.querySelectorAll('a')].find(a => a.href === focusedHref)?.focus({preventScroll: true});
    const stamp = new Date(data.fetchedAt).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
    status.textContent = `${stale ? 'Delayed · ' : ''}${data.week ? 'Week ' + data.week + ' · ' : ''}${stamp}`;
    navigation();
  }
  async function refresh(force = false) {
    if (document.hidden) return;
    const sport = selected;
    const existing = cache.get(sport);
    if (!force && existing && Date.now() - existing.fetchedAt < REFRESH_MS) { render(sport); return; }
    if (activeRequest?.sport === sport) return;
    activeRequest?.controller.abort();
    const request = {sport, controller: new AbortController()}; activeRequest = request;
    const timeout = setTimeout(() => request.controller.abort(), 12000);
    try {
      const config = SPORTS[sport];
      const response = await fetch(`https://site.api.espn.com/apis/site/v2/sports/football/${config.path}/scoreboard?limit=200${config.query}`, {signal: request.controller.signal, credentials: 'omit'});
      if (!response.ok) throw new Error('Scores unavailable');
      const payload = await response.json();
      const games = normalize(payload);
      if (activeRequest !== request) return;
      cache.set(sport, {games, week: Number(payload.week?.number) || null, fetchedAt: Date.now()});
      if (sport === selected) render(sport);
    } catch (error) {
      if (sport !== selected || activeRequest !== request) return;
      if (cache.has(sport)) render(sport, true);
      else {
        status.textContent = 'Scores unavailable';
        track.replaceChildren(el('p', 'lb-score-message', 'Scores are temporarily unavailable. Try the ESPN scores link.'));
        navigation();
      }
    } finally {
      clearTimeout(timeout);
      if (activeRequest === request) activeRequest = null;
    }
  }
  root.querySelectorAll('[data-score-sport]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.scoreSport === selected) return;
    selected = button.dataset.scoreSport;
    root.querySelectorAll('[data-score-sport]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    source.href = `https://www.espn.com/${SPORTS[selected].path}/scoreboard`;
    track.setAttribute('aria-label', `${SPORTS[selected].label} games; scroll for more`);
    track.scrollLeft = 0;
    if (cache.has(selected)) render(selected, Date.now() - cache.get(selected).fetchedAt >= REFRESH_MS * 2);
    else { status.textContent = 'Loading scores…'; track.replaceChildren(el('p', 'lb-score-message', 'Loading this week’s games…')); }
    refresh();
  }));
  root.querySelectorAll('[data-score-direction]').forEach(button => button.addEventListener('click', () => {
    track.scrollBy({left: Number(button.dataset.scoreDirection) * track.clientWidth * .8,
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
  }));
  track.addEventListener('scroll', navigation, {passive: true});
  window.addEventListener('resize', navigation, {passive: true});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(true); });
  setInterval(() => refresh(), REFRESH_MS);
  refresh();
})();
