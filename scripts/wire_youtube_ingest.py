#!/usr/bin/env python3
"""YouTube ingestion for the Wire. Local only, dark launch, publishes nothing.

    python3 scripts/wire_youtube_ingest.py                 # every active channel
    python3 scripts/wire_youtube_ingest.py --only purple
    python3 scripts/wire_youtube_ingest.py --dry-run

Run this on a laptop. Caption retrieval is refused from datacenter addresses
often enough that CI is not a place to depend on it, and a residential proxy
is not something to add before the pipeline has earned it.

Candidates land in the same table and the same review loop as articles, so
there is one queue and one approval path, not two.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import youtube
from wire.store import WireStore


def candidate_from(ch, video, tr, spans, mode, ready) -> dict:
    """The reviewable record.

    `speaker` is deliberately absent from every span. The transcript does not
    say who is talking and this pipeline will not guess: the video-level
    speaker_mode is the whole of what is known, and MULTI_SPEAKER means a
    human decides who said what by opening the timestamp.
    """
    return {
        "kind": "youtube",
        "source_id": ch.source_id,
        "source_name": ch.source_name,
        "channel_id": ch.channel_id,
        "teams": [ch.team],
        "approved_reporters": ch.approved_reporters,
        "classification": ch.classification,
        "attends_practice": ch.attends_practice,
        "reporting_type": ("FIRSTHAND_PRACTICE" if ch.attends_practice
                           else "ANALYSIS"),
        "trust_tier": 1,
        "video_id": video["video_id"],
        "canonical_url": video["url"],
        "headline": video["title"],
        "description": video.get("description", "")[:600],
        "published_at": video["published_at"],
        "original_language": tr["language"],
        "transcript_source": tr["transcript_source"],
        "transcript_chars": tr["chars"],
        "speaker_mode": mode,
        "readiness": ready,
        "content_sha256": hashlib.sha256(
            "".join(s["text"] for s in spans).encode()).hexdigest(),
        "evidence_spans": spans[:60],
        "excerpt": " ".join(s["text"] for s in spans[:4])[:600],
        "facts": [],
        "fantasy_relevance": "",
        "wire_label": "",
        "publication_confidence": None,
        "review_notes": (
            "Auto-generated captions: verify names, negation and numbers "
            "against the video before publishing."
            if tr["transcript_source"] == "AUTO_CAPTIONS" else ""),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int, default=5,
                    help="videos per channel per run")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    channels, rules = youtube.load()
    bad = youtube.problems(channels)
    if bad:
        for b in bad:
            print(f"  registry: {b}")
        sys.exit("  youtube registry is invalid; nothing ingested")

    pool = [c for c in channels if c.pollable]
    if args.only:
        pool = [c for c in pool if args.only.lower() in c.source_id.lower()]
    blocked = [c for c in channels if not c.pollable]
    print(f"  {len(pool)} channel(s) pollable, {len(blocked)} not "
          f"({', '.join(c.team for c in blocked) or 'none'})")

    store = None if args.dry_run else WireStore()
    made = skipped = short = 0

    for ch in pool:
        vids, err = youtube.uploads(ch, limit=args.limit)
        print(f"\n  {ch.team} {ch.source_name}  {len(vids)} recent"
              + (f"  [{err}]" if err else ""))
        for v in vids:
            # A network channel carries other sports. Filter before spending
            # a transcript request on a Sixers show.
            if ch.title_filter and ch.title_filter.lower() not in v["title"].lower():
                skipped += 1
                continue
            if store is not None and store.seen_url(v["url"]) and not args.force:
                skipped += 1
                continue

            mode = youtube.speaker_mode(v["title"], rules)
            ready = youtube.readiness(ch, mode)
            tr = youtube.fetch_transcript(v["video_id"], ch.transcript_languages)
            time.sleep(1.5)

            if not tr["ok"]:
                print(f"    {v['video_id']}  {v['title'][:42]:<43} "
                      f"no transcript ({tr['error'][:40]})")
                continue
            if tr["transcript_source"] not in rules.allowed_transcript_sources:
                print(f"    {v['video_id']}  {tr['transcript_source']} not allowed")
                continue
            if tr["chars"] < rules.min_transcript_chars:
                # A caption on a highlight clip, not a report. Rejected unless
                # somebody looks at it deliberately.
                short += 1
                print(f"    {v['video_id']}  {v['title'][:42]:<43} "
                      f"too short ({tr['chars']} chars) -> rejected")
                continue

            spans = youtube.evidence_spans(v["video_id"], tr["segments"])
            payload = candidate_from(ch, v, tr, spans, mode, ready)
            print(f"    {v['video_id']}  {v['title'][:42]:<43} "
                  f"{mode:<14}{ready:<19}{tr['chars']:,} chars, {len(spans)} spans")
            if args.dry_run:
                continue
            art_like = type("A", (), {
                "source_id": ch.source_id, "canonical_url": v["url"],
                "headline": v["title"], "author": ", ".join(ch.approved_reporters),
                "published_at": v["published_at"], "retrieved_at": "",
                "original_language": tr["language"],
                "raw_text": " ".join(s["text"] for s in tr["segments"]),
                "content_sha256": payload["content_sha256"],
                "extraction_status": "COMPLETE", "http_status": 200,
                "note": f"{tr['transcript_source']} transcript"})()
            item_id = store.save_item(art_like)
            store.add_candidate(payload["content_sha256"][:16], item_id,
                                ch.source_id, payload,
                                hashlib.sha256(v["url"].encode()).hexdigest()[:20])
            made += 1

    print(f"\n  new candidates {made}, skipped {skipped}, "
          f"rejected as too short {short}")
    if store:
        n, changed = store.export_publications()
        print(f"  {n} published items{' (file updated)' if changed else ''}")
        print("  nothing was published; candidates await review_wire.py")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
