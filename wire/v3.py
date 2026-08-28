"""Story-first records for the review-only Wire V3 dark launch."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from . import mobile_dedupe
from .v2 import candidate_quality, parse_time


WINDOW_HOURS = 12
MATERIAL = re.compile(
    r"(?i)\b(traded?|released?|waived?|signed?|activated|placed on (?:ir|pup)|"
    r"suspend(?:ed|sion)|diagnos(?:ed|is)|surgery|torn|fractur(?:e|ed)|"
    r"ruled out|will miss|week 1|named (?:the )?starter|first[- ]team|"
    r"official depth chart|return(?:ed|ing) to practice|cleared)\b")
SIGNATURES = (
    re.compile(r"(?i)\b(charged?|charges?|arrested|misdemeanor|felony)\b"),
    re.compile(r"(?i)\b(traded?|acquired)\b"),
    re.compile(r"(?i)\b(released?|waived?|cut)\b"),
    re.compile(r"(?i)\b(signed?|agreed to)\b"),
    re.compile(r"(?i)\b(activated|off (?:pup|ir)|returned? to practice)\b"),
)
BROAD_LIST = re.compile(
    r"(?i)\b(players? not dressing|not dressing|inactive(?:s)?|"
    r"offensive starters\s*:|defensive starters\s*:|starters\s*:)\b")
PRESEASON_LINEUP = re.compile(
    r"(?i)(?:\bpreseason\b.{0,120}\bstarter|\bstarter.{0,120}\bpreseason\b|"
    r"offensive starters\s*:)")
REGULAR_SEASON = re.compile(
    r"(?i)\b(week 1|regular season|official depth chart|named (?:the )?starter|"
    r"first[- ]team promotion)\b")


def canonical_url(value: str) -> str:
    parts = urlsplit(str(value or ""))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path.rstrip("/"), "", ""))


def _candidate_tokens(row: dict) -> set[str]:
    return set(mobile_dedupe.normalized(str(row.get("evidence") or "")).split())


def same_story(left: dict, right: dict, window_hours: int = WINDOW_HOURS) -> bool:
    a_time, b_time = parse_time(left.get("published_at")), parse_time(right.get("published_at"))
    if not a_time or not b_time or abs((a_time - b_time).total_seconds()) > window_hours * 3600:
        return False
    if canonical_url(left.get("source_url")) == canonical_url(right.get("source_url")):
        return True
    # Cross-URL joining stays conservative: the reports must share a player and
    # a concrete event marker, or be near-identical rewrites.
    left_ids = {str(left.get("player_id") or "")}
    right_ids = {str(right.get("player_id") or "")}
    if not left_ids & right_ids:
        return False
    a, b = _candidate_tokens(left), _candidate_tokens(right)
    similarity = len(a & b) / len(a | b) if a and b else 0
    shared_marker = bool(mobile_dedupe.markers(left) & mobile_dedupe.markers(right) &
                         mobile_dedupe.PRECALL_EVENT_MARKERS)
    left_text = str(left.get("evidence") or "")
    right_text = str(right.get("evidence") or "")
    shared_signature = any(pattern.search(left_text) and pattern.search(right_text)
                           for pattern in SIGNATURES)
    return similarity >= 0.45 or ((shared_marker or shared_signature) and similarity >= 0.12)


def _story_id(rows: list[dict]) -> str:
    raw = "|".join(sorted(str(row.get("candidate_id") or "") for row in rows))
    return "wire-v3:" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def _report(row: dict) -> dict:
    return {
        "candidate_id": str(row.get("candidate_id") or ""),
        "source_id": str(row.get("source_id") or ""),
        "source_name": str(row.get("source_name") or ""),
        "author": str(row.get("author") or ""),
        "url": str(row.get("source_url") or ""),
        "published_at": str(row.get("published_at") or ""),
        "ownership": str(row.get("ownership") or ""),
        "origin": str(row.get("origin") or ""),
        "evidence": str(row.get("evidence") or ""),
    }


def cluster(candidates: list[dict]) -> list[dict]:
    """Group reports by underlying story before any per-player interpretation."""
    remaining = list(candidates)
    groups = []
    while remaining:
        seed = remaining.pop(0)
        group = [seed]
        # Compare to the seed/primary story, avoiding broad transitive joins.
        for row in list(remaining):
            if same_story(seed, row):
                group.append(row)
                remaining.remove(row)
        groups.append(group)
    stories = []
    for group in groups:
        ordered = sorted(group, key=candidate_quality, reverse=True)
        players = {}
        reports = []
        report_keys = set()
        for row in ordered:
            players[str(row.get("player_id") or "")] = {
                "player": str(row.get("player") or ""),
                "player_id": str(row.get("player_id") or ""),
                "team": str(row.get("team") or ""),
                "position": str(row.get("position") or ""),
            }
            report = _report(row)
            key = (canonical_url(report["url"]), report["evidence"])
            if key not in report_keys:
                reports.append(report)
                report_keys.add(key)
        stories.append({
            "story_id": _story_id(group),
            "published_at": max(str(row.get("published_at") or "") for row in group),
            "report_count": len(reports),
            "candidate_count": len(group),
            "candidate_ids": sorted({str(row.get("candidate_id") or "") for row in group}),
            "players": list(players.values()),
            "primary_candidate_id": ordered[0]["candidate_id"],
            "reports": reports,
        })
    return sorted(stories, key=lambda row: row["published_at"], reverse=True)


def complete_evidence(story: dict, max_chars: int = 18_000) -> str:
    blocks = []
    for number, report in enumerate(story.get("reports") or [], 1):
        blocks.append(
            f"REPORT {number}\nAuthor: {report['author']}\n"
            f"Outlet: {report['source_name']}\nURL: {report['url']}\n"
            f"Published: {report['published_at']}\nEvidence:\n{report['evidence']}")
    return "\n\n---\n\n".join(blocks)[:max_chars]


def is_broad_roster_list(story: dict) -> bool:
    evidence = "\n".join(row.get("evidence", "") for row in story.get("reports") or [])
    return len(story.get("players") or []) >= 5 and bool(BROAD_LIST.search(evidence))


def is_preseason_lineup(text: str) -> bool:
    return bool(PRESEASON_LINEUP.search(text or "")) and not bool(REGULAR_SEASON.search(text or ""))
