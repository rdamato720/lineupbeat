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
from datetime import datetime, timedelta, timezone

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


SEARCH_PATH = "/twitter/tweet/advanced_search"
TIMELINE_PATH = "/twitter/user/last_tweets"

# Pages of a single poll's catch-up. A writer filing more than a hundred
# posts between two-hourly polls does not happen; the cap is here so a
# malformed cursor cannot walk forever.
MAX_SEARCH_PAGES = 5

# Re-ask from slightly before where we got to. The index lags real time by a
# little, and seen_items drops whatever comes back twice, so the overlap
# costs a few credits and closes the gap where a post lands between the last
# tweet we saw and the moment we asked.
OVERLAP_SECONDS = 300

# How long a source may return nothing before we spend a full timeline read
# on it. Search came back empty once for a writer who had posted an hour
# earlier -- transient, and it corrected on the next call, but a source that
# is genuinely missing from the index would otherwise go quiet forever and
# look like a slow news week. This bounds that to a day.
RECONCILE_AFTER_HOURS = 24


def _hw_key(source_id: str) -> str:
    return source_id


def _rec_key(source_id: str) -> str:
    return f"{source_id}#reconciled"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _advance(store, source: Source, raw: list[dict],
             high_water: datetime | None) -> None:
    """Move the high-water mark to the newest post we actually received.

    Forward only, and only from a real post. An empty response never reaches
    here, which is the point: the mark is where the reporting got to, not
    where the clock got to.
    """
    newest = max((_parse_time(t.get("createdAt") or t.get("created_at") or "")
                  for t in raw), default=None)
    if newest and (high_water is None or newest > high_water):
        store.set_cursor(_hw_key(source.id), _iso(newest))


def _bill(store, source: Source, n_tweets: int) -> None:
    """One request, billed on what it returned.

    There is a floor: a request that finds nothing still costs the price of
    one tweet. Measured against the account balance, an empty two-hour
    window charged exactly that. Busy pages occasionally charge slightly
    more than the tweets we end up parsing, so treat this column as close
    rather than exact -- `cli spend` is for spotting a runaway, and the
    provider's own dashboard is the invoice.
    """
    store.record_spend("twitterapi", source.id, max(1, n_tweets),
                       max(1, n_tweets) * COST_PER_TWEET)


def _timeline(source: Source, store, key: str, handle: str) -> list[dict]:
    """One page of the writer's timeline. The old behaviour, kept as the
    fallback and as the reconciliation read.

    includeReplies is on. It defaults to false, which is why no thread
    continuation ever reached the extractor: a beat writer files practice
    notes as a self-reply chain and the reporting is in posts two through
    six. Measured on three handles, the flag changes which twenty posts come
    back and costs nothing extra.
    """
    payload = _get(TIMELINE_PATH,
                   {"userName": handle, "includeReplies": "true"}, key)
    raw = tweets_from(payload)
    if raw:
        _bill(store, source, len(raw))
    return raw


def _search(source: Source, store, key: str, handle: str,
            since: datetime, until: datetime) -> list[dict]:
    """Everything the writer posted in a window, and nothing else.

    This is the whole cost argument. `last_tweets` has no way to say "only
    what is new": it returns its twenty most recent posts and bills for all
    twenty, so a two-hourly poll pays for the same twenty posts twelve times
    a day to collect the two that are new. Measured over a real day on one
    handle, twelve polls cost 3,600 credits that way and 465 this way.
    """
    out: list[dict] = []
    cursor, pages = "", 0
    while pages < MAX_SEARCH_PAGES:
        params = {
            "query": (f"from:{handle} since_time:{int(since.timestamp())} "
                      f"until_time:{int(until.timestamp())}"),
            "queryType": "Latest",
        }
        if cursor:
            params["cursor"] = cursor
        payload = _get(SEARCH_PATH, params, key)
        page = tweets_from(payload)
        _bill(store, source, len(page))
        out += page
        pages += 1
        cursor = payload.get("next_cursor") or ""
        if not payload.get("has_next_page") or not cursor:
            break
    return out


