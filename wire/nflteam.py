"""One adapter, thirty-two club websites. Team-owned, and labelled as such.

Every NFL club runs the same publishing platform, so a single adapter serves
all of them: a /news/ index of article links, and article pages carrying
schema.org metadata with an exact byline and publication time. All 32 answered
200 with discoverable links.

WHY THESE SOURCES ARE KEPT APART

A club's own writer can be at every practice and count every repetition --
Jim Wyatt's "Ten Observations" carries a Nashville dateline and eleven
numbered practice notes -- and still cannot corroborate the club. The risk is
not that a team writer invents a rep; it is the story he does not file. An
omission has no marker, no hedge and no byline, so no author allowlist
detects it.

So team-owned evidence is admitted for what it can establish -- transactions,
official designations, published depth charts, direct quotations, and what a
named team-employed reporter personally saw -- and never counts toward
independent corroboration. Two articles from one club, or an article and its
matching team video, are one source family.

WHAT IT REFUSES

Club sites publish far more marketing than reporting: ticketing, broadcast
notes, community events, sponsored features, foundation 5Ks. Tennessee's news
index is alphabetical rather than chronological, and the first four links it
offers are a youth camp and a charity run. Those are excluded by pattern, and
an author is admitted only for a named recurring series where that is the
only reliably identifiable qualifying format.
"""

from __future__ import annotations

import json
import re

from .capture import _get

# team -> club domain. Verified reachable 2026-08-21.
SITES = {
    "ARI": "azcardinals.com", "ATL": "atlantafalcons.com",
    "BAL": "baltimoreravens.com", "BUF": "buffalobills.com",
    "CAR": "panthers.com", "CHI": "chicagobears.com", "CIN": "bengals.com",
    "CLE": "clevelandbrowns.com", "DAL": "dallascowboys.com",
    "DEN": "denverbroncos.com", "DET": "detroitlions.com", "GB": "packers.com",
    "HOU": "houstontexans.com", "IND": "colts.com", "JAX": "jaguars.com",
    "KC": "chiefs.com", "LAC": "chargers.com", "LAR": "therams.com",
    "LV": "raiders.com", "MIA": "miamidolphins.com", "MIN": "vikings.com",
    "NE": "patriots.com", "NO": "neworleanssaints.com", "NYG": "giants.com",
    "NYJ": "newyorkjets.com", "PHI": "philadelphiaeagles.com",
    "PIT": "steelers.com", "SEA": "seahawks.com", "SF": "49ers.com",
    "TB": "buccaneers.com", "TEN": "tennesseetitans.com",
    "WAS": "commanders.com",
}

TEAM_OWNED = "TEAM_OWNED"

NEWS_LINK = re.compile(r'href="(/news/[a-z0-9][a-z0-9\-]{10,})"')
LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
                re.S | re.I)

# Club-site content that is never reporting. Checked on the url slug and the
# headline, because a foundation 5K and a practice notebook live at the same
# kind of address.
NOT_REPORTING = [
    ("marketing or ticketing", re.compile(
        r"(?i)\b(tickets?|season ticket|single[- ]game|presented by|"
        r"sweepstakes|giveaway|merchandise|pro shop|gift|shop now|"
        r"partnership|sponsor)\b")),
    ("broadcast or schedule", re.compile(
        r"(?i)\b(how to watch|tv map|broadcast|radio network|"
        r"listen live|game time|kickoff time|schedule release)\b")),
    ("community or foundation", re.compile(
        r"(?i)\b(foundation|charity|5k|community|youth (camp|summit|football)|"
        r"toy drive|food bank|volunteer|scholarship|award (winner|finalist)s?|"
        r"mr\. football|diversity coaching|cheerleader|mascot)\b")),
    ("mailbag or speculation", re.compile(
        r"(?i)\b(mailbag|ask (the )?\w+|fan question|o-?zone)\b")),
    ("historical feature", re.compile(
        r"(?i)\b(ring of honor|hall of fame|throwback|flashback|"
        r"anniversary|all[- ]time|greatest)\b")),
    ("fantasy or betting", re.compile(
        r"(?i)\b(fantasy|sleeper|start[/ ]sit|odds|betting|parlay)\b")),
]

# A concrete football observation. Team-produced enthusiasm without one of
# these is hype, and hype is not evidence.
CONCRETE = re.compile(
    r"(?i)\b(practice|reps?|snaps?|first[- ]team|second[- ]team|"
    r"depth chart|injur|did not (practice|participate)|limited|"
    r"returned to|activated|placed on|signed|waived|released|claimed|"
    r"targets?|carries|touchdown|interception|completion|drill|"
    r"observations?|roster move|designated to return)\b")


def news_url(team: str) -> str:
    return f"https://www.{SITES[team]}/news/"


def discover(team: str, limit: int = 40, fetch=_get) -> tuple[list[dict], dict]:
    """Article links from a club's news index. No article fetching here."""
    if team not in SITES:
        raise ValueError(f"{team!r} has no registered club site")
    url = news_url(team)
    meta = {"url": url, "reachable": False, "http": None, "links": 0}
    try:
        status, body, _ = fetch(url, timeout=40)
    except Exception as e:
        meta["http"] = type(e).__name__
        return [], meta
    meta["http"] = status
    if not (isinstance(status, int) and status == 200 and body):
        return [], meta
    meta["reachable"] = True
    paths = list(dict.fromkeys(NEWS_LINK.findall(body)))
    meta["links"] = len(paths)
    return [{"url": f"https://www.{SITES[team]}{p}",
             "discovery_url": url,
             "source_ownership": TEAM_OWNED} for p in paths[:limit]], meta


def parse_article(html: str) -> dict:
    """Byline, headline and time from the article's own structured data.

    The club sites carry schema.org on the article page but not on the index,
    and trafilatura reads the byline off some of them as "Copied" -- the text
    of a share button. The embedded block says "Jim Wyatt".
    """
    out = {"author": "", "headline": "", "published_at": ""}
    for blob in LD.findall(html or ""):
        try:
            data = json.loads(blob)
        except Exception:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            if not node.get("headline") and not node.get("author"):
                continue
            auth = node.get("author")
            if isinstance(auth, list):
                auth = auth[0] if auth else {}
            name = ((auth or {}).get("name", "") if isinstance(auth, dict)
                    else str(auth or ""))
            if not name and node.get("creator"):
                c = node["creator"]
                name = c[0] if isinstance(c, list) and c else str(c)
            out["author"] = out["author"] or (name or "").strip()
            out["headline"] = out["headline"] or (node.get("headline") or "").strip()
            out["published_at"] = (out["published_at"]
                                   or (node.get("datePublished") or "").strip())
    return out


def content_exclusion(headline: str, url: str, text: str = "") -> str:
    """Why a club article may not become evidence, or ""."""
    hay = f"{headline} {url}"
    for reason, pat in NOT_REPORTING:
        m = pat.search(hay)
        if m:
            return f"{reason} ({m.group(0).strip().lower()!r})"
    if text and not CONCRETE.search(text):
        return "team-produced copy with no concrete football observation"
    return ""


def series_ok(headline: str, pattern: str) -> bool:
    """Does this headline belong to the author's approved series?

    An approved club writer is approved for a named recurring series and not
    for everything he files. Wyatt's observation pieces qualify; his mailbags
    and previews do not, and the difference has to be visible in the headline
    or the restriction cannot be enforced in code.
    """
    if not pattern:
        return True
    return bool(re.search(pattern, headline or ""))
