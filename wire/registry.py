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
# One adapter, thirty-two landing pages. See wire/si.py.
SI_TEAM_PAGE = "SI_TEAM_PAGE"
# The national Fantasy On SI landing page. It is a manual editorial-analysis
# lane, not a source of firsthand team reporting.
SI_FANTASY_PAGE = "SI_FANTASY_PAGE"
# A paid publisher we may look at and may not read. See PAID_ONLY below.
PAID_METADATA_ONLY = "PAID_METADATA_ONLY"
# One reusable adapter for the 32 club websites.
NFL_TEAM_SITE = "NFL_TEAM_SITE"

# What kind of source this is. The class decides what its evidence may be
# used for, which is not the same question as whether the evidence is good.
SI_ONSI = "SI_ONSI"
SI_ONSI_ANALYSIS = "SI_ONSI_ANALYSIS"
OFFICIAL_TEAM_SITE = "OFFICIAL_TEAM_SITE"
INDEPENDENT_LOCAL = "INDEPENDENT_LOCAL"
SOURCE_CLASSES = {SI_ONSI, SI_ONSI_ANALYSIS, OFFICIAL_TEAM_SITE, INDEPENDENT_LOCAL,
                  "DISCOVERY_ONLY_PAID"}

# Who owns the publication. A club's own writer may be excellent and at every
# practice, and still cannot corroborate the club: TEAM_OWNED evidence never
# counts toward independent_source_count.
TEAM_OWNED = "TEAM_OWNED"
INDEPENDENT = "INDEPENDENT"

ADAPTERS = {
    "rss_full_text": FULL_TEXT_FEED,
    "rss_excerpt_then_fetch": EXCERPT_FEED_PAGE_FETCH,
    "rss_sitewide_filtered": SITE_FEED_AUTHOR_FILTER,
    "author_page_scrape": AUTHOR_PAGE_SCRAPE,
    "si_team_page": SI_TEAM_PAGE,
    "si_fantasy_page": SI_FANTASY_PAGE,
    "paid_metadata_only": PAID_METADATA_ONLY,
    "nfl_team_site": NFL_TEAM_SITE,
}

AUTO_READY = "AUTO_READY"
MANUAL_URL_ONLY = "MANUAL_URL_ONLY"
CUSTOM_ADAPTER_NEEDED = "CUSTOM_ADAPTER_NEEDED"
BLOCKED = "BLOCKED"
# A source whose extraction works but whose every article goes to a human.
# This is where new SI teams start and where they stay until an author has
# been read and promoted by hand.
MANUAL_REVIEW_ONLY = "MANUAL_REVIEW_ONLY"
# Worth reading, never a firsthand claim.
ANALYSIS_ONLY = "ANALYSIS_ONLY"
# Discovery is permitted; the body is not ours to take. Metadata supplied by
# the publisher's own feed, an external link for a human, and nothing else.
DISCOVERY_ONLY_PAID = "DISCOVERY_ONLY_PAID"

# Every state a source may hold.
STATES = {AUTO_READY, MANUAL_URL_ONLY, CUSTOM_ADAPTER_NEEDED, BLOCKED,
          MANUAL_REVIEW_ONLY, ANALYSIS_ONLY, DISCOVERY_ONLY_PAID}

