#!/usr/bin/env python3
"""Pull a YouTube transcript and run it through the wire.

    pip install youtube-transcript-api
    python3 scripts/video_test.py https://www.youtube.com/watch?v=fma6ltYXhZs --team HOU
    python3 scripts/video_test.py fma6ltYXhZs --team HOU --save

WHY

Beat writers say things on camera they never file in print. A fifteen-minute
Texans segment carried Tank Dell's first padded individual drills, British
Brooks breaking his hand, Jake Hansen's ankle and Nico Collins getting a rest
day -- four things a reader needs, in a format nothing in our pipeline reads.

The competition is already doing this. Their nuggets carry YouTube's
auto-caption errors corrected by inference: the transcript says "Derek Single
Jr." and their post says "Derrick Stingley Jr.", which is a model reading
mangled captions, not a person transcribing.

WHAT THIS TESTS

Whether our extractor handles spoken language and broken captions. An article
is edited prose; a transcript is somebody talking, with names misspelled by a
machine, sentences abandoned halfway, and fifteen minutes of digression
around the four facts that matter.

If claims come out clean, adding video is a fetcher and a source entry.
If they do not, that is the thing to fix before building anything.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def video_id(s: str) -> str:
    """Accept a URL in any of its shapes, or a bare id."""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", s)
    return m.group(1) if m else s.strip()


def transcript(vid: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        sys.exit("  pip install youtube-transcript-api")
    try:
        api = YouTubeTranscriptApi()
        parts = api.fetch(vid)
    except Exception:
        # older versions of the package expose a classmethod instead
        try:
            parts = YouTubeTranscriptApi.get_transcript(vid)
        except Exception as exc:
            sys.exit(f"  no transcript: {str(exc)[:110]}")
    out = []
    for p in parts:
        t = p.get("text") if isinstance(p, dict) else getattr(p, "text", "")
        if t:
            out.append(t.replace("\n", " "))
    return " ".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--team", help="team code, so mentions resolve locally")
    ap.add_argument("--title", default="Beat writer video")
    ap.add_argument("--save", action="store_true", help="write to the database")
    ap.add_argument("--db", default="beatwire.db")
    args = ap.parse_args()

    vid = video_id(args.url)
    print(f"\n  video {vid}")
    text = transcript(vid)
    words = len(text.split())
    print(f"  transcript: {words:,} words, ~{len(text)//4:,} tokens")
    if words < 50:
        sys.exit("  too short to be a real transcript")

    # Auto-captions mangle names. Show a few so the damage is visible before
    # we ask the model to work with it.
    suspects = re.findall(r"\b[A-Z][a-z]+ (?:Single|Stra|Toento|Rutled)\w*", text)
    if suspects:
        print(f"  caption damage, first few: {', '.join(suspects[:4])}")

    from beatwire.extract import extract
    from beatwire.models import RawItem, Source
    from beatwire.registry import Registry
    from beatwire.resolve import Resolver
    import anthropic

    reg = Registry("nfl")
    resolver = Resolver(reg.players)
    src = Source(id=f"nfl-{(args.team or 'natl').lower()}-yt-{vid[:6]}",
                 sport="nfl", kind="rss", name=args.title, outlet="YouTube",
                 url=f"https://www.youtube.com/watch?v={vid}",
                 teams=[args.team] if args.team else [], weight=1.0)
    from datetime import datetime, timezone
    item = RawItem(source_id=src.id, url=src.url, title=args.title,
                   body=text,
                   published_at=datetime.now(timezone.utc).isoformat(),
                   kind="article")

    print("  extracting…")
    nuggets = extract(item, src, reg.profile, resolver,
                      client=anthropic.Anthropic(), stub=False)

    print(f"\n  {len(nuggets)} claims\n")
    print(f"  {'':<4}{'PLAYER':<24}{'EVENT':<20}CLAIM")
    unres = 0
    for n in sorted(nuggets, key=lambda x: -x.actionability):
        who = n.player_name or f"?{n.mention}" if hasattr(n, "mention") else "?"
        if not n.resolved:
            unres += 1
        print(f"  [{n.actionability}] {str(who)[:24]:<24}{n.event[:20]:<20}"
              f"{n.claim[:56]}")

    print(f"\n  {unres} of {len(nuggets)} did not match a player.")
    print(f"  That number is the whole question: auto-captions break names,")
    print(f"  and a claim we cannot attach to somebody helps nobody.")

    if args.save:
        import sqlite3
        from beatwire import store as store_mod
        conn = sqlite3.connect(args.db)
        st = store_mod.Store(conn) if hasattr(store_mod, "Store") else None
        if st is None:
            print("\n  --save not wired for this store shape; skipped")
        else:
            st.mark_seen(item.id, src.id, item.url, item)
            new = sum(1 for n in nuggets if st.add_nugget(n) == "new")
            st.commit()
            print(f"\n  saved, {new} new")


if __name__ == "__main__":
    main()
