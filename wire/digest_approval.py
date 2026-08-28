"""Immutable manifests and authorized comments for curated Wire digests."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import zlib
from copy import deepcopy

SCHEMA = "wire-digest-inbox-v1"
LEDGER_SCHEMA = "wire-digest-approval-ledger-v1"
ACTOR = "rdamato720"
APPROVER = "Ralph Damato"
LABEL = "wire-digest-inbox"
START = "<!-- WIRE_DIGEST_MANIFEST_V1:"
END = ":WIRE_DIGEST_MANIFEST_V1 -->"
SELECT = re.compile(r"^(approve|reject)\s+(all|\d+(?:\s*[, ]\s*\d+)*)$", re.I)
EDIT = re.compile(r"^edit\s+(\d+)\s*\|\s*(.+)$", re.I)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode()


def hash_body(value: dict) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def make_manifest(updates: list[dict], generated_at: str, source_sha256: str,
                  publication_sha256: str, publication_count: int,
                  model_calls: int, cost_usd: float) -> dict:
    if not updates or len(updates) > 20:
        raise ValueError("digest inbox requires 1-20 updates")
    body = {"schema_version": SCHEMA, "generated_at": generated_at,
            "source_sha256": source_sha256,
            "publication_sha256_at_draft": publication_sha256,
            "publication_count_at_draft": publication_count,
            "model_calls": model_calls, "cost_usd": round(cost_usd, 6),
            "updates": deepcopy(updates)}
    return {**body, "batch_id": hash_body(body)}


def validate_manifest(manifest: dict) -> list[str]:
    errors = []
    if manifest.get("schema_version") != SCHEMA:
        errors.append("unsupported digest manifest schema")
    body = {key: value for key, value in manifest.items() if key != "batch_id"}
    if hash_body(body) != manifest.get("batch_id"):
        errors.append("digest manifest hash mismatch")
    for field in ("source_sha256", "publication_sha256_at_draft"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(field) or "")):
            errors.append(f"invalid {field}")
    updates = manifest.get("updates")
    if not isinstance(updates, list) or not 1 <= len(updates) <= 20:
        errors.append("digest manifest requires 1-20 updates")
        return errors
    ids = []
    for number, row in enumerate(updates, 1):
        for field in ("player_id", "player", "team", "position", "event_type",
                      "bullet", "evidence_quote", "source_url", "author",
                      "source_name", "published_at", "report_id"):
            if not str(row.get(field) or "").strip():
                errors.append(f"update {number} has no {field}")
        ids.append(str(row.get("report_id") or "") + ":" + str(row.get("player_id") or ""))
    if len(ids) != len(set(ids)):
        errors.append("digest manifest repeats a report/player update")
    return errors


def encode(manifest: dict) -> str:
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError("; ".join(errors))
    token = base64.urlsafe_b64encode(zlib.compress(canonical(manifest), 9)).decode().rstrip("=")
    return START + token + END


def decode(issue_body: str) -> dict:
    matches = re.findall(re.escape(START) + r"([A-Za-z0-9_-]+)" + re.escape(END),
                         issue_body or "")
    if len(matches) != 1:
        raise ValueError("issue must contain exactly one digest manifest")
    token = matches[0] + "=" * (-len(matches[0]) % 4)
    try:
        payload = json.loads(zlib.decompress(base64.urlsafe_b64decode(token)).decode())
    except Exception as exc:
        raise ValueError(f"digest manifest cannot be decoded: {exc}") from None
    errors = validate_manifest(payload)
    if errors:
        raise ValueError("; ".join(errors))
    return payload


def parse_commands(comment: str, count: int) -> dict:
    approved, rejected, edits = set(), set(), {}
    for line in [row.strip() for row in str(comment or "").splitlines() if row.strip()]:
        edit = EDIT.fullmatch(line)
        if edit:
            number = int(edit.group(1))
            if not 1 <= number <= count:
                raise ValueError(f"update {number} is outside this digest")
            edits[number] = edit.group(2).strip()
            approved.add(number)
            rejected.discard(number)
            continue
        selected = SELECT.fullmatch(line)
        if not selected:
            raise ValueError("commands must be approve all, approve 1,3, reject 2, or edit 3 | replacement")
        numbers = (set(range(1, count + 1)) if selected.group(2).lower() == "all"
                   else {int(value) for value in re.findall(r"\d+", selected.group(2))})
        if any(not 1 <= number <= count for number in numbers):
            raise ValueError("digest command contains an invalid update number")
        if selected.group(1).lower() == "approve":
            approved.update(numbers); rejected.difference_update(numbers)
        else:
            rejected.update(numbers); approved.difference_update(numbers)
            for number in numbers: edits.pop(number, None)
    if not approved and not rejected:
        raise ValueError("comment does not decide any digest updates")
    return {"approved": sorted(approved), "rejected": sorted(rejected), "edits": edits}


def validate_event(event: dict) -> tuple[dict, str]:
    actor = str((event.get("sender") or {}).get("login") or "")
    if actor.casefold() != ACTOR.casefold():
        raise ValueError("GitHub actor is not authorized")
    issue = event.get("issue") or {}
    if issue.get("pull_request") or str(issue.get("state") or "open").lower() != "open":
        raise ValueError("digest approval must come from an open issue")
    labels = {str(row.get("name") or "") for row in issue.get("labels") or []}
    if LABEL not in labels:
        raise ValueError(f"issue is missing the {LABEL} label")
    if event.get("action") != "created":
        raise ValueError("only a newly created comment can approve a digest")
    return issue, str((event.get("comment") or {}).get("body") or "")
