"""YouTube Data API v3, for public metadata only.

The key lives in one place -- the YOUTUBE_API_KEY environment variable -- and
leaves this module in exactly one direction: onto the wire of an outgoing
request. It is never returned, never stored, never logged, and every error
raised from here is scrubbed before it is raised, because the request URL
contains the key and an unhandled traceback is a log line.

Only public reads happen here, so an API key is sufficient and OAuth is not
needed:

    playlistItems.list   the channel's uploads playlist, 1 quota unit
    videos.list          contentDetails for duration, 1 unit

search.list is deliberately absent. It costs 100 units a call, and at
ten-minute polling across a hundred channels that is ninety-six times the
default daily quota; the uploads playlist is the same information for one.

When there is no key, or the key is refused, discovery falls back to the
public channel RSS feed. That fallback is labelled YOUTUBE_RSS everywhere it
appears and is never described as playlistItems.list -- it returns less (no
duration), and a record that misstates where it came from is worse than a
record that admits it.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.googleapis.com/youtube/v3"

DATA_API = "YOUTUBE_DATA_API"
RSS = "YOUTUBE_RSS"


class YouTubeAPIError(RuntimeError):
    """An API failure, already scrubbed of the key."""


def api_key() -> str | None:
    """The key, or None. Read only from the environment, never from a file."""
    k = (os.environ.get("YOUTUBE_API_KEY") or "").strip()
    return k or None


def available() -> bool:
    return api_key() is not None


def redact(text: str) -> str:
    """Remove anything key-shaped from a string before it is shown to anyone.

    Two passes on purpose. The first removes the key we hold, which covers
    our own URLs; the second removes anything shaped like a Google API key,
    which covers a key we do not hold appearing in a response body or a
    message written by a library underneath us.
    """
    out = str(text)
    k = api_key()
    if k:
        out = out.replace(k, "[REDACTED]")
    out = re.sub(r"(?i)(key=)[A-Za-z0-9_\-]{10,}", r"\1[REDACTED]", out)
    out = re.sub(r"\bAIza[0-9A-Za-z_\-]{20,}", "[REDACTED]", out)
    return out


# Metadata calls made in this process. Quota-billed by Google, and entirely
# separate from the transcript budget -- they are different endpoints with
# different limits, and reporting them as one number is how a discovery run
# gets blamed for spending a caption allowance it never touched.
CALLS: dict = {"playlistItems": 0, "videos": 0}


def calls_made() -> int:
    return sum(CALLS.values())


def _call(endpoint: str, params: dict) -> dict:
    key = api_key()
    if not key:
        raise YouTubeAPIError("YOUTUBE_API_KEY is not set")
    q = dict(params)
    q["key"] = key
    CALLS[endpoint] = CALLS.get(endpoint, 0) + 1
    url = f"{API}/{endpoint}?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat-wire/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        reason = ""
        try:
            reason = (json.loads(body).get("error", {})
                      .get("errors", [{}])[0].get("reason", ""))
        except Exception:
            reason = ""
        # 400 keyInvalid, 403 keyRestricted / quotaExceeded. All of them mean
        # the same thing to the caller: use RSS instead of guessing.
        raise YouTubeAPIError(
            redact(f"{endpoint} returned HTTP {e.code}"
                   + (f" ({reason})" if reason else ""))) from None
    except Exception as e:
        raise YouTubeAPIError(
            redact(f"{endpoint} failed: {type(e).__name__}")) from None


def uploads_playlist(channel_id: str) -> str:
    """A channel's uploads playlist id.

    UC... -> UU... is a documented identity, so this costs no quota. Asking
    channels.list for contentDetails would return the same string for a unit.
    """
    if not re.fullmatch(r"UC[\w-]{22}", channel_id or ""):
        raise YouTubeAPIError(f"{channel_id!r} is not a UC channel id")
    return "UU" + channel_id[2:]


def list_uploads(channel_id: str, limit: int = 10) -> list[dict]:
    """Recent uploads. One quota unit.

    videoOwnerChannelId comes back on every item and is kept: the playlist is
    addressed by id, but the item says which channel actually owns the video,
    and that is the identity everything downstream is checked against.
    """
    data = _call("playlistItems", {
        "part": "snippet,contentDetails",
        "playlistId": uploads_playlist(channel_id),
        "maxResults": max(1, min(limit, 50)),
    })
    out = []
    for it in data.get("items", []):
        sn = it.get("snippet") or {}
        cd = it.get("contentDetails") or {}
        vid = cd.get("videoId") or (sn.get("resourceId") or {}).get("videoId")
        if not vid:
            continue
        out.append({
            "video_id": vid,
            "title": sn.get("title", ""),
            "description": (sn.get("description") or "")[:2000],
            "published_at": (cd.get("videoPublishedAt")
                             or sn.get("publishedAt") or ""),
            "channel_id": sn.get("videoOwnerChannelId") or "",
            "channel_name": sn.get("videoOwnerChannelTitle") or "",
            "url": f"https://www.youtube.com/watch?v={vid}",
            "discovery_method": DATA_API,
        })
    return out


ISO_DUR = re.compile(
    r"P(?:(?P<d>\d+)D)?T?(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?")


def parse_duration(iso: str) -> int | None:
    """PT1H2M3S -> seconds. None when it cannot be read safely.

    None matters: a video whose length cannot be established is not eligible
    for an automatic transcript request, so guessing zero would be worse than
    admitting ignorance.
    """
    if not iso or not iso.startswith("P"):
        return None
    m = ISO_DUR.fullmatch(iso)
    if not m:
        return None
    d, h, mi, s = (int(m.group(x) or 0) for x in ("d", "h", "m", "s"))
    total = d * 86400 + h * 3600 + mi * 60 + s
    return total or None


def durations(video_ids: list[str]) -> dict[str, int | None]:
    """Length for up to fifty videos in one unit."""
    out: dict[str, int | None] = {v: None for v in video_ids}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        data = _call("videos", {"part": "contentDetails",
                                "id": ",".join(chunk)})
        for it in data.get("items", []):
            vid = it.get("id")
            iso = ((it.get("contentDetails") or {}).get("duration") or "")
            if vid:
                out[vid] = parse_duration(iso)
    return out
