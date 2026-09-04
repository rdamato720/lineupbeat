const AUTHORIZE_URL = 'https://api.login.yahoo.com/oauth2/request_auth';
const TOKEN_URL = 'https://api.login.yahoo.com/oauth2/get_token';
const API_ROOT = 'https://fantasysports.yahooapis.com/fantasy/v2';
const SESSION_COOKIE = 'lb_yahoo_session';
const STATE_COOKIE = 'lb_yahoo_state';
const MAX_SESSION_AGE = 60 * 60 * 24 * 30;
const MAX_LEAGUES = 50;
const MAX_WEEKS = 25;
const LEAGUE_KEY = /^\d+\.l\.\d+$/;

class YahooError extends Error {
  constructor(message, status = 422) {
    super(message);
    this.status = status;
  }
}

function json(value, status = 200, headers = {}) {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      ...headers
    }
  });
}

function redirect(location, headers = {}) {
  return new Response(null, {status: 302, headers: {Location: location, ...headers}});
}

function clean(value) {
  return String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
}

function number(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function integer(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : fallback;
}

function truthy(value) {
  return value === true || value === 1 || value === '1' || value === 'true';
}

function own(object, key) {
  return object && typeof object === 'object' &&
    !Array.isArray(object) && Object.prototype.hasOwnProperty.call(object, key);
}

function primitive(value) {
  if (Array.isArray(value) && value.length === 1) return primitive(value[0]);
  if (value == null || ['string', 'number', 'boolean'].includes(typeof value)) return value;
  return undefined;
}

function deepValue(node, key) {
  if (own(node, key)) {
    const found = primitive(node[key]);
    if (found !== undefined) return found;
  }
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = deepValue(child, key);
      if (found !== undefined) return found;
    }
  } else if (node && typeof node === 'object') {
    for (const child of Object.values(node)) {
      const found = deepValue(child, key);
      if (found !== undefined) return found;
    }
  }
  return undefined;
}

function resourceValue(resource, key) {
  if (Array.isArray(resource) && resource.length) {
    const head = deepValue(resource[0], key);
    if (head !== undefined) return head;
  }
  return deepValue(resource, key);
}

function namedResources(node, name, output = []) {
  if (Array.isArray(node)) {
    node.forEach(child => namedResources(child, name, output));
  } else if (node && typeof node === 'object') {
    for (const [key, child] of Object.entries(node)) {
      if (key === name) output.push(child);
      namedResources(child, name, output);
    }
  }
  return output;
}

function namedValue(node, name, key) {
  for (const resource of namedResources(node, name)) {
    const found = resourceValue(resource, key);
    if (found !== undefined) return found;
  }
  return undefined;
}

function normalizeLeagueLink(value) {
  const text = clean(value);
  if (LEAGUE_KEY.test(text)) return text;
  const legacy = text.match(/^(\d+)_(\d+)$/);
  return legacy ? `${legacy[1]}.l.${legacy[2]}` : '';
}

function leagueRecords(payload) {
  const rows = [];
  const seen = new Set();
  for (const resource of namedResources(payload, 'league')) {
    const leagueKey = clean(resourceValue(resource, 'league_key'));
    const season = integer(resourceValue(resource, 'season'));
    const gameCode = clean(resourceValue(resource, 'game_code'));
    if (!LEAGUE_KEY.test(leagueKey) || !season || (gameCode && gameCode !== 'nfl') || seen.has(leagueKey)) {
      continue;
    }
    seen.add(leagueKey);
    rows.push({
      leagueKey,
      leagueId: clean(resourceValue(resource, 'league_id')),
      name: clean(resourceValue(resource, 'name')) || 'Yahoo league',
      season,
      teamCount: integer(resourceValue(resource, 'num_teams')),
      isFinished: truthy(resourceValue(resource, 'is_finished')),
      renew: normalizeLeagueLink(resourceValue(resource, 'renew')),
      renewed: normalizeLeagueLink(resourceValue(resource, 'renewed'))
    });
  }
  return rows.sort((a, b) => b.season - a.season || a.name.localeCompare(b.name));
}

