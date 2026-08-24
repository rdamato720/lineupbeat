"""One adapter, thirty-two team pages: si.com/nfl/<slug>.

SI has no usable per-team RSS, but every team landing page embeds its
articles as schema.org NewsArticle blocks -- canonical url, headline, author
and publication time, already structured. That is the discovery path. It is
better than scraping anchors and it is better than the page's own metadata:
trafilatura reads the byline off these articles as "Ralph Ventre; Un..." and
the embedded block says "Ralph Ventre".

WHY A TEAM PAGE IS NOT A TEAM

A landing page is a feed, not a claim of authorship or subject. Across the
four pilot teams, 144 unique articles appeared and:

    30  had no team segment in the url at all -- national SI stories
        syndicated onto every team page
     4  carried another team's segment
    15  appeared on more than one team page
     8  authors appeared on all four pages

Albert Breer's Eagles training-camp notebook sits on the Bills page. It is
firsthand reporting, by a credentialed reporter, about the wrong team. No
title filter catches that and no author allowlist does either -- the article
is fine, the association is not. So team association is decided by the
canonical url and nothing else, and an article that does not carry the
registered team's segment is refused with a reason rather than dropped.

WHAT THIS MODULE WILL NOT DO

It will not treat the SI brand as approval. Authors are classified one at a
time, per team, by name, in sources/wire_si_authors.json, and an author who
is not in that file cannot reach AUTO_READY no matter what he writes.

It will not classify a whole article as firsthand. An article may hold a
practice observation and a paragraph of speculation; the article-level
checks here decide only whether the article is eligible to be *read*. Every
individual span still faces the claim classifier in evidence.py.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .capture import _get

ROOT = Path(__file__).resolve().parent.parent
AUTHORS = ROOT / "sources" / "wire_si_authors.json"

BASE = "https://www.si.com/nfl"

# slug -> team code. The slug is SI's, the code is the Wire's.
TEAMS = {
    "cardinals": "ARI", "falcons": "ATL", "ravens": "BAL", "bills": "BUF",
    "panthers": "CAR", "bears": "CHI", "bengals": "CIN", "browns": "CLE",
    "cowboys": "DAL", "broncos": "DEN", "lions": "DET", "packers": "GB",
    "texans": "HOU", "colts": "IND", "jaguars": "JAX", "chiefs": "KC",
    "chargers": "LAC", "rams": "LAR", "raiders": "LV", "dolphins": "MIA",
    "vikings": "MIN", "patriots": "NE", "saints": "NO", "giants": "NYG",
    "jets": "NYJ", "eagles": "PHI", "steelers": "PIT", "seahawks": "SEA",
    "49ers": "SF", "buccaneers": "TB", "titans": "TEN", "commanders": "WAS",
}
CODE_TO_SLUG = {v: k for k, v in TEAMS.items()}

# ---------------------------------------------------------------- authors
FIRSTHAND_APPROVED = "FIRSTHAND_APPROVED"
REPORTING = "REPORTING"
ANALYSIS_ONLY = "ANALYSIS_ONLY"
AGGREGATION = "AGGREGATION"
UNKNOWN = "UNKNOWN"

AUTHOR_CLASSES = {FIRSTHAND_APPROVED, REPORTING, ANALYSIS_ONLY,
                  AGGREGATION, UNKNOWN}

# Only this one may ever be promoted, and only by an explicit registry edit.
PROMOTABLE = {FIRSTHAND_APPROVED}

# ------------------------------------------------------------- exclusions
# No topic label blocks an On SI item before named-human review. URL sections
# that are purely promotional remain excluded below.
EXCLUSIONS = []

# Sections in the url that are never reporting.
EXCLUDED_SECTIONS = {"video", "sportsbook-promos", "promos"}

NO_AUTHOR = re.compile(r"^\s*(si (video )?staff|staff report|admin|"
                       r"the editors?|onsi staff)?\s*$", re.I)


@dataclass
class SIArticle:
    canonical_url: str
    headline: str = ""
    author: str = ""
    published_at: str = ""
    description: str = ""
    team: str = ""              # the team code we accepted it for
    url_slug: str = ""          # the slug found in the url, if any
    section: str = ""
    discovery_url: str = ""     # the landing page it was found on
    discovery_route: str = ""   # ONSI or TEAM_PAGE_FALLBACK
    author_class: str = UNKNOWN
    eligible: bool = False
    exclusion_reason: str = ""
    seen_on: list = field(default_factory=list)


# ---------------------------------------------------------------- parsing
LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
                re.S | re.I)
URL_TEAM = re.compile(r"^https?://(?:www\.)?si\.com/nfl/([a-z0-9-]+)(?:/|$)")

# The On SI section of a team's own channel. This is the identity that
# matters: /nfl/bills/onsi/<slug> is a Bills On SI article, and nothing else
# on the broader team page is, whatever it is filed next to.
ONSI_PATH = re.compile(r"^https?://(?:www\.)?si\.com/nfl/([a-z0-9-]+)/onsi/")
FANTASY_PATH = re.compile(r"^https?://(?:www\.)?si\.com/onsi/fantasy/")


def onsi_team(url: str) -> str | None:
    """The team whose On SI section this canonical url belongs to."""
    m = ONSI_PATH.match((url or "").strip())
    if not m:
        return None
    slug = m.group(1)
    return slug if slug in TEAMS else None


def team_in_url(url: str) -> str | None:
    """The team slug a canonical url claims, or None for a national story.

    None is a real answer and the common one: thirty of a hundred and
    forty-four pilot articles were /nfl/<headline> with no team at all.
    """
    m = URL_TEAM.match((url or "").strip())
    if not m:
        return None
    slug = m.group(1)
    return slug if slug in TEAMS else None


def section_of(url: str) -> str:
    """The path segment after the team, e.g. /nfl/bills/onsi/... -> onsi."""
    parts = [p for p in (url or "").split("si.com/")[-1].split("/") if p]
    if len(parts) >= 2 and parts[0] == "nfl":
        if parts[1] in TEAMS:
            return parts[2] if len(parts) > 2 else ""
        return parts[1]
    if len(parts) >= 2 and parts[:2] == ["onsi", "fantasy"]:
        return parts[2] if len(parts) > 2 else ""
    return ""


def parse_landing(html: str) -> list[dict]:
    """Every NewsArticle block embedded in a landing page.

    Team pages expose top-level NewsArticle objects. Fantasy On SI wraps the
    same objects in an ItemList, so both shapes are traversed explicitly.
    """
    out, seen = [], set()

    def news_nodes(value):
        if isinstance(value, list):
            for child in value:
                yield from news_nodes(child)
        elif isinstance(value, dict):
            if value.get("@type") == "NewsArticle":
                yield value
            for key in ("itemListElement", "@graph"):
                if key in value:
                    yield from news_nodes(value[key])

    for blob in LD.findall(html or ""):
        try:
            data = json.loads(blob)
        except Exception:
            continue
        for node in news_nodes(data):
            url = (node.get("@id") or node.get("url") or "").split("?")[0]
            if not url or url in seen:
                continue
            seen.add(url)
            auth = node.get("author")
            if isinstance(auth, list):
                auth = auth[0] if auth else {}
            name = (auth or {}).get("name", "") if isinstance(auth, dict) else str(auth or "")
            out.append({
                "canonical_url": url,
                "headline": node.get("headline", "") or "",
                "author": (name or "").strip(),
                "published_at": node.get("datePublished", "") or "",
                "description": node.get("description", "") or "",
            })
    return out


# ------------------------------------------------------------ classifying
def load_authors(path: Path | None = None) -> dict:
    p = Path(path or AUTHORS)
    if not p.exists():
        return {"teams": {}, "national": {}}
    return json.loads(p.read_text())


def classify_author(name: str, team: str, authors: dict) -> str:
    """Exact name, scoped to the team. No inference, ever.

    A name absent from the file is UNKNOWN, which is not a failure state --
    it is the correct description of somebody nobody has looked at yet.
    """
    key = (name or "").strip()
    if not key:
        return UNKNOWN
    national = authors.get("national", {})
    if key in national:
        return national[key].get("classification", UNKNOWN)
    per_team = (authors.get("teams", {}).get(team, {}) or {}).get("authors", {})
    entry = per_team.get(key)
    if not entry:
        return UNKNOWN
    return entry.get("classification", UNKNOWN)


def content_exclusion(headline: str, url: str, text: str = "") -> str:
    """Why this article may not produce an automatic claim, or "".

    Title, section and body, because a title filter alone is not enough: a
    betting article can be titled like a news story and a fantasy column can
    be headlined with a player's name. The body is checked with a threshold
    rather than a single hit, so a reporter mentioning the word "fantasy"
    once in a practice notebook does not lose the article.
    """
    sec = section_of(url)
    if sec in EXCLUDED_SECTIONS:
        return f"section '{sec}' is never reporting"
    hay = f"{headline} {url}"
    for reason, pat in EXCLUSIONS:
        if pat.search(hay):
            return f"{reason} (from headline or url)"
    if text:
        for reason, pat in EXCLUSIONS:
            hits = len(pat.findall(text))
            if hits >= 3:
                return f"{reason} ({hits} mentions in the body)"
    return ""


def evaluate(raw: dict, team: str, authors: dict,
             discovery_url: str = "") -> SIArticle:
    """One discovered item, judged against one registered team.

    Order matters. Team association is checked before anything else, because
    a firsthand notebook about another team is still the wrong article and
    approving its author would let it through.
    """
    art = SIArticle(
        canonical_url=raw.get("canonical_url", ""),
        headline=raw.get("headline", ""),
        author=(raw.get("author") or "").strip(),
        published_at=raw.get("published_at", ""),
        description=raw.get("description", ""),
        team=team,
        discovery_url=discovery_url or raw.get("discovery_url", ""),
        discovery_route=raw.get("discovery_route", ""),
    )
    art.url_slug = team_in_url(art.canonical_url) or ""
    art.section = section_of(art.canonical_url)

    if not art.url_slug:
        art.exclusion_reason = ("national SI story syndicated to a team page "
                                "(no team segment in the canonical url)")
        return art
    if TEAMS[art.url_slug] != team:
        art.exclusion_reason = (
            f"canonical url is a {TEAMS[art.url_slug]} article, "
            f"discovered on the {team} page")
        return art

    # The canonical url must be in this team's On SI section. Where the
    # article was found does not soften this: a "More {Team}" tile on an On
    # SI page, or anything the fallback page surfaces, still has to be an
    # /onsi/ article for this team or it is not one.
    if onsi_team(art.canonical_url) != art.url_slug:
        art.exclusion_reason = (
            f"canonical url is not in the {team} On SI section "
            f"(section {art.section!r})")
        return art

    if NO_AUTHOR.match(art.author or ""):
        art.exclusion_reason = "no identifiable author"
        return art

    art.author_class = classify_author(art.author, team, authors)
    why = content_exclusion(art.headline, art.canonical_url)
    if why:
        art.exclusion_reason = why
        return art

    art.eligible = True
    return art


def evaluate_fantasy(raw: dict, discovery_url: str = "") -> SIArticle:
    """A Fantasy On SI item eligible for manual editorial review.

    Eligibility means only that the complete article may be read. The byline
    receives no firsthand authority, and every player claim remains labelled
    analysis, relay or uncertainty downstream.
    """
    art = SIArticle(
        canonical_url=raw.get("canonical_url", ""),
        headline=raw.get("headline", ""),
        author=(raw.get("author") or "").strip(),
        published_at=raw.get("published_at", ""),
        description=raw.get("description", ""),
        discovery_url=discovery_url or raw.get("discovery_url", ""),
        discovery_route="FANTASY_ONSI",
        author_class=ANALYSIS_ONLY,
        section=section_of(raw.get("canonical_url", "")),
    )
    if not FANTASY_PATH.match(art.canonical_url):
        art.exclusion_reason = "canonical url is not in Fantasy On SI"
        return art
    if NO_AUTHOR.match(art.author or ""):
        art.exclusion_reason = "no identifiable author"
        return art
    why = content_exclusion(art.headline, art.canonical_url)
    if why:
        art.exclusion_reason = why
        return art
    art.eligible = True
    return art


# ---------------------------------------------------------------- fetching
def landing_url(slug: str, page: int = 1, onsi: bool = True) -> str:
    """The On SI section by default; the broader team page as fallback.

    The broad page is a mixed feed -- national SI, syndication, fantasy,
    betting, video -- and the On SI section is the team's own channel. On the
    four pilot teams the section returned 44, 62, 67 and 50 articles against
    34 from the broad page, and every one of Buffalo's was a Bills On SI
    article where the broad page managed 25 of 34. San Francisco went from
    14 usable articles to 42.
    """
    base = f"{BASE}/{slug}" + ("/onsi" if onsi else "")
    return base + ("" if page <= 1 else f"?page={page}")


def discover_team(slug: str, pages: int = 2, fetch=_get,
                  onsi: bool = True, fallback: bool = True
                  ) -> tuple[list[dict], dict]:
    """Raw items from a team's On SI section, deduplicated by canonical url.

    Pagination overlaps heavily -- page two of the Bills repeats twenty-nine
    of thirty-four -- so pages are merged into one canonical-url-keyed set
    and the count that matters is the union, not the sum.

    The broad team page is consulted only when the On SI section returns
    nothing, and it buys no leniency: whatever it surfaces still has to pass
    the same /onsi/ canonical test in evaluate().
    """
    if slug not in TEAMS:
        raise ValueError(f"{slug!r} is not an SI NFL team slug")
    items: dict[str, dict] = {}
    meta = {"pages_fetched": 0, "pagination_works": False, "http": [],
            "reachable": False, "used_fallback": False,
            "primary_url": landing_url(slug, 1, onsi=True),
            "fallback_url": landing_url(slug, 1, onsi=False)}

    def sweep(use_onsi: bool) -> int:
        first: set = set()
        got = 0
        for page in range(1, max(1, pages) + 1):
            url = landing_url(slug, page, onsi=use_onsi)
            try:
                status, body, _ = fetch(url, timeout=45)
            except Exception as e:
                meta["http"].append(f"page {page}: {type(e).__name__}")
                break
            meta["http"].append(status)
            if not (isinstance(status, int) and status == 200 and body):
                break
            meta["reachable"] = True
            meta["pages_fetched"] += 1
            found = parse_landing(body)
            got += len(found)
            if page == 1:
                first = {f["canonical_url"] for f in found}
            elif {f["canonical_url"] for f in found} - first:
                meta["pagination_works"] = True
            for f in found:
                f.setdefault("discovery_url", url)
                f.setdefault("discovery_route",
                             "ONSI" if use_onsi else "TEAM_PAGE_FALLBACK")
                items.setdefault(f["canonical_url"], f)
        return got

    if onsi:
        sweep(True)
    if not items and fallback:
        meta["used_fallback"] = True
        sweep(False)
    return list(items.values()), meta


def discover_fantasy(pages: int = 2, fetch=_get) -> tuple[list[dict], dict]:
    """Discover the national Fantasy On SI section for manual review."""
    items: dict[str, dict] = {}
    meta = {"pages_fetched": 0, "http": [], "reachable": False,
            "primary_url": "https://www.si.com/onsi/fantasy"}
    for page in range(1, max(1, pages) + 1):
        url = meta["primary_url"] + ("" if page == 1 else f"?page={page}")
        try:
            status, body, _ = fetch(url, timeout=45)
        except Exception as exc:
            meta["http"].append(f"page {page}: {type(exc).__name__}")
            break
        meta["http"].append(status)
        if not (isinstance(status, int) and status == 200 and body):
            break
        meta["reachable"] = True
        meta["pages_fetched"] += 1
        for row in parse_landing(body):
            row.setdefault("discovery_url", url)
            items.setdefault(row["canonical_url"], row)
    return list(items.values()), meta


def confirms_team(html: str, slug: str) -> bool:
    """Does this page actually represent the team we asked for?

    SI answers 200 for a good deal, so the page is required to name itself:
    a SportsTeam block, or a breadcrumb, carrying the slug we requested.
    """
    for blob in LD.findall(html or ""):
        try:
            data = json.loads(blob)
        except Exception:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            if node.get("@type") == "SportsTeam":
                ident = json.dumps(node).lower()
                if f"/nfl/{slug}" in ident or slug.rstrip("s") in ident:
                    return True
            if node.get("@type") == "BreadcrumbList":
                if f"/nfl/{slug}" in json.dumps(node).lower():
                    return True
    return False
