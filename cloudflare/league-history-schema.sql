CREATE TABLE IF NOT EXISTS league_history_publications (
  slug TEXT PRIMARY KEY,
  league_name TEXT NOT NULL,
  visibility TEXT NOT NULL CHECK (visibility IN ('unlisted', 'public')),
  archive_json TEXT NOT NULL,
  manage_token_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_league_history_visibility_updated
ON league_history_publications (visibility, updated_at DESC);

CREATE TABLE IF NOT EXISTS league_history_rate_limits (
  scope TEXT PRIMARY KEY,
  window_start INTEGER NOT NULL,
  requests INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_league_history_rate_limit_expiry
ON league_history_rate_limits (expires_at);

PRAGMA optimize;
