#!/usr/bin/env python3
"""Dark-launch ingestion for the article Wire.

    python3 scripts/wire_ingest.py                    # every AUTO_READY source
    python3 scripts/wire_ingest.py --only pewter      # substring on source_id
    python3 scripts/wire_ingest.py --url https://...  # manual submission
    python3 scripts/wire_ingest.py --dry-run

Discovers, captures and files candidates for review. It publishes nothing:
every candidate lands in EDITORIAL_REVIEW and only `review_wire.py` can move
one further. The site build reads `data/wire_publications.json` and has no
access to the candidate table at all.

This is the article half only. No X, no fantasy data, no model calls yet --
the point of the pilot is to measure how often discovery and extraction
actually work on live sources before anything is built on top of them.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import capture, registry
from wire.store import WireStore


def fingerprint(source_id: str, url: str) -> str:
    """Identity for an event.

    The full shape in the spec is player + event type + team + body part +
    date, and it needs the extraction step to exist. Until then the canonical
    URL is the honest key: it merges re-runs over the same article and merges
    nothing else, which is the safer error while there is no player yet.
    """
    return hashlib.sha256(url.encode()).hexdigest()[:20]


def candidate_from(art, src) -> dict:
    """The reviewable record. Facts only -- no interpretation yet.

    `fantasy_relevance` stays empty on purpose. It is LineupBeat's voice, it
    is not the reporter's, and nothing should generate it until a human has
    agreed the underlying capture is sound.
    """
    return {
        "source_id": src.source_id,
        "source_name": src.source_name,
        "reporter_name": art.author or src.reporter_name,
        "teams": src.teams,
        "reporting_type": src.reporting_type,
        "trust_tier": src.trust_tier,
        "canonical_url": art.canonical_url,
        "headline": art.headline,
        "published_at": art.published_at,
        "retrieved_at": art.retrieved_at,
        "original_language": art.original_language,
        "content_sha256": art.content_sha256,
        "body_chars": len(art.raw_text),
        "excerpt": art.raw_text[:600],
        "facts": [],
        "fantasy_relevance": "",
        "wire_label": "",
        "publication_confidence": None,
    }


def ingest_one(store, src, item, args) -> str:
    url = item.get("url", "")
    if not url:
        return "no url"
    if store is not None and store.seen_url(url) and not args.force:
        return "seen"
    art = capture.capture(src, item)
    if args.dry_run:
        return (f"{art.extraction_status.lower()} "
                f"({len(art.raw_text)} chars)")
    item_id = store.save_item(art)
    if not art.usable:
        # Kept, not discarded. A source that starts failing should be visible
        # in the table rather than silently producing nothing.
        return f"{art.extraction_status.lower()}: {art.note}"
    cid = art.content_sha256[:16]
    store.add_candidate(cid, item_id, src.source_id,
                        candidate_from(art, src), fingerprint(src.source_id, url))
    return f"candidate {cid}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="substring match on source_id")
    ap.add_argument("--url", help="manually submitted article URL")
    ap.add_argument("--limit", type=int, default=10,
                    help="max new articles per source per run")
    ap.add_argument("--force", action="store_true",
                    help="re-capture URLs already stored")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sources = registry.load()
    bad = registry.problems(sources)
    if bad:
        for b in bad:
            print(f"  registry: {b}")
        sys.exit("  registry is invalid; nothing ingested")

    store = None if args.dry_run else WireStore()

    # -- manual submission ------------------------------------------------
    if args.url:
        owner = next((s for s in sources if s.owns(args.url)), None)
        if owner is None:
            sys.exit(f"  {args.url} belongs to no approved source")
        if not owner.manual_ok:
            # The distinction the spec insists on: manual submission routes
            # around missing discovery, never around a publisher's refusal.
            sys.exit(f"  {owner.source_id} is {owner.status}"
                     + (f" ({owner.blocked_reason})" if owner.blocked_reason else "")
                     + ". Manual submission is not a way past that.")
        print(f"  manual: {owner.source_id}")
        print(f"    {ingest_one(store, owner, {'url': args.url}, args)}")
        if store:
            n, changed = store.export_publications()
            print(f"  {n} published items{' (file updated)' if changed else ''}")
        return 0

    # -- scheduled discovery ----------------------------------------------
    pool = [s for s in sources if s.pollable]
    if args.only:
        pool = [s for s in pool if args.only.lower() in s.source_id.lower()]
    skipped = [s for s in sources if not s.pollable and s.active]
    print(f"  {len(pool)} pollable source(s)"
          + (f", {len(skipped)} active but not AUTO_READY" if skipped else ""))

    totals = {"candidates": 0, "seen": 0, "failed": 0}
    for src in pool:
        found = capture.discover(src, limit=args.limit)
        print(f"\n  {src.source_id}  [{src.adapter}]  {len(found)} in feed")
        for item in found:
            result = ingest_one(store, src, item, args)
            if result == "seen":
                totals["seen"] += 1
                continue
            if result.startswith("candidate"):
                totals["candidates"] += 1
            elif not args.dry_run:
                totals["failed"] += 1
            print(f"    {result:<38} {item['url'][:70]}")
            capture.polite_sleep(1.0)

    print(f"\n  new candidates {totals['candidates']}, "
          f"already seen {totals['seen']}, unusable {totals['failed']}")
    if store:
        n, changed = store.export_publications()
        print(f"  {n} published items{' (file updated)' if changed else ''}")
        print("  nothing was published; candidates await review_wire.py")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
