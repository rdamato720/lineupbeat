const MAX_PUBLICATION_BYTES = 1_800_000;
const MAX_SEASONS = 50;
const MAX_TEAMS_PER_SEASON = 32;
const MAX_MATCHUPS = 6_000;
const MAX_IDENTITIES = 128;
const SLUG = /^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$/;
const PUBLICATION_SCHEMA = [
  `CREATE TABLE IF NOT EXISTS league_history_publications (
    slug TEXT PRIMARY KEY,
    league_name TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('unlisted', 'public')),
    archive_json TEXT NOT NULL,
    manage_token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  )`,
  `CREATE INDEX IF NOT EXISTS idx_league_history_visibility_updated
   ON league_history_publications (visibility, updated_at DESC)`
];
let schemaPromise = null;

function fail(message, status = 422) {
  const error = new Error(message);
  error.status = status;
  throw error;
}

function object(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail(`${label} is invalid.`);
  }
  return value;
}

function text(value, label, max = 120) {
  const cleaned = String(value == null ? '' : value).trim();
  if (!cleaned || cleaned.length > max) fail(`${label} is invalid.`);
  return cleaned;
}

function integer(value, label, min, max) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    fail(`${label} is invalid.`);
  }
  return parsed;
}

function finite(value, label, min = 0, max = 10000000) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < min || parsed > max) {
    fail(`${label} is invalid.`);
  }
  return parsed;
}

function isoDate(value, label) {
  const cleaned = text(value, label, 40);
  if (!Number.isFinite(Date.parse(cleaned))) fail(`${label} is invalid.`);
  return new Date(cleaned).toISOString();
}

function visibility(value) {
  const cleaned = String(value || '').toLowerCase();
  if (!['unlisted', 'public'].includes(cleaned)) {
    fail('Choose unlisted or public visibility.');
  }
  return cleaned;
}

function byteLength(value) {
  return new TextEncoder().encode(value).byteLength;
}

