#!/usr/bin/env python3
"""Import already-fetched X posts into the editorial Wire review queue.

    python scripts/wire_x_import.py --hours 72
    python scripts/wire_x_import.py --hours 72 --dry-run

This bridge performs no network request and no model call.  It opens the
broader beat feed's SQLite cache read-only, admits only enabled single-team X
sources from ``sources/nfl.yaml``, and sends their original post text through
the Wire's deterministic segmentation, identity, relevance and evidence
classification.

Every new evidence row is PENDING.  Being configured as an X source is
not firsthand authority, so declarative posts remain UNCERTAIN until that
account has its own researched Wire authority record.  Nothing here can write
``data/wire_publications.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import wire_extract as extractor
from wire import players as pl
from wire import registry as artreg
from wire.store import WireStore


SOURCE_DB = ROOT / "beatwire.db"
WIRE_DB = ROOT / "wire.db"
SOURCE_REGISTRY = ROOT / "sources" / "nfl.yaml"
ARTICLE_REGISTRY = ROOT / "sources" / "wire_articles.yaml"
PUBLICATIONS = ROOT / "data" / "wire_publications.json"
X_KINDS = {"twitterapi", "x"}
X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
STATUS_PATH = re.compile(r"^/[^/]+/status/\d+(?:/)?$")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_sources(path: Path = SOURCE_REGISTRY) -> tuple[dict[str, dict], int]:
    """Enabled, single-team X sources; national accounts stay out of V1."""
    raw = yaml.safe_load(path.read_text()) or {}
    sources: dict[str, dict] = {}
    national = 0
    for row in raw.get("sources", []):
        if row.get("kind") not in X_KINDS or row.get("enabled", True) is False:
            continue
        teams = row.get("teams") or []
        if len(teams) != 1:
            national += 1
            continue
        team = str(teams[0] or "").upper()
        source_id = str(row.get("id") or "").strip()
        handle = str(row.get("handle") or "").strip().lstrip("@")
        if not source_id or not handle or team not in pl.NFL_TEAMS:
            raise ValueError(f"invalid team-scoped X source {source_id!r}")
        sources[source_id] = {
            "source_id": source_id,
            "team": team,
            "handle": handle,
            "name": str(row.get("name") or handle).strip(),
            "outlet": str(row.get("outlet") or "X").strip(),
        }
    if not sources:
        raise ValueError("X source registry contains no enabled team sources")
    return sources, national


def valid_post_url(value: str, expected_handle: str) -> bool:
    parsed = urlparse(value or "")
    parts = parsed.path.strip("/").split("/")
    return (parsed.scheme == "https" and parsed.hostname in X_HOSTS
            and bool(STATUS_PATH.fullmatch(parsed.path))
            and len(parts) == 3
            and parts[0].casefold() == expected_handle.casefold())


def researched_authorities(
        sources: dict[str, dict], path: Path = ARTICLE_REGISTRY,
        ) -> dict[str, dict]:
    """Exact X-source matches to already researched Wire reporters.

    An X account does not gain authority from ``sources/nfl.yaml``.  The
    crosswalk must also match one named independent reporter, team and handle
    in the editorial article registry.  Analysis/aggregation sources are
    excluded, and an ambiguous match is a configuration error.
    """
    authorities: dict[str, dict] = {}
    for article in artreg.load(path):
        reporter = (article.reporter_name or "").strip()
        handle = (article.x_handle or "").strip().lstrip("@")
        if (not reporter or "staff" in reporter.casefold() or not handle
                or len(article.teams) != 1
                or article.source_ownership != artreg.INDEPENDENT
                or article.reporting_type in {"ANALYSIS", "AGGREGATOR"}
                or article.status in {artreg.ANALYSIS_ONLY,
                                      artreg.DISCOVERY_ONLY_PAID}):
            continue
        team = article.teams[0].upper()
        matches = [source for source in sources.values()
                   if source["team"] == team
                   and source["handle"].casefold() == handle.casefold()]
        for source in matches:
            source_id = source["source_id"]
            if source_id in authorities:
                raise ValueError(
                    f"ambiguous researched X authority for {source_id}")
            authorities[source_id] = {
                "reporter_name": reporter,
                "source_name": article.source_name,
                "matched_wire_source_id": article.source_id,
                "team": team,
                "handle": handle,
            }
    return authorities


def open_cache(path: Path) -> sqlite3.Connection:
    """Open without ever creating or mutating the paid-fetch cache."""
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "items" not in tables:
        conn.close()
        raise ValueError("beatwire cache has no items table")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    required = {"item_id", "source_id", "url", "title", "body",
                "published_at", "fetched_at"}
    if not required.issubset(columns):
        conn.close()
        raise ValueError("beatwire items table is missing required columns")
    return conn


def publication_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_cached_x(*, source_db: Path = SOURCE_DB,
                    wire_db: Path = WIRE_DB,
                    source_registry: Path = SOURCE_REGISTRY,
                    article_registry: Path = ARTICLE_REGISTRY,
                    publications: Path = PUBLICATIONS,
                    hours: int = 72, limit: int = 2000,
                    dry_run: bool = False,
                    now: datetime | None = None) -> dict:
    """Bridge recent cached posts into PENDING evidence; return accounting."""
    if not 1 <= int(hours) <= 96:
        raise ValueError("hours must be between 1 and 96")
    if not 1 <= int(limit) <= 10000:
        raise ValueError("limit must be between 1 and 10000")

    stats = {
        "configured_team_sources": 0, "national_sources_deferred": 0,
        "cache_missing": 0, "cached_rows": 0, "inside_window": 0,
        "invalid_url": 0, "empty_text": 0, "items_imported": 0,
        "spans": 0, "with_players": 0, "candidates": 0, "new": 0,
        "context_only": 0, "unresolved": 0, "refused": 0,
        "superseded": 0, "not_relevant": 0, "duplicates": 0,
        "same_underlying_report": 0, "model_calls_made": 0,
        "publications_applied": 0,
    }
    if not source_db.is_file():
        stats["cache_missing"] = 1
        return stats

    sources, national = load_sources(source_registry)
    authorities = researched_authorities(sources, article_registry)
    stats["configured_team_sources"] = len(sources)
    stats["national_sources_deferred"] = national
    stats["researched_authority_sources"] = len(authorities)
    before = publication_sha(publications)
    now = now or datetime.now(timezone.utc)
    if not now.tzinfo:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=hours)

    cache = open_cache(source_db)
    try:
        marks = ",".join("?" for _ in sources)
        rows = cache.execute(
            "SELECT item_id, source_id, url, title, body, published_at, "
            "fetched_at FROM items WHERE source_id IN (" + marks + ") "
            "ORDER BY published_at DESC, item_id ASC",
            tuple(sorted(sources)),
        ).fetchall()
    finally:
        cache.close()
    stats["cached_rows"] = len(rows)

    store = WireStore(Path(":memory:") if dry_run else wire_db)
    registry = pl.load()
    if not registry.players:
        store.conn.close()
        raise ValueError("Wire player registry is empty")
    player_cfg = json.loads(pl.REGISTRY.read_text())
    seen_claims: dict = {}
    seen_reports: dict = {}
    imported = 0
    try:
        for row in rows:
            if imported >= limit:
                break
            published = parse_time(row["published_at"])
            if published is None or published < cutoff or published > now:
                continue
            stats["inside_window"] += 1
            source = sources[row["source_id"]]
            if not valid_post_url(row["url"], source["handle"]):
                stats["invalid_url"] += 1
                continue
            text = str(row["body"] or "").strip()
            if not text:
                stats["empty_text"] += 1
                continue

            authority = authorities.get(source["source_id"])
            author = (authority["reporter_name"] if authority
                      else source["name"])
            outlet = (authority["source_name"] if authority
                      else source["outlet"])
            title = str(row["title"] or "").strip() or (
                f"X post from {author}")
            content_hash = hashlib.sha256(text.encode()).hexdigest()
            article = SimpleNamespace(
                source_id=source["source_id"], canonical_url=row["url"],
                headline=title, author=author,
                published_at=published.isoformat(),
                retrieved_at=str(row["fetched_at"] or ""),
                original_language="", raw_text=text,
                content_sha256=content_hash, extraction_status="COMPLETE",
                http_status="cached", note=extractor.X_BRIDGE_NOTE,
            )
            item_id = store.save_item(article)
            item = {
                "source_item_id": item_id, "source_id": source["source_id"],
                "canonical_url": row["url"], "headline": title,
                "author": author,
                "published_at": published.isoformat(), "raw_text": text,
            }
            context = {
                "type": "x", "name": outlet,
                "author": author, "teams": [source["team"]],
                "reporter_voice": bool(authority),
                "authority_verified": bool(authority),
                "auto_captions": False, "multi_speaker": False,
                "channel_id": "", "paid": False, "si": False,
                "refuse": "", "ownership": "INDEPENDENT",
            }
            result = extractor.extract_item(
                store, item, registry, context, player_cfg, dry=dry_run,
                seen_claims=seen_claims, seen_reports=seen_reports)
            for key in ("spans", "with_players", "candidates", "new",
                        "context_only", "unresolved", "refused",
                        "superseded", "not_relevant", "duplicates",
                        "same_underlying_report"):
                stats[key] += int(result.get(key, 0))
            stats["items_imported"] += 1
            imported += 1
    finally:
        store.conn.close()

    after = publication_sha(publications)
    if before != after:
        raise RuntimeError("publication file changed during cached X import")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", type=Path, default=SOURCE_DB)
    parser.add_argument("--wire-db", type=Path, default=WIRE_DB)
    parser.add_argument("--sources", type=Path, default=SOURCE_REGISTRY)
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stats = import_cached_x(
        source_db=args.source_db, wire_db=args.wire_db,
        source_registry=args.sources, hours=args.hours, limit=args.limit,
        dry_run=args.dry_run)
    if stats["cache_missing"]:
        print(f"  no cached X database at {args.source_db}; 0 calls, 0 imported")
        return 0
    label = "would import" if args.dry_run else "imported"
    print(f"  X review bridge: {stats['configured_team_sources']} enabled "
          f"team source(s); {stats['national_sources_deferred']} national "
          "source(s) deferred")
    print(f"  {stats['researched_authority_sources']} exact reporter authority "
          "match(es); every other account remains uncertain")
    print(f"  {stats['cached_rows']} cached post(s); "
          f"{stats['inside_window']} inside {args.hours}h")
    print(f"  {label} {stats['items_imported']} post(s), "
          f"{stats['candidates']} PENDING evidence candidate(s), "
          f"{stats['new']} new")
    print(f"  {stats['not_relevant']} not actionable; "
          f"{stats['invalid_url']} invalid URL(s); "
          f"{stats['duplicates']} duplicate(s)")
    print("  0 network calls, 0 model calls, 0 publications")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
