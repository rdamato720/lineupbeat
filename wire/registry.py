"""The approved article sources, and what each one needs to be read.

One registry, one entry per reporter. There is no universal path: the
feasibility probe found four different shapes among five working publishers,
and assumes a fifth for the next one. So an entry names its adapter rather
than hoping a generic reader copes.

A source is only polled when `active` is true AND its status is AUTO_READY.
Everything else stays in the file with the reason recorded -- a blocked
publisher that is deleted gets rediscovered and retried six months later by
somebody who does not know it was tried.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "sources" / "wire_articles.yaml"

# How a source is read. The names are the adapter contract.
FULL_TEXT_FEED = "FULL_TEXT_FEED"
EXCERPT_FEED_PAGE_FETCH = "EXCERPT_FEED_PAGE_FETCH"
SITE_FEED_AUTHOR_FILTER = "SITE_FEED_AUTHOR_FILTER"
AUTHOR_PAGE_SCRAPE = "AUTHOR_PAGE_SCRAPE"

ADAPTERS = {
    "rss_full_text": FULL_TEXT_FEED,
    "rss_excerpt_then_fetch": EXCERPT_FEED_PAGE_FETCH,
    "rss_sitewide_filtered": SITE_FEED_AUTHOR_FILTER,
    "author_page_scrape": AUTHOR_PAGE_SCRAPE,
}

AUTO_READY = "AUTO_READY"
MANUAL_URL_ONLY = "MANUAL_URL_ONLY"
CUSTOM_ADAPTER_NEEDED = "CUSTOM_ADAPTER_NEEDED"
BLOCKED = "BLOCKED"

REPORTING_TYPES = {"FIRSTHAND_PRACTICE", "LOCAL_BEAT", "PRESS_CONFERENCE",
                   "NATIONAL_REPORTER", "ANALYSIS", "AGGREGATOR"}


@dataclass
class Source:
    source_id: str
    source_name: str
    reporter_name: str
    teams: list[str]
    domains: list[str]
    status: str
    reporting_type: str
    adapter: str | None = None
    feed_url: str = ""
    author_page: str = ""
    default_language: str = "en"
    trust_tier: int = 1
    active: bool = False
    x_handle: str = ""
    blocked_reason: str = ""
    filter_author: str = ""
    filter_url_pattern: str = ""
    filter_categories: list[str] = field(default_factory=list)
    feed_scope: str = "site"
    strip_patterns: list[str] = field(default_factory=list)

    @property
    def pollable(self) -> bool:
        """Only AUTO_READY sources are polled unattended."""
        return self.active and self.status == AUTO_READY and bool(self.adapter)

    @property
    def manual_ok(self) -> bool:
        """Whether a hand-submitted URL may be ingested for this source.

        Extraction has to be known to work. Manual submission is a route
        around missing DISCOVERY, never around a paywall or a bot challenge:
        a publisher that refuses a fetch refuses it just as firmly when a
        person pastes the link.
        """
        return self.status in (AUTO_READY, MANUAL_URL_ONLY,
                               CUSTOM_ADAPTER_NEEDED)

    def owns(self, url: str) -> bool:
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        host = host.split(":")[0]
        return any(host == d or host.endswith("." + d) for d in self.domains)


def load(path: Path | None = None) -> list[Source]:
    doc = yaml.safe_load((path or REGISTRY).read_text()) or {}
    out = []
    for row in doc.get("sources", []):
        f = row.get("filter") or {}
        out.append(Source(
            source_id=row["source_id"],
            source_name=row.get("source_name", ""),
            reporter_name=row.get("reporter_name", ""),
            teams=row.get("teams") or [],
            domains=row.get("domains") or [],
            status=row.get("status", BLOCKED),
            reporting_type=row.get("reporting_type", "LOCAL_BEAT"),
            adapter=ADAPTERS.get(row.get("discovery", "")),
            feed_url=row.get("feed_url", "") or "",
            author_page=row.get("author_page", "") or "",
            default_language=row.get("default_language", "en"),
            trust_tier=int(row.get("trust_tier", 1)),
            active=bool(row.get("active", False)),
            x_handle=row.get("x_handle", "") or "",
            blocked_reason=row.get("blocked_reason", "") or "",
            filter_author=(f.get("author") or ""),
            filter_url_pattern=(f.get("url_pattern") or ""),
            filter_categories=[c.lower() for c in (f.get("categories") or [])],
            feed_scope=row.get("feed_scope", "site"),
            strip_patterns=row.get("strip_patterns") or [],
        ))
    return out


def problems(sources: list[Source]) -> list[str]:
    """Registry rules, checked rather than assumed."""
    bad, seen = [], set()
    for s in sources:
        if s.source_id in seen:
            bad.append(f"{s.source_id}: duplicate source_id")
        seen.add(s.source_id)
        if s.reporting_type not in REPORTING_TYPES:
            bad.append(f"{s.source_id}: unknown reporting_type "
                       f"{s.reporting_type!r}")
        if s.status == AUTO_READY and not s.adapter:
            bad.append(f"{s.source_id}: AUTO_READY with no known adapter")
        if s.status == AUTO_READY and not (s.feed_url or s.author_page):
            bad.append(f"{s.source_id}: AUTO_READY with nothing to poll")
        if s.status == BLOCKED and s.active:
            bad.append(f"{s.source_id}: BLOCKED but still active")
        # Any feed that is not a single-team publication must say who it is
        # for. This is not hypothetical tidiness: the first pilot run pulled
        # Notre Dame and Oklahoma football from A to Z and Diamondbacks
        # coverage from Arizona Sports, because both were registered as
        # whole-feed sources and neither is.
        if s.pollable and s.feed_url and s.feed_scope != "publication" and not (
                s.filter_author or s.filter_url_pattern or s.filter_categories):
            bad.append(f"{s.source_id}: feed_scope is {s.feed_scope!r} but no "
                       f"filter -- it would ingest the whole publication")
        if s.feed_scope not in ("site", "publication", "section"):
            bad.append(f"{s.source_id}: unknown feed_scope {s.feed_scope!r}")
        if not s.domains:
            bad.append(f"{s.source_id}: no domains, so no URL can be matched")
    return bad
