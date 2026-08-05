#!/usr/bin/env python3
"""Transcribe one beat-writer clip and measure what it adds over the caption.

    pip install faster-whisper --break-system-packages
    python3 scripts/try_transcribe.py --list
    python3 scripts/try_transcribe.py --url https://video.twimg.com/....mp4 --team NYJ
    python3 scripts/try_transcribe.py --pick 1

This is a measurement, not a pipeline. The only number that decides whether
audio is worth building is: how many usable nuggets does a clip produce that
the tweet text did not already give us? Everything else -- cost, storage,
which provider -- is downstream of that answer.

So the script does one clip, prints the caption's nuggets and the transcript's
nuggets side by side, and leaves the judgement to you.

Runs locally with faster-whisper: no API key, no per-minute charge, model
downloads once (~150MB for `base`). Slower than a hosted API and accurate
enough to answer the question. If the answer is yes, swap in a paid provider
for the real thing -- and pass the team's roster in as keyterms, since proper
nouns are where sports transcription actually fails.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatwire.extract import extract
from beatwire.models import RawItem, Source
from beatwire.registry import Registry
from beatwire.resolve import Resolver

ROOT = Path(__file__).resolve().parent.parent


def clips_from_db(db: str, limit: int = 12) -> list[dict]:
    """Video attached to stored nuggets, newest first."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT player_name, team, claim, media, published_at,
                  json_extract(attributions,'$[0].source_name') AS src
           FROM nuggets WHERE media != '[]' ORDER BY published_at DESC LIMIT ?""",
        (limit * 3,),
    ).fetchall()

    out, seen = [], set()
    for r in rows:
        for m in json.loads(r["media"] or "[]"):
            url = m.get("audio_url")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({
                "player": r["player_name"], "team": r["team"], "caption": r["claim"],
                "src": r["src"] or "", "audio_url": url,
                "tweet_url": m.get("tweet_url", ""),
                "secs": round((m.get("duration_ms") or 0) / 1000),
            })
            if len(out) >= limit:
                return out
    return out


def transcribe(url: str, model_size: str = "base", minutes: int = 0) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("  pip install faster-whisper --break-system-packages")

    print(f"  downloading clip …")
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, \
         tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fh:
        fh.write(r.read())
        path = fh.name

    print(f"  loading model '{model_size}' (first run downloads it) …")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    if minutes:
        print(f"  transcribing first {minutes} minutes …")
    else:
        print("  transcribing …")
    # Cap the window for long episodes. A 45 minute show on CPU is a long
    # wait, and the first few minutes of a practice-day podcast is enough to
    # judge whether the content is dense.
    segments, info = model.transcribe(
        path, beam_size=5, vad_filter=True,
        clip_timestamps=[0, minutes * 60] if minutes else "0",
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    Path(path).unlink(missing_ok=True)
    print(f"  {info.duration:.0f}s of audio, {len(text)} characters\n")
    return text


def nuggets_from(text: str, team: str | None, sport: str, stub: bool) -> list:
    reg = Registry(sport)
    resolver = Resolver(reg.players, reg.profile.position_groups)
    src = Source(id="transcribe-test", sport=sport, kind="podcast",
                 name="clip", outlet="test", teams=[team] if team else [])
    item = RawItem(source_id=src.id, sport=sport, url="", title="",
                   body=text, published_at=datetime.now(timezone.utc))
    client = None
    if not stub:
        import os
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("  ANTHROPIC_API_KEY not set (or pass --stub)")
        import anthropic
        client = anthropic.Anthropic()
    return extract(item, src, reg.profile, resolver, client=client, stub=stub)


def show(label: str, rows: list) -> None:
    print(f"  {label}: {len(rows)}")
    for n in rows:
        who = n.player_name if n.resolved else f"{n.player_name} (unmatched)"
        print(f"    [{n.category:<12} a{n.actionability}] {who:<22} {n.claim[:64]}")
    if not rows:
        print("    (nothing)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--list", action="store_true", help="show clips available to test")
    ap.add_argument("--pick", type=int, help="test the Nth clip from --list")
    ap.add_argument("--url", help="an mp4 url to test directly")
    ap.add_argument("--team", help="team code, for resolving bare surnames")
    ap.add_argument("--caption", default="", help="the tweet text, for comparison")
    ap.add_argument("--model", default="base",
                    help="tiny | base | small — larger is slower and better")
    ap.add_argument("--minutes", type=int, default=0,
                    help="only transcribe the first N minutes (long episodes)")
    ap.add_argument("--stub", action="store_true", help="no API call, keyword extractor")
    args = ap.parse_args()

    if args.list or (args.pick is None and not args.url):
        clips = clips_from_db(args.db)
        if not clips:
            sys.exit("  No clips with audio in the database.\n"
                     "  Run the pipeline against X sources first: media is only\n"
                     "  captured from twitterapi, not from RSS.")
        print(f"  {len(clips)} clips available\n")
        for i, c in enumerate(clips, 1):
            print(f"  {i:>2}. {c['player']:<20} {c['team']:<4} {c['secs']:>3}s  "
                  f"{c['src'][:18]:<18} {c['caption'][:44]}")
        print("\n  python3 scripts/try_transcribe.py --pick 1")
        return

    if args.pick:
        clips = clips_from_db(args.db)
        if args.pick < 1 or args.pick > len(clips):
            sys.exit(f"  --pick must be 1..{len(clips)}")
        c = clips[args.pick - 1]
        url, team, caption = c["audio_url"], c["team"], c["caption"]
        print(f"\n  {c['player']} ({c['team']}) · {c['secs']}s · {c['src']}")
        print(f"  {c['tweet_url']}\n")
    else:
        url, team, caption = args.url, args.team, args.caption

    text = transcribe(url, args.model, args.minutes)
    if not text:
        sys.exit("  Empty transcript. Likely ambient practice footage with no\n"
                 "  speech, which is exactly the class of clip not worth paying\n"
                 "  to transcribe. Try a presser or interview clip instead.")

    print("  TRANSCRIPT")
    print("  " + "\n  ".join(text[i:i+86] for i in range(0, min(len(text), 900), 86)))
    if len(text) > 900:
        print(f"  … ({len(text) - 900} more characters)")
    print()

    cap_rows = nuggets_from(caption, team, args.sport, args.stub) if caption else []
    tr_rows = nuggets_from(text, team, args.sport, args.stub)

    print("  " + "-" * 72)
    show("From the caption alone", cap_rows)
    print()
    show("From the transcript", tr_rows)

    cap_players = {n.player_id for n in cap_rows if n.resolved}
    new_players = {n.player_id for n in tr_rows if n.resolved} - cap_players
    print("\n  " + "-" * 72)
    print(f"  NET NEW: {max(0, len(tr_rows) - len(cap_rows))} nuggets, "
          f"{len(new_players)} players the caption never mentioned")
    print("\n  The number to watch across a dozen clips is that second one. If")
    print("  transcripts keep surfacing players the text missed, audio is worth")
    print("  building. If they mostly restate the caption, it is not.")


if __name__ == "__main__":
    main()
