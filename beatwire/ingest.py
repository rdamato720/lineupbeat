"""Fetch layer.

Deliberately has no X/Twitter adapter. X pay-per-use pricing plus the 2M
read cap makes near-real-time polling of a few hundred accounts a business
risk rather than a data source. Everything here is RSS or podcast RSS, which
is free, stable, and legally uncomplicated to poll.

Adding a new adapter means adding a function to ADAPTERS keyed by Source.kind.
"""

from __future__ import annotations

import html
import json
import os
import re
import calendar
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import feedparser

from .models import RawItem, Source

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def _clean(html_text: str) -> str:
    """Strip tags, then decode entities.

    Order matters and the decode is not optional. Feed content is full of
    &#8217; and &mdash;, and if they survive into the extractor they end up
    quoted verbatim in nuggets, which looks broken to a reader and wastes
    tokens on the way in.
    """
    text = TAG_RE.sub(" ", html_text or "")
    text = html.unescape(text)
    return WS_RE.sub(" ", text).strip()


def _parse_date(entry) -> datetime:
    """Feed timestamps, read as UTC.

    feedparser returns `published_parsed` already normalised to UTC, but
    `time.mktime` interprets a struct_time as LOCAL time. On a machine in
    New York that shifted every RSS timestamp four hours forward, which put
    the newest items in the future and made every other one quietly wrong.
    `calendar.timegm` is the UTC counterpart and is what this always needed.

    A feed can still legitimately carry a scheduled future time, so clamp:
    something published later today is, for our purposes, published now.
    """
    for key in ("published_parsed", "updated_parsed"):
        val = getattr(entry, key, None)
        if val:
            when = datetime.fromtimestamp(calendar.timegm(val), tz=timezone.utc)
            now = datetime.now(timezone.utc)
            return min(when, now)
    return datetime.now(timezone.utc)


def fetch_rss(source: Source, limit: int = 40) -> list[RawItem]:
    feed = feedparser.parse(source.url)
    items = []
    for entry in feed.entries[:limit]:
        body = ""
        if getattr(entry, "content", None):
            body = entry.content[0].get("value", "")
        body = body or getattr(entry, "summary", "")
        items.append(
            RawItem(
                source_id=source.id,
                sport=source.sport,
                url=getattr(entry, "link", ""),
                title=_clean(getattr(entry, "title", "")),
                body=_clean(body),
                published_at=_parse_date(entry),
                kind="rss",
            )
        )
    return items


def fetch_podcast(source: Source, limit: int = 10) -> list[RawItem]:
    """Podcast episodes come back with an audio_url and no transcript.

    Transcription is a separate, deliberately pluggable step. This is the
    part of the pipeline that is actually differentiated: most of the useful
    beat information in every sport is spoken on a local radio hit or a team
    podcast and never written down anywhere.
    """
    feed = feedparser.parse(source.url)
    items = []
    for entry in feed.entries[:limit]:
        audio = None
        for link in getattr(entry, "links", []):
            if link.get("type", "").startswith("audio"):
                audio = link.get("href")
                break
        items.append(
            RawItem(
                source_id=source.id,
                sport=source.sport,
                url=getattr(entry, "link", "") or (audio or ""),
                title=_clean(getattr(entry, "title", "")),
                body=_clean(getattr(entry, "summary", "")),
                published_at=_parse_date(entry),
                kind="podcast",
                audio_url=audio,
            )
        )
    return items


def _fixture_time(value: str) -> datetime:
    """Accept an ISO timestamp or a relative offset like '-9h' / '-3d'.

    Relative offsets keep decay behaviour testable: a fixture written as
    '-9h' sits in the same part of its useful-life window every time you run
    it, instead of drifting into the past and quietly going stale.
    """
    from datetime import timedelta

    m = re.fullmatch(r"\s*-(\d+)([hd])\s*", value)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = timedelta(hours=n) if unit == "h" else timedelta(days=n)
        return datetime.now(timezone.utc) - delta
    return datetime.fromisoformat(value)


