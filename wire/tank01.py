"""Private Tank01 news transport and schema boundary.

Tank01 is a dark-launch discovery source.  Nothing in this module creates Wire
evidence, model input, public copy, or a publication.  The first live response
is deliberately treated as an unknown external contract: known shapes are
normalized, and every other shape fails visibly instead of looking like a
quiet news hour.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HOST = "tank01-nfl-live-in-game-real-time-statistics-nfl.p.rapidapi.com"
URL = f"https://{HOST}/getNFLNews"
KEY_ENV = "TANK01_RAPIDAPI_KEY"
CONTAINER_KEYS = (
    "news", "items", "articles", "playerNews", "fantasyNews", "topNews",
    "headlines", "results",
)
TITLE_KEYS = ("title", "headline", "newsTitle", "name")
SUMMARY_KEYS = ("description", "summary", "news", "body", "content", "blurb")
URL_KEYS = ("link", "url", "newsLink", "sourceURL", "sourceUrl", "articleUrl")
SOURCE_KEYS = ("source", "sourceName", "publisher", "outlet")
TIME_KEYS = (
    "publishedAt", "published", "publishedDate", "date", "updatedAt",
    "updated", "lastUpdated", "newsUpdate", "timestamp",
)
ID_KEYS = ("newsID", "newsId", "articleID", "articleId", "id")
PLAYER_KEYS = ("playerID", "playerId", "playerIDs", "playerIds")
TEAM_KEYS = ("teamAbv", "team", "teamAbvs", "teams")


class Tank01Error(RuntimeError):
    """A scrubbed provider or schema failure."""


def redact(value: object, key: str = "") -> str:
    text = str(value)
    held = key or os.environ.get(KEY_ENV, "")
    if held:
        text = text.replace(held, "[REDACTED]")
    text = re.sub(
        r"(?i)(x-rapidapi-key|rapidapi[_-]?key|api[_-]?key)[\"':=\s]+[^\s\"',}]+",
        r"\1: [REDACTED]", text,
    )
    return text


def scrub_payload(value: object, key: str = "") -> object:
    """Recursively remove a held credential before any response is serialized."""
    if isinstance(value, dict):
        return {str(k): scrub_payload(v, key) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_payload(item, key) for item in value]
    if isinstance(value, tuple):
        return [scrub_payload(item, key) for item in value]
    if isinstance(value, str):
        return redact(value, key)
    return value


def _default_transport(url: str, headers: dict, timeout: int) -> object:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            detail = ""
        raise Tank01Error(redact(f"Tank01 HTTP {exc.code}: {detail}")) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise Tank01Error(redact(f"Tank01 transport error: {type(exc).__name__}: {exc}")) from None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        raise Tank01Error("Tank01 response was not JSON") from None


def fetch_news(key: str | None = None, timeout: int = 30, transport=None) -> object:
    secret = key or os.environ.get(KEY_ENV, "")
    if not secret:
        raise Tank01Error(f"{KEY_ENV} is not set")
    if len(secret.strip()) < 12:
        raise Tank01Error(f"{KEY_ENV} is not a plausible RapidAPI key")
    headers = {
        "X-RapidAPI-Key": secret,
        "X-RapidAPI-Host": HOST,
        "Accept": "application/json",
        "User-Agent": "lineupbeat-tank01-dark-launch/1.0",
    }
    caller = transport or _default_transport
    try:
        return caller(URL, headers, timeout)
    except Tank01Error:
        raise
    except Exception as exc:
        raise Tank01Error(redact(
            f"Tank01 transport error: {type(exc).__name__}: {exc}", secret)) from None


def _json_body(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _looks_like_story(row: dict) -> bool:
    return any(row.get(key) not in (None, "") for key in TITLE_KEYS) and any(
        row.get(key) not in (None, "") for key in URL_KEYS + SUMMARY_KEYS)


def extract_items(payload: object) -> list[dict]:
    """Extract a documented/observed news collection or refuse the schema."""
    if not isinstance(payload, dict):
        raise Tank01Error("Tank01 root must be an object")
    status = payload.get("statusCode")
    if status is not None and str(status) not in {"200", "200.0"}:
        detail = payload.get("error") or payload.get("message") or "provider error"
        raise Tank01Error(redact(f"Tank01 statusCode {status}: {detail}"))
    has_body = "body" in payload
    body = _json_body(payload["body"] if has_body else payload)
    # Tank01 returns a successful empty result as statusCode=200, body=[],
    # error="Your query returned no results."  An empty list is a valid news
    # collection, not a provider failure.  Keep failing when an error arrives
    # without any parseable response body.
    if payload.get("error") and (not has_body or body in (None, "", {})):
        raise Tank01Error(redact(f"Tank01 error: {payload['error']}"))
    if isinstance(body, list):
        if not all(isinstance(row, dict) for row in body):
            raise Tank01Error("Tank01 news list contains a non-object item")
        return body
    if not isinstance(body, dict):
        raise Tank01Error(f"Tank01 body has unsupported type {type(body).__name__}")
    if _looks_like_story(body):
        return [body]

    for key in CONTAINER_KEYS:
        value = _json_body(body.get(key))
        if isinstance(value, list):
            if not all(isinstance(row, dict) for row in value):
                raise Tank01Error(f"Tank01 {key} contains a non-object item")
            return value
        if isinstance(value, dict):
            rows = list(value.values())
            if rows and all(isinstance(row, dict) for row in rows):
                return rows
    root_keys = sorted(str(key) for key in payload)[:30]
    body_keys = sorted(str(key) for key in body)[:30]
    raise Tank01Error(
        f"Tank01 schema has no recognized news collection; root_keys={root_keys}; "
        f"body_keys={body_keys}")


def _first(row: dict, keys: tuple[str, ...]) -> object:
    for key in keys:
        if row.get(key) not in (None, "", [], {}):
            return row[key]
    return ""


def _strings(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        values = list(value.keys()) if all(not isinstance(v, (dict, list)) for v in value.values()) \
            else list(value.values())
        out = []
        for item in values:
            out.extend(_strings(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_strings(item))
        return out
    return [str(value).strip()] if str(value).strip() else []


def parse_time(value: object) -> str:
    if value in (None, ""):
        return ""
    raw = str(value).strip()
    try:
        number = float(raw)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def normalize(row: dict) -> dict:
    if not isinstance(row, dict):
        raise Tank01Error("Tank01 news item must be an object")
    headline = str(_first(row, TITLE_KEYS) or "").strip()
    summary = str(_first(row, SUMMARY_KEYS) or "").strip()
    url = str(_first(row, URL_KEYS) or "").strip()
    source_value = _first(row, SOURCE_KEYS)
    source = json.dumps(source_value, sort_keys=True, ensure_ascii=False) \
        if isinstance(source_value, (dict, list)) else str(source_value or "").strip()
    published_at = parse_time(_first(row, TIME_KEYS))
    player_ids = sorted(set(_strings(_first(row, PLAYER_KEYS))))
    teams = sorted(set(_strings(_first(row, TEAM_KEYS))))
    supplied_id = str(_first(row, ID_KEYS) or "").strip()
    fingerprint = json.dumps(
        [headline, summary, url, source, published_at, player_ids, teams],
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    story_id = supplied_id or hashlib.sha256(fingerprint.encode()).hexdigest()[:24]
    return {
        "story_id": story_id,
        "headline": headline[:1000],
        "summary": summary[:5000],
        "url": url,
        "source": source[:500],
        "published_at": published_at,
        "provider_player_ids": player_ids,
        "teams": teams,
        "raw_keys": sorted(str(key) for key in row),
    }


def schema_fingerprint(payload: object, items: list[dict]) -> dict:
    root_keys = sorted(payload) if isinstance(payload, dict) else []
    body = _json_body(payload.get("body")) if isinstance(payload, dict) else None
    body_keys = sorted(body) if isinstance(body, dict) else []
    item_keys = sorted({str(key) for row in items for key in row})
    raw = json.dumps([root_keys, body_keys, item_keys], separators=(",", ":"))
    return {
        "sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "root_keys": root_keys,
        "body_keys": body_keys,
        "item_keys": item_keys,
    }
