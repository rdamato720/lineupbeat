#!/usr/bin/env python3
"""Build a dark-launch, event-centric Wire V2 review batch.

This script can spend only inside explicit call and dollar ceilings. It cannot
approve, publish, or write the production publication file.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import players, v2
from wire.public_labels import DIRECTION_LABELS
from wire.providers.openai import MODEL
from wire.v2_draft import OpenAIV2DraftProvider, validate
import wire_mobile_draft as capture


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "wire_v2_dark_batch.json"
STATE = ROOT / "wire-v2-state.json"
X_DB = ROOT / "wire-v2-x.db"
SCHEMA = "wire-v2-dark-batch-v1"
STATE_SCHEMA = "wire-v2-state-v1"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": STATE_SCHEMA, "candidate_ids": [], "events": []}
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != STATE_SCHEMA or not isinstance(
            payload.get("candidate_ids"), list):
        raise ValueError("Wire V2 state is invalid")
    payload.setdefault("events", [])
    if not isinstance(payload["events"], list):
        raise ValueError("Wire V2 event state is invalid")
    return payload


def save_state(path: Path, state: dict) -> None:
    state["candidate_ids"] = sorted(set(state["candidate_ids"]))
    state["events"] = state.get("events", [])[-500:]
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
    temp.replace(path)


def event_candidate_ids(event: dict) -> set[str]:
    return {str(row.get("candidate_id") or "") for row in event.get("sources") or []}


def event_prototype(event: dict) -> dict:
    source = event["sources"][0]
    return {
        "event_id": event["event_id"], "player": event["player"],
        "player_id": event["player_id"], "team": event["team"],
        "position": event["position"], "candidate_id": source["candidate_id"],
        "source_name": source["source_name"], "source_id": source["source_id"],
        "source_url": source["url"], "published_at": source["published_at"],
        "evidence": source["evidence"], "origin": source["origin"],
        "ownership": source["ownership"], "author": source["author"],
    }


def prioritize(events: list[dict]) -> list[dict]:
    """Detailed and corroborated events first, then pure recency."""
    return sorted(events, key=lambda event: (
        event.get("source_count", 0) > 1,
        event.get("source_count", 0),
        event.get("published_at", ""),
    ), reverse=True)


def card_from(event: dict, result: dict) -> dict:
    primary = event["sources"][0]
    return {
        "event_id": event["event_id"],
        "player": event["player"], "player_id": event["player_id"],
        "team": event["team"], "position": event["position"],
        "event_type": result["event_type"],
        "direction": result["direction"],
        "reader_label": DIRECTION_LABELS[result["direction"]],
        "what_changed": result["what_changed"].strip(),
        "lineupbeat_impact": result["lineupbeat_impact"].strip(),
        "evidence_basis": result["evidence_basis"],
        "limitations": result["limitations"],
        "confidence": result["confidence"],
        "primary_source": {key: primary[key] for key in (
            "candidate_id", "source_id", "source_name", "author", "url",
            "published_at", "ownership", "origin")},
        "sources": event["sources"],
        "review_status": "DARK_LAUNCH_PENDING",
        "published": False,
    }


def run(args, provider=None) -> dict:
    provider = provider or OpenAIV2DraftProvider(model=args.model)
    provider.authenticate()
    generated = now_utc()
    cutoff = generated - timedelta(hours=args.hours)
    registry = players.load()
    capture.BEAT_DB = args.x_db
    raw = (capture.article_candidates(registry, cutoff) +
           capture.x_candidates(registry, cutoff))

    state = load_state(args.state)
    seen = set(state["candidate_ids"])
    prior_events = state.get("events") or []
    fresh, cross_run_duplicates = [], []
    for row in raw:
        if row["candidate_id"] in seen:
            continue
        prior = next((old for old in prior_events if v2.same_event(row, old)[0]), None)
        if prior:
            state["candidate_ids"].append(row["candidate_id"])
            cross_run_duplicates.append({
                "candidate_id": row["candidate_id"],
                "prior_event_id": prior["event_id"],
                "player": row["player"], "source_url": row["source_url"],
            })
        else:
            fresh.append(row)
    if cross_run_duplicates:
        save_state(args.state, state)
    events = prioritize(v2.cluster(fresh))

    proposals, outcomes = [], []
    calls, cost = 0, 0.0
    for event in events:
        if calls >= args.max_calls or cost >= args.cap:
            break
        ids = event_candidate_ids(event)
        state["candidate_ids"].extend(ids)
        state["events"].append(event_prototype(event))
        save_state(args.state, state)
        result, meta = provider.draft(event)
        calls += 1
        cost += float(meta["cost_usd"])
        failures = validate(result, event)
        decision = result.get("decision", "ABSTAIN")
        if failures:
            decision = "VALIDATION_FAILED"
        if decision == "PROPOSE":
            proposals.append(card_from(event, result))
        outcomes.append({
            "event_id": event["event_id"], "player": event["player"],
            "team": event["team"], "source_count": event["source_count"],
            "decision": decision, "event_type": result.get("event_type", ""),
            "reason": result.get("reason", ""),
            "validation_failures": failures,
            "cost_usd": round(float(meta["cost_usd"]), 6),
        })

    counts = Counter(row["decision"] for row in outcomes)
    payload = {
        "schema_version": SCHEMA,
        "generated_at": generated.replace(microsecond=0).isoformat(),
        "window": {"hours": args.hours, "from": cutoff.isoformat(),
                   "to": generated.isoformat()},
        "dark_launch": True, "published": False,
        "model": args.model, "model_calls": calls,
        "cost_usd": round(cost, 6),
        "limits": {"max_calls": args.max_calls, "cap_usd": args.cap},
        "raw_candidate_count": len(raw),
        "fresh_candidate_count": len(fresh),
        "event_count": len(events),
        "reports_merged": max(0, len(fresh) - len(events)),
        "cross_run_duplicate_count": len(cross_run_duplicates),
        "cross_run_duplicates": cross_run_duplicates,
        "reviewed_event_count": calls,
        "unreviewed_event_count": max(0, len(events) - calls),
        "proposal_count": len(proposals),
        "outcome_counts": dict(sorted(counts.items())),
        "proposals": proposals, "outcomes": outcomes,
    }
    args.output.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--cap", type=float, required=True)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--x-db", type=Path, default=X_DB)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if args.hours <= 0 or not 1 <= args.max_calls <= 40 or not 0 < args.cap <= 1:
        raise SystemExit("hours must be positive; max calls 1-40; cap >0 and <=1")
    payload = run(args)
    print(
        f"  Wire V2 dark launch: {payload['raw_candidate_count']} raw reports -> "
        f"{payload['event_count']} events ({payload['reports_merged']} merged), "
        f"{payload['model_calls']} calls, ${payload['cost_usd']:.4f}, "
        f"{payload['proposal_count']} proposals, 0 publications"
    )
    if payload["unreviewed_event_count"]:
        print(f"  {payload['unreviewed_event_count']} events remain unreviewed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
