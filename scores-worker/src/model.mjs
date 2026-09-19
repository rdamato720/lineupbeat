// Provider data is used only for the score strip, never fantasy inputs.
export const DAY = 86400000;
export const MINUTE = 60000;
const finals = new Set(['STATUS_FINAL', 'STATUS_FINAL_OT', 'STATUS_FINAL_AET', 'STATUS_FINAL_PEN', 'STATUS_FORFEIT']);
const active = new Set(['STATUS_IN_PROGRESS', 'STATUS_HALFTIME', 'STATUS_END_PERIOD', 'STATUS_END_OF_REGULATION', 'STATUS_OVERTIME', 'STATUS_FIRST_HALF', 'STATUS_SECOND_HALF']);
const labels = {STATUS_SCHEDULED:'Scheduled', STATUS_TBD:'Time TBD', STATUS_POSTPONED:'Postponed', STATUS_CANCELED:'Canceled', STATUS_CANCELLED:'Canceled', STATUS_SUSPENDED:'Suspended', STATUS_DELAYED:'Delayed', STATUS_RAIN_DELAY:'Weather delay', STATUS_HALFTIME:'Halftime', STATUS_END_PERIOD:'End of period', STATUS_END_OF_REGULATION:'End of regulation', STATUS_OVERTIME:'Overtime'};
const conferences = new Set(['acc','atlanticcoast','bigten','big10','big12','bigtwelve','southeastern','sec','americanathletic','american','theamerican','usa','cusa','midamerican','mac','mountainwest','mwc','pac12','pacific12','sunbelt','fbsindependents','iaindependents','divisionifbsindependents']);
const compact = s => String(s || '').toLowerCase().replace(/conference/g, '').replace(/[^a-z0-9]/g, '');
export function fbsTeams(payload) {
  if (!Array.isArray(payload.teams)) throw new Error('Invalid college team directory');
  const ids = payload.teams.filter(t => {
    const division = compact(t.division?.name);
    const name = compact(t.name);
    return conferences.has(compact(t.conference?.name)) || ['fbs','divisionifbs','footballbowlsubdivision'].includes(division) || ['notredame','connecticut','uconn'].includes(name);
  }).map(t => String(t.team_id));
  // Fail closed if provider classifications changed; never call a 68-team list all FBS.
  if (new Set(ids).size < 130 || new Set(ids).size > 160) throw new Error('FBS directory requires review');
  return [...new Set(ids)];
}
const points = n => n !== null && n !== undefined && n !== '' && Number.isInteger(Number(n)) && Number(n) >= 0 ? Number(n) : null;
export function normalize(events, sport, fbs = []) {
  if (!Array.isArray(events)) throw new Error('Invalid games');
  const allowed = new Set(fbs.map(String));
  return events.flatMap(e => {
    if (e.sport_id !== (sport === 'nfl' ? 2 : 1)) return [];
    const s = e.score || {};
    const away = e.teams?.find(t => t.is_away === true || (s.team_id_away != null && t.team_id === s.team_id_away));
    const home = e.teams?.find(t => t.is_home === true || (s.team_id_home != null && t.team_id === s.team_id_home));
    if (!away || !home || away.team_id === home.team_id || !/^[a-zA-Z0-9-]{1,80}$/.test(e.event_id || '') || !Number.isFinite(Date.parse(e.event_date))) return [];
    if (sport === 'college' && ![away,home].some(t => allowed.has(String(t.team_id)))) return [];
    const status = s.event_status || 'STATUS_NOT_AVAILABLE';
    const completed = finals.has(status);
    const state = completed ? 'post' : active.has(status) ? 'in' : status === 'STATUS_SCHEDULED' || status === 'STATUS_TBD' ? 'pre' : 'unknown';
    const map = (t, n, winner) => {
      const name = [t.name,t.mascot].filter(Boolean).join(' ').slice(0,120);
      return {name, abbreviation: String(t.abbreviation || t.name || 'Team').slice(0,45), score: state === 'pre' ? null : points(n), winner: completed && Number(winner) === 1};
    };
    return [{id:e.event_id, date:e.event_date, state, completed, timeValid:status !== 'STATUS_TBD', detail:completed ? 'Final' : labels[status] || (state === 'in' ? 'Live' : 'Status unavailable'), away:map(away,s.score_away,s.winner_away), home:map(home,s.score_home,s.winner_home)}];
  });
}
export function marketDay(now) {
  // UTC-5 is a fixed provider market-day boundary, matching offset=300.
  return new Date(now - 5 * 3600000).toISOString().slice(0,10);
}
export function dates(now) {
  const start = Date.parse(marketDay(now) + 'T00:00:00Z');
  return Array.from({length:8}, (_,i) => new Date(start + (i - 1) * DAY).toISOString().slice(0,10));
}
export function ttl(games, now, day) {
  if (games.some(g => g.state === 'in' || (!g.completed && g.state === 'pre' && Date.parse(g.date) <= now + 5*MINUTE && Date.parse(g.date) > now - 8*3600000))) return MINUTE;
  const base = day === marketDay(now) ? 15*MINUTE : 6*3600000;
  const starts = games.filter(g => g.state === 'pre' && Date.parse(g.date) > now).map(g => Math.max(MINUTE, Date.parse(g.date)-now-5*MINUTE));
  return Math.min(base, ...starts);
}
export function sortGames(games) {
  const priority = g => g.state === 'in' ? 0 : g.completed ? 2 : 1;
  return games.sort((a,b) => priority(a)-priority(b) || (a.completed && b.completed ? -1 : 1)*(Date.parse(a.date)-Date.parse(b.date)));
}
