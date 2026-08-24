"""Immutable mobile review batches and GitHub comment approvals.

The issue body is a transport, not authority.  It contains the exact card
payload inside a compressed manifest whose SHA-256 is recomputed before a
comment can be acted on.  Authority comes separately from the allow-listed
GitHub actor and the explicit command in that comment.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import zlib
from copy import deepcopy


INBOX_SCHEMA = "wire-mobile-inbox-v1"
APPROVAL_SCHEMA = "wire-mobile-approval-ledger-v1"
APPROVED_GITHUB_ACTOR = "rdamato720"
APPROVER_NAME = "Ralph Damato"
INBOX_LABEL = "wire-inbox"
MAX_CARDS = 10
MANIFEST_START = "<!-- WIRE_MOBILE_MANIFEST_V1:"
MANIFEST_END = ":WIRE_MOBILE_MANIFEST_V1 -->"

_SELECT = re.compile(
    r"^(approve|reject)\s+(all|\d+(?:\s*[, ]\s*\d+)*)$", re.I)
_EDIT = re.compile(r"^edit\s+(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)$", re.I)


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def batch_id(payload_without_id: dict) -> str:
    return hashlib.sha256(canonical_bytes(payload_without_id)).hexdigest()


def make_manifest(cards: list[dict], *, generated_at: str,
                  publication_sha256: str, publication_count: int,
                  source_batch_sha256: str,
                  model_calls: int = 0, cost_usd: float = 0.0) -> dict:
    if not cards:
        raise ValueError("mobile inbox requires at least one card")
    if len(cards) > MAX_CARDS:
        raise ValueError(f"mobile inbox is capped at {MAX_CARDS} cards")
    body = {
        "schema_version": INBOX_SCHEMA,
        "generated_at": generated_at,
        "publication_sha256_at_draft": publication_sha256,
        "publication_count_at_draft": int(publication_count),
        "source_batch_sha256": source_batch_sha256,
        "model_calls": int(model_calls),
        "cost_usd": round(float(cost_usd), 6),
        "cards": deepcopy(cards),
    }
    return {**body, "batch_id": batch_id(body)}


def validate_manifest(manifest: dict) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != INBOX_SCHEMA:
        errors.append("unsupported mobile inbox schema")
    supplied = str(manifest.get("batch_id") or "")
    body = {k: v for k, v in manifest.items() if k != "batch_id"}
    if not re.fullmatch(r"[0-9a-f]{64}", supplied):
        errors.append("mobile inbox has no valid batch id")
    elif batch_id(body) != supplied:
        errors.append("mobile inbox batch hash does not match its cards")
    for field in ("publication_sha256_at_draft", "source_batch_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(field) or "")):
            errors.append(f"mobile inbox has no valid {field}")
    count = manifest.get("publication_count_at_draft")
    if not isinstance(count, int) or count < 0:
        errors.append("mobile inbox has no valid publication_count_at_draft")
    cards = manifest.get("cards")
    if not isinstance(cards, list) or not cards:
        errors.append("mobile inbox has no cards")
        return errors
    if len(cards) > MAX_CARDS:
        errors.append(f"mobile inbox exceeds {MAX_CARDS} cards")
    ids = [str(c.get("evidence_candidate_id") or "")
           for c in cards if isinstance(c, dict)]
    if len(ids) != len(cards) or any(not x for x in ids):
        errors.append("every mobile card needs an evidence candidate id")
    elif len(ids) != len(set(ids)):
        errors.append("mobile inbox repeats an evidence candidate id")
    for index, card in enumerate(cards, 1):
        if not isinstance(card, dict):
            errors.append(f"card {index} is not an object")
            continue
        for field in ("player", "player_id", "team", "position", "evidence",
                      "public_summary", "commentary", "url", "author",
                      "source", "direction", "mechanism", "strength",
                      "horizon", "content_type"):
            if not str(card.get(field) or "").strip():
                errors.append(f"card {index} has no {field}")
    return errors


def encode_manifest(manifest: dict) -> str:
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError("; ".join(errors))
    packed = zlib.compress(canonical_bytes(manifest), level=9)
    token = base64.urlsafe_b64encode(packed).decode().rstrip("=")
    return f"{MANIFEST_START}{token}{MANIFEST_END}"


def decode_manifest(issue_body: str) -> dict:
    pattern = re.escape(MANIFEST_START) + r"([A-Za-z0-9_-]+)" + \
        re.escape(MANIFEST_END)
    matches = re.findall(pattern, issue_body or "")
    if len(matches) != 1:
        raise ValueError("issue must contain exactly one mobile inbox manifest")
    token = matches[0]
    token += "=" * (-len(token) % 4)
    try:
        payload = json.loads(zlib.decompress(
            base64.urlsafe_b64decode(token.encode())).decode())
    except Exception as exc:
        raise ValueError(f"mobile inbox manifest cannot be decoded: {exc}") \
            from None
    errors = validate_manifest(payload)
    if errors:
        raise ValueError("; ".join(errors))
    return payload


def parse_commands(comment: str, card_count: int) -> dict:
    """Return approved/rejected card numbers and exact mobile edits."""
    approved: set[int] = set()
    rejected: set[int] = set()
    edits: dict[int, dict[str, str]] = {}
    lines = [line.strip() for line in str(comment or "").splitlines()
             if line.strip()]
    if not lines:
        raise ValueError("approval comment is empty")
    for line in lines:
        edit = _EDIT.fullmatch(line)
        if edit:
            number = int(edit.group(1))
            if not 1 <= number <= card_count:
                raise ValueError(f"card {number} is outside this batch")
            edits[number] = {
                "public_summary": edit.group(2).strip(),
                "commentary": edit.group(3).strip(),
            }
            approved.add(number)
            rejected.discard(number)
            continue
        selected = _SELECT.fullmatch(line)
        if not selected:
            raise ValueError(
                "commands must be 'approve all', 'approve 1,3', "
                "'reject 2', or 'edit 3 | summary | impact'")
        action, raw = selected.group(1).lower(), selected.group(2).lower()
        numbers = (set(range(1, card_count + 1)) if raw == "all" else
                   {int(x) for x in re.findall(r"\d+", raw)})
        bad = sorted(x for x in numbers if not 1 <= x <= card_count)
        if bad:
            raise ValueError(f"cards outside this batch: {bad}")
        if action == "approve":
            approved.update(numbers)
            rejected.difference_update(numbers)
        else:
            rejected.update(numbers)
            approved.difference_update(numbers)
            for number in numbers:
                edits.pop(number, None)
    if not approved and not rejected:
        raise ValueError("comment does not decide any cards")
    return {"approved": sorted(approved), "rejected": sorted(rejected),
            "edits": edits}


def validate_event(event: dict) -> tuple[dict, str]:
    actor = str((event.get("sender") or {}).get("login") or "")
    if actor.casefold() != APPROVED_GITHUB_ACTOR.casefold():
        raise ValueError(f"GitHub actor {actor!r} is not authorized")
    issue = event.get("issue") or {}
    if issue.get("pull_request"):
        raise ValueError("mobile Wire approvals must come from an issue")
    if str(issue.get("state") or "open").lower() != "open":
        raise ValueError("mobile Wire approval issue is not open")
    author = str((issue.get("user") or {}).get("login") or "")
    if author not in {"github-actions[bot]", APPROVED_GITHUB_ACTOR}:
        raise ValueError("Wire inbox was not created by the trusted workflow")
    labels = {str(x.get("name") or "") for x in issue.get("labels") or []}
    if INBOX_LABEL not in labels:
        raise ValueError(f"issue is missing the {INBOX_LABEL} label")
    action = str(event.get("action") or "")
    if action != "created":
        raise ValueError("only newly created comments can approve cards")
    comment = str((event.get("comment") or {}).get("body") or "")
    return issue, comment
