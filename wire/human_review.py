"""Validate durable, named-human review receipts for Wire dark launches.

The receipt is deliberately separate from model output.  A model verdict can
describe a suppression, but only this ledger records that a named human saw
and accepted the reviewed batch.  Receipts are append-only audit records; they
never authorize publication.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path


LEDGER = Path("data/wire_human_reviews.json")
PAID = Path("data/wire_paid_candidates.json")
SCHEMA_VERSION = "wire-human-review-ledger-v1"
RECEIPT_ACTION = "APPROVE_SUPPRESSIONS"
OUTCOME_ACTION = "SUPPRESS"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_RESERVED_ACTORS = {
    "ai", "assistant", "automation", "model", "openai", "reviewer",
    "system", "unknown",
}
_TOP_KEYS = {"schema_version", "active_receipt_id", "count", "receipts"}
_RECEIPT_KEYS = {
    "receipt_id", "approved_by", "approved_by_handle", "approved_on",
    "approval_statement", "action", "review_package_sha256",
    "review_package_schema_version", "review_package_run_status",
    "candidate_count", "outcomes", "generator_calls", "reviewer_calls",
    "generator_cost_usd", "reviewer_cost_usd", "total_cost_usd",
    "publication_count_before", "publication_count_after",
    "publications_applied",
}
_OUTCOME_KEYS = {
    "candidate_id", "player_id", "player_name", "team", "position",
    "source_id", "decision", "generator_decision",
    "independent_verdict", "evidence_integrity_status", "evidence_sha256",
    "generator_request_sha256", "reviewer_request_sha256",
}


def _load(path: Path, label: str, errors: list[str]) -> dict:
    if not path.exists():
        errors.append(f"{label} is missing: {path}")
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is not valid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label} must be a JSON object")
        return {}
    return payload


def validate_ledger(path: Path = LEDGER, paid_path: Path = PAID) \
        -> tuple[dict | None, list[str]]:
    """Return the active receipt and every validation error.

    The paid-candidate ledger is part of the check: a human receipt cannot
    make an unbanked provider call disappear from spend accounting.
    """
    errors: list[str] = []
    payload = _load(path, "human-review ledger", errors)
    if not payload:
        return None, errors
    extra = set(payload) - _TOP_KEYS
    if extra:
        errors.append("human-review ledger has unexpected fields: " +
                      ", ".join(sorted(extra)))
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"human-review schema must be {SCHEMA_VERSION}")
    receipts = payload.get("receipts")
    if not isinstance(receipts, list):
        errors.append("human-review receipts must be a list")
        return None, errors
    if payload.get("count") != len(receipts):
        errors.append("human-review count does not match receipts")
    ids = [x.get("receipt_id") for x in receipts if isinstance(x, dict)]
    if (len(ids) != len(receipts)
            or any(not isinstance(x, str) or not x for x in ids)):
        errors.append("every human-review receipt needs a receipt_id")
    elif len(ids) != len(set(ids)):
        errors.append("human-review receipt ids must be unique")
    active_id = payload.get("active_receipt_id")
    active = next((x for x in receipts
                   if isinstance(x, dict) and x.get("receipt_id") == active_id),
                  None)
    if active is None:
        errors.append("active human-review receipt does not exist")
        return None, errors

    extra = set(active) - _RECEIPT_KEYS
    missing = _RECEIPT_KEYS - set(active)
    if extra:
        errors.append("active receipt has unexpected fields: " +
                      ", ".join(sorted(extra)))
    if missing:
        errors.append("active receipt is missing fields: " +
                      ", ".join(sorted(missing)))
    actor = str(active.get("approved_by") or "").strip()
    actor_words = [x for x in re.split(r"\s+", actor) if x]
    if len(actor_words) < 2 or actor.lower() in _RESERVED_ACTORS:
        errors.append("active receipt must name a human approver")
    handle = str(active.get("approved_by_handle") or "").strip().lower()
    if not handle or handle in _RESERVED_ACTORS:
        errors.append("active receipt must carry a human approver handle")
    if not _DATE.fullmatch(str(active.get("approved_on") or "")):
        errors.append("active receipt approved_on must be YYYY-MM-DD")
    raw_statement = active.get("approval_statement")
    statement = raw_statement.strip() if isinstance(raw_statement, str) else ""
    if (not re.search(r"\bapprove(?:d)?\b", statement, re.IGNORECASE)
            or "suppression" not in statement.lower()):
        errors.append(
            "active receipt does not carry a suppression approval statement")
    if active.get("action") != RECEIPT_ACTION:
        errors.append(f"active receipt action must be {RECEIPT_ACTION}")
    if not _SHA256.fullmatch(str(active.get("review_package_sha256") or "")):
        errors.append("active receipt needs a review-package SHA-256")
    if not re.fullmatch(r"independent-review-v\d+",
                        str(active.get("review_package_schema_version") or "")):
        errors.append("active receipt review-package schema is unsupported")
    if active.get("review_package_run_status") != "VALID":
        errors.append("active receipt review package was not VALID")

    outcomes = active.get("outcomes")
    if not isinstance(outcomes, list):
        errors.append("active receipt outcomes must be a list")
        return active, errors
    if active.get("candidate_count") != len(outcomes) or not outcomes:
        errors.append("active receipt candidate count is invalid")
    candidate_ids = [x.get("candidate_id") for x in outcomes
                     if isinstance(x, dict)]
    if (len(candidate_ids) != len(outcomes)
            or any(not isinstance(x, str) or not x for x in candidate_ids)):
        errors.append("every reviewed outcome needs a candidate id")
        candidate_ids = [x for x in candidate_ids if isinstance(x, str)]
    if len(candidate_ids) != len(set(candidate_ids)):
        errors.append("reviewed candidate ids must be unique")
    for i, outcome in enumerate(outcomes, 1):
        if not isinstance(outcome, dict):
            errors.append(f"outcome {i} must be an object")
            continue
        prefix = f"outcome {outcome.get('candidate_id') or i}"
        extra = set(outcome) - _OUTCOME_KEYS
        missing = _OUTCOME_KEYS - set(outcome)
        if extra:
            errors.append(f"{prefix} has unexpected fields: " +
                          ", ".join(sorted(extra)))
        if missing:
            errors.append(f"{prefix} is missing fields: " +
                          ", ".join(sorted(missing)))
        if outcome.get("decision") != OUTCOME_ACTION:
            errors.append(f"{prefix} is not a suppression")
        if outcome.get("generator_decision") != "NO_FANTASY_IMPACT":
            errors.append(f"{prefix} generator decision is not no-impact")
        if outcome.get("independent_verdict") != "AUTO_APPROVE":
            errors.append(f"{prefix} independent verdict is not AUTO_APPROVE")
        if outcome.get("evidence_integrity_status") != "OK":
            errors.append(f"{prefix} evidence integrity is not OK")
        for field in ("evidence_sha256", "generator_request_sha256",
                      "reviewer_request_sha256"):
            if not _SHA256.fullmatch(str(outcome.get(field) or "")):
                errors.append(f"{prefix} has invalid {field}")
        for field in ("player_id", "player_name", "team", "position",
                      "source_id"):
            if not str(outcome.get(field) or "").strip():
                errors.append(f"{prefix} has no {field}")

    paid = _load(paid_path, "paid-candidate ledger", errors)
    paid_ids = paid.get("candidate_ids") if paid else []
    if (not isinstance(paid_ids, list)
            or any(not isinstance(x, str) for x in paid_ids)):
        errors.append("paid-candidate ids must be a list")
        paid_ids = []
    unbanked = sorted(set(candidate_ids) - set(paid_ids))
    if unbanked:
        errors.append("human-reviewed candidates are absent from paid ledger: " +
                      ", ".join(unbanked))

    n = len(outcomes)
    if active.get("generator_calls") != n or active.get("reviewer_calls") != n:
        errors.append("human-review call counts do not match outcomes")
    try:
        generator_cost = float(active.get("generator_cost_usd"))
        reviewer_cost = float(active.get("reviewer_cost_usd"))
        total_cost = float(active.get("total_cost_usd"))
        if (not all(math.isfinite(x) and x >= 0
                    for x in (generator_cost, reviewer_cost, total_cost))):
            errors.append("human-review costs must be finite and non-negative")
        elif round(generator_cost + reviewer_cost, 4) != round(total_cost, 4):
            errors.append("human-review costs do not reconcile")
    except (TypeError, ValueError):
        errors.append("human-review costs must be numeric")
    if active.get("publications_applied") != 0:
        errors.append("a suppression receipt cannot apply publications")
    for field in ("publication_count_before", "publication_count_after"):
        value = active.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"{field} must be a non-negative integer")
    if active.get("publication_count_before") != \
            active.get("publication_count_after"):
        errors.append("publication count changed during suppression review")
    return active, errors


def readiness(path: Path = LEDGER, paid_path: Path = PAID) \
        -> tuple[bool, str]:
    receipt, errors = validate_ledger(path, paid_path)
    if errors:
        return False, errors[0]
    return True, (f"{receipt['approved_by']} approved "
                  f"{receipt['candidate_count']} suppression(s); "
                  f"receipt {receipt['receipt_id']}")
