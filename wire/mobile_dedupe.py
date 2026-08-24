"""Deterministic event-level deduplication for mobile Wire drafts."""

from __future__ import annotations

import re
from datetime import datetime, timezone


WINDOW_HOURS = 12
STATEFUL = {
    "FIRST_TEAM_REPS", "SECOND_TEAM_REPS", "THIRD_TEAM_REPS", "SNAP_SHARE",
    "ROUTES", "TARGETS", "CARRIES", "RED_ZONE", "DEPTH_CHART",
    "ROLE_EXPANSION", "ROLE_REDUCTION", "INJURY", "ABSENT_FROM_PRACTICE",
    "LIMITED_PARTICIPATION", "RETURN_TO_PRACTICE", "TRANSACTION",
}
STOP = set("a an and are as at be been being but by for from had has have he "
           "her him his i in into is it its of on or our said says she that the "
           "their them they this to was were will with report reported reports "
           "source player practice today monday tuesday wednesday thursday "
           "friday saturday sunday".split())
MARKERS = {
    "absent": r"\b(absent|dnp|did not practice|missed practice)\b",
    "limited": r"\b(limited|individual drills?|individual work)\b",
    "full": r"\b(full participant|full participation|fully participated)\b",
    "seven_on_seven": r"\b(7 on 7|seven on seven)\b",
    "eleven_on_eleven": r"\b(11 on 11|eleven on eleven|team periods?)\b",
    "first_team": r"\b(first team|with the ones|starting offense)\b",
    "second_team": r"\b(second team|with the twos|qb2)\b",
    "third_team": r"\b(third team|with the threes|qb3)\b",
    "red_zone": r"\b(red zone|goal line)\b",
    "traded": r"\b(trade|traded|acquired)\b",
    "signed": r"\b(sign|signed|signing)\b",
    "released": r"\b(released|waived|cut)\b",
    "activated": r"\b(activated|activation)\b",
    "ankle": r"\bankle\b", "knee": r"\bknee\b", "hamstring": r"\bhamstring\b",
    "shoulder": r"\bshoulder\b", "foot": r"\bfoot\b", "groin": r"\bgroin\b",
    "back": r"\b(back injury|back soreness|injured (his|her) back)\b",
    "concussion": r"\bconcussion\b",
}
CONFLICT_GROUPS = (
    {"absent", "limited", "full"},
    {"first_team", "second_team", "third_team"},
    {"traded", "signed", "released", "activated"},
    {"ankle", "knee", "hamstring", "shoulder", "foot", "groin", "back",
     "concussion"},
)


def parse_time(value):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def text(card: dict) -> str:
    return " ".join(str(card.get(key) or "") for key in (
        "public_summary", "public_evidence_summary", "evidence",
        "reporter_found", "commentary", "lineupbeat_impact"))


def normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def tokens(card: dict) -> set[str]:
    player_words = set(normalized(card.get("player") or card.get(
        "player_name", "")).split())
    return {word for word in normalized(text(card)).split()
            if len(word) > 2 and word not in STOP and word not in player_words}


def markers(card: dict) -> set[str]:
    value = normalized(text(card))
    return {name for name, pattern in MARKERS.items()
            if re.search(pattern, value)}


def similarity(left: dict, right: dict) -> float:
    a, b = tokens(left), tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def identity(card: dict) -> str:
    return str(card.get("player_id") or normalized(
        card.get("player") or card.get("player_name", "")))


def date(card: dict):
    return parse_time(card.get("date") or card.get("published_date") or
                      card.get("published_at"))


def marker_conflict(left: set[str], right: set[str]) -> bool:
    for group in CONFLICT_GROUPS:
        a, b = left & group, right & group
        if a and b and a.isdisjoint(b):
            return True
    return False


def duplicate(left: dict, right: dict,
              window_hours: int = WINDOW_HOURS) -> tuple[bool, dict]:
    if not identity(left) or identity(left) != identity(right):
        return False, {}
    if left.get("mechanism") != right.get("mechanism") or \
            left.get("direction") != right.get("direction"):
        return False, {}
    if str(left.get("content_type") or "REPORTING") != str(
            right.get("content_type") or "REPORTING"):
        return False, {}
    a_time, b_time = date(left), date(right)
    if not a_time or not b_time or abs((a_time - b_time).total_seconds()) > \
            window_hours * 3600:
        return False, {}
    a_markers, b_markers = markers(left), markers(right)
    if marker_conflict(a_markers, b_markers):
        return False, {}
    score = similarity(left, right)
    mechanism = str(left.get("mechanism") or "")
    threshold = 0.35 if left.get("content_type") == "FANTASY_ANALYSIS" else \
        (0.12 if mechanism in STATEFUL else 0.40)
    same = score >= threshold or (
        bool(a_markers & b_markers) and mechanism in STATEFUL)
    return same, {"similarity": round(score, 4),
                  "shared_markers": sorted(a_markers & b_markers),
                  "window_hours": window_hours}


def find_duplicate(card: dict, existing: list[dict]):
    matches = []
    for index, prior in enumerate(existing):
        same, detail = duplicate(card, prior)
        if same:
            matches.append((detail["similarity"], index, prior, detail))
    return max(matches, default=None, key=lambda item: item[0])


def quality(card: dict) -> tuple:
    strength = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(card.get("strength"), 0)
    stamp = date(card)
    return strength, len(markers(card)), len(tokens(card)), \
        stamp.timestamp() if stamp else 0


def source_ref(card: dict) -> dict:
    return {"author": card.get("author", ""), "source": card.get("source", ""),
            "url": card.get("url", ""), "date": card.get("date", ""),
            "evidence_candidate_id": card.get("evidence_candidate_id", "")}
