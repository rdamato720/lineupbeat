"""Wire storage. Its own database, and its own file.

Two rules shape this.

Candidates and publications are separate tables, not one table with a flag.
A flag is one careless UPDATE away from putting an unreviewed candidate on
the site; separate tables mean the site build has nothing to read from.

And publications are mirrored to a tracked JSON file. CI runs from a fresh
checkout, so anything not committed does not exist on the next run -- the
same reason the rankings board is tracked rather than gitignored. The
database is the working copy; the JSON is what survives.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "wire.db"
PUBLICATIONS = ROOT / "data" / "wire_publications.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS wire_source_items (
  source_item_id   TEXT PRIMARY KEY,
  source_id        TEXT NOT NULL,
  canonical_url    TEXT NOT NULL UNIQUE,
  headline         TEXT,
  author           TEXT,
  published_at     TEXT,
  retrieved_at     TEXT,
  original_language TEXT,
  raw_text         TEXT,
  content_sha256   TEXT,
  extraction_status TEXT,
  http_status      TEXT,
  note             TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_source ON wire_source_items(source_id);

-- Never read by the site build. Nothing here has been seen by a human.
CREATE TABLE IF NOT EXISTS wire_candidates (
  candidate_id     TEXT PRIMARY KEY,
  source_item_id   TEXT NOT NULL,
  source_id        TEXT NOT NULL,
  state            TEXT NOT NULL,
  event_fingerprint TEXT,
  payload          TEXT NOT NULL,
  created_at       TEXT,
  updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_cand_state ON wire_candidates(state);

-- Only a reviewer writes here.
CREATE TABLE IF NOT EXISTS wire_publications (
  publication_id   TEXT PRIMARY KEY,
  candidate_id     TEXT,
  event_fingerprint TEXT,
  version          INTEGER NOT NULL DEFAULT 1,
  payload          TEXT NOT NULL,
  published_at     TEXT,
  updated_at       TEXT,
  retracted        INTEGER NOT NULL DEFAULT 0
);

-- Every transcript ever retrieved, kept forever. A caption request is the
-- scarcest thing this pipeline has: five a day, and a repeat spends one of
-- them to learn something already known.
CREATE TABLE IF NOT EXISTS wire_transcripts (
  video_id          TEXT PRIMARY KEY,
  channel_id        TEXT,
  transcript_source TEXT,
  language          TEXT,
  chars             INTEGER,
  segments          TEXT,
  fetched_at        TEXT
);

-- The ledger the daily budget is counted from, successes and failures alike.
-- A refused request still cost an attempt against the address.
CREATE TABLE IF NOT EXISTS wire_transcript_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id     TEXT,
  requested_at TEXT,
  outcome      TEXT,
  detail       TEXT
);

-- When the pipeline may next ask YouTube for anything.
CREATE TABLE IF NOT EXISTS wire_cooldown (
  scope     TEXT PRIMARY KEY,
  until     TEXT,
  reason    TEXT,
  set_at    TEXT
);

-- Every video ever seen, with why it was accepted or excluded. Idempotent on
-- (video_id): discovery may run as often as it likes and creates no
-- duplicates. Costs nothing against the transcript budget.
CREATE TABLE IF NOT EXISTS wire_discovery (
  video_id         TEXT PRIMARY KEY,
  channel_id       TEXT NOT NULL,
  channel_name     TEXT,
  source_id        TEXT,
  canonical_url    TEXT,
  title            TEXT,
  description      TEXT,
  published_at     TEXT,
  duration_seconds INTEGER,
  discovery_method TEXT,
  discovered_at    TEXT,
  eligible         INTEGER,
  speaker_mode     TEXT,
  reasons          TEXT,
  last_seen_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_disc_chan ON wire_discovery(channel_id);

-- The Wire's own player identities. Built from the public nflverse roster
-- and nothing else -- never from rosters/nfl.csv, which carries ADP.
-- Identity fields only; a test walks this and the JSON for fantasy fields.
CREATE TABLE IF NOT EXISTS wire_players (
  player_id        TEXT,
  full_name        TEXT NOT NULL,
  display_name     TEXT,
  aliases          TEXT,
  team             TEXT,
  position         TEXT,
  status           TEXT,
  season           INTEGER,
  fantasy_candidate INTEGER,
  context_only     INTEGER,
  registry_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_players_team ON wire_players(team, position);

CREATE TABLE IF NOT EXISTS wire_event_history (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  event_fingerprint TEXT,
  candidate_id     TEXT,
  action           TEXT,
  actor            TEXT,
  detail           TEXT,
  at               TEXT
);
"""