def fetch_fixture(source: Source, limit: int = 40) -> list[RawItem]:
    """Offline adapter. Reads fixtures/<source_id>.json.

    Lets the whole pipeline run and be tested with no network at all.
    """
    path = Path(__file__).resolve().parent.parent / "fixtures" / f"{source.id}.json"
    if not path.exists():
        return []
    items = []
    for row in json.loads(path.read_text())[:limit]:
        items.append(
            RawItem(
                source_id=source.id,
                sport=source.sport,
                url=row["url"],
                title=row["title"],
                body=row.get("body", ""),
                published_at=_fixture_time(row["published_at"]),
                kind=row.get("kind", "rss"),
                transcript=row.get("transcript"),
            )
        )
    return items


# ---------------------------------------------------------------------------
# Threads (the multi-post kind)
# ---------------------------------------------------------------------------
#
# Beat writers file practice reports as threads. The first post is often just
# a header ("Practice notes, Wednesday:") and the actual reporting lives in
# posts two through six. Excluding replies, which is the obvious default on
# both platforms, therefore throws away most of the content and keeps the part
# with none of it.
#
# Self-replies are thread continuations and must be kept. Replies to other
# people are conversation, not reporting, and are still dropped.
#
# Stitching matters as much as keeping. A lone post reading "he was limited
# again" has no antecedent, and neither the resolver nor the extractor can do
# anything with it. Reassembled into its thread, it reads normally.

def stitch_threads(posts: list[dict]) -> list[dict]:
    """Group posts by thread id and concatenate in chronological order.

    Each post is a dict with: id, thread_id, text, created, url.
    Returns one dict per thread, using the first post's url and timestamp.
    """
    threads: dict[str, list[dict]] = {}
    for post in posts:
        threads.setdefault(post["thread_id"] or post["id"], []).append(post)

    out = []
    for parts in threads.values():
        parts.sort(key=lambda p: p["created"])
        head = parts[0]
        # Strip "1/6" style counters; they are noise to the extractor.
        text = "\n\n".join(
            re.sub(r"^\s*\(?\d{1,2}\s*/\s*\d{1,2}\)?[.:)]?\s*", "", p["text"])
            for p in parts
        )
        out.append({
            "id": head["id"],
            "text": text.strip(),
            "created": head["created"],
            "url": head["url"],
            "n_posts": len(parts),
            # Media from anywhere in the thread, not just the first post.
            "media": [m for p in parts for m in (p.get("media") or [])],
        })
    return out


# ---------------------------------------------------------------------------
# Bluesky
# ---------------------------------------------------------------------------
#
# The AT Protocol AppView serves public reads with no auth, no key, and no
# paid tier. That is the whole reason this adapter exists where an X adapter
# does not: the cost of a platform repricing you out of business is zero here.
#
# Design note: this polls each writer's author feed rather than consuming the
# Jetstream firehose. The firehose is the right tool for keyword discovery
# across the whole network, but you are watching a known list of maybe two
# hundred accounts, and filtering ~850 MB/day down to a trickle to find them
# is strictly worse than asking for them directly.

BSKY_API = "https://public.api.bsky.app/xrpc"


def _bsky_get(endpoint: str, params: dict, timeout: int = 20) -> dict:
    url = f"{BSKY_API}/{endpoint}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post_url(handle: str, uri: str) -> str:
    """at://did/app.bsky.feed.post/<rkey> -> https://bsky.app/profile/h/post/rkey"""
    rkey = uri.rsplit("/", 1)[-1] if uri else ""
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def parse_author_feed(payload: dict, source: Source) -> list[RawItem]:
    """Split out from the fetch so it can be tested without a network call."""
    posts, own_did = [], None

    for entry in payload.get("feed", []):
        # Reposts are someone else's reporting. Attribution would be wrong and
        # the original is very likely already in the feed from its own source.
        if entry.get("reason"):
            continue
        post = entry.get("post") or {}
        record = post.get("record") or {}
        text = (record.get("text") or "").strip()
        if not text:
            continue

        author = post.get("author") or {}
        if own_did is None:
            own_did = author.get("did")
        handle = author.get("handle") or source.handle

        # Keep thread continuations, drop conversation with other people.
        reply = entry.get("reply") or {}
        parent_did = (((reply.get("parent") or {}).get("author") or {}).get("did"))
        if parent_did and parent_did != author.get("did"):
            continue

        root_uri = ((reply.get("root") or {}).get("uri")) or post.get("uri", "")
        created = record.get("createdAt") or post.get("indexedAt")
        try:
            published = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            published = datetime.now(timezone.utc)

        posts.append({
            "id": post.get("uri", ""),
            "thread_id": root_uri,
            "text": text,
            "created": published,
            "url": post_url(handle, post.get("uri", "")),
        })

    return [
        RawItem(
            source_id=source.id, sport=source.sport, url=th["url"], title="",
            body=th["text"], published_at=th["created"], kind="bluesky",
        )
        for th in stitch_threads(posts)
    ]


