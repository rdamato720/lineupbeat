"""Deterministic, facts-only event extraction for the Lineup Beat dark launch.

This module never interprets an article as a whole. It selects a single source
sentence that names one authoritative player and contains one explicit event.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from . import mobile_dedupe


WINDOW_HOURS = 24
EVENTS = (
    ("TRADE", re.compile(r"(?i)\b(traded?|acquired in exchange|agreed to trade)\b")),
    ("RELEASE", re.compile(r"(?i)\b(released?|waived?|cut|placed on waivers)\b")),
    ("SIGNING", re.compile(r"(?i)\b(signed?|signing|agreed to (?:a|an) .{0,30}(?:deal|contract))\b")),
    ("ACTIVATION", re.compile(r"(?i)\b(activated|removed from .{0,20}(?:pup|ir)|"
                               r"off (?:the )?(?:pup|ir))\b")),
    ("SUSPENSION", re.compile(r"(?i)\b(suspended?|suspension|discipline)\b")),
    ("LEGAL", re.compile(r"(?i)\b(charged?|arrested|misdemeanor|felony)\b")),
    ("STATUS", re.compile(r"(?i)\b(ruled out|questionable|doubtful|will miss|"
                           r"expected to miss|on track for week 1|cleared|"
                           r"returned? to practice|did not practice|dnp|"
                           r"limited participant|full participant|walking boot|"
                           r"concussion|diagnosed|underwent surgery|injured)\b")),
    ("ROLE", re.compile(r"(?i)\b(named (?:the )?starter|will start|starting job|"
                         r"quarterback competition|qb competition|first[- ]team|"
                         r"larger role|top backup|wr1|rb1|depth chart|"
                         r"inactive this season|game[- ]day role)\b")),
)
HISTORICAL = re.compile(
    r"(?i)\b(earlier in (?:the )?offseason|last season|last year|in 20\d\d|"
    r"previously|had been|months ago)\b")
PRESEASON_ONLY = re.compile(
    r"(?i)\b(preseason finale|preseason game|preseason week|didn['’]t play in "
    r"the preseason|missed the preseason)\b")
REGULAR_CONTEXT = re.compile(
    r"(?i)\b(week 1|regular season|season opener|53-man|active roster|"
    r"named (?:the )?starter|will start|injured reserve|\bir\b|\bpup\b)\b")
URL = re.compile(r"https?://\S+")
HASHTAG = re.compile(r"(?<!\w)#[A-Za-z0-9_]+\s*")


def parse_time(value):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def norm(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def sentence_spans(text: str) -> list[str]:
    """Keep source wording intact while separating lines and sentences."""
    chunks = []
    for block in re.split(r"[\r\n]+", str(text or "")):
        block = block.strip(" \t•-*—")
        if not block:
            continue
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z#@])", block)
        chunks.extend(part.strip() for part in parts if part.strip())
    return chunks


def player_is_named(sentence: str, player: str) -> bool:
    words = norm(player).split()
    value = f" {norm(sentence)} "
    if not words:
        return False
    full = " ".join(words)
    if f" {full} " in value:
        return True
    # A surname is still an explicit subject; pronouns and "both players" are not.
    surname = words[-1]
    return len(surname) >= 4 and f" {surname} " in value


def source_allowed(candidate: dict) -> tuple[bool, str]:
    if candidate.get("origin") == "X":
        return True, "trusted_x"
    if candidate.get("ownership") in {"OFFICIAL", "TEAM_OWNED"}:
        return True, "official_team"
    return False, "independent_article_is_discovery_only"


def classify(sentence: str) -> str:
    for event_type, pattern in EVENTS:
        if pattern.search(sentence):
            return event_type
    return ""


def clean_bullet(sentence: str) -> str:
    value = URL.sub("", sentence)
    value = HASHTAG.sub("", value)
    value = re.sub(r"(?i)^trade\s*:\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -—")
    if value and value[-1] not in ".!?":
        value += "."
    return value


def extract(candidate: dict) -> tuple[dict | None, str]:
    allowed, reason = source_allowed(candidate)
    if not allowed:
        return None, reason
    possibilities = []
    for sentence in sentence_spans(candidate.get("evidence") or ""):
        if not player_is_named(sentence, candidate.get("player") or ""):
            continue
        event_type = classify(sentence)
        if not event_type:
            continue
        if HISTORICAL.search(sentence):
            continue
        if PRESEASON_ONLY.search(sentence) and not REGULAR_CONTEXT.search(sentence):
            continue
        bullet = clean_bullet(sentence)
        if not 12 <= len(bullet) <= 280:
            continue
        possibilities.append((event_type, sentence, bullet))
    if not possibilities:
        return None, "no_current_named_fact_sentence"
    event_type, evidence, bullet = possibilities[0]
    event_id = "wire-fact:" + hashlib.sha256(
        f"{candidate.get('player_id')}|{event_type}|{norm(evidence)}".encode()).hexdigest()[:20]
    return {
        "event_id": event_id, "event_type": event_type,
        "player": candidate["player"], "player_id": candidate["player_id"],
        "team": candidate["team"], "position": candidate["position"],
        "bullet": bullet, "exact_evidence": evidence,
        "candidate_id": candidate["candidate_id"],
        "source_name": candidate["source_name"], "author": candidate["author"],
        "source_url": candidate["source_url"],
        "published_at": candidate["published_at"], "source_reason": reason,
        "corroborating_reports": [], "published": False,
    }, "accepted"


def subtype(fact: dict) -> tuple[str, ...]:
    markers = mobile_dedupe.markers({"evidence": fact.get("exact_evidence", "")})
    if fact.get("event_type") in {"TRADE", "RELEASE", "SIGNING", "ACTIVATION"}:
        return (fact.get("event_type", ""),)
    areas = tuple(sorted(markers & {"ankle", "knee", "hamstring", "shoulder",
                                    "foot", "groin", "back", "concussion"}))
    return (fact.get("event_type", ""), *areas)


def same_event(left: dict, right: dict, window_hours: int = WINDOW_HOURS) -> bool:
    if left.get("player_id") != right.get("player_id") or subtype(left) != subtype(right):
        return False
    a, b = parse_time(left.get("published_at")), parse_time(right.get("published_at"))
    return bool(a and b and abs((a - b).total_seconds()) <= window_hours * 3600)


def quality(fact: dict) -> tuple:
    official = 1 if fact.get("source_reason") == "official_team" else 0
    detail = len(set(norm(fact.get("exact_evidence", "")).split()))
    stamp = parse_time(fact.get("published_at"))
    return (detail, official, stamp.timestamp() if stamp else 0)


def deduplicate(facts: list[dict]) -> tuple[list[dict], list[dict]]:
    kept, duplicates = [], []
    for fact in sorted(facts, key=quality, reverse=True):
        prior = next((row for row in kept if same_event(fact, row)), None)
        if prior is None:
            kept.append(fact)
            continue
        prior["corroborating_reports"].append({
            "candidate_id": fact["candidate_id"], "author": fact["author"],
            "source_name": fact["source_name"], "source_url": fact["source_url"],
            "exact_evidence": fact["exact_evidence"],
        })
        duplicates.append({"candidate_id": fact["candidate_id"],
                           "primary_event_id": prior["event_id"],
                           "player": fact["player"]})
    return sorted(kept, key=lambda row: row["published_at"], reverse=True), duplicates
