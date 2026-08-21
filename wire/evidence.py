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

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"])")


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


def group_id(source_key: str, location: str, text: str) -> str:
    """One id per evidence span, shared by every player linked to it."""
    return hashlib.sha256(
        f"{source_key}|{location}|{pl.norm(text)[:300]}".encode()).hexdigest()[:20]


def candidate_id(group: str, player_id: str, name: str) -> str:
    """Stable across runs, so re-extraction updates rather than duplicates."""
    return hashlib.sha256(
        f"{group}|{player_id or pl.norm(name)}".encode()).hexdigest()[:20]