export function discoverYahooLeagues(payload) {
  const leagues = leagueRecords(payload).slice(0, MAX_LEAGUES);
  const byKey = new Map(leagues.map(row => [row.leagueKey, row]));
  const parent = new Map(leagues.map(row => [row.leagueKey, row.leagueKey]));
  function find(key) {
    while (parent.get(key) !== key) {
      parent.set(key, parent.get(parent.get(key)));
      key = parent.get(key);
    }
    return key;
  }
  function union(left, right) {
    if (!parent.has(left) || !parent.has(right)) return;
    left = find(left);
    right = find(right);
    if (left !== right) parent.set(right, left);
  }
  leagues.forEach(row => {
    if (row.renew) union(row.leagueKey, row.renew);
    if (row.renewed) union(row.leagueKey, row.renewed);
  });
  const groups = new Map();
  leagues.forEach(row => {
    const root = find(row.leagueKey);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(row);
  });
  return [...groups.values()].map(rows => {
    rows.sort((a, b) => a.season - b.season);
    const latest = rows[rows.length - 1];
    return {
      id: latest.leagueKey,
      name: latest.name,
      firstSeason: rows[0].season,
      lastSeason: latest.season,
      seasons: rows
    };
  }).sort((a, b) => b.lastSeason - a.lastSeason || a.name.localeCompare(b.name));
}

function managersForTeam(team, leagueKey) {
  const managers = [];
  const seen = new Set();
  for (const manager of namedResources(team, 'manager')) {
    const managerId = clean(resourceValue(manager, 'manager_id'));
    const guid = clean(resourceValue(manager, 'guid'));
    const rawId = guid || (managerId ? `${leagueKey}:${managerId}` : '');
    const name = clean(resourceValue(manager, 'nickname') ||
      resourceValue(manager, 'display_name'));
    if (!rawId || !name || seen.has(rawId)) continue;
    seen.add(rawId);
    managers.push({identityId: `yahoo:${rawId}`, displayName: name});
  }
  return managers;
}

function teamsFromStandings(payload, leagueKey) {
  const teams = [];
  const identities = new Map();
  const seen = new Set();
  for (const team of namedResources(payload, 'team')) {
    const teamKey = clean(resourceValue(team, 'team_key'));
    if (!teamKey || seen.has(teamKey)) continue;
    const managers = managersForTeam(team, leagueKey);
    if (!managers.length) {
      throw new YahooError('Yahoo returned a team without a manager. Import stopped without saving.');
    }
    managers.forEach(row => identities.set(row.identityId, row));
    seen.add(teamKey);
    teams.push({
      teamId: `yahoo:${teamKey}`,
      teamName: clean(resourceValue(team, 'name')) || 'Unnamed Yahoo team',
      ownerIds: managers.map(row => row.identityId),
      owners: managers.map(row => ({id: row.identityId, displayName: row.displayName})),
      wins: number(namedValue(team, 'outcome_totals', 'wins')),
      losses: number(namedValue(team, 'outcome_totals', 'losses')),
      ties: number(namedValue(team, 'outcome_totals', 'ties')),
      pointsFor: number(resourceValue(team, 'points_for')),
      pointsAgainst: number(resourceValue(team, 'points_against')),
      playoffSeed: number(resourceValue(team, 'playoff_seed')),
      finalStanding: number(resourceValue(team, 'rank'))
    });
  }
  if (teams.length < 2) {
    throw new YahooError('Yahoo did not return enough teams for this season. Import stopped without saving.');
  }
  return {teams, identities: [...identities.values()]};
}

function matchupRows(payload, playoffStartWeek, complete, usesPlayoff = true) {
  const rows = [];
  const seen = new Set();
  for (const matchup of namedResources(payload, 'matchup')) {
    const week = integer(resourceValue(matchup, 'week'));
    const status = clean(resourceValue(matchup, 'status')).toLowerCase();
    if (!week || (!complete && status && status !== 'postevent')) continue;
    const teams = [];
    const teamSeen = new Set();
    for (const team of namedResources(matchup, 'team')) {
      const key = clean(resourceValue(team, 'team_key'));
      if (!key || teamSeen.has(key)) continue;
      const total = namedValue(team, 'team_points', 'total');
      if (total === undefined || total === '') {
        throw new YahooError(`Yahoo returned an invalid score in Week ${week}. Import stopped without saving.`);
      }
      teamSeen.add(key);
      teams.push({key, score: number(total, NaN)});
    }
    if (teams.length === 1) continue;
    if (teams.length !== 2 || teams.some(row => !Number.isFinite(row.score))) {
      throw new YahooError(`Yahoo returned an invalid matchup in Week ${week}. Import stopped without saving.`);
    }
    const id = `${week}:${teams.map(row => row.key).sort().join(':')}`;
    if (seen.has(id)) continue;
    seen.add(id);
    rows.push({
      id: `yahoo:${id}`,
      week,
      playoff: truthy(resourceValue(matchup, 'is_playoffs')) ||
        (usesPlayoff && week >= playoffStartWeek),
      homeTeamId: `yahoo:${teams[0].key}`,
      awayTeamId: `yahoo:${teams[1].key}`,
      homeScore: teams[0].score,
      awayScore: teams[1].score
    });
  }
  return rows.sort((a, b) => a.week - b.week || a.id.localeCompare(b.id));
}