export function sanitizePublication(rawArchive, rawReview) {
  const archive = object(rawArchive, 'League history');
  const review = object(rawReview, 'Manager review');
  if (archive.schemaVersion !== 'lineupbeat-espn-history-capture-v1') {
    fail('This league-history format is not supported.');
  }
  if (review.schemaVersion !== 'lineupbeat-history-identity-review-v1') {
    fail('Manager matching must be completed before publishing.');
  }

  const league = object(archive.league, 'League');
  const leagueName = text(league.name, 'League name', 100);
  const capturedAt = isoDate(archive.capturedAt, 'Capture date');
  const identityReview = object(archive.identityReview, 'Manager identities');
  const sourceIdentities = Array.isArray(identityReview.identities)
    ? identityReview.identities : fail('Manager identities are invalid.');
  if (!sourceIdentities.length || sourceIdentities.length > MAX_IDENTITIES) {
    fail('Manager identities are invalid.');
  }

  const identityIds = new Map();
  const identities = sourceIdentities.map((raw, index) => {
    const row = object(raw, 'Manager identity');
    const sourceId = text(row.identityId, 'Manager identity', 180);
    if (identityIds.has(sourceId)) fail('Manager identities contain a duplicate.');
    const publicId = `m${index + 1}`;
    identityIds.set(sourceId, publicId);
    return {
      identityId: publicId,
      displayName: text(row.displayName, 'Manager name', 80)
    };
  });

  const reviewById = new Map();
  if (!Array.isArray(review.identities)) fail('Manager review is invalid.');
  review.identities.forEach(raw => {
    const row = object(raw, 'Manager review');
    const sourceId = text(row.identityId, 'Manager identity', 180);
    if (!identityIds.has(sourceId)) fail('Manager review contains an unknown identity.');
    const mergeInto = row.mergeInto == null ? null : text(row.mergeInto, 'Manager match', 180);
    if (mergeInto && !identityIds.has(mergeInto)) {
      fail('Manager review contains an unknown match.');
    }
    reviewById.set(sourceId, mergeInto);
  });
  if (reviewById.size !== identities.length) {
    fail('Every manager must be reviewed before publishing.');
  }
  const publicReview = {
    schemaVersion: 'lineupbeat-public-history-review-v1',
    identities: sourceIdentities.map((raw, index) => ({
      identityId: `m${index + 1}`,
      displayName: identities[index].displayName,
      mergeInto: reviewById.get(String(raw.identityId).trim())
        ? identityIds.get(reviewById.get(String(raw.identityId).trim())) : null
    }))
  };

  const rawSeasons = Array.isArray(archive.seasons) ? archive.seasons : [];
  if (!rawSeasons.length || rawSeasons.length > MAX_SEASONS) {
    fail('League seasons are invalid.');
  }
  let matchupCount = 0;
  const seasons = rawSeasons.map((rawSeason, seasonIndex) => {
    const season = object(rawSeason, 'Season');
    const year = integer(season.year, 'Season year', 1990, 2200);
    const rawTeams = Array.isArray(season.teams) ? season.teams : [];
    if (rawTeams.length < 2 || rawTeams.length > MAX_TEAMS_PER_SEASON) {
      fail(`Teams for ${year} are invalid.`);
    }
    const teamIds = new Map();
    const teams = rawTeams.map((rawTeam, teamIndex) => {
      const team = object(rawTeam, 'Team');
      const sourceTeamId = text(team.teamId, 'Team identity', 120);
      if (teamIds.has(sourceTeamId)) fail(`Teams for ${year} contain a duplicate.`);
      const publicTeamId = `s${seasonIndex + 1}t${teamIndex + 1}`;
      teamIds.set(sourceTeamId, publicTeamId);
      const ownerIds = Array.isArray(team.ownerIds) ? team.ownerIds : [];
      if (!ownerIds.length || ownerIds.length > 4) fail(`Owners for ${year} are invalid.`);
      return {
        teamId: publicTeamId,
        teamName: text(team.teamName, 'Team name', 100),
        ownerIds: ownerIds.map(ownerId => {
          const mapped = identityIds.get(String(ownerId).trim());
          if (!mapped) fail(`A team in ${year} has an unknown manager.`);
          return mapped;
        }),
        wins: finite(team.wins || 0, 'Wins', 0, 100),
        losses: finite(team.losses || 0, 'Losses', 0, 100),
        ties: finite(team.ties || 0, 'Ties', 0, 100),
        pointsFor: finite(team.pointsFor || 0, 'Points for'),
        pointsAgainst: finite(team.pointsAgainst || 0, 'Points against'),
        playoffSeed: finite(team.playoffSeed || 0, 'Playoff seed', 0, 64),
        finalStanding: finite(team.finalStanding || 0, 'Final standing', 0, 64)
      };
    });
    const rawMatchups = Array.isArray(season.matchups) ? season.matchups : [];
    matchupCount += rawMatchups.length;
    if (matchupCount > MAX_MATCHUPS) fail('This archive has too many matchups.');
    const matchups = rawMatchups.map((rawMatchup, matchupIndex) => {
      const matchup = object(rawMatchup, 'Matchup');
      const homeTeamId = teamIds.get(String(matchup.homeTeamId).trim());
      const awayTeamId = teamIds.get(String(matchup.awayTeamId).trim());
      if (!homeTeamId || !awayTeamId || homeTeamId === awayTeamId) {
        fail(`A matchup in ${year} has an invalid team.`);
      }
      return {
        id: `s${seasonIndex + 1}g${matchupIndex + 1}`,
        week: integer(matchup.week, 'Matchup week', 1, 30),
        playoff: Boolean(matchup.playoff),
        homeTeamId,
        awayTeamId,
        // Custom fantasy scoring can produce a legitimate negative weekly total.
        // Keep the bound finite and defensive without rejecting valid history.
        homeScore: finite(matchup.homeScore, 'Home score', -1000, 1000),
        awayScore: finite(matchup.awayScore, 'Away score', -1000, 1000)
      };
    });
    return {
      year,
      leagueName,
      regularSeasonWeeks: integer(season.regularSeasonWeeks, 'Regular-season weeks', 1, 25),
      complete: Boolean(season.complete),
      teams,
      matchups
    };
  }).sort((a, b) => a.year - b.year);

  const publicArchive = {
    schemaVersion: 'lineupbeat-public-history-v1',
    capturedAt,
    league: {name: leagueName},
    seasons,
    incomplete: (Array.isArray(archive.incomplete) ? archive.incomplete : [])
      .slice(0, MAX_SEASONS).map(raw => ({
        year: integer(object(raw, 'Unavailable season').year, 'Unavailable season', 1990, 2200)
      })),
    identityReview: {identities, suggestions: []},
    counts: {
      seasons: seasons.length,
      teams: Math.max(...seasons.map(row => row.teams.length)),
      matchups: matchupCount,
      identities: identities.length
    }
  };
  const value = {archive: publicArchive, review: publicReview};
  const encoded = JSON.stringify(value);
  if (byteLength(encoded) > MAX_PUBLICATION_BYTES) {
    fail('This league archive is too large to publish.', 413);
  }
  return {value, encoded};
}

export function slugBase(name) {
  const base = String(name || '').toLowerCase().normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '').slice(0, 42).replace(/-+$/g, '');
  return base || 'league';
}

