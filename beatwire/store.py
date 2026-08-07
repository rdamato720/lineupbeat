"""Persistence and deduplication.

The merge behaviour is a product decision, not a storage detail. When five
beat writers report the same thing, the user should see one nugget carrying
five attributions, not five nuggets. Corroboration count is also the raw
material for reporter accuracy scoring later, so it gets stored, not
discarded.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from rapidfuzz import fuzz

from .models import Nugget

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_items (
    item_id     TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    url         TEXT,
    fetched_at  TEXT NOT NULL
);

-- The reporting a claim came from, kept so paraphrasing can be audited.
--
-- Without this there is no way to answer the one question that matters for a
-- site built on other people's work: did we paraphrase this writer, or did we
-- reproduce him. The claim is all that survived extraction and the original
-- was gone forever.
--
-- Also means re-extraction does not need re-fetching, which is the difference
-- between a prompt change costing minutes and costing money.
--
-- Pruned by age; see prune_items(). This is a working copy for verification,
-- not an archive of anyone's journalism.
CREATE TABLE IF NOT EXISTS items (
    item_id     TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    fetched_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_url ON items(url);
CREATE INDEX IF NOT EXISTS idx_items_fetched ON items(fetched_at);

CREATE TABLE IF NOT EXISTS nuggets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key    TEXT NOT NULL,
    sport         TEXT NOT NULL,
    player_id     TEXT,               -- NULL when the mention did not resolve
    player_name   TEXT NOT NULL,
    mention       TEXT NOT NULL DEFAULT '',
    team          TEXT NOT NULL,
    category      TEXT NOT NULL,
    horizon       TEXT NOT NULL DEFAULT 'day',
    event         TEXT NOT NULL DEFAULT '',
    claim         TEXT NOT NULL,
    actionability INTEGER NOT NULL,
    confidence    REAL NOT NULL,
    published_at  TEXT NOT NULL,
    tags          TEXT NOT NULL DEFAULT '[]',
    attributions  TEXT NOT NULL DEFAULT '[]',
    weight        REAL NOT NULL DEFAULT 1.0,
    media         TEXT NOT NULL DEFAULT '[]'
);

-- Per-source cursor. On a metered API this is not an optimisation, it is the
-- difference between $96/month and $12,600/month: without it you re-fetch and
-- re-pay for the same posts on every single poll.
CREATE TABLE IF NOT EXISTS cursors (
    source_id  TEXT PRIMARY KEY,
    cursor     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Metered spend, tracked locally. X pay-per-use has no monthly cap, which
-- means a polling bug cannot hit a wall, it just keeps billing. The cap has
-- to live here instead.
CREATE TABLE IF NOT EXISTS api_spend (
    day       TEXT NOT NULL,
    provider  TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    units     INTEGER NOT NULL DEFAULT 0,
    cost      REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_spend_day ON api_spend(day, provider);
CREATE INDEX IF NOT EXISTS idx_nug_dedupe ON nuggets(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_nug_player ON nuggets(sport, player_id);
CREATE INDEX IF NOT EXISTS idx_nug_time   ON nuggets(published_at DESC);
"""

# Two claims about the same player, category and day above this similarity
# are treated as the same story reported twice.
MERGE_THRESHOLD = 78