export function parseYahooSeason({league, settings, standings, scoreboards}) {
  const leagueResource = namedResources(league, 'league')[0];
  if (!leagueResource) throw new YahooError('Yahoo league details are unavailable.');
  const leagueKey = clean(resourceValue(leagueResource, 'league_key'));
  const year = integer(resourceValue(leagueResource, 'season'));
  const leagueName = clean(resourceValue(leagueResource, 'name')) || 'Yahoo league';
  const startWeek = Math.max(1, integer(resourceValue(leagueResource, 'start_week'), 1));
  const endWeek = integer(resourceValue(leagueResource, 'end_week'));
  const complete = truthy(resourceValue(leagueResource, 'is_finished'));
  const usesPlayoffValue = deepValue(settings, 'uses_playoff');
  const usesPlayoff = usesPlayoffValue === undefined ? true : truthy(usesPlayoffValue);
  const playoffStart = usesPlayoff ?
    integer(deepValue(settings, 'playoff_start_week'), endWeek || 15) : endWeek + 1;
  if (!LEAGUE_KEY.test(leagueKey) || year < 1990 || year > 2200 || !endWeek || endWeek > MAX_WEEKS) {
    throw new YahooError('Yahoo returned invalid league-season details. Import stopped without saving.');
  }
  const parsedTeams = teamsFromStandings(standings, leagueKey);
  const matchups = matchupRows(scoreboards, playoffStart, complete, usesPlayoff);
  if (!matchups.length && complete) {
    throw new YahooError(`Yahoo returned no completed matchups for ${year}. Import stopped without saving.`);
  }
  const teamIds = new Set(parsedTeams.teams.map(row => row.teamId));
  if (matchups.some(row => !teamIds.has(row.homeTeamId) || !teamIds.has(row.awayTeamId))) {
    throw new YahooError(`Yahoo returned a matchup with an unknown team in ${year}. Import stopped without saving.`);
  }
  return {
    league: {id: leagueKey, name: leagueName},
    season: {
      year,
      leagueName,
      regularSeasonWeeks: Math.max(1, Math.min(endWeek, playoffStart - 1)),
      complete,
      teams: parsedTeams.teams,
      matchups,
      source: {provider: 'yahoo', leagueKey, startWeek, endWeek}
    },
    identities: parsedTeams.identities
  };
}

function cookieMap(request) {
  const output = {};
  for (const part of (request.headers.get('Cookie') || '').split(';')) {
    const index = part.indexOf('=');
    if (index > 0) output[part.slice(0, index).trim()] = part.slice(index + 1).trim();
  }
  return output;
}

function base64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function fromBase64url(value) {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(normalized + '='.repeat((4 - normalized.length % 4) % 4));
  return Uint8Array.from(binary, char => char.charCodeAt(0));
}

async function sessionKey(secret) {
  if (!secret || String(secret).length < 32) {
    throw new YahooError('Yahoo connection is not configured.', 503);
  }
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(String(secret)));
  return crypto.subtle.importKey('raw', digest, 'AES-GCM', false, ['encrypt', 'decrypt']);
}

export async function sealYahooSession(value, secret) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encoded = new TextEncoder().encode(JSON.stringify(value));
  const encrypted = await crypto.subtle.encrypt({name: 'AES-GCM', iv}, await sessionKey(secret), encoded);
  return `${base64url(iv)}.${base64url(new Uint8Array(encrypted))}`;
}

export async function openYahooSession(value, secret) {
  try {
    const [iv, encrypted] = String(value || '').split('.');
    if (!iv || !encrypted) return null;
    const decoded = await crypto.subtle.decrypt(
      {name: 'AES-GCM', iv: fromBase64url(iv)},
      await sessionKey(secret), fromBase64url(encrypted));
    const session = JSON.parse(new TextDecoder().decode(decoded));
    return session && typeof session === 'object' ? session : null;
  } catch (_) {
    return null;
  }
}

