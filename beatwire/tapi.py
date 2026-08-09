"""twitterapi.io adapter.

Third-party X reseller. ~$0.15 per 1000 tweets against $0.005 each on the
official API, which is the difference between affording 32 writers and
affording all 111. No X approval process.

The tradeoff, stated plainly: this is a scraping-based reseller, not an
authorised X client. That means terms exposure and a service that could
disappear. Everything here is therefore written to be swappable -- it shares
the cursor and spend plumbing with the official `x` adapter, so moving back
is a config change rather than a rewrite.

Two rules, same as the official adapter:
  1. Never re-fetch what a cursor already covers.
  2. Hard local daily spend cap. Nothing upstream will stop a runaway loop.

Lives in its own module because patching it into ingest.py by hand kept
failing. Import is one line at the bottom of ingest.py.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .ingest import SpendCapExceeded, stitch_threads
from .models import RawItem, Source

BASE = "https://api.twitterapi.io"
# Nothing older than this is worth reading, and on a fresh database
# it is the difference between a few days and several years.
MAX_AGE_DAYS = 4

COST_PER_TWEET = 0.00015


def _get(path: str, params: dict, key: str, timeout: int = 20) -> dict:
    url = f"{BASE}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"X-API-Key": key, "User-Agent": "lineupbeat/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def tweets_from(payload: dict) -> list[dict]:
    """Dig the tweet list out of the response envelope.

    Defensive across several plausible shapes because I could not verify the
    exact one. If a real call returns nothing, print the raw payload and fix
    this single function.
    """
    if isinstance(payload.get("tweets"), list):
        return payload["tweets"]
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("tweets"), list):
        return data["tweets"]
    return []


def _parse_time(raw: str) -> datetime:
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw, fmt)
        except (ValueError, TypeError):
            continue
    try:
        return datetime.fromisoformat((raw or "").replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return datetime.now(timezone.utc)


def extract_media(tw: dict) -> list[dict]:
    """Pull video/photo out of extendedEntities.

    We keep the tweet permalink and a thumbnail, not the raw mp4. Re-hosting a
    beat writer's clip is the fastest way to earn a takedown and burn the
    relationship; embedding sends them the engagement instead.
    """
    out = []
    for m in (tw.get("extendedEntities") or {}).get("media", []) or []:
        kind = m.get("type")
        if kind not in ("video", "animated_gif", "photo"):
            continue
        vi = m.get("video_info") or {}
        # Lowest-bitrate mp4 is fine: this is only ever used for audio.
        mp4s = [v for v in vi.get("variants", [])
                if (v.get("content_type") or "").endswith("mp4") and v.get("bitrate")]
        mp4s.sort(key=lambda v: v.get("bitrate", 0))
        out.append({
            "type": kind,
            "thumb": m.get("media_url_https") or "",
            "tweet_url": m.get("expanded_url") or "",
            "duration_ms": vi.get("duration_millis"),
            "audio_url": mp4s[0]["url"] if mp4s else None,
        })
    return out


def parse_timeline(payload: dict, source: Source, handle: str) -> list[RawItem]:
    posts = []
    own = handle.lower().lstrip("@")

    for tw in tweets_from(payload):
        text = (tw.get("text") or "").strip()
        if not text or text.startswith("RT @"):
            continue

        # A quote tweet's own text often has no player name in it: an analyst
        # writes "which thumb joint, and is there ligament damage?" over a beat
        # writer's report naming A.J. Brown. Without the quoted text the
        # prefilter drops it for mentioning nobody, and the whole commentary
        # layer disappears. Append it as context, clearly marked, so the
        # extractor knows who is being discussed and whose claim is whose.
        quoted = tw.get("quoted_tweet") or {}
        qtext = (quoted.get("text") or "").strip() if isinstance(quoted, dict) else ""
        if qtext:
            text = f"{text}\n\n[Responding to]: {qtext}"

        # Keep thread continuations, drop replies to other people. Beat
        # writers file practice reports as threads and the reporting lives in
        # posts 2-6, so excluding all replies keeps the header and discards
        # the news. Field naming varies, so check the variants.
        reply_to = (
            tw.get("inReplyToUsername")
            or tw.get("in_reply_to_username")
            or tw.get("inReplyToUserName")
            or ""
        )
        if reply_to and reply_to.lower().lstrip("@") != own:
            continue

        tid = str(tw.get("id") or tw.get("id_str") or "")
        posts.append({
            "media": extract_media(tw),
            "id": tid,
            "thread_id": str(
                tw.get("conversationId") or tw.get("conversation_id") or tid
            ),
            "text": text,
            "created": _parse_time(tw.get("createdAt") or tw.get("created_at") or ""),
            "url": tw.get("url") or f"https://x.com/{handle}/status/{tid}",
        })

    items = []
    for th in stitch_threads(posts):
        item = RawItem(
            source_id=source.id, sport=source.sport, url=th["url"], title="",
            body=th["text"], published_at=th["created"], kind="twitterapi",
        )
        item.media = th.get("media") or []
        items.append(item)
    return items


def fetch(source: Source, store=None, key: str | None = None,
          daily_cap: float = 12.0) -> list[RawItem]:
    """Read one writer's timeline.

    The cap was two dollars, which was right for eighty-nine handles. At a
    hundred and seventy-four it bought under four runs: the wire went silent
    before ten in the morning and the site sat two hours stale for the rest
    of the day, with the log full of refusals nobody was reading.

    Twelve covers twenty runs across every handle. It is a ceiling, not a
    budget -- a normal day spends well under it, and it exists to stop a
    loop costing hundreds overnight.
    """
    key = key or os.environ.get("TWITTERAPI_IO_KEY")
    if not key:
        raise ValueError("TWITTERAPI_IO_KEY not set")
    if store is None:
        raise ValueError("twitterapi fetch needs the store for cursor and spend")

    spent = store.spend_today("twitterapi")
    if spent >= daily_cap:
        raise SpendCapExceeded(
            f"twitterapi spend today ${spent:.2f} has reached the "
            f"${daily_cap:.2f} cap"
        )

    handle = source.handle.lstrip("@")
    params = {"userName": handle}

    # No cursor. Deliberately.
    #
    # `last_tweets` is newest-first and its next_cursor means "the page
    # BEFORE this one". Storing that and sending it back next run resumed
    # the poll one page deeper into the past, every hour, forever. The
    # oldest post this collected was from February 2018, and the model was
    # paid to read all of it -- roughly 2,500 items a run against a real
    # publishing rate near a thousand a day across every source combined.
    #
    # Page one is what has been posted since the last poll, plus overlap.
    # The overlap is free: seen_items drops it before extraction, which is
    # what "where I got to" actually means here. A pagination cursor was
    # never the right thing to persist.

    payload = _get("/twitter/user/last_tweets", params, key)
    raw = tweets_from(payload)

    # Bill on what came back. Page one always returns something, so this is
    # a real cost per poll now rather than free-when-empty -- but it is one
    # page, not an unbounded walk.
    if raw:
        store.record_spend("twitterapi", source.id, len(raw),
                           len(raw) * COST_PER_TWEET)

    items = parse_timeline(payload, source, handle)

    # And a floor on age, so a first run against an empty database picks up
    # a few days rather than whatever the page happens to reach back to.
    if MAX_AGE_DAYS:
        from datetime import datetime, timedelta, timezone
        floor = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
        kept = []
        for it in items:
            when = getattr(it, "published_at", None)
            if when is None:
                kept.append(it)
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when >= floor:
                kept.append(it)
        items = kept

    return items