def fetch_bluesky(source: Source, limit: int = 50) -> list[RawItem]:
    if not source.handle:
        raise ValueError(f"{source.id}: bluesky source needs a `handle`")
    payload = _bsky_get(
        "app.bsky.feed.getAuthorFeed",
        # posts_with_replies, not posts_no_replies: thread continuations are
        # replies to yourself, and they carry most of the reporting.
        {"actor": source.handle, "limit": min(limit, 100),
         "filter": "posts_with_replies"},
    )
    return parse_author_feed(payload, source)


# ---------------------------------------------------------------------------
# X
# ---------------------------------------------------------------------------
#
# X moved to pay-per-use with no monthly cap. That removes the wall that made
# this unusable, and replaces it with a different hazard: nothing stops a
# polling bug from billing all night. So this adapter is built around two
# non-negotiables.
#
# 1. NEVER re-fetch. Billing is per resource fetched, so polling a timeline
#    and getting the same ten posts back charges you for ten posts, every
#    time. At 200 writers polled every 20 minutes that is ~$12,600/month. The
#    identical workload with `since_id` is ~$600/month, and at one writer per
#    team it is ~$96/month. The cursor is not an optimisation, it is the
#    entire difference between viable and ruinous.
#
# 2. A hard local spend cap. There is no monthly cap upstream to catch you.
#
# Endpoint shapes are from the v2 docs and have NOT been checked against the
# new Developer Console. Run `python -m beatwire.cli spend --provider x` after
# the first day and confirm the billing matches what you expect before you
# leave it running unattended.

X_API = "https://api.x.com/2"
X_COST_PER_POST = 0.005


class SpendCapExceeded(RuntimeError):
    pass


def _x_get(path: str, params: dict, token: str, timeout: int = 20) -> dict:
    url = f"{X_API}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}",
                      "User-Agent": "lineupbeat/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def x_user_id(handle: str, token: str) -> str:
    """One User: Read per handle, billed separately. Cache it in the registry
    as `x_user_id` so you pay this once rather than every run."""
    payload = _x_get(f"users/by/username/{handle.lstrip('@')}", {}, token)
    return (payload.get("data") or {}).get("id", "")


def parse_x_timeline(payload: dict, source: Source, handle: str,
                     own_id: str = "") -> list[RawItem]:
    """Separated from fetching so it is testable without spending money."""
    posts = []
    for post in payload.get("data", []) or []:
        text = (post.get("text") or "").strip()
        if not text or text.startswith("RT @"):
            continue

        # Self-replies continue a thread and are kept. Replies to anyone else
        # are conversation and are dropped.
        in_reply_to = post.get("in_reply_to_user_id")
        if in_reply_to and own_id and in_reply_to != own_id:
            continue

        created = post.get("created_at")
        try:
            published = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            published = datetime.now(timezone.utc)

        posts.append({
            "id": post.get("id", ""),
            "thread_id": post.get("conversation_id") or post.get("id", ""),
            "text": text,
            "created": published,
            "url": f"https://x.com/{handle}/status/{post.get('id','')}",
        })

    return [
        RawItem(
            source_id=source.id, sport=source.sport, url=th["url"], title="",
            body=th["text"], published_at=th["created"], kind="x",
        )
        for th in stitch_threads(posts)
    ]