function cookie(name, value, maxAge = MAX_SESSION_AGE) {
  return `${name}=${value}; Path=/api/yahoo; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Lax`;
}

function configured(env) {
  return Boolean(env && env.YAHOO_CLIENT_ID && env.YAHOO_CLIENT_SECRET &&
    env.YAHOO_SESSION_SECRET && String(env.YAHOO_SESSION_SECRET).length >= 32);
}

function callbackUrl(request) {
  return new URL('/api/yahoo/callback', request.url).toString();
}

async function tokenRequest(env, body) {
  const authorization = btoa(`${env.YAHOO_CLIENT_ID}:${env.YAHOO_CLIENT_SECRET}`);
  const response = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: {
      Authorization: `Basic ${authorization}`,
      'Content-Type': 'application/x-www-form-urlencoded',
      Accept: 'application/json'
    },
    body: new URLSearchParams(body)
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.access_token) {
    throw new YahooError('Yahoo authorization could not be completed.', 502);
  }
  return {
    accessToken: clean(result.access_token),
    refreshToken: clean(result.refresh_token || body.refresh_token),
    expiresAt: Date.now() + Math.max(60, integer(result.expires_in, 3600)) * 1000
  };
}

async function authorizedSession(request, env) {
  const value = cookieMap(request)[SESSION_COOKIE];
  let session = await openYahooSession(value, env.YAHOO_SESSION_SECRET);
  if (!session || !session.accessToken) throw new YahooError('Connect Yahoo to continue.', 401);
  let setCookie = '';
  if (number(session.expiresAt) <= Date.now() + 60000) {
    if (!session.refreshToken) throw new YahooError('Reconnect Yahoo to continue.', 401);
    session = await tokenRequest(env, {
      grant_type: 'refresh_token',
      refresh_token: session.refreshToken,
      redirect_uri: callbackUrl(request)
    });
    setCookie = cookie(SESSION_COOKIE, await sealYahooSession(session, env.YAHOO_SESSION_SECRET));
  }
  return {session, setCookie};
}

async function yahooFetch(path, accessToken) {
  const response = await fetch(`${API_ROOT}${path}${path.includes('?') ? '&' : '?'}format=json`, {
    headers: {Authorization: `Bearer ${accessToken}`, Accept: 'application/json'}
  });
  const value = await response.json().catch(() => null);
  if (!response.ok || !value) {
    if (response.status === 401) throw new YahooError('Reconnect Yahoo to continue.', 401);
    throw new YahooError('Yahoo league data is temporarily unavailable.', 502);
  }
  return value;
}

async function status(request, env) {
  let connected = false;
  if (configured(env)) {
    connected = Boolean(await openYahooSession(cookieMap(request)[SESSION_COOKIE], env.YAHOO_SESSION_SECRET));
  }
  return json({configured: configured(env), connected});
}

async function connect(request, env) {
  if (!configured(env)) throw new YahooError('Yahoo connection is not configured.', 503);
  const stateBytes = crypto.getRandomValues(new Uint8Array(24));
  const state = base64url(stateBytes);
  const target = new URL(AUTHORIZE_URL);
  target.searchParams.set('client_id', env.YAHOO_CLIENT_ID);
  target.searchParams.set('redirect_uri', callbackUrl(request));
  target.searchParams.set('response_type', 'code');
  target.searchParams.set('state', state);
  return redirect(target.toString(), {'Set-Cookie': cookie(STATE_COOKIE, state, 600)});
}

async function callback(request, env) {
  if (!configured(env)) throw new YahooError('Yahoo connection is not configured.', 503);
  const url = new URL(request.url);
  const state = clean(url.searchParams.get('state'));
  const expected = cookieMap(request)[STATE_COOKIE];
  if (!state || !expected || state !== expected) {
    throw new YahooError('Yahoo connection expired. Please try again.', 400);
  }
  if (url.searchParams.get('error')) {
    return redirect('/league-history/?yahoo=cancelled', {
      'Set-Cookie': cookie(STATE_COOKIE, '', 0)
    });
  }
  const code = clean(url.searchParams.get('code'));
  if (!code) throw new YahooError('Yahoo did not return an authorization code.', 400);
  const session = await tokenRequest(env, {
    grant_type: 'authorization_code',
    code,
    redirect_uri: callbackUrl(request)
  });
  const sealed = await sealYahooSession(session, env.YAHOO_SESSION_SECRET);
  const headers = new Headers({Location: '/league-history/?yahoo=connected'});
  headers.append('Set-Cookie', cookie(SESSION_COOKIE, sealed));
  headers.append('Set-Cookie', cookie(STATE_COOKIE, '', 0));
  return new Response(null, {status: 302, headers});
}

