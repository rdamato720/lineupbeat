"""Read a writer's timeline from Sorsa, in place of twitterapi.

Same interface as tapi.fetch, so ingest does not care which is running.
Set BEATWIRE_X_PROVIDER=sorsa and SORSA_API_KEY, and nothing else changes.

WHY A SECOND PROVIDER RATHER THAN A REPLACEMENT

twitterapi charges per tweet returned, so the bill moves with how much
other people post -- a busy Sunday costs more than a quiet Tuesday for the
same coverage. Sorsa charges per request, which for 174 fixed sources is a
number you can predict a month ahead.

The switch is an environment variable because a provider that is cheaper on
paper is not automatically better in practice. If coverage turns out to be
thinner, going back is one variable rather than a rewrite.

WHAT IS DIFFERENT ABOUT THE SHAPE

It is a POST with a JSON body, not a GET with query parameters. Fields are
snake_case: full_text, created_at, user.username. There is no cursor
handling here at all -- one page, newest first, and seen_items already
knows what we have. A sample of a busy writer's twenty most recent posts
reached back three days, which is far more headroom than an hourly poll
needs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from .models import RawItem, Source
from .tapi import _parse_time, stitch_threads

BASE = "https://api.sorsa.io/v3"

# One request, whatever it returns. Recorded so the spend table stays
# meaningful across a provider change: units are requests here, not tweets,
# and the cost column is what actually matters.
COST_PER_REQUEST = 0.00049      # Starter: $49 for 100k requests

# Same floor as twitterapi. A writer's twenty most recent posts can reach
# back a week in the offseason, and re-reading month-old camp reports is
# how a bill grows without the wire improving.
MAX_AGE_DAYS = 4


def _post(path: str, body: dict, key: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"ApiKey": key, "Content-Type": "application/json",
                 "User-Agent": "lineupbeat/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def extract_media(tw: dict) -> list[dict]:
    """Video and photo, if the payload carries them.

    Written defensively because Sorsa's docs say fields can be added at any
    time, and a media key that does not exist yet should mean no media
    rather than a traceback in the middle of a run.
    """
    out = []
    media = (tw.get("media") or tw.get("extended_entities", {}).get("media")
             or [])
    if not isinstance(media, list):
        return out
    for m in media:
        if not isinstance(m, dict):
            continue
        kind = m.get("type")
        if kind not in ("video", "animated_gif", "photo"):
            continue
        out.append({
            "kind": "video" if kind != "photo" else "photo",
            "thumb": m.get("preview_image_url") or m.get("media_url_https")
            or m.get("url") or "",
            "duration": m.get("duration_millis"),
        })
    return out


def parse_timeline(payload: dict, source: Source, handle: str) -> list[RawItem]:
    """Sorsa's tweets into RawItems, matching tapi's output exactly."""
    posts = []
    own = handle.lower().lstrip("@")
    floor = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

    for tw in (payload.get("tweets") or []):
        if not isinstance(tw, dict):
            continue
        text = (tw.get("full_text") or tw.get("text") or "").strip()
        if not text or text.startswith("RT @"):
            continue

        # A quote tweet's own text often names nobody: an analyst writes
        # "which thumb joint?" over a beat writer's report about A.J. Brown.
        # Without the quoted text the prefilter drops it for mentioning no
        # player, and the commentary layer disappears.
        quoted = tw.get("quoted_tweet") or tw.get("quoted") or {}
        if isinstance(quoted, dict):
            qtext = (quoted.get("full_text") or quoted.get("text") or "").strip()
            if qtext:
                text = f"{text}\n\n[Responding to]: {qtext}"

        # Thread continuations stay, replies to other people go. Beat
        # writers file practice reports as threads and the news is in posts
        # two through six.
        reply = tw.get("in_reply_to") or {}
        reply_to = ""
        if isinstance(reply, dict):
            reply_to = (reply.get("username") or "")
        reply_to = reply_to or tw.get("in_reply_to_username") or ""
        if reply_to and reply_to.lower().lstrip("@") != own:
            continue

        created = _parse_time(tw.get("created_at") or "")
        if created and created < floor:
            continue

        tid = str(tw.get("id") or tw.get("id_str") or "")
        posts.append({
            "media": extract_media(tw),
            "id": tid,
            "thread_id": str(tw.get("conversation_id") or tid),
            "text": text,
            "created": created,
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
    """One writer's timeline. Signature matches tapi.fetch deliberately.

    The cap is kept even though the arithmetic barely reaches it: a flat
    per-request price makes a runaway cheaper, not impossible, and the point
    of a ceiling is the bug you have not written yet.
    """
    key = key or os.environ.get("SORSA_API_KEY")
    if not key:
        raise ValueError("SORSA_API_KEY not set")

    handle = (source.handle or "").lstrip("@")
    if not handle:
        raise ValueError(f"{source.id} has no handle")

    if store is not None and daily_cap:
        spent = store.spend_today("sorsa")
        if spent >= daily_cap:
            raise RuntimeError(
                f"sorsa daily cap reached (${spent:.2f} of ${daily_cap:.2f})")

    try:
        payload = _post("/user-tweets", {"username": handle}, key)
    except urllib.error.HTTPError as e:
        # 402 is out of requests, 404 is a handle that no longer exists.
        # Both are worth saying plainly rather than as a stack trace.
        body = e.read().decode("utf-8", "replace")[:120]
        raise RuntimeError(f"HTTP {e.code}: {body}") from None

    if store is not None:
        store.record_spend("sorsa", source.id, 1, COST_PER_REQUEST)

    return parse_timeline(payload, source, handle)