def fetch_x(source: Source, store=None, token: str | None = None,
            daily_cap: float = 5.0, max_results: int = 50) -> list[RawItem]:
    token = token or os.environ.get("X_BEARER_TOKEN")
    if not token:
        raise ValueError("X_BEARER_TOKEN not set")
    if store is None:
        raise ValueError("fetch_x needs the store for cursor and spend tracking")

    spent = store.spend_today("x")
    if spent >= daily_cap:
        raise SpendCapExceeded(
            f"x spend today ${spent:.2f} has reached the ${daily_cap:.2f} cap"
        )

    handle = source.handle.lstrip("@")
    user_id = source.x_user_id or x_user_id(handle, token)

    params = {
        "max_results": max(5, min(max_results, 100)),
        # conversation_id groups a thread; in_reply_to_user_id separates a
        # thread continuation from a reply to someone else.
        "tweet.fields": "created_at,conversation_id,in_reply_to_user_id",
        # Only retweets excluded. Excluding replies would drop most of the
        # reporting, since practice notes are filed as threads.
        "exclude": "retweets",
    }
    since = store.get_cursor(source.id)
    if since:
        params["since_id"] = since

    payload = _x_get(f"users/{user_id}/tweets", params, token)
    items = parse_x_timeline(payload, source, handle, own_id=user_id)

    # Bill on what actually came back. An empty incremental poll costs nothing,
    # which is exactly why the cursor matters so much.
    n = len(payload.get("data", []) or [])
    if n:
        store.record_spend("x", source.id, n, n * X_COST_PER_POST)
    newest = (payload.get("meta") or {}).get("newest_id")
    if newest:
        store.set_cursor(source.id, newest)

    return items


# ---------------------------------------------------------------------------
# Meta Threads
# ---------------------------------------------------------------------------
#
# NOT ENABLED. Kept because it is written and correct, not because it should
# be switched on.
#
# The Profile Discovery API (GET /profile_posts?username=...) genuinely does
# return other people's public posts. The blocker is not technical and not
# cost, since the API is free. It is the stated permitted use:
#
#   "You may use the Threads API to enable people to create and publish
#    content on a person's behalf on Threads, and to display those posts
#    within your app solely to the person who created it."
#
# That describes a publishing client. This project reads other people's posts
# and shows them to third parties, which is the opposite. There is real
# tension between that sentence and the existence of Profile Discovery and
# Keyword Search, and App Review is exactly where that tension is resolved,
# by Meta rather than by us.
#
# The rate limit design corroborates the reading: calls = 4800 * impressions
# on YOUR OWN Threads account, floored at 10 impressions. A headless
# aggregator sits at that floor permanently. The quota assumes an app
# publishing for an active account.
#
# If you want to pursue it anyway, read the Platform Terms first and treat
# App Review as the decision point. Do not build a launch plan around it.

THREADS_API = "https://graph.threads.com/v1.0"
THREADS_DAILY_QUOTA = 1000

THREADS_FIELDS = "id,text,timestamp,permalink,username,is_quote_post,media_type"


class QuotaExceeded(RuntimeError):
    pass


def parse_threads_posts(payload: dict, source: Source) -> list[RawItem]:
    """Testable without approved access, which matters because approval is
    weeks away and this should not be the thing blocking a deploy."""
    posts = []
    for post in payload.get("data", []) or []:
        text = (post.get("text") or "").strip()
        if not text or post.get("is_quote_post"):
            continue
        raw = post.get("timestamp")
        try:
            published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            published = datetime.now(timezone.utc)
        posts.append({
            "id": post.get("id", ""),
            # No conversation id is exposed here, so threads cannot be
            # reassembled the way they can on X and Bluesky. Posts arrive as
            # fragments; expect weaker extraction from this source and weight
            # it accordingly.
            "thread_id": post.get("id", ""),
            "text": text,
            "created": published,
            "url": post.get("permalink", ""),
        })
    return [
        RawItem(
            source_id=source.id, sport=source.sport, url=th["url"], title="",
            body=th["text"], published_at=th["created"], kind="threads",
        )
        for th in stitch_threads(posts)
    ]


