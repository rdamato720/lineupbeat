"""Discovery and capture. Everything before a model sees anything.

Two jobs, kept apart because they fail apart. Discovery finds candidate URLs;
capture turns one URL into a stored article with a body, an author, a date and
a hash. The probe found a publisher with a perfect feed and a 403 on every
article (MassLive) and another with a 13,000-character body and no feed at all
(Boston Herald). One "does it work" number would have hidden both.

Nothing here is published. Capture writes source items; a candidate is only
ever created from a COMPLETE one, and only a reviewer promotes it further.
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

import feedparser
import trafilatura

from .registry import (AUTHOR_PAGE_SCRAPE, EXCERPT_FEED_PAGE_FETCH,
                       FULL_TEXT_FEED, SITE_FEED_AUTHOR_FILTER, Source)

# A real browser string, and not as a trick. Several publishers answer a bare
# urllib agent with 403 and a browser agent with the article -- Arizona Sports
# is one, which is why it looked blocked in the first probe and is a working
# source now. Where a publisher genuinely refuses (MassLive, ESPN), it refuses
# this too, and that answer is taken.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Below this a "body" is a teaser, a consent wall or a headline echo. The
# shortest genuine beat article measured in the probe was just over 2,000
# characters; 700 is deliberately generous and still refuses a stub.
MIN_BODY_CHARS = 700

PAYWALL = re.compile(
    r"subscribe (now|today)|already a subscriber|create a free account|"
    r"this content is for subscribers|sign in to (read|continue)|"
    r"you have \d+ free|unlimited digital access|register to continue", re.I)

COMPLETE, INCOMPLETE, BLOCKED_FETCH = "COMPLETE", "INCOMPLETE", "BLOCKED"


@dataclass
class Article:
    source_id: str
    canonical_url: str
    headline: str = ""
    author: str = ""
    published_at: str = ""
    retrieved_at: str = ""
    original_language: str = "en"
    raw_text: str = ""
    content_sha256: str = ""
    extraction_status: str = INCOMPLETE
    http_status: object = None
    note: str = ""

    @property
    def usable(self) -> bool:
        return (self.extraction_status == COMPLETE
                and len(self.raw_text) >= MIN_BODY_CHARS
                and bool(self.canonical_url) and bool(self.published_at))


def _decode(resp, raw: bytes) -> str:
    """Decompress before decoding.

    urllib does not do this and does not complain: a gzipped page comes back
    as bytes that decode to mojibake, trafilatura finds no article in it and
    returns nothing. That reads exactly like a publisher blocking extraction,
    and it cost a source that was working fine -- PhillyVoice returned 200
    with 20KB of body and zero extracted characters until this was added.
    """
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc:
        import gzip
        raw = gzip.decompress(raw)
    elif "deflate" in enc:
        import zlib
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    elif "br" in enc:
        try:
            import brotli
            raw = brotli.decompress(raw)
        except ImportError:
            # Never guess at the bytes. An undecodable body is a failed
            # capture, not an empty article.
            raise RuntimeError("brotli response but brotli is not installed")
    charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, "replace")


def _get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        # Only what we can actually decode. Asking for brotli and then being
        # unable to read it is worse than a slightly larger download.
        "Accept-Encoding": "gzip, deflate",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _decode(r, r.read()), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, "", url
    except Exception as e:
        return f"ERR {type(e).__name__}", "", url


def _clean(html_or_text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html_or_text or "")
    return re.sub(r"\s+", " ", t).strip()


def _entry_time(e) -> str:
    for k in ("published_parsed", "updated_parsed"):
        v = getattr(e, k, None)
        if v:
            return datetime(*v[:6], tzinfo=timezone.utc).isoformat()
    return ""


def _matches(src: Source, entry) -> bool:
    """Does this feed item belong to this reporter?

    A site-wide feed carries the whole publication. PhillyVoice's has crime
    and events in it; si.com's has every sport. Both signals are checked
    where the registry supplies them, and the URL shape is trusted over the
    byline because bylines vary in formatting.
    """
    link = getattr(entry, "link", "") or ""
    path = urllib.parse.urlparse(link).path
    if src.filter_url_pattern and not re.search(src.filter_url_pattern, path):
        return False
    if src.filter_author:
        author = (getattr(entry, "author", "") or "").lower()
        if src.filter_author.lower() not in author:
            return False
    if src.filter_categories:
        terms = {t.get("term", "").lower()
                 for t in (getattr(entry, "tags", None) or [])}
        if not (terms & set(src.filter_categories)):
            return False
    return True


def discover(src: Source, limit: int = 25) -> list[dict]:
    """Candidate URLs for one source. No fetching of article pages here."""
    if src.adapter == AUTHOR_PAGE_SCRAPE:
        return _discover_author_page(src, limit)
    if not src.feed_url:
        return []
    status, body, _ = _get(src.feed_url)
    if not (isinstance(status, int) and status == 200 and body):
        return []
    out = []
    for e in feedparser.parse(body).entries[:200]:
        if not _matches(src, e):
            continue
        content = ""
        if getattr(e, "content", None):
            content = e.content[0].get("value", "")
        summary = getattr(e, "summary", "")
        best = content if len(content) > len(summary) else summary
        out.append({
            "url": getattr(e, "link", "").split("?utm_")[0],
            "headline": getattr(e, "title", ""),
            "author": getattr(e, "author", "") or src.reporter_name,
            "published_at": _entry_time(e),
            # The markup, not stripped text. A full-text feed carries the
            # publisher's furniture with it -- Pewter Report's entries open
            # with a sponsor block offering a free tailgate grill -- and
            # trafilatura strips that where a tag-strip cannot.
            "feed_html": best,
            "feed_text": _clean(best),
        })
        if len(out) >= limit:
            break
    return out


def _discover_author_page(src: Source, limit: int) -> list[dict]:
    """Boston Herald has no usable feed and a perfectly good author page.

    Link shapes only -- headline, author and date come from the article
    itself, so nothing here has to understand the publisher's markup beyond
    "this looks like a story URL".
    """
    if not src.author_page:
        return []
    status, html, final = _get(src.author_page)
    if not (isinstance(status, int) and status == 200 and html):
        return []
    host = urllib.parse.urlparse(final).netloc
    seen, out = set(), []
    for href in re.findall(r'href="([^"]+)"', html):
        url = urllib.parse.urljoin(final, href).split("?")[0]
        if urllib.parse.urlparse(url).netloc != host or url in seen:
            continue
        if not re.search(r"/20\d\d/\d{2}/\d{2}/", url):
            continue
        seen.add(url)
        out.append({"url": url, "headline": "", "author": src.reporter_name,
                    "published_at": "", "feed_text": ""})
        if len(out) >= limit:
            break
    return out


def capture(src: Source, item: dict) -> Article:
    """One candidate URL becomes one stored article, or an honest failure."""
    art = Article(source_id=src.source_id,
                  canonical_url=item.get("url", ""),
                  headline=item.get("headline", ""),
                  author=item.get("author", "") or src.reporter_name,
                  published_at=item.get("published_at", ""),
                  retrieved_at=datetime.now(timezone.utc).isoformat(),
                  original_language=src.default_language)

    # A feed that already carries the article is the whole capture. Pewter
    # Report and A to Z both do this, and not fetching the page is faster,
    # politer and immune to whatever the page does to scrapers.
    if src.adapter == FULL_TEXT_FEED and len(item.get("feed_text", "")) >= MIN_BODY_CHARS:
        # Run the extractor over the feed's own HTML rather than trusting it.
        # Same boilerplate rules as a fetched page, and no request made.
        cleaned = trafilatura.extract(item.get("feed_html") or "",
                                      include_comments=False,
                                      include_tables=False,
                                      favor_precision=True) or ""
        art.raw_text = cleaned if len(cleaned) >= MIN_BODY_CHARS else item["feed_text"]
        art.extraction_status = COMPLETE
        art.note = ("body from feed, boilerplate stripped"
                    if len(cleaned) >= MIN_BODY_CHARS else "body from feed, raw")
    else:
        status, html, final = _get(art.canonical_url)
        art.http_status = status
        if not (isinstance(status, int) and status == 200 and html):
            art.extraction_status = BLOCKED_FETCH
            art.note = f"fetch returned {status}"
            return art
        body = trafilatura.extract(html, include_comments=False,
                                   include_tables=False,
                                   favor_precision=True) or ""
        meta = trafilatura.extract_metadata(html)
        if meta:
            art.headline = art.headline or (meta.title or "")
            art.author = art.author or (meta.author or "")
            art.published_at = art.published_at or (meta.date or "")
            art.canonical_url = meta.url or final or art.canonical_url
        art.raw_text = body
        if len(body) < MIN_BODY_CHARS:
            # A headline, an excerpt or a consent wall. Never publishable:
            # §17 refuses anything without a full body, and a partial article
            # is exactly how a claim gets quoted without its qualifier.
            art.extraction_status = INCOMPLETE
            art.note = ("paywalled" if PAYWALL.search(html[:400000])
                        else f"body only {len(body)} chars")
        else:
            art.extraction_status = COMPLETE

    # Publisher furniture the extractor cannot recognise. In a feed body a
    # sponsor block is just another paragraph -- Pewter Report opens every
    # entry with one -- so the pattern is declared per source rather than
    # guessed at. This is why adapters are source-specific.
    for pat in src.strip_patterns:
        art.raw_text = re.sub(pat, " ", art.raw_text, flags=re.I | re.S)
    art.raw_text = re.sub(r"\s{2,}", " ", art.raw_text).strip()
    if len(art.raw_text) < MIN_BODY_CHARS and art.extraction_status == COMPLETE:
        art.extraction_status = INCOMPLETE
        art.note = (art.note or "") + "; too short once boilerplate removed"

    art.content_sha256 = hashlib.sha256(art.raw_text.encode()).hexdigest()
    if not art.published_at:
        art.extraction_status = INCOMPLETE
        art.note = (art.note or "") + "; no publication date"
    return art


def polite_sleep(seconds: float = 1.0):
    time.sleep(seconds)