async function leagues(request, env) {
  const auth = await authorizedSession(request, env);
  const raw = await yahooFetch('/users;use_login=1/games;game_codes=nfl/leagues', auth.session.accessToken);
  const families = discoverYahooLeagues(raw);
  if (!families.length) throw new YahooError('No Yahoo Fantasy Football leagues were found for this account.', 404);
  return json({families}, 200, auth.setCookie ? {'Set-Cookie': auth.setCookie} : {});
}

async function season(request, env) {
  const leagueKey = clean(new URL(request.url).searchParams.get('league_key'));
  if (!LEAGUE_KEY.test(leagueKey)) throw new YahooError('Choose a valid Yahoo league season.', 400);
  const auth = await authorizedSession(request, env);
  const league = await yahooFetch(`/league/${encodeURIComponent(leagueKey)}`, auth.session.accessToken);
  const resource = namedResources(league, 'league')[0];
  const endWeek = integer(resourceValue(resource, 'end_week'));
  const currentWeek = integer(resourceValue(resource, 'current_week'), endWeek);
  const complete = truthy(resourceValue(resource, 'is_finished'));
  const lastWeek = Math.min(MAX_WEEKS, complete ? endWeek : currentWeek);
  if (!endWeek || lastWeek < 1) throw new YahooError('Yahoo returned invalid season dates.', 502);
  const [settings, standings] = await Promise.all([
    yahooFetch(`/league/${encodeURIComponent(leagueKey)}/settings`, auth.session.accessToken),
    yahooFetch(`/league/${encodeURIComponent(leagueKey)}/standings`, auth.session.accessToken)
  ]);
  const scoreboards = [];
  for (let offset = 0; offset < lastWeek; offset += 5) {
    const chunk = await Promise.all(
      Array.from({length: Math.min(5, lastWeek - offset)}, (_, index) =>
        yahooFetch(`/league/${encodeURIComponent(leagueKey)}/scoreboard;week=${offset + index + 1}`,
          auth.session.accessToken))
    );
    scoreboards.push(...chunk);
  }
  const parsed = parseYahooSeason({league, settings, standings, scoreboards});
  return json(parsed, 200, auth.setCookie ? {'Set-Cookie': auth.setCookie} : {});
}

function disconnect() {
  const headers = new Headers({'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store'});
  headers.append('Set-Cookie', cookie(SESSION_COOKIE, '', 0));
  headers.append('Set-Cookie', cookie(STATE_COOKIE, '', 0));
  return new Response(JSON.stringify({ok: true}), {status: 200, headers});
}

function route(request) {
  const match = new URL(request.url).pathname.match(/^\/api\/yahoo\/([^/]+)\/?$/);
  return match ? match[1] : '';
}

function errorResponse(error) {
  const statusCode = error && error.status || 503;
  const message = error && error.status ? error.message : 'Yahoo connection is temporarily unavailable.';
  if (!error || !error.status) console.error('Yahoo history request failed.');
  return json({error: message}, statusCode);
}

export async function onRequestGet(context) {
  try {
    const path = route(context.request);
    if (path === 'status') return await status(context.request, context.env);
    if (path === 'connect') return await connect(context.request, context.env);
    if (path === 'callback') return await callback(context.request, context.env);
    if (path === 'leagues') return await leagues(context.request, context.env);
    if (path === 'season') return await season(context.request, context.env);
    return json({error: 'Not found.'}, 404);
  } catch (error) {
    return errorResponse(error);
  }
}

export async function onRequestPost(context) {
  try {
    if (route(context.request) !== 'disconnect') return json({error: 'Not found.'}, 404);
    const origin = context.request.headers.get('Origin');
    if (origin && origin !== new URL(context.request.url).origin) {
      return json({error: 'Origin not allowed.'}, 403);
    }
    return disconnect();
  } catch (error) {
    return errorResponse(error);
  }
}

export function onRequestOptions() {
  return new Response(null, {status: 204, headers: {Allow: 'GET, POST, OPTIONS'}});
}