class Store:
    def __init__(self, path: str | Path = "beatwire.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Older databases predate the horizon column. Adding it is cheap and
        # a great deal kinder than failing on insert with "no such column".
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(nuggets)")}
        if "horizon" not in have:
            self.conn.execute(
                "ALTER TABLE nuggets ADD COLUMN horizon TEXT NOT NULL DEFAULT 'day'")
            self.conn.commit()
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(nuggets)")}
        if "weight" not in cols:
            self.conn.execute(
                "ALTER TABLE nuggets ADD COLUMN weight REAL NOT NULL DEFAULT 1.0"
            )
        if "event" not in cols:
            self.conn.execute(
                "ALTER TABLE nuggets ADD COLUMN event TEXT NOT NULL DEFAULT ''"
            )
        if "media" not in cols:
            self.conn.execute(
                "ALTER TABLE nuggets ADD COLUMN media TEXT NOT NULL DEFAULT '[]'"
            )
        self.conn.commit()

    # -- item dedupe --------------------------------------------------------

    def is_seen(self, item_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM seen_items WHERE item_id = ?", (item_id,)
        )
        return cur.fetchone() is not None

    def mark_seen(self, item_id: str, source_id: str, url: str,
                  item=None) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            "INSERT OR IGNORE INTO seen_items VALUES (?, ?, ?, ?)",
            (item_id, source_id, url, now),
        )
        if item is None:
            return
        # Truncated, because the point is verification rather than archival:
        # a few thousand characters is more than enough to check whether a
        # 25-word claim paraphrases or copies.
        body = (getattr(item, "body", "") or "")[:6000]
        title = (getattr(item, "title", "") or "")[:500]
        pub = getattr(item, "published_at", None)
        self.conn.execute(
            "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?)",
            (item_id, source_id, url, title, body,
             pub.isoformat() if hasattr(pub, "isoformat") else None, now),
        )

    def prune_items(self, days: int = 45) -> int:
        """Drop stored source text past its useful life.

        Long enough to investigate a complaint or re-run extraction after a
        prompt change; short enough that this stays a verification buffer
        rather than a library of other people's writing.
        """
        cur = self.conn.execute(
            "DELETE FROM items WHERE fetched_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        self.conn.commit()
        return cur.rowcount

    # -- nugget merge -------------------------------------------------------

    def add_nugget(self, n: Nugget) -> str:
        """Returns 'new' or 'merged'."""
        attribution = {
            "source_id": n.source_id,
            "source_name": n.source_name,
            "outlet": n.outlet,
            "url": n.url,
            "published_at": n.published_at.isoformat(),
        }

        key = n.dedupe_key
        if ":?join:" in key:
            # A joiner attaches to whatever story this player already has on
            # this day, preferring the most corroborated one. With nothing to
            # join it becomes its own item, which is the right fallback: a
            # timeline filed before the news it elaborates is still news.
            prefix, _, day = key.rpartition(":")
            stem = prefix.rsplit(":?join", 1)[0]
            host = self.conn.execute(
                """SELECT dedupe_key FROM nuggets
                   WHERE dedupe_key LIKE ? AND dedupe_key NOT LIKE ?
                   ORDER BY json_array_length(attributions) DESC, id ASC
                   LIMIT 1""",
                (f"{stem}:%:{day}", f"{stem}:?join:{day}"),
            ).fetchone()
            if host:
                key = host["dedupe_key"]

        rows = self.conn.execute(
            "SELECT id, claim, attributions, tags FROM nuggets WHERE dedupe_key = ?",
            (key,),
        ).fetchall()

        # When the extractor gave us an event, the key IS the identity and the
        # prose is irrelevant to matching. Six writers reporting one signing
        # write six different sentences; running a similarity test on them was
        # the bug, not the solution.
        #
        # Without an event we fall back to fuzzy matching, which is weak but
        # better than nothing. Note it cuts both ways: it merges too little on
        # paraphrase (~40-57) and too much on near-identical wording about
        # different facts ("knee" vs "ankle injury" scores 93), so the
        # threshold is set high and misses rather than corrupts.
        for row in rows:
            same = bool(n.event) or (
                fuzz.token_set_ratio(n.claim, row["claim"]) >= MERGE_THRESHOLD
            )
            if same:
                attrs = json.loads(row["attributions"])
                if not any(a["source_id"] == n.source_id for a in attrs):
                    attrs.append(attribution)
                    # Union the tags. A physician's explanation of a PCL tear
                    # merged into a beat writer's one-liner that arrived first,
                    # and the `medical` tag went with the loser -- so the
                    # section it was written for never saw it. Whether an item
                    # belongs in a section is a property of who contributed to
                    # it, not of who filed first.
                    merged_tags = sorted(set(json.loads(row["tags"] or "[]"))
                                         | set(n.tags or []))
                    self.conn.execute(
                        "UPDATE nuggets SET attributions = ?, tags = ? WHERE id = ?",
                        (json.dumps(attrs), json.dumps(merged_tags), row["id"]),
                    )
                    # Keep the fullest phrasing of a merged story -- and move
                    # its author to the front of the attribution list.
                    #
                    # These have to move together. The displayed byline is
                    # attributions[0], so replacing the claim without
                    # reordering credited a physician's analysis to whichever
                    # beat writer happened to file first. Showing a claim under
                    # the wrong name is worse than showing a shorter claim.
                    if len(n.claim) > len(row["claim"] or ""):
                        attrs = [a for a in attrs if a["source_id"] != n.source_id]
                        attrs.insert(0, attribution)
                        self.conn.execute(
                            "UPDATE nuggets SET claim = ?, attributions = ? WHERE id = ?",
                            (n.claim, json.dumps(attrs), row["id"]),
                        )
                return "merged"

        self.conn.execute(
            """INSERT INTO nuggets
               (dedupe_key, sport, player_id, player_name, mention, team,
                category, horizon, event, claim, actionability, confidence,
                published_at, tags, attributions, weight, media)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                key, n.sport, n.player_id, n.player_name, n.mention,
                n.team, n.category, n.horizon, n.event, n.claim, n.actionability, n.confidence,
                n.published_at.isoformat(), json.dumps(n.tags),
                json.dumps([attribution]), n.weight, json.dumps(n.media),
            ),
        )
        return "new"

    def commit(self) -> None:
        self.conn.commit()

    # -- cursors ------------------------------------------------------------

    def get_cursor(self, source_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT cursor FROM cursors WHERE source_id = ?", (source_id,)
        ).fetchone()
        return row["cursor"] if row else None

    def set_cursor(self, source_id: str, cursor: str) -> None:
        self.conn.execute(
            "INSERT INTO cursors VALUES (?,?,?) ON CONFLICT(source_id) "
            "DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at",
            (source_id, cursor, datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    # -- spend --------------------------------------------------------------

    def record_spend(self, provider: str, source_id: str,
                     units: int, cost: float) -> None:
        self.conn.execute(
            "INSERT INTO api_spend (day, provider, source_id, units, cost) "
            "VALUES (?,?,?,?,?)",
            (datetime.utcnow().date().isoformat(), provider, source_id, units, cost),
        )
        self.conn.commit()

    def spend_today(self, provider: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost),0) c FROM api_spend WHERE day = ? AND provider = ?",
            (datetime.utcnow().date().isoformat(), provider),
        ).fetchone()
        return row["c"]

    def spend_report(self, provider: str, days: int = 14) -> list[dict]:
        rows = self.conn.execute(
            """SELECT day, SUM(units) units, SUM(cost) cost
               FROM api_spend WHERE provider = ?
               GROUP BY day ORDER BY day DESC LIMIT ?""",
            (provider, days),
        ).fetchall()
        return [dict(r) for r in rows]

    def spend_by_source(self, provider: str, days: int = 30) -> list[dict]:
        """Cost per source. Pair this with nugget counts to get yield per
        dollar, which is how you decide who to keep polling."""
        rows = self.conn.execute(
            """SELECT s.source_id,
                      SUM(s.units) units,
                      SUM(s.cost)  cost,
                      (SELECT COUNT(*) FROM nuggets n
                       WHERE json_extract(n.attributions,'$[0].source_id') = s.source_id)
                      AS nuggets
               FROM api_spend s
               WHERE s.provider = ? AND s.source_id != ''
               GROUP BY s.source_id ORDER BY cost DESC""",
            (provider,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- reads --------------------------------------------------------------

    def feed(
        self,
        sport: str | None = None,
        player_ids: list[str] | None = None,
        min_actionability: int = 0,
        limit: int = 100,
    ) -> list[dict]:
        """The personalized feed query.

        Passing player_ids is the whole product for a fantasy user: show me
        only what touches my roster, ranked by whether it changes a decision.
        """
        sql = "SELECT * FROM nuggets WHERE actionability >= ?"
        params: list = [min_actionability]
        if sport:
            sql += " AND sport = ?"
            params.append(sport)
        if player_ids:
            # Roster filtering necessarily excludes unresolved nuggets. That
            # is the honest cost of not guessing, and it is why the team feed
            # must stay available alongside the roster view.
            sql += f" AND player_id IN ({','.join('?' * len(player_ids))})"
            params.extend(player_ids)
        # Recent first, then tier, then source trust.
        #
        # Tier-first ranking is right for deciding what to READ first, and
        # wrong for deciding what to KEEP when the limit binds: a practice
        # note filed twenty minutes ago was losing to a contract report from
        # Tuesday, so the newest thing on a wire that promises recency was
        # two hours old on the live site while the database had it fresh.
        #
        # Anything from the last day is kept regardless of tier. Below that
        # cutoff the old ranking applies, because among things that are all
        # stale, consequence is the right tiebreak.
        sql += (" ORDER BY (published_at > datetime('now','-1 day')) DESC,"
                " actionability DESC, weight DESC, published_at DESC LIMIT ?")
        params.append(limit)

        out = []
        for row in self.conn.execute(sql, params):
            d = dict(row)
            d["tags"] = json.loads(d["tags"])
            d["attributions"] = json.loads(d["attributions"])
            d["corroborations"] = len(d["attributions"])
            d["resolved"] = d["player_id"] is not None
            d["media"] = json.loads(d.get("media") or "[]")
            out.append(d)
        return out

    def stats(self) -> dict:
        n = self.conn.execute("SELECT COUNT(*) c FROM nuggets").fetchone()["c"]
        i = self.conn.execute("SELECT COUNT(*) c FROM seen_items").fetchone()["c"]
        u = self.conn.execute(
            "SELECT COUNT(*) c FROM nuggets WHERE player_id IS NULL"
        ).fetchone()["c"]
        return {"nuggets": n, "items_seen": i, "unresolved": u}

    def unresolved_mentions(self, sport: str, limit: int = 40) -> list[tuple]:
        """The roster-health queue. Frequent unresolved mentions are usually
        a missing alias or a player who was signed since your last import."""
        rows = self.conn.execute(
            """SELECT mention, team, COUNT(*) n FROM nuggets
               WHERE player_id IS NULL AND sport = ? AND mention != ''
               GROUP BY mention, team ORDER BY n DESC LIMIT ?""",
            (sport, limit),
        ).fetchall()
        return [(r["mention"], r["team"], r["n"]) for r in rows]
