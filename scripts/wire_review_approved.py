#!/usr/bin/env python3
"""Run the exact human-approved Wire review cohort without publishing.

This is the local equivalent of the GitHub review-only job. It validates the
tracked approval manifest, authenticates both providers before the slow source
crawl, enforces the per-pass and total ceilings, and guards the publication
file byte-for-byte. It never calls the publisher or deployer.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"
SELECTION = DATA / "wire_review_selection.json"
PUBLICATIONS = DATA / "wire_publications.json"
STATE = DATA / "wire_backfill.json"
INDEPENDENT = DATA / "wire_independent_review.json"
PACKAGE = DATA / "wire_review_package.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_selection() -> dict:
    selection = json.loads(SELECTION.read_text())
    required = {
        "schema_version": "wire-review-selection-v1",
        "approved_by": "Ralph Damato",
        "approval_statement": "approved",
        "approved_cost_usd": 1.0,
        "approved_max_calls": 40,
        "generator_cap_usd": 0.40,
        "generator_max_calls": 20,
        "reviewer_cap_usd": 0.40,
        "reviewer_max_calls": 20,
        "hours": 72,
        "publications_authorized": 0,
        "deployment_authorized": False,
    }
    bad = [key for key, value in required.items()
           if selection.get(key) != value]
    ids = selection.get("candidate_ids")
    if bad:
        raise SystemExit(f"selection authorization mismatch: {bad}; 0 API calls")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 20:
        raise SystemExit("selection requires 1-20 candidate ids; 0 API calls")
    if any(not isinstance(value, str) or
           not re.fullmatch(r"[0-9a-f]{20}", value) for value in ids):
        raise SystemExit("selection has an invalid candidate id; 0 API calls")
    if len(set(ids)) != len(ids):
        raise SystemExit("selection repeats a candidate id; 0 API calls")
    if selection.get("publication_sha256_before") != sha256(PUBLICATIONS):
        raise SystemExit("publication boundary differs from approval; 0 API calls")
    return selection


def run(*parts: str) -> None:
    completed = subprocess.run([sys.executable, *parts], cwd=ROOT, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def result_ids(payload: dict, key: str) -> list[str | None]:
    return [(item.get("candidate") or {}).get("candidate_id")
            for item in payload.get(key, [])]


def verify_generator(selection: dict) -> dict:
    state = json.loads(STATE.read_text())
    summary = state.get("openai") or {}
    if summary.get("provider_failures"):
        raise SystemExit("generator provider failure; independent review refused")
    if result_ids(state, "results") != selection["candidate_ids"]:
        raise SystemExit("generator did not complete the exact approved cohort")
    if int(summary.get("calls", 0)) > selection["generator_max_calls"]:
        raise SystemExit("generator exceeded its approved call limit")
    if float(summary.get("cost_usd", 0)) > selection["generator_cap_usd"]:
        raise SystemExit("generator exceeded its approved cost limit")
    return state


def verify_final(selection: dict, publications_before: str) -> tuple[int, float]:
    state = json.loads(STATE.read_text())
    independent = json.loads(INDEPENDENT.read_text())
    package = json.loads(PACKAGE.read_text())
    generator = state.get("openai") or {}
    selected_ids = selection["candidate_ids"]
    if result_ids(state, "results") != selected_ids:
        raise SystemExit("generator cohort changed after review")
    if result_ids(independent, "items") != selected_ids:
        raise SystemExit("independent review did not complete the approved cohort")
    generator_calls = int(generator.get("calls", 0))
    reviewer_calls = int(independent.get("calls", 0))
    generator_cost = float(generator.get("cost_usd", 0))
    reviewer_cost = float(independent.get("cost_usd", 0))
    if generator_calls > selection["generator_max_calls"] or \
            generator_cost > selection["generator_cap_usd"]:
        raise SystemExit("generator exceeded its approved boundary")
    if reviewer_calls > selection["reviewer_max_calls"] or \
            reviewer_cost > selection["reviewer_cap_usd"]:
        raise SystemExit("independent reviewer exceeded its approved boundary")
    calls = generator_calls + reviewer_calls
    cost = generator_cost + reviewer_cost
    if calls > selection["approved_max_calls"]:
        raise SystemExit("approved total call limit exceeded")
    if cost > selection["approved_cost_usd"]:
        raise SystemExit("approved total cost limit exceeded")
    if package.get("publications_applied") != 0:
        raise SystemExit("review package reports publications")
    if sha256(PUBLICATIONS) != publications_before:
        raise SystemExit("publication file changed during review")
    return calls, cost


def main() -> int:
    selection = load_selection()

    from wire.providers.openai import OpenAISemanticProvider
    from wire.providers.openai_review import OpenAIIndependentReviewer

    generator = OpenAISemanticProvider()
    reviewer = OpenAIIndependentReviewer()
    if not generator.available() or not reviewer.available():
        raise SystemExit("OPENAI_API_KEY is unavailable; 0 API calls")
    generator.authenticate()
    OpenAISemanticProvider(model=reviewer.model).authenticate()
    print("review providers authenticated; 0 Responses API calls")

    run("scripts/test_wire_review.py")
    run("scripts/wire_health.py", "--check")
    publications_before = sha256(PUBLICATIONS)
    for name in ("wire_backfill_plan.json", "wire_independent_review.json",
                 "wire_review_package.json", "wire_review_package.html"):
        (DATA / name).unlink(missing_ok=True)

    hours = str(selection["hours"])
    run("scripts/wire_backfill.py", "--discover", "--report", "--hours", hours)
    run("scripts/wire_extract.py", "--limit", "1000")
    command = ["scripts/wire_backfill.py", "--interpret", "--report",
               "--hours", hours, "--cap", str(selection["generator_cap_usd"]),
               "--max-calls", str(selection["generator_max_calls"])]
    for candidate_id in selection["candidate_ids"]:
        command.extend(("--candidate-id", candidate_id))
    run(*command)
    verify_generator(selection)

    run("scripts/wire_independent_review.py",
        "--cap", str(selection["reviewer_cap_usd"]),
        "--max-calls", str(selection["reviewer_max_calls"]))
    run("scripts/wire_review_package.py")
    calls, cost = verify_final(selection, publications_before)
    print(f"approved boundary held: {calls}/40 calls, ${cost:.4f}/$1.00, 0 published")
    print("review package: data/wire_review_package.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