function randomText(bytes = 18) {
  const data = new Uint8Array(bytes);
  crypto.getRandomValues(data);
  return btoa(String.fromCharCode(...data))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

export function createManageToken() {
  return randomText(32);
}

function randomSlugSuffix(bytes = 5) {
  const data = new Uint8Array(bytes);
  crypto.getRandomValues(data);
  return [...data].map(value => value.toString(16).padStart(2, '0')).join('');
}

export function createSlug(name) {
  return `${slugBase(name)}-${randomSlugSuffix()}`;
}

export async function hashToken(token) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token));
  return [...new Uint8Array(digest)]
    .map(value => value.toString(16).padStart(2, '0')).join('');
}

function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'X-Content-Type-Options': 'nosniff',
      ...extraHeaders
    }
  });
}

function sameOrigin(request) {
  const origin = request.headers.get('Origin');
  return !origin || origin === new URL(request.url).origin;
}

function pathSlug(request) {
  const match = new URL(request.url).pathname.match(/^\/api\/leagues\/([^/]+)\/?$/);
  const slug = match ? decodeURIComponent(match[1]) : '';
  return SLUG.test(slug) ? slug : '';
}

async function body(request) {
  const length = Number(request.headers.get('Content-Length') || 0);
  if (length > MAX_PUBLICATION_BYTES + 100000) fail('This league archive is too large.', 413);
  try { return await request.json(); }
  catch { fail('The publish request is invalid.', 400); }
}

function authorization(request) {
  const header = request.headers.get('Authorization') || '';
  return header.startsWith('Bearer ') ? header.slice(7) : '';
}

async function ensureSchema(env) {
  if (!env || !env.LEAGUE_HISTORY_DB ||
      typeof env.LEAGUE_HISTORY_DB.prepare !== 'function') {
    fail('League publishing storage is unavailable.', 503);
  }
  if (!schemaPromise) {
    schemaPromise = (async () => {
      for (const statement of PUBLICATION_SCHEMA) {
        await env.LEAGUE_HISTORY_DB.prepare(statement).run();
      }
    })()
      .catch(error => {
        schemaPromise = null;
        throw error;
      });
  }
  await schemaPromise;
}