def fetch_threads(source: Source, store=None, token: str | None = None,
                  daily_quota: int = THREADS_DAILY_QUOTA,
                  limit: int = 25) -> list[RawItem]:
    token = token or os.environ.get("THREADS_ACCESS_TOKEN")
    if not token:
        raise ValueError("THREADS_ACCESS_TOKEN not set")
    if store is None:
        raise ValueError("fetch_threads needs the store for quota tracking")

    used = int(store.spend_today("threads_requests"))
    if used >= daily_quota:
        raise QuotaExceeded(
            f"threads quota {used}/{daily_quota} used for the rolling day"
        )

    params = {
        "username": source.handle.lstrip("@"),
        "fields": THREADS_FIELDS,
        "limit": min(limit, 100),
        "access_token": token,
    }
    since = store.get_cursor(source.id)
    if since:
        params["since"] = since

    url = f"{THREADS_API}/profile_posts?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        payload = json.loads(r.read().decode())

    store.record_spend("threads_requests", source.id, 1, 0.0)
    items = parse_threads_posts(payload, source)
    if items:
        newest = max(i.published_at for i in items)
        store.set_cursor(source.id, newest.date().isoformat())
    return items


ADAPTERS = {
    "rss": fetch_rss,
    "podcast": fetch_podcast,
    "bluesky": fetch_bluesky,
    "fixture": fetch_fixture,
}


def fetch(source: Source, offline: bool = False, store=None,
          x_daily_cap: float = 5.0,
          tapi_daily_cap: float = 12.0) -> list[RawItem]:
    if offline:
        return fetch_fixture(source)
    try:
        if source.kind == "x":
            return fetch_x(source, store=store, daily_cap=x_daily_cap)
        if source.kind == "threads":
            return fetch_threads(source, store=store)
        if source.kind == "twitterapi":
            # Which provider reads X.
            #
            # twitterapi charges per tweet returned, so the bill moves with
            # how much other people post. Sorsa charges per request, which
            # for a fixed 174 sources is a number you can predict. The
            # source registry does not care which is running, so this is an
            # environment variable rather than 174 edits -- and switching
            # back if coverage disappoints is the same one variable.
            if os.environ.get("BEATWIRE_X_PROVIDER", "").lower() == "sorsa":
                from .sorsa import fetch as _sorsa_fetch
                return _sorsa_fetch(source, store=store,
                                    daily_cap=tapi_daily_cap)
            from .tapi import fetch as _tapi_fetch
            return _tapi_fetch(source, store=store,
                               daily_cap=tapi_daily_cap)
        adapter = ADAPTERS.get(source.kind)
        if adapter is None:
            raise ValueError(f"No adapter for source kind '{source.kind}'")
        return adapter(source)
    except (SpendCapExceeded, QuotaExceeded) as exc:
        # Loud, and not swallowed like a dead feed: this one costs money.
        print(f"  $ {source.id}: {exc}")
        return []
    except Exception as exc:  # a dead feed must never kill a run
        print(f"  ! {source.id}: fetch failed ({exc})")
        return []


# ---------------------------------------------------------------------------
# Transcription hook
# ---------------------------------------------------------------------------

def transcribe(item: RawItem, backend: str = "none") -> RawItem:
    """Fill item.transcript from item.audio_url.

    Left as a seam on purpose. Options, cheapest first:
      - local faster-whisper on a GPU box, effectively free at this volume
      - a hosted speech-to-text API, roughly cents per episode
      - skip audio entirely for v1 and run text-only

    Cost sanity check before you build this: one team's podcast and radio
    output is maybe 3 hours a day in season. Thirty-two teams is ~100 hours
    a day. That number is what decides whether audio is your moat or your
    bankruptcy, so measure it on one team first.
    """
    if backend == "none" or not item.audio_url:
        return item
    raise NotImplementedError(
        "Wire your transcription backend here. See docstring for options."
    )
