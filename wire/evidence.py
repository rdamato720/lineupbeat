"""Turn a stored article or transcript into reviewable evidence.

This layer produces evidence, not reporting. Its output is a queue for a
human, and the classification below is a first opinion offered to that human
rather than a verdict. That framing decides every judgement call in here: when
a rule could go either way, it goes to UNCERTAIN, because a reviewer correcting
an over-cautious label costs a moment and a reviewer failing to catch an
over-confident one costs a published mistake.

Nothing here writes to wire_publications.json, and nothing here can.

THREE THINGS IT REFUSES TO DO

It will not turn analysis into observation. "I think Gibbs looks quicker" is
an opinion about a thing the writer saw, and the opinion is what is on the
page. Hedging language wins over observation language whenever both appear.

It will not turn a writer relaying another outlet into a firsthand report.
"Schefter reports" is attributed reporting no matter who repeats it.

It will not guess who is speaking. A quotation is only a quotation when the
source names the speaker; on an auto-captioned multi-speaker video, nothing
is. Transcript order is not evidence of anything -- the reporter's question
and the player's answer are adjacent lines with no label between them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from . import players as pl

FIRSTHAND_OBSERVATION = "FIRSTHAND_OBSERVATION"
DIRECT_QUOTATION = "DIRECT_QUOTATION"
ANALYSIS_OR_OPINION = "ANALYSIS_OR_OPINION"
# Somebody else's reporting, arriving second-hand. Its own class rather than
# a flavour of analysis: a reviewer needs to see at a glance that the outlet
# on the byline is not the outlet that did the work.
RELAYED_REPORTING = "RELAYED_REPORTING"
UNCERTAIN = "UNCERTAIN"

PENDING = "PENDING"

# Hedges. Any of these and the passage is an opinion, whatever else it
# contains -- a writer who says "probably" has told you he is not reporting.
HEDGE = re.compile(
    r"\b(i think|i'd|i would|i expect|i suspect|my guess|in my (view|opinion)|"
    r"probably|likely|unlikely|could|should|might|may|seems?|appears?|"
    r"looks like|project(?:ed|ing)?|predict|expect(?:s|ed|ing)?|"
    r"if he|would be|potential(?:ly)?|hope(?:ful|s)?|believe)\b", re.I)

# The writer placing himself at the event. Only these count as observation,
# and only in a reporter's own voice.
OBSERVED = re.compile(
    r"\b(i (?:saw|watched|counted|noticed|observed)|"
    r"by my count|from what i saw|during (?:the )?(?:practice|drills?|"
    r"walkthrough|session)|at practice|on the field|in team drills|"
    r"took (?:first|second|1st|2nd)[- ]team reps|"
    r"lined up (?:at|with|as)|worked (?:with|as|at)|"
    r"was (?:limited|held out|absent|present|in uniform)|"
    r"did not (?:practice|participate)|split reps|rotated (?:in|with))\b",
    re.I)

# Somebody else did the reporting. Never firsthand, whoever repeats it.
ATTRIBUTED = re.compile(
    r"\b(according to|per |reports? that|reported (?:by|that)|"
    r"cited|citing|via |sources? (?:say|said|tell)|"
    r"(?:schefter|rapoport|pelissero|garafolo)\b|"
    r"first reported|as reported)\b", re.I)

# A quotation only counts when somebody is named as saying it.
SAID = re.compile(
    r"\b(said|says|told|explained|added|noted|acknowledged|insisted|"
    r"admitted|stated)\b", re.I)
QUOTED = re.compile(r"[\"“”]([^\"“”]{12,})[\"“”]")

# Medical claims need explicit attribution or they are UNCERTAIN, whatever
# else the sentence looks like.
MEDICAL = re.compile(
    r"\b(torn|tear|acl|mcl|pcl|achilles|fracture[ds]?|broken|sprain(?:ed)?|"
    r"strain(?:ed)?|concussion|surgery|out for the (?:season|year)|"
    r"weeks? to|re-?injur|ligament|hamstring|groin|high[- ]ankle)\b", re.I)

# Another outlet's reporting, arriving second-hand. Named outlets, and the
# shape "Surname wrote/reported", which is how a beat aggregator cites one.
# The paid outlets are listed first and deliberately: our own registry
# refuses to fetch The Athletic, and a rewrite of an Athletic story is the
# same content taking a different door.
RELAY = re.compile(
    # An outlet only counts when it is doing the reporting: possessive, or
    # next to a reporting verb, or introduced by per/via. A bare "ESPN"
    # matched site chrome in every article a publisher wrote -- trafilatura
    # keeps some navigation -- and marked twelve of twelve of one reporter's
    # articles as relayed when none of them were.
    r"(?i)\b(the athletic|espn|nfl network|nfl\.com|pro football talk|"
    r"the ringer|cbs sports|fox sports|yahoo sports|bleacher report|"
    r"pro football focus|pff|the ringer)"
    r"(?:'s|\u2019s)\s+[A-Z]"                       # ESPN's Adam Schefter
    r"|\b(?:per|via|according to)\s+"
    r"(?:the athletic|espn|nfl network|nfl\.com|pff|pro football focus|"
    r"cbs sports|fox sports|yahoo|bleacher report|multiple reports?|"
    r"a report)\b"
    r"|\b(the athletic|espn|nfl network|cbs sports|fox sports)\b"
    r"\s+(?:first )?(?:reported|reports)\b"
    # A named journalist doing the reporting. The surname has to look like a
    # surname: "And wrote" is a sentence, not a source.
    r"|\b(?!And|But|He|She|They|It|The|This|That|Who|Coach)"
    r"[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\s+"
    r"(?:first )?(?:reported|wrote|tweeted|posted)\b"
    r"|\b(?:per|according to)\s+"
    r"(?!head|assistant|offensive|defensive|the team|the coach|sources)"
    r"[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b")            # per Adam Schefter

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"])")


# --------------------------------------------------------------- relevance
# A span has to be a current football development. Most of what a team page
# publishes is not: biography, schedule notes, sponsor copy, and the
# publisher's own fantasy advice, which is the one category that must never
# become evidence no matter how confidently it is written. Each pattern
# carries the reason it fires, because "excluded" without "why" cannot be
# audited.
NOT_ACTIONABLE = [
    ("source fantasy advice", re.compile(
        r"(?i)\b(sleeper|breakout candidate|bust|draft him|worth a (?:pick|round)|"
        r"start(?:/| or )sit|start him|sit him|waiver (?:wire|add|claim)|"
        r"must[- ]draft|league winner|fantasy (?:points|value|relevance|"
        r"rankings?|projections?|advice|manager|owner|football)|"
        r"\badp\b|\bdfs\b|flex play|roster(?:ed)? in \d+%|"
        r"(?:round|rd\.?) \d+ (?:pick|value|target))\b")),
    ("betting or props", re.compile(
        r"(?i)\b(odds|betting|sportsbook|parlay|point spread|prop bets?|"
        r"over/under|moneyline|promo code|to win the|\+\d{3}\b)\b")),
    ("mock draft or power ranking", re.compile(
        r"(?i)\b(mock draft|power rankings?|draft grades?)\b")),
    ("trade proposal", re.compile(
        r"(?i)\b(trade proposal|blockbuster trade|should trade for|trade idea)\b")),
    ("simulation or hypothetical", re.compile(
        r"(?i)\b(ai (?:simulation|predicts?|model)|grok|chatgpt|"
        r"simulat(?:ed|ion|es)|hypothetical|what if the|"
        r"blockbuster (?:trade|signing)|proposed (?:trade|signing)|"
        r"roster simulation|madden sim)\b")),
    ("listicle or entertainment", re.compile(
        r"(?i)\b(\d+ (?:things|reasons|takes|players) (?:you|that|why)|"
        r"went viral|social media (?:reacts|reaction)|fans react|"
        r"savage|roasted|clapped back|hilarious)\b")),
    ("national list", re.compile(
        r"(?i)\b(top \d+ (?:players|prospects|quarterbacks)|"
        r"ranking the \w+|greatest \w+ of all time)\b")),
    ("schedule or fixture note", re.compile(
        r"(?i)\b(kickoff is (?:set|scheduled)|will be televised|"
        r"tv schedule|how to watch|game time is|tickets? (?:are )?(?:on sale|available))\b")),
    ("promotional or sponsor copy", re.compile(
        r"(?i)\b(sign up (?:for|now)|subscribe (?:to|now)|use promo|"
        r"presented by|sponsored by|shop now|our partners|"
        r"download the app|follow us on)\b")),
    ("biography or history", re.compile(
        r"(?i)\b(was born in|attended \w+ high school|drafted in the \w+ round of the "
        r"\d{4}|his college career|back in (?:19|20)\d\d|"
        r"a decade ago|hall of fame (?:induction|ceremony))\b")),
]

# What makes a span current and actionable. One of these has to be present:
# a role, a usage, a health or availability fact, or a performance event.
ACTIONABLE = re.compile(
    r"(?i)\b(practice|reps?|snaps?|targets?|carries|routes?|depth chart|"
    r"first[- ]team|second[- ]team|starter|starting|injur|limited|"
    r"did not (?:practice|participate)|returned|activated|ruled out|"
    r"questionable|doubtful|red[- ]zone|touchdown|caught|completion|"
    r"role|rotation|package|workload|snap share|camp|drill)\b")


def relevance(text: str) -> str:
    """Why this span may not become a claim, or "".

    The fantasy-advice rule runs first and is absolute. A source contributes
    its real-world reporting; its conclusions about who to draft are its own
    and are not ours to carry, however the sentence is phrased.
    """
    t = text or ""
    for reason, pat in NOT_ACTIONABLE:
        m = pat.search(t)
        if m:
            return f"{reason} ({m.group(0).strip().lower()!r})"
    if not ACTIONABLE.search(t):
        return "no current role, usage, health or performance development"
    return ""


def claim_key(player_id: str, text: str) -> str:
    """Identity of a claim: this player, this normalised passage.

    Normalised so that whitespace and punctuation differences between two
    copies of a syndicated story do not read as two separate observations.
    """
    return hashlib.sha256(
        f"{player_id}|{norm_claim(text)}".encode()).hexdigest()[:20]


def norm_claim(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split())


def overlap(a: str, b: str) -> float:
    """How much two passages share, as a fraction of the shorter one."""
    wa, wb = set(norm_claim(a).split()), set(norm_claim(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


@dataclass
class Evidence:
    evidence_group_id: str
    evidence_text: str
    evidence_class: str
    classification_confidence: float
    reasons: list[str] = field(default_factory=list)
    start_seconds: float | None = None
    end_seconds: float | None = None
    location: str = ""


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(text or "") if s.strip()]


def window(sents: list[str], i: int, before: int = 1, after: int = 1) -> str:
    """The claim plus a sentence either side.

    A name on its own is not evidence and a clause on its own is not
    understandable. The surrounding sentence is what lets a reviewer see
    whether the writer was reporting or musing.
    """
    lo, hi = max(0, i - before), min(len(sents), i + after + 1)
    return " ".join(sents[lo:hi]).strip()


def classify(text: str, *, reporter_voice: bool,
             auto_captions: bool = False,
             multi_speaker: bool = False) -> tuple[str, float, list[str]]:
    """A first opinion on what kind of claim this is.

    `reporter_voice` says whether we know an approved reporter is the one
    talking. On a multi-speaker auto-captioned video we do not, and almost
    everything collapses to UNCERTAIN as a result -- which is the correct
    answer, not a failure of the classifier.
    """
    t = text or ""
    why: list[str] = []

    if multi_speaker or (auto_captions and not reporter_voice):
        why.append("speaker cannot be established from an auto-captioned "
                   "multi-speaker source")
        return UNCERTAIN, 0.2, why

    # Relay is checked before quotation, and the order is the whole point. A
    # writer quoting another outlet's reporter -- "Tough finish for the
    # offense," Fishbain wrote -- has quotation marks and an attribution verb
    # and is not a direct quotation from anyone at the facility. Checked the
    # other way round it read as DIRECT_QUOTATION at 0.80, which is how a
    # paywalled outlet's reporting arrives second-hand wearing our highest
    # confidence score.
    relay = RELAY.search(t)
    if relay:
        why.append(f"relays another outlet or reporter "
                   f"({relay.group(0).strip().lower()!r})")
        return RELAYED_REPORTING, 0.35, why

    quoted = QUOTED.search(t)
    if quoted:
        # Words in quotation marks are only a quotation when the source says
        # whose they are. Otherwise we have a sentence containing quote marks.
        if SAID.search(t):
            why.append("quoted words with an explicit attribution verb")
            return DIRECT_QUOTATION, 0.8, why
        why.append("quoted words with no named speaker")
        return UNCERTAIN, 0.3, why

    if ATTRIBUTED.search(t):
        why.append("relays another outlet or an unnamed source")
        return ANALYSIS_OR_OPINION if HEDGE.search(t) else UNCERTAIN, 0.4, why

    if MEDICAL.search(t) and not SAID.search(t):
        # A diagnosis nobody is named as giving. The single most damaging
        # thing this pipeline could publish confidently.
        why.append("medical claim with no explicit attribution")
        return UNCERTAIN, 0.3, why

    hedged = HEDGE.search(t)
    observed = OBSERVED.search(t)
    if hedged:
        why.append(f"hedging language ({hedged.group(0).lower()!r})")
        if observed:
            why.append(f"also observation language "
                       f"({observed.group(0).lower()!r}); hedge wins")
        return ANALYSIS_OR_OPINION, 0.7, why

    if observed and reporter_voice:
        why.append(f"observation language ({observed.group(0).lower()!r}) "
                   f"in an approved reporter's voice")
        return FIRSTHAND_OBSERVATION, 0.7, why
    if observed:
        why.append("observation language but the speaker is not established")
        return UNCERTAIN, 0.3, why

    why.append("no observation, attribution or hedging markers")
    return UNCERTAIN, 0.25, why


_ALIAS_INDEX: dict = {}


def _alias_index(registry: pl.Registry, team: str) -> dict:
    """alias -> players, built once per team and reused.

    The obvious loop -- every player, every alias, for every span -- is three
    thousand players times a dozen aliases times several hundred spans, and it
    does not finish. This inverts it: the text is scanned for n-grams and each
    is looked up, so the cost is the length of the passage rather than the
    size of the league.
    """
    key = (id(registry), team)
    idx = _ALIAS_INDEX.get(key)
    if idx is not None:
        return idx
    idx = {}
    for p in registry.players:
        if team and p.team != team:
            continue
        for alias in p.aliases:
            if " " not in alias:
                continue          # never match on a surname alone
            idx.setdefault(alias, []).append(p)
    _ALIAS_INDEX[key] = idx
    return idx


def find_players(text: str, registry: pl.Registry, team: str
                 ) -> list[tuple[str, list, str]]:
    """Explicit name mentions, resolved only through the Wire registry.

    Names are matched as whole two- and three-word phrases against the
    source's own team, which is what makes the team-scoped registry the thing
    doing the safety work. Nothing is fuzzy-matched: a misheard name resolves
    to nobody and goes to a human.
    """
    idx = _alias_index(registry, team)
    words = pl.norm(text).split()
    found: dict[str, tuple[str, list, str]] = {}
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            for p in idx.get(phrase, []):
                if p.full_name in found:
                    continue
                hits, how = registry.resolve(p.full_name, p.team, p.position)
                found[p.full_name] = (p.full_name, hits, how)
    return list(found.values())


SPEAKER = re.compile(
    r"([A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){0,2})\s+"
    r"(?:said|says|told|explained|added|noted|acknowledged|insisted|"
    r"admitted|stated)", re.I)


def is_speaker(player_name: str, text: str) -> bool:
    """Is this player the one being quoted, or merely mentioned nearby?

    A span can quote one player and list another two sentences later. Filed
    per player without this check, the second one acquires a direct
    quotation he never gave: a Patriots span quoting Hassan Haskins was
    filed as a direct quotation from Jam Miller, who is named once, in a
    list of running backs.
    """
    last = pl.norm(player_name).split()[-1] if player_name else ""
    if not last:
        return False
    for m in SPEAKER.finditer(text or ""):
        if last in pl.norm(m.group(1)).split():
            return True
    return False


# Who actually did the reporting, when the rewrite says so. Stored rather
# than merely detected: a reviewer deciding whether two articles corroborate
# each other needs to see that both trace to one original.
ORIGIN = re.compile(
    r"(?i)\b(?:according to|per|via|citing|as (?:shared|posted|reported) by|"
    r"told(?: the)?)\s+"
    r"(?P<outlet>the athletic|espn|nfl network|nfl\.com|cbs sports|fox sports|"
    r"yahoo sports|bleacher report|pro football focus|pff|the boston herald|"
    r"boston herald|the ringer)"
    r"|(?P<person>(?!And|But|He|She|They|It|The|This|That|Who|Coach)"
    r"[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\s+"
    r"(?:first )?(?:reported|wrote|tweeted|posted)"
    r"|\b(?:per|according to)\s+"
    r"(?P<person2>(?!head|assistant|offensive|defensive|the)"
    r"[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,})")

OUTLET_URL = re.compile(r'href="(https?://(?!www\.si\.com)[^"]+)"')


def origin_of(text: str, html: str = "") -> dict:
    """The reporter and outlet a rewrite is standing on, where stated.

    An A to Z rewrite of a Boston Herald story is not independent
    corroboration of the Boston Herald. Recording the origin is what lets
    two candidates that trace to one original be linked rather than counted
    twice.
    """
    out = {"origin_reporter": "", "origin_outlet": "", "origin_url": ""}
    m = ORIGIN.search(text or "")
    if m:
        out["origin_outlet"] = (m.group("outlet") or "").strip()
        out["origin_reporter"] = ((m.group("person") or m.group("person2") or "")
                                  .strip())
    if html:
        link = OUTLET_URL.search(html)
        if link:
            out["origin_url"] = link.group(1)[:300]
    return out


def underlying_report_id(origin: dict, text: str) -> str:
    """One id per original report, shared by every rewrite of it."""
    key = (origin.get("origin_reporter") or origin.get("origin_outlet") or "")
    if not key:
        return ""
    return hashlib.sha256(
        f"{key.lower()}|{norm_claim(text)[:160]}".encode()).hexdigest()[:20]


def group_id(source_key: str, location: str, text: str) -> str:
    """One id per evidence span, shared by every player linked to it."""
    return hashlib.sha256(
        f"{source_key}|{location}|{pl.norm(text)[:300]}".encode()).hexdigest()[:20]


def candidate_id(group: str, player_id: str, name: str) -> str:
    """Stable across runs, so re-extraction updates rather than duplicates."""
    return hashlib.sha256(
        f"{group}|{player_id or pl.norm(name)}".encode()).hexdigest()[:20]
