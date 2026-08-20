#!/usr/bin/env python3
"""YouTube ingestion for the Wire. Local, budgeted, dark launch.

    python3 scripts/wire_youtube_ingest.py            # take today's next slot
    python3 scripts/wire_youtube_ingest.py --plan     # what it would do, no requests
    python3 scripts/wire_youtube_ingest.py --url ...  # one video, same budget
    python3 scripts/wire_youtube_ingest.py --status   # budget and cooldown

Run on a laptop. Captions are rate-limited by address and the ceiling is low:
thirty requests worked, roughly forty in an hour earned an IpBlocked that
outlasted several minutes. So this does not poll. It takes at most five
transcripts a day, one per channel, forty-five minutes apart, and stops
entirely for a day the moment YouTube says no.

Discovery is separate from that budget. Titles, ids, publication times and
durations cost nothing against the caption limit, so eligibility is decided
before a single transcript is requested.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import youtube
from wire.store import WireStore, now


def iso(dt) -> str:
    return dt.replace(microsecond=0).isoformat()


def budget_state(store) -> dict:
    """What the day has left, and whether YouTube is speaking to us."""
    used = store.requests_today()
    last = store.last_request_at()
    cooling = store.cooldown_until()
    blocked_until = None
    if cooling and cooling > now():
        blocked_until = cooling
    wait_minutes = 0
    if last:
        nxt = (datetime.fromisoformat(last)
               + timedelta(minutes=youtube.MIN_MINUTES_BETWEEN))
        if iso(nxt) > now():
            wait_minutes = max(
                1, int((nxt - datetime.now(timezone.utc)).total_seconds() // 60))
    return {"used": len(used),
            "remaining": max(0, youtube.MAX_REQUESTS_PER_DAY - len(used)),
            "last": last, "wait_minutes": wait_minutes,
            "blocked_until": blocked_until,
            "channels_done": store.channels_done_today()}


def may_request(state: dict) -> tuple[bool, str]:
    if state["blocked_until"]:
        return False, (f"YouTube returned IpBlocked; no transcript requests "
                       f"until {state['blocked_until']}")
    if state["remaining"] <= 0:
        return False, (f"the day's {youtube.MAX_REQUESTS_PER_DAY} transcript "
                       f"requests are spent")
    if state["wait_minutes"]:
        return False, (f"{state['wait_minutes']} min until the next slot "
                       f"({youtube.MIN_MINUTES_BETWEEN} min apart)")
    return True, ""


def pick(store, channels, rules, verbose=True) -> list[tuple]:
    """One eligible video per channel, cheapest checks first.

    Everything here is metadata: the feed, the title and the video's length.
    None of it touches the caption endpoint, so a channel with nothing worth
    transcribing today costs nothing to rule out.
    """
    done = store.channels_done_today()
    plan = []
    for ch in channels:
        if ch.channel_id in done:
            if verbose:
                print(f"  {ch.team:<4}{ch.source_name[:26]:<27} already had its "
                      f"video today")
            continue
        vids, err = youtube.uploads(ch, limit=8)
        chosen = None
        for v in vids:
            if store.cached_transcript(v["video_id"]):
                continue                      # never ask twice
            ok, mode, why = youtube.eligible(ch, v, rules)
            if not ok:
                continue
            secs, derr = youtube.duration_seconds(v["video_id"])
            ok, mode, why = youtube.eligible(ch, v, rules, seconds=secs)
            if not ok:
                if verbose:
                    print(f"  {ch.team:<4}{v['title'][:40]:<41} skipped: {why}")
                continue
            chosen = (ch, v, mode, secs)
            break
        if chosen:
            plan.append(chosen)
            if verbose:
                ch_, v, mode, secs = chosen
                print(f"  {ch.team:<4}{v['title'][:40]:<41} "
                      f"{mode} {secs//60}m -> eligible")
        elif verbose:
            print(f"  {ch.team:<4}{ch.source_name[:26]:<27} nothing eligible"
                  + (f" ({err})" if err else ""))
    return plan


def candidate_from(ch, video, tr, spans, mode, secs) -> dict:
    """The reviewable record. No span claims to know who is speaking."""
    return {
        "kind": "youtube",
        "source_id": ch.source_id, "source_name": ch.source_name,
        "channel_id": ch.channel_id, "teams": [ch.team],
        "approved_reporters": ch.approved_reporters,
        "classification": ch.classification,
        "attends_practice": ch.attends_practice,
        "reporting_type": ("FIRSTHAND_PRACTICE" if ch.attends_practice
                           else "ANALYSIS"),
        "trust_tier": 1,
        "video_id": video["video_id"], "canonical_url": video["url"],
        "headline": video["title"],
        "description": video.get("description", "")[:600],
        "published_at": video["published_at"],
        "duration_seconds": secs,
        "original_language": tr["language"],
        "transcript_source": tr["transcript_source"],
        "transcript_chars": tr["chars"],
        "speaker_mode": mode,
        "readiness": youtube.readiness(ch, mode),
        "content_sha256": hashlib.sha256(
            "".join(s["text"] for s in spans).encode()).hexdigest(),
        "evidence_spans": spans[:60],
        "excerpt": " ".join(s["text"] for s in spans[:4])[:600],
        "facts": [], "fantasy_relevance": "", "wire_label": "",
        "publication_confidence": None,
        "review_notes": (
            "Auto-generated captions: verify names, negation and numbers "
            "against the video before publishing."
            if tr["transcript_source"] == "AUTO_CAPTIONS" else ""),
    }


def take_one(store, ch, video, mode, secs, rules) -> str:
    """Spend one request. Every outcome is logged, including refusals."""
    cached = store.cached_transcript(video["video_id"])
    if cached:
        return "already cached; no request made"

    tr = youtube.fetch_transcript(video["video_id"], ch.transcript_languages)
    if not tr["ok"]:
        store.log_request(video["video_id"], "FAILED", tr["error"][:120])
        if "IpBlocked" in tr["error"] or "TooManyRequests" in tr["error"]:
            # Stop the whole thing for a day rather than hammering an address
            # that has just said no. Retrying is what turns a rate limit into
            # a longer ban.
            until = iso(datetime.now(timezone.utc)
                        + timedelta(hours=youtube.COOLDOWN_HOURS_AFTER_BLOCK))
            store.set_cooldown(until, tr["error"][:120])
            return f"IpBlocked -- all transcript requests paused until {until}"
        return f"no transcript ({tr['error'][:60]})"

    store.log_request(video["video_id"], "OK", tr["transcript_source"])
    store.save_transcript(video["video_id"], ch.channel_id, tr)

    if tr["chars"] < rules.min_transcript_chars:
        return (f"transcript cached but only {tr['chars']} chars "
                f"-> no candidate")

    spans = youtube.evidence_spans(video["video_id"], tr["segments"])
    payload = candidate_from(ch, video, tr, spans, mode, secs)
    art_like = type("A", (), {
        "source_id": ch.source_id, "canonical_url": video["url"],
        "headline": video["title"], "author": ", ".join(ch.approved_reporters),
        "published_at": video["published_at"], "retrieved_at": now(),
        "original_language": tr["language"],
        "raw_text": " ".join(s["text"] for s in tr["segments"]),
        "content_sha256": payload["content_sha256"],
        "extraction_status": "COMPLETE", "http_status": 200,
        "note": f"{tr['transcript_source']} transcript"})()
    item_id = store.save_item(art_like)
    store.add_candidate(payload["content_sha256"][:16], item_id, ch.source_id,
                        payload,
                        hashlib.sha256(video["url"].encode()).hexdigest()[:20])
    return (f"candidate {payload['content_sha256'][:12]} "
            f"({tr['chars']:,} chars, {len(spans)} spans)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true",
                    help="show what is eligible; request no transcripts")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--url", help="one video, still counted against the budget")
    ap.add_argument("--all-slots", action="store_true",
                    help="keep taking slots until the day's budget is spent")
    args = ap.parse_args()

    channels, rules = youtube.load()
    bad = youtube.problems(channels)
    if bad:
        for b in bad:
            print(f"  registry: {b}")
        sys.exit("  youtube registry is invalid; nothing ingested")

    store = WireStore()
    state = budget_state(store)
    pool = [c for c in channels if c.pollable]

    print(f"  budget: {state['used']}/{youtube.MAX_REQUESTS_PER_DAY} used today"
          f", {state['remaining']} left"
          + (f", blocked until {state['blocked_until']}"
             if state["blocked_until"] else "")
          + (f", next slot in {state['wait_minutes']} min"
             if state["wait_minutes"] else ""))
    print(f"  {len(pool)} channel(s) active, "
          f"{len(channels) - len(pool)} disabled or blocked")
    if args.status:
        return 0

    if args.url:
        vid = args.url.split("v=")[-1].split("&")[0]
        ch = next((c for c in pool if True), None)
        owner = None
        for c in pool:
            vids, _ = youtube.uploads(c, limit=15)
            if any(v["video_id"] == vid for v in vids):
                owner = c
                video = next(v for v in vids if v["video_id"] == vid)
                break
        if owner is None:
            sys.exit(f"  {vid} is not a recent upload of any approved channel")
        ok, why = may_request(state)
        if not ok:
            sys.exit(f"  {why}")
        secs, _ = youtube.duration_seconds(vid)
        mode = youtube.speaker_mode(video["title"], rules)
        print(f"  manual: {owner.team} {video['title'][:52]}")
        print(f"    {take_one(store, owner, video, mode, secs, rules)}")
        return 0

    plan = pick(store, pool, rules, verbose=True)
    if not plan:
        print("\n  nothing eligible today")
        return 0
    print(f"\n  {len(plan)} eligible; budget allows {state['remaining']}")

    if args.plan:
        print("  --plan, no transcript requested")
        return 0

    taken = 0
    for ch, video, mode, secs in plan:
        state = budget_state(store)
        ok, why = may_request(state)
        if not ok:
            print(f"\n  stopping: {why}")
            break
        print(f"\n  {ch.team} {video['title'][:56]}")
        result = take_one(store, ch, video, mode, secs, rules)
        print(f"    {result}")
        taken += 1
        if "IpBlocked" in result:
            break
        if not args.all_slots:
            print(f"    one slot per run; "
                  f"{youtube.MIN_MINUTES_BETWEEN} min until the next")
            break

    n, changed = store.export_publications()
    print(f"\n  {taken} request(s) made. {n} published items"
          f"{' (file updated)' if changed else ''}")
    print("  nothing was published; candidates await review_wire.py")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