# The states an item may hold, in order. Skipping is not allowed: a candidate
# cannot reach APPROVED without having been EDITORIAL_REVIEW first, which is
# what stops "publish everything" being one flag away.
STATES = ["DISCOVERED", "FETCHED", "EXTRACTION_FAILED", "PLAYER_UNRESOLVED",
          "NOT_FANTASY_RELEVANT", "EVIDENCE_EXTRACTED", "VALIDATION_FAILED",
          "TRANSLATION_REVIEW", "EDITORIAL_REVIEW", "APPROVED", "PUBLISHED",
          "UPDATED", "RETRACTED", "REJECTED"]


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class WireStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or DB)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # -- source items ------------------------------------------------------

    def seen_url(self, url: str) -> bool:
        return bool(self.conn.execute(
            "SELECT 1 FROM wire_source_items WHERE canonical_url = ?",
            (url,)).fetchone())

    def save_item(self, art) -> str:
        """Store a captured article. Returns its id.

        Keyed on the canonical URL, so re-running discovery over a feed that
        still lists yesterday's article does not create a second copy of it.
        """
        item_id = art.content_sha256[:16] or art.canonical_url[-16:]
        self.conn.execute(
            "INSERT OR REPLACE INTO wire_source_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (item_id, art.source_id, art.canonical_url, art.headline,
             art.author, art.published_at, art.retrieved_at,
             art.original_language, art.raw_text, art.content_sha256,
             art.extraction_status, str(art.http_status), art.note))
        self.conn.commit()
        return item_id

    def item(self, item_id: str):
        return self.conn.execute(
            "SELECT * FROM wire_source_items WHERE source_item_id = ?",
            (item_id,)).fetchone()

    # -- candidates --------------------------------------------------------

    def add_candidate(self, candidate_id, source_item_id, source_id,
                      payload: dict, fingerprint: str,
                      state: str = "EDITORIAL_REVIEW") -> None:
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        self.conn.execute(
            "INSERT OR REPLACE INTO wire_candidates VALUES (?,?,?,?,?,?,?,?)",
            (candidate_id, source_item_id, source_id, state, fingerprint,
             json.dumps(payload), now(), now()))
        self.log(fingerprint, candidate_id, "CANDIDATE_CREATED", "pipeline", state)
        self.conn.commit()

    def candidates(self, state: str = "EDITORIAL_REVIEW") -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM wire_candidates WHERE state = ? ORDER BY created_at",
            (state,)).fetchall()
        return [dict(r) for r in rows]

    def set_state(self, candidate_id: str, state: str, actor: str = "reviewer",
                  detail: str = "") -> None:
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        row = self.conn.execute(
            "SELECT event_fingerprint FROM wire_candidates WHERE candidate_id = ?",
            (candidate_id,)).fetchone()
        self.conn.execute(
            "UPDATE wire_candidates SET state = ?, updated_at = ? "
            "WHERE candidate_id = ?", (state, now(), candidate_id))
        self.log(row["event_fingerprint"] if row else "", candidate_id,
                 state, actor, detail)
        self.conn.commit()

    def update_payload(self, candidate_id: str, payload: dict) -> None:
        self.conn.execute(
            "UPDATE wire_candidates SET payload = ?, updated_at = ? "
            "WHERE candidate_id = ?",
            (json.dumps(payload), now(), candidate_id))
        self.conn.commit()

    # -- publications ------------------------------------------------------

    def publish(self, candidate_id: str, payload: dict, fingerprint: str,
                actor: str = "reviewer") -> str:
        """Promote an approved candidate. Same event updates, never duplicates.

        A second report of the same event is a new version of one card, not a
        second card -- syndicated copies are not independent confirmation.
        """
        prior = self.conn.execute(
            "SELECT * FROM wire_publications WHERE event_fingerprint = ? "
            "AND retracted = 0", (fingerprint,)).fetchone()
        if prior:
            pub_id, version = prior["publication_id"], prior["version"] + 1
            self.conn.execute(
                "UPDATE wire_publications SET payload = ?, version = ?, "
                "updated_at = ?, candidate_id = ? WHERE publication_id = ?",
                (json.dumps(payload), version, now(), candidate_id, pub_id))
            self.log(fingerprint, candidate_id, "UPDATED", actor,
                     f"version {version}")
        else:
            pub_id = candidate_id
            self.conn.execute(
                "INSERT INTO wire_publications VALUES (?,?,?,?,?,?,?,0)",
                (pub_id, candidate_id, fingerprint, 1, json.dumps(payload),
                 now(), now()))
            self.log(fingerprint, candidate_id, "PUBLISHED", actor, "")
        self.set_state(candidate_id, "PUBLISHED", actor)
        self.conn.commit()
        return pub_id

    def publications(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM wire_publications WHERE retracted = 0 "
            "ORDER BY published_at DESC").fetchall()
        return [dict(r) for r in rows]

    # -- player registry ---------------------------------------------------

    def replace_players(self, payload: dict) -> int:
        """Swap the whole table in one transaction.

        All or nothing: a half-written registry would resolve some names and
        silently fail others, which is worse than resolving none.
        """
        rows = payload.get("players") or []
        ver = payload.get("registry_version", "")
        with self.conn:
            self.conn.execute("DELETE FROM wire_players")
            self.conn.executemany(
                "INSERT INTO wire_players VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(p.get("player_id", ""), p["full_name"],
                  p.get("display_name", ""), json.dumps(p.get("aliases") or []),
                  p.get("team", ""), p.get("position", ""),
                  p.get("status", ""), int(p.get("season") or 0),
                  1 if p.get("fantasy_candidate") else 0,
                  1 if p.get("context_only") else 0, ver)
                 for p in rows])
        return len(rows)

    def player_registry_version(self) -> str | None:
        r = self.conn.execute(
            "SELECT registry_version FROM wire_players LIMIT 1").fetchone()
        return r["registry_version"] if r else None

    # -- discovery ---------------------------------------------------------

    def record_discovery(self, rec: dict) -> bool:
        """Store or refresh one discovered video. True if it is new.

        Re-running discovery updates what can change -- the duration once it
        is known, the eligibility verdict, when it was last seen -- and never
        rewrites when it was first discovered.
        """
        existing = self.conn.execute(
            "SELECT video_id, discovered_at FROM wire_discovery "
            "WHERE video_id = ?", (rec["video_id"],)).fetchone()
        first_seen = existing["discovered_at"] if existing else now()
        self.conn.execute(
            "INSERT OR REPLACE INTO wire_discovery VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec["video_id"], rec["channel_id"], rec.get("channel_name", ""),
             rec.get("source_id", ""), rec.get("canonical_url", ""),
             rec.get("title", ""), (rec.get("description") or "")[:2000],
             rec.get("published_at", ""), rec.get("duration_seconds"),
             rec.get("discovery_method", ""), first_seen,
             1 if rec.get("eligible") else 0, rec.get("speaker_mode", ""),
             json.dumps(rec.get("reasons") or []), now()))
        self.conn.commit()
        return existing is None

    def discovered(self, channel_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM wire_discovery"
        args: list = []
        if channel_id:
            sql += " WHERE channel_id = ?"
            args.append(channel_id)
        sql += " ORDER BY published_at DESC"
        out = []
        for r in self.conn.execute(sql, args).fetchall():
            d = dict(r)
            d["reasons"] = json.loads(d.get("reasons") or "[]")
            out.append(d)
        return out

    def last_discovery_at(self) -> str | None:
        r = self.conn.execute(
            "SELECT MAX(last_seen_at) m FROM wire_discovery").fetchone()
        return r["m"] if r and r["m"] else None

    # -- transcript cache and budget ---------------------------------------

    def cached_transcript(self, video_id: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM wire_transcripts WHERE video_id = ?",
            (video_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["segments"] = json.loads(d["segments"] or "[]")
        return d

    def save_transcript(self, video_id, channel_id, tr) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO wire_transcripts VALUES (?,?,?,?,?,?,?)",
            (video_id, channel_id, tr["transcript_source"], tr["language"],
             tr["chars"], json.dumps(tr["segments"]), now()))
        self.conn.commit()

    def log_request(self, video_id, outcome, detail="") -> None:
        self.conn.execute(
            "INSERT INTO wire_transcript_log (video_id, requested_at, outcome, "
            "detail) VALUES (?,?,?,?)", (video_id, now(), outcome, detail))
        self.conn.commit()

    def requests_today(self) -> list[dict]:
        today = now()[:10]
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM wire_transcript_log WHERE substr(requested_at,1,10) = ?"
            " ORDER BY requested_at", (today,)).fetchall()]

    def last_request_at(self) -> str | None:
        r = self.conn.execute(
            "SELECT requested_at FROM wire_transcript_log "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return r["requested_at"] if r else None

    def channels_done_today(self) -> set:
        """Which channels already spent their one video today."""
        today = now()[:10]
        rows = self.conn.execute(
            "SELECT DISTINCT t.channel_id FROM wire_transcripts t "
            "WHERE substr(t.fetched_at,1,10) = ?", (today,)).fetchall()
        return {r["channel_id"] for r in rows if r["channel_id"]}

    def cooldown_until(self, scope: str = "youtube") -> str | None:
        r = self.conn.execute(
            "SELECT until FROM wire_cooldown WHERE scope = ?", (scope,)).fetchone()
        return r["until"] if r else None

    def set_cooldown(self, until_iso: str, reason: str,
                     scope: str = "youtube") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO wire_cooldown VALUES (?,?,?,?)",
            (scope, until_iso, reason, now()))
        self.conn.commit()

    def log(self, fingerprint, candidate_id, action, actor, detail=""):
        self.conn.execute(
            "INSERT INTO wire_event_history "
            "(event_fingerprint, candidate_id, action, actor, detail, at) "
            "VALUES (?,?,?,?,?,?)",
            (fingerprint, candidate_id, action, actor, detail, now()))

    def history(self, fingerprint: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM wire_event_history WHERE event_fingerprint = ? "
            "ORDER BY id", (fingerprint,)).fetchall()]

    # -- the tracked mirror ------------------------------------------------

    def export_publications(self, path: Path | None = None) -> tuple[int, bool]:
        """Write approved items to the tracked JSON, atomically.

        Returns (count, changed). Unchanged output is not rewritten: this file
        is committed, and a build every ten minutes that rewrites a timestamp
        would bury the one commit where a card actually changed.
        """
        out = Path(path or PUBLICATIONS)
        rows = self.publications()
        payload = {
            "generated_at": now(),
            "count": len(rows),
            "publications": [
                {"publication_id": r["publication_id"],
                 "version": r["version"],
                 "published_at": r["published_at"],
                 "updated_at": r["updated_at"],
                 **json.loads(r["payload"])}
                for r in rows],
        }
        prior = {}
        if out.exists():
            try:
                prior = json.loads(out.read_text())
            except (ValueError, OSError):
                prior = {}
        if prior.get("publications") == payload["publications"]:
            return len(rows), False
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=1) + "\n")
            json.loads(tmp.read_text())      # never leave a truncated file
            os.replace(tmp, out)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return len(rows), True

    def commit(self):
        self.conn.commit()