# States whose articles may never yield an evidence span, whatever else is
# true of them. Checked at the source, at capture and at extraction, because
# one check is a bug away from being no check.
PAID_ONLY = {DISCOVERY_ONLY_PAID}
PAID_LABEL = "PAID_SUBSCRIPTION_REQUIRED"

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
    si_team_slug: str = ""
    landing_page: str = ""
    fallback_page: str = ""
    source_class: str = ""
    source_ownership: str = INDEPENDENT
    qualifying_series: str = ""
    evidence_access: str = ""

    @property
    def team_owned(self) -> bool:
        return self.source_ownership == TEAM_OWNED

    @property
    def paid(self) -> bool:
        """A paid source. Discovery only, forever, unless a human changes it."""
        return self.status in PAID_ONLY or self.adapter == PAID_METADATA_ONLY

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
        if self.paid:
            # The one case manual submission must not rescue. A subscription
            # wall refuses a person exactly as firmly as it refuses a
            # fetcher, and pasting the link is not a licence to take the body.
            return False
        return self.status in (AUTO_READY, MANUAL_URL_ONLY,
                               CUSTOM_ADAPTER_NEEDED, MANUAL_REVIEW_ONLY)

    def owns(self, url: str) -> bool:
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        host = host.split(":")[0]
        if not any(host == d or host.endswith("." + d) for d in self.domains):
            return False
        path = "/" + url.split("/", 3)[3].split("?", 1)[0] \
            if "/" in re.sub(r"^https?://", "", url) else "/"
        if self.adapter == SI_TEAM_PAGE:
            return bool(re.match(
                rf"^/nfl/{re.escape(self.si_team_slug)}/onsi/", path))
        if self.adapter == SI_FANTASY_PAGE:
            return path.startswith("/onsi/fantasy/")
        if self.filter_url_pattern:
            return bool(re.search(self.filter_url_pattern, path))
        return True


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
            si_team_slug=row.get("si_team_slug", "") or "",
            landing_page=row.get("landing_page", "") or "",
            fallback_page=row.get("fallback_page", "") or "",
            source_class=row.get("source_class", "") or "",
            source_ownership=row.get("source_ownership", INDEPENDENT),
            qualifying_series=row.get("qualifying_series", "") or "",
            evidence_access=row.get("evidence_access", "") or "",
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

        if s.status not in STATES:
            bad.append(f"{s.source_id}: unknown status {s.status!r}")
        if s.source_class and s.source_class not in SOURCE_CLASSES:
            bad.append(f"{s.source_id}: unknown source_class {s.source_class!r}")
        if s.source_ownership not in (TEAM_OWNED, INDEPENDENT):
            bad.append(f"{s.source_id}: unknown source_ownership "
                       f"{s.source_ownership!r}")
        # A club website is team-owned by definition, and mislabelling one as
        # independent would let it corroborate itself.
        if s.source_class == OFFICIAL_TEAM_SITE and not s.team_owned:
            bad.append(f"{s.source_id}: OFFICIAL_TEAM_SITE must be TEAM_OWNED")
        if s.adapter == NFL_TEAM_SITE and s.source_class != OFFICIAL_TEAM_SITE:
            bad.append(f"{s.source_id}: nfl_team_site adapter must be "
                       f"OFFICIAL_TEAM_SITE")
        # A team-owned source may never be polled unattended: its omissions
        # are the risk, and no author allowlist detects an omission.
        if s.team_owned and s.status == AUTO_READY:
            bad.append(f"{s.source_id}: team-owned source marked AUTO_READY")

        # A paid source may never be promoted, polled or manually rescued.
        # Three separate assertions because each has been a different bug in
        # a different pipeline.
        if s.paid:
            if s.status == AUTO_READY:
                bad.append(f"{s.source_id}: paid source marked AUTO_READY")
            if s.pollable:
                bad.append(f"{s.source_id}: paid source is pollable")
            if s.manual_ok:
                bad.append(f"{s.source_id}: paid source accepts manual URLs")
        if s.status == DISCOVERY_ONLY_PAID and s.adapter != PAID_METADATA_ONLY:
            bad.append(f"{s.source_id}: DISCOVERY_ONLY_PAID must use the "
                       f"paid_metadata_only adapter, not {s.adapter!r}")

        if s.adapter == SI_TEAM_PAGE:
            from . import si as _si
            if s.si_team_slug not in _si.TEAMS:
                bad.append(f"{s.source_id}: {s.si_team_slug!r} is not an SI "
                           f"team slug")
            elif [_si.TEAMS[s.si_team_slug]] != s.teams:
                bad.append(f"{s.source_id}: slug {s.si_team_slug!r} is "
                           f"{_si.TEAMS[s.si_team_slug]}, registered as {s.teams}")
            # The brand is not an allowlist. An SI source may only be
            # promoted when a named author on that team has been read and
            # classified FIRSTHAND_APPROVED, one team at a time.
            if s.status == AUTO_READY:
                authors = _si.load_authors()
                team = s.teams[0] if s.teams else ""
                approved = [n for n, e in (authors.get("teams", {})
                            .get(team, {}) or {}).get("authors", {}).items()
                            if e.get("classification") == _si.FIRSTHAND_APPROVED]
                if not approved:
                    bad.append(f"{s.source_id}: AUTO_READY with no "
                               f"FIRSTHAND_APPROVED author for {team}")
        if s.adapter == SI_FANTASY_PAGE:
            if s.source_class != SI_ONSI_ANALYSIS:
                bad.append(f"{s.source_id}: Fantasy On SI must be classed "
                           f"{SI_ONSI_ANALYSIS}")
            if s.status != MANUAL_REVIEW_ONLY:
                bad.append(f"{s.source_id}: Fantasy On SI must remain "
                           "MANUAL_REVIEW_ONLY")
            if s.reporting_type != "ANALYSIS":
                bad.append(f"{s.source_id}: Fantasy On SI must use ANALYSIS")
            if s.teams:
                bad.append(f"{s.source_id}: Fantasy On SI cannot claim one "
                           "team")
    return bad
