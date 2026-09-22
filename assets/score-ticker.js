/* Independent display feed. Never writes fantasy projections or rankings. */
(() => {
  'use strict';
  const SPORTS = {
    nfl: {label: 'NFL'},
    college: {label: 'College'}
  };
  const REFRESH_MS = 60000;
  function normalize(payload) {
    if (!Array.isArray(payload.games)) throw new Error('Invalid scoreboard');
    return payload.games;
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
    stale = stale || data.stale;
    const fragment = document.createDocumentFragment();
    for (const game of data.games) {
      const card = el('div', 'lb-score-card' + (game.state === 'in' && !stale ? ' is-live' : ''));
      card.setAttribute('aria-label', `${game.away.name} at ${game.home.name}: ${gameStatus(game)}. ${game.away.score ?? ''} to ${game.home.score ?? ''}`);
      card.append(el('span', 'lb-score-state', (stale && game.state === 'in' ? 'Last: ' : '') + gameStatus(game)));
      for (const team of [game.away, game.home]) {
        const row = el('span', 'lb-score-team' + (game.completed && team.winner ? ' is-winner' : ''));
        row.append(el('span', '', team.abbreviation), el('b', '', game.state === 'pre' ? '–' : team.score ?? '–'));
        card.append(row);
      }
      fragment.append(card);
    }
    if (!data.games.length) fragment.append(el('p', 'lb-score-message', 'No games scheduled. Check back for the next slate.'));
    const scrollLeft = track.scrollLeft;
    track.replaceChildren(fragment); track.scrollLeft = scrollLeft;
    const stamp = new Date(data.fetchedAt).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
    status.textContent = `${stale ? 'Cached · ' : ''}${data.delaySeconds}s delay · ${stamp}`;
    navigation();
  }
  async function refresh(force = false) {
    if (document.hidden) return;
    const sport = selected;
    if (cache.has(sport) && Date.now() - cache.get(sport).fetchedAt >= 86400000) cache.delete(sport);
    const existing = cache.get(sport);
    if (!force && existing && Date.now() - existing.checkedAt < REFRESH_MS) { render(sport); return; }
    if (activeRequest?.sport === sport) return;
    activeRequest?.controller.abort();
    const request = {sport, controller: new AbortController()}; activeRequest = request;
    const timeout = setTimeout(() => request.controller.abort(), 40000);
    try {
      const response = await fetch(`https://scores.lineupbeat.com/${sport}`, {signal: request.controller.signal, credentials: 'omit'});
      if (!response.ok) throw new Error('Scores unavailable');
      const payload = await response.json();
      const games = normalize(payload);
      if (activeRequest !== request) return;
      cache.set(sport, {games, stale: payload.stale || !payload.complete, delaySeconds: payload.delaySeconds, fetchedAt: Date.parse(payload.updatedAt), checkedAt: Date.now()});
      if (sport === selected) render(sport);
    } catch (error) {
      if (sport !== selected || activeRequest !== request) return;
      if (cache.has(sport)) render(sport, true);
      else {
        status.textContent = 'Scores unavailable';
        track.replaceChildren(el('p', 'lb-score-message', 'Scores are temporarily unavailable. Please check back shortly.'));
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
