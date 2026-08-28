"""Event-centric records for the dark-launch Wire V2.

V2 receives already captured, identity-resolved article and X candidates. It
does no editorial filtering. Its only job is to merge reports about the same
player event before a model call and retain every source for human review.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from . import mobile_dedupe


WINDOW_HOURS = 12
RETURN = re.compile(
    r"(?i)\b(is back|returned|returning|back at practice|cleared|"
    r"activat(?:ed|ing).{0,40}(?:pup|nfi)|coming off (?:the )?(?:pup|nfi)|"
    r"removed .{0,40} from (?:the )?(?:pup|nfi)|"
    r"taking .{0,40} off (?:the )?(?:pup|nfi))\b")
CURRENT_INJURY = re.compile(
    r"(?i)\b(left (?:practice|the field)|suffered|reaggravated|re-?injured|"
    r"working through .{0,30}(?:issue|injury)|diagnosed|hurt|"
    r"new .{0,20}(?:injury|issue))\b")
EVENT_SIGNATURES = (
    ("LEGAL_CHARGE", re.compile(
        r"(?i)\b(?:charged|charges?|misdemeanor|felony|arrested)\b")),
    ("WAIVER_RELEASE", re.compile(
        r"(?i)\b(?:waived|waiving|released|releasing|cut)\b")),
    ("TRADE", re.compile(
        r"(?i)\b(?:traded|trade(?:s|d)?|acquired in exchange)\b")),
    ("SIGNING", re.compile(
        r"(?i)\b(?:signed|signing|agreed to (?:a|an)|one-year deal)\b")),
)


def parse_time(value: str):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def phase(candidate: dict) -> str:
    evidence = str(candidate.get("evidence") or "")
    if RETURN.search(evidence):
        return "RETURN"
    if CURRENT_INJURY.search(evidence):
        return "CURRENT_INJURY"
    return ""


def event_signatures(candidate: dict) -> set[str]:
    """Return narrow action anchors that safely join rewrites of one event."""
    evidence = str(candidate.get("evidence") or "")
    return {name for name, pattern in EVENT_SIGNATURES if pattern.search(evidence)}


def same_event(left: dict, right: dict,
               window_hours: int = WINDOW_HOURS) -> tuple[bool, dict]:
    """Conservatively decide whether two raw reports describe one event."""
    if mobile_dedupe.identity(left) != mobile_dedupe.identity(right):
        return False, {}
    a_time, b_time = mobile_dedupe.date(left), mobile_dedupe.date(right)
    if not a_time or not b_time or abs((a_time - b_time).total_seconds()) > \
            window_hours * 3600:
        return False, {}
    a_phase, b_phase = phase(left), phase(right)
    if a_phase and b_phase and a_phase != b_phase:
        return False, {"reason": "event_phase_conflict"}
    shared_signatures = event_signatures(left) & event_signatures(right)
    signature_similarity = mobile_dedupe.similarity(left, right)
    if shared_signatures and signature_similarity >= 0.12:
        return True, {
            "reason": "shared_event_signature",
            "signatures": sorted(shared_signatures),
            "similarity": round(signature_similarity, 4),
            "window_hours": window_hours,
        }
    same, detail = mobile_dedupe.precall_duplicate(
        left, right, window_hours=window_hours)
    if same:
        return True, detail
    score = mobile_dedupe.similarity(left, right)
    return score >= 0.32, {
        "similarity": round(score, 4), "window_hours": window_hours,
    }


def candidate_quality(candidate: dict) -> tuple:
    ownership = 1 if candidate.get("ownership") in {"OFFICIAL", "TEAM_OWNED"} else 0
    event_markers = mobile_dedupe.markers(candidate) & \
        mobile_dedupe.PRECALL_EVENT_MARKERS
    evidence = str(candidate.get("evidence") or "")
    stamp = parse_time(candidate.get("published_at"))
    return (ownership, len(event_markers), len(mobile_dedupe.tokens(candidate)),
            len(evidence), stamp.timestamp() if stamp else 0)


def _event_id(candidates: list[dict]) -> str:
    ids = sorted(str(row.get("candidate_id") or "") for row in candidates)
    raw = "|".join(ids).encode()
    return "wire-v2:" + hashlib.sha256(raw).hexdigest()[:20]


def _source(candidate: dict) -> dict:
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "source_id": str(candidate.get("source_id") or ""),
        "source_name": str(candidate.get("source_name") or ""),
        "author": str(candidate.get("author") or ""),
        "url": str(candidate.get("source_url") or ""),
        "published_at": str(candidate.get("published_at") or ""),
        "ownership": str(candidate.get("ownership") or ""),
        "origin": str(candidate.get("origin") or ""),
        "evidence": str(candidate.get("evidence") or ""),
    }


def cluster(candidates: list[dict]) -> list[dict]:
    """Merge transitively connected raw reports into player-event records."""
    remaining = list(candidates)
    groups: list[list[dict]] = []
    while remaining:
        group = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for candidate in list(remaining):
                candidate_phase = phase(candidate)
                group_phases = {phase(prior) for prior in group if phase(prior)}
                if (candidate_phase and group_phases and
                        candidate_phase not in group_phases):
                    continue
                if any(same_event(candidate, prior)[0] for prior in group):
                    group.append(candidate)
                    remaining.remove(candidate)
                    changed = True
        groups.append(group)

    events = []
    for group in groups:
        ordered = sorted(group, key=candidate_quality, reverse=True)
        primary = ordered[0]
        sources = [_source(row) for row in ordered]
        events.append({
            "event_id": _event_id(group),
            "player": primary["player"],
            "player_id": primary["player_id"],
            "team": primary["team"],
            "position": primary["position"],
            "primary_candidate_id": primary["candidate_id"],
            "published_at": max(str(row.get("published_at") or "")
                                for row in group),
            "source_count": len(sources),
            "sources": sources,
        })
    return sorted(events, key=lambda row: row["published_at"], reverse=True)


def complete_evidence(event: dict, max_chars: int = 18_000) -> str:
    """Render every source distinctly; never blend evidence into prose."""
    blocks = []
    for number, source in enumerate(event.get("sources") or [], 1):
        blocks.append(
            f"SOURCE {number}\n"
            f"Author: {source['author']}\n"
            f"Outlet: {source['source_name']}\n"
            f"URL: {source['url']}\n"
            f"Published: {source['published_at']}\n"
            f"Evidence:\n{source['evidence']}"
        )
    return "\n\n---\n\n".join(blocks)[:max_chars]