def fetch(source: Source, store=None, key: str | None = None,
          daily_cap: float = 12.0) -> list[RawItem]:
    """Read one writer's timeline, paying only for what is new.

    HOW THE WINDOW IS TRACKED, AND WHY THIS IS NOT THE 2018 BUG

    There was a cursor here once and it was a disaster: `last_tweets`
    paginates backwards, its `next_cursor` means *the page before this one*,
    and persisting it walked each poll further into the past until the model
    was being paid to read posts from February 2018. That cursor was removed
    and page one taken every time instead.

    What is stored now is not that. It is a timestamp -- the moment of the
    newest post we have actually seen -- and it only ever moves forward. It
    is the answer to "what is new since I last looked", which is the
    question a poll is asking, and it cannot walk anywhere.

    It advances only from a post we actually received. Never to "now", and
    never on an empty response: search returned an empty page once for a
    writer who had posted an hour before, and a high-water mark set to the
    clock would have skipped that window permanently. Leaving it where it is
    means a transient miss is picked up on the next poll instead.

    THE FALLBACK

    Anything that goes wrong with the search path -- an error, or a source
    that stays silent past RECONCILE_AFTER_HOURS -- falls back to reading
    the timeline the old way. It costs more, which is the point: it is what
    stops a search-side problem from looking like a quiet news day. Set
    BEATWIRE_TAPI_MODE=timeline to take the old path for everything.

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
    now = datetime.now(timezone.utc)
    floor = now - timedelta(days=MAX_AGE_DAYS) if MAX_AGE_DAYS else now - timedelta(days=1)

    raw: list[dict] = []
    if os.environ.get("BEATWIRE_TAPI_MODE", "").lower() == "timeline":
        raw = _timeline(source, store, key, handle)
    else:
        high_water = _parse_iso(store.get_cursor(_hw_key(source.id)))
        since = (high_water - timedelta(seconds=OVERLAP_SECONDS)
                 if high_water else floor)
        # Never ask for more history than the age filter would keep anyway.
        since = max(since, floor)
        try:
            raw = _search(source, store, key, handle, since, now)
        except Exception as exc:
            print(f"  ~ {source.id}: search failed ({str(exc)[:60]}), "
                  f"falling back to the timeline")
            raw = _timeline(source, store, key, handle)

        if raw:
            _advance(store, source, raw, high_water)
            store.set_cursor(_rec_key(source.id), _iso(now))
        else:
            # Nothing found. Do not move the high-water mark -- see the
            # docstring. Start the clock if this source has never had one, so
            # "silent since" means something on the next poll.
            if high_water is None:
                store.set_cursor(_hw_key(source.id), _iso(floor))
            last_seen = _parse_iso(store.get_cursor(_rec_key(source.id))) or high_water
            silent_for = None if last_seen is None else now - last_seen
            if silent_for is None or silent_for > timedelta(hours=RECONCILE_AFTER_HOURS):
                hours = "no history" if silent_for is None \
                    else f"{silent_for.total_seconds() / 3600:.0f}h"
                print(f"  ~ {source.id}: search has found nothing ({hours}), "
                      f"reading the timeline")
                raw = _timeline(source, store, key, handle)
                store.set_cursor(_rec_key(source.id), _iso(now))
                _advance(store, source, raw, high_water)

    items = parse_timeline({"tweets": raw}, source, handle)

    # And a floor on age, so a first run against an empty database picks up
    # a few days rather than whatever the page happens to reach back to.
    if MAX_AGE_DAYS:
        kept = []
        for it in items:
            whenever = getattr(it, "published_at", None)
            if whenever is None:
                kept.append(it)
                continue
            if whenever.tzinfo is None:
                whenever = whenever.replace(tzinfo=timezone.utc)
            if whenever >= floor:
                kept.append(it)
        items = kept

    return items
