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
          daily_cap: float = 2.0) -> list[RawItem]:
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
    cursor = store.get_cursor(source.id)
    if cursor:
        params["cursor"] = cursor

    payload = _get("/twitter/user/last_tweets", params, key)
    raw = tweets_from(payload)

    # Bill on what actually came back, so an empty incremental poll is free.
    # That is the whole point of keeping the cursor.
    if raw:
        store.record_spend("twitterapi", source.id, len(raw),
                           len(raw) * COST_PER_TWEET)

    items = parse_timeline(payload, source, handle)

    nxt = payload.get("next_cursor") or payload.get("nextCursor")
    if not nxt and raw:
        nxt = str(raw[0].get("id") or raw[0].get("id_str") or "")
    if nxt:
        store.set_cursor(source.id, str(nxt))

    return items
