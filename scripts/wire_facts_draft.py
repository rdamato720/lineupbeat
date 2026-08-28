#!/usr/bin/env python3
"""Build the zero-model, facts-only Wire dark-launch batch."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import facts, players
import wire_mobile_draft as capture

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/wire_facts_dark_batch.json"
STATE = ROOT / "wire-facts-state.json"
X_DB = ROOT / "wire-facts-x.db"


def now_utc():
    return datetime.now(timezone.utc)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": "wire-facts-state-v1", "candidate_ids": []}
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != "wire-facts-state-v1":
        raise ValueError("facts-only state is invalid")
    return payload


def save_state(path: Path, payload: dict) -> None:
    payload["candidate_ids"] = sorted(set(payload.get("candidate_ids") or []))[-20_000:]
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=1) + "\n")
    temp.replace(path)


def run(args) -> dict:
    generated = now_utc()
    cutoff = generated - timedelta(hours=args.hours)
    registry = players.load()
    capture.BEAT_DB = args.x_db
    raw = capture.article_candidates(registry, cutoff) + capture.x_candidates(registry, cutoff)
    state = load_state(args.state)
    seen = set(state["candidate_ids"])
    fresh = [row for row in raw if row["candidate_id"] not in seen]
    extracted, rejected = [], []
    for row in fresh:
        fact, reason = facts.extract(row)
        state["candidate_ids"].append(row["candidate_id"])
        if fact:
            extracted.append(fact)
        else:
            rejected.append({"candidate_id": row["candidate_id"],
                             "player": row["player"], "reason": reason,
                             "source_url": row["source_url"]})
    save_state(args.state, state)
    accepted, duplicates = facts.deduplicate(extracted)
    counts = Counter(row["reason"] for row in rejected)
    payload = {
        "schema_version": "wire-facts-dark-batch-v1",
        "generated_at": generated.replace(microsecond=0).isoformat(),
        "window": {"hours": args.hours, "from": cutoff.isoformat(), "to": generated.isoformat()},
        "dark_launch": True, "published": False, "model_calls": 0, "cost_usd": 0,
        "raw_candidate_count": len(raw), "fresh_candidate_count": len(fresh),
        "extracted_fact_count": len(extracted), "proposal_count": len(accepted),
        "duplicate_count": len(duplicates), "rejected_count": len(rejected),
        "rejection_counts": dict(sorted(counts.items())),
        "proposals": accepted, "duplicates": duplicates, "rejected": rejected,
    }
    args.output.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--x-db", type=Path, default=X_DB)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if not 1 <= args.hours <= 48:
        raise SystemExit("hours must be 1-48")
    payload = run(args)
    print(f"  Facts-only Wire: {payload['raw_candidate_count']} candidates -> "
          f"{payload['proposal_count']} facts, {payload['duplicate_count']} duplicates, "
          "0 model calls, $0.0000, 0 publications")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