async function createPublication(request, env) {
  await ensureSchema(env);
  const raw = await body(request);
  const access = visibility(raw.visibility);
  const publication = sanitizePublication(raw.archive, raw.review);
  const leagueName = publication.value.archive.league.name;
  const token = createManageToken();
  const tokenHash = await hashToken(token);
  const now = new Date().toISOString();
  let slug;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const candidate = createSlug(leagueName);
    const found = await env.LEAGUE_HISTORY_DB.prepare(
      'SELECT slug FROM league_history_publications WHERE slug = ?'
    ).bind(candidate).first();
    if (!found) { slug = candidate; break; }
  }
  if (!slug) fail('A share link could not be created. Try again.', 503);
  await env.LEAGUE_HISTORY_DB.prepare(
    `INSERT INTO league_history_publications
     (slug, league_name, visibility, archive_json, manage_token_hash, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  ).bind(slug, leagueName, access, publication.encoded, tokenHash, now, now).run();
  const origin = new URL(request.url).origin;
  return json({ok: true, slug, url: `${origin}/leagues/${slug}`,
    visibility: access, manageToken: token, updatedAt: now}, 201,
  {'Cache-Control': 'no-store'});
}

async function updatePublication(request, env, slug) {
  const authorized = await authorizePublication(request, env, slug);
  if (authorized.response) return authorized.response;
  const raw = await body(request);
  const access = visibility(raw.visibility);
  const publication = sanitizePublication(raw.archive, raw.review);
  const now = new Date().toISOString();
  await env.LEAGUE_HISTORY_DB.prepare(
    `UPDATE league_history_publications
     SET league_name = ?, visibility = ?, archive_json = ?, updated_at = ?
     WHERE slug = ?`
  ).bind(publication.value.archive.league.name, access,
    publication.encoded, now, slug).run();
  const origin = new URL(request.url).origin;
  return json({ok: true, slug, url: `${origin}/leagues/${slug}`,
    visibility: access, updatedAt: now}, 200, {'Cache-Control': 'no-store'});
}

async function authorizePublication(request, env, slug) {
  await ensureSchema(env);
  const token = authorization(request);
  if (!token || token.length > 200) {
    return {response: json({error: 'Publishing access is required.'}, 401)};
  }
  const current = await env.LEAGUE_HISTORY_DB.prepare(
    `SELECT slug, league_name, visibility, manage_token_hash, updated_at
     FROM league_history_publications WHERE slug = ?`
  ).bind(slug).first();
  if (!current || current.manage_token_hash !== await hashToken(token)) {
    return {response: json({error: 'That share link and recovery key do not match.'}, 403)};
  }
  return {current};
}

async function recoverPublication(request, env, slug) {
  const authorized = await authorizePublication(request, env, slug);
  if (authorized.response) return authorized.response;
  const row = authorized.current;
  const origin = new URL(request.url).origin;
  return json({ok: true, slug: row.slug, name: row.league_name,
    url: `${origin}/leagues/${row.slug}`, visibility: row.visibility,
    updatedAt: row.updated_at}, 200, {'Cache-Control': 'no-store'});
}

async function deletePublication(request, env, slug) {
  const authorized = await authorizePublication(request, env, slug);
  if (authorized.response) return authorized.response;
  await env.LEAGUE_HISTORY_DB.prepare(
    'DELETE FROM league_history_publications WHERE slug = ?'
  ).bind(slug).run();
  return new Response(null, {
    status: 204,
    headers: {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
  });
}

export async function onRequestPost(context) {
  if (!sameOrigin(context.request)) return json({error: 'Origin not allowed.'}, 403);
  const path = new URL(context.request.url).pathname;
  if (!/^\/api\/leagues\/?$/.test(path)) return json({error: 'Not found.'}, 404);
  try { return await createPublication(context.request, context.env); }
  catch (error) {
    console.error('League publication failed.', error && error.message);
    return json({error: error && error.status ? error.message :
      'League publishing is temporarily unavailable.'}, error && error.status || 503);
  }
}

export async function onRequestPut(context) {
  if (!sameOrigin(context.request)) return json({error: 'Origin not allowed.'}, 403);
  const slug = pathSlug(context.request);
  if (!slug) return json({error: 'Not found.'}, 404);
  try { return await updatePublication(context.request, context.env, slug); }
  catch (error) {
    console.error('League update failed.', error && error.message);
    return json({error: error && error.status ? error.message :
      'League publishing is temporarily unavailable.'}, error && error.status || 503);
  }
}

export async function onRequestPatch(context) {
  if (!sameOrigin(context.request)) return json({error: 'Origin not allowed.'}, 403);
  const slug = pathSlug(context.request);
  if (!slug) return json({error: 'Not found.'}, 404);
  try { return await recoverPublication(context.request, context.env, slug); }
  catch (error) {
    console.error('League recovery failed.', error && error.message);
    return json({error: error && error.status ? error.message :
      'League publishing is temporarily unavailable.'}, error && error.status || 503);
  }
}

export async function onRequestDelete(context) {
  if (!sameOrigin(context.request)) return json({error: 'Origin not allowed.'}, 403);
  const slug = pathSlug(context.request);
  if (!slug) return json({error: 'Not found.'}, 404);
  try { return await deletePublication(context.request, context.env, slug); }
  catch (error) {
    console.error('League unpublish failed.', error && error.message);
    return json({error: error && error.status ? error.message :
      'League publishing is temporarily unavailable.'}, error && error.status || 503);
  }
}

export async function onRequestGet(context) {
  const slug = pathSlug(context.request);
  if (!slug) return json({error: 'League not found.'}, 404);
  try {
    await ensureSchema(context.env);
    const row = await context.env.LEAGUE_HISTORY_DB.prepare(
      `SELECT slug, league_name, visibility, archive_json, created_at, updated_at
       FROM league_history_publications WHERE slug = ?`
    ).bind(slug).first();
    if (!row) return json({error: 'League not found.'}, 404,
      {'Cache-Control': 'public, max-age=30'});
    const publication = JSON.parse(row.archive_json);
    return json({slug: row.slug, name: row.league_name,
      visibility: row.visibility, createdAt: row.created_at,
      updatedAt: row.updated_at, ...publication}, 200,
    {'Cache-Control': 'no-store'});
  } catch (error) {
    console.error('League read failed.', error && error.message);
    return json({error: 'League history is temporarily unavailable.'}, 503,
      {'Cache-Control': 'no-store'});
  }
}

export function onRequestOptions() {
  return new Response(null, {
    status: 204,
    headers: {'Allow': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
      'Cache-Control': 'no-store'}
  });
}

export function sharedLeagueRedirect(request) {
  const match = new URL(request.url).pathname.match(/^\/leagues\/([^/]+)\/?$/);
  const slug = match ? decodeURIComponent(match[1]) : '';
  if (!SLUG.test(slug)) return new Response('League not found.', {status: 404});
  const target = new URL('/league-history/', request.url);
  target.searchParams.set('league', slug);
  return Response.redirect(target.toString(), 302);
}
