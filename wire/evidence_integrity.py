"""Prove that generator, reviewer and human saw identical evidence.

Evidence hashes contain evidence text only.  Request hashes are deliberately
separate: an older input hash mixed player ids into the passage hash and made
it impossible to prove what 37 model calls had actually read.

Completeness is structural, not stylistic.  Missing terminal punctuation is
valid publisher copy.  Quote windows may begin inside a longer quotation.
Only a known source-body boundary can establish that a stored span was cut.
"""

from __future__ import annotations

import hashlib
import json


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def request_sha256(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def source_boundary_complete(source_body: str | None, start: int | None,
                             end: int | None) -> tuple[bool, list[str]]:
    """Check only cuts that can be proved from source offsets.

    A span ending before an alphanumeric continuation is a mid-word cut.  A
    newline/whitespace boundary and the beginning/end of the source are valid.
    Without body+offsets, completeness is unknown rather than guessed from
    punctuation; hash equality still protects transport integrity.
    """
    if source_body is None or start is None or end is None:
        return True, []
    if start < 0 or end < start or end > len(source_body):
        return False, ["invalid source offsets"]
    reasons = []
    if start > 0 and source_body[start - 1].isalnum() and source_body[start].isalnum():
        reasons.append("span begins inside a word")
    if end < len(source_body) and end > 0:
        if source_body[end - 1].isalnum() and source_body[end].isalnum():
            reasons.append("span ends inside a word")
    return not reasons, reasons


def build_record(evidence_text: str, *, generator_evidence_sha: str = "",
                 reviewer_evidence_sha: str = "", human_evidence_sha: str = "",
                 generator_request_sha: str = "", reviewer_request_sha: str = "",
                 source_body: str | None = None, segment_start: int | None = None,
                 segment_end: int | None = None) -> dict:
    evidence_sha = sha256_text(evidence_text)
    complete, reasons = source_boundary_complete(
        source_body, segment_start, segment_end)
    four = [evidence_sha, generator_evidence_sha, reviewer_evidence_sha,
            human_evidence_sha]
    recorded = all(four)
    match = recorded and len(set(four)) == 1
    blocked = not complete or not match
    return {
        "evidence_text": evidence_text,
        "evidence_sha256": evidence_sha,
        "generator_input_evidence_sha256": generator_evidence_sha,
        "reviewer_input_evidence_sha256": reviewer_evidence_sha,
        "human_display_evidence_sha256": human_evidence_sha,
        "generator_request_sha256": generator_request_sha,
        "reviewer_request_sha256": reviewer_request_sha,
        "evidence_chars": len(evidence_text or ""),
        "segment_start": segment_start,
        "segment_end": segment_end,
        "evidence_complete": complete,
        "incompleteness_reasons": reasons,
        "hashes_match": match,
        "hashes_recorded": recorded,
        "blocks_automatic_approval": blocked,
        "status": "OK" if not blocked else "REQUIRE_HUMAN",
    }
