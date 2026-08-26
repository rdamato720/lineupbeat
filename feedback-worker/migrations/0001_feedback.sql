CREATE TABLE IF NOT EXISTS feedback (
  id TEXT PRIMARY KEY,
  category TEXT NOT NULL CHECK (category IN ('ERROR', 'FEATURE', 'GENERAL')),
  message TEXT NOT NULL,
  email TEXT,
  page_url TEXT NOT NULL,
  user_agent TEXT,
  ip_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'NEW'
    CHECK (status IN ('NEW', 'REVIEWED', 'PLANNED', 'RESOLVED', 'ARCHIVED')),
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS feedback_created_at_idx
  ON feedback(created_at DESC);
CREATE INDEX IF NOT EXISTS feedback_status_idx
  ON feedback(status, created_at DESC);
CREATE INDEX IF NOT EXISTS feedback_rate_idx
  ON feedback(ip_hash, created_at DESC);

