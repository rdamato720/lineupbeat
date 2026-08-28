#!/usr/bin/env python3
"""Build a review-only, story-first Wire V3 dark-launch batch."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import players, v3
from wire.public_labels import DIRECTION_LABELS
from wire.providers.openai import MODEL
from wire.v3_draft import OpenAIV3DraftProvider, load_relevance, validate
import wire_mobile_draft as capture

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "wire_v3_dark_batch.json"
STATE = ROOT / "wire-v3-state.json"
X_DB = ROOT / "wire-v3-x.db"
RELEVANCE = ROOT / "data" / "wire_fantasy_relevance.json"
STATE_SCHEMA = "wire-v3-state-v1"


def now_utc():
    return datetime.now(timezone.utc)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": STATE_SCHEMA, "candidate_ids": [], "stories": []}
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != STATE_SCHEMA:
        raise ValueError("Wire V3 state is invalid")
    return payload


def save_state(path: Path, state: dict) -> None:
    state["candidate_ids"] = sorted(set(state.get("candidate_ids") or []))
    state["stories"] = (state.get("stories") or [])[-500:]
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
    temp.replace(path)


def prototype(story: dict) -> dict:
    report = story["reports"][0]
    player = story["players"][0]
    return {**player, "story_id": story["story_id"],
            "candidate_id": report["candidate_id"], "source_url": report["url"],
            "published_at": report["published_at"], "evidence": report["evidence"]}


def card_from(story: dict, result: dict) -> list[dict]:
    identities = {row["player_id"]: row for row in story["players"]}
    primary = story["reports"][0]
    cards = []
    for row in result["cards"]:
        identity = identities[row["player_id"]]
        cards.append({
            "story_id": story["story_id"], **identity,
            "event_type": row["event_type"], "direction": row["direction"],
            "reader_label": DIRECTION_LABELS[row["direction"]],
            "what_changed": row["what_changed"].strip(),
            "lineupbeat_impact": row["lineupbeat_impact"].strip(),
            "evidence_basis": row["evidence_basis"], "limitations": row["limitations"],
            "confidence": row["confidence"], "primary_source": primary,
            "sources": story["reports"], "review_status": "DARK_LAUNCH_PENDING",
            "published": False,
        })
    return cards


def run(args, provider=None) -> dict:
    provider = provider or OpenAIV3DraftProvider(model=args.model)
    provider.authenticate()
    generated = now_utc()
    cutoff = generated - timedelta(hours=args.hours)
    registry = players.load()
    capture.BEAT_DB = args.x_db
    raw = capture.article_candidates(registry, cutoff) + capture.x_candidates(registry, cutoff)
    state = load_state(args.state)
    seen = set(state.get("candidate_ids") or [])
    fresh, cross_run_duplicates = [], []
    for row in raw:
        if row["candidate_id"] in seen:
            continue
        prior = next((old for old in state.get("stories") or [] if v3.same_story(row, old)), None)
        if prior:
            state["candidate_ids"].append(row["candidate_id"])
            cross_run_duplicates.append(row["candidate_id"])
        else:
            fresh.append(row)
    if cross_run_duplicates:
        save_state(args.state, state)
    stories = sorted(v3.cluster(fresh), key=lambda row: (
        row["report_count"] > 1, row["report_count"], row["published_at"]), reverse=True)
    relevance = load_relevance(args.relevance)
    proposals, outcomes, calls, cost = [], [], 0, 0.0
    for story in stories:
        if calls >= args.max_calls or cost >= args.cap:
            break
        state["candidate_ids"].extend(story["candidate_ids"])
        state["stories"].append(prototype(story))
        save_state(args.state, state)
        result, meta = provider.draft(story)
        calls += 1
        cost += float(meta["cost_usd"])
        failures = validate(result, story, relevance)
        decision = "VALIDATION_FAILED" if failures else result.get("decision", "ABSTAIN")
        if decision == "PROPOSE":
            proposals.extend(card_from(story, result))
        outcomes.append({
            "story_id": story["story_id"], "players": [p["player"] for p in story["players"]],
            "report_count": story["report_count"], "decision": decision,
            "reason": result.get("reason", ""), "validation_failures": failures,
            "cost_usd": round(float(meta["cost_usd"]), 6),
        })
    counts = Counter(row["decision"] for row in outcomes)
    payload = {
        "schema_version": "wire-v3-dark-batch-v1",
        "generated_at": generated.replace(microsecond=0).isoformat(),
        "window": {"hours": args.hours, "from": cutoff.isoformat(), "to": generated.isoformat()},
        "dark_launch": True, "published": False, "model": args.model,
        "model_calls": calls, "cost_usd": round(cost, 6),
        "limits": {"max_calls": args.max_calls, "cap_usd": args.cap},
        "raw_candidate_count": len(raw), "fresh_candidate_count": len(fresh),
        "story_count": len(stories), "reports_merged": max(0, len(fresh) - len(stories)),
        "cross_run_duplicate_count": len(cross_run_duplicates),
        "reviewed_story_count": calls, "unreviewed_story_count": max(0, len(stories) - calls),
        "proposal_count": len(proposals), "outcome_counts": dict(sorted(counts.items())),
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
    parser.add_argument("--relevance", type=Path, default=RELEVANCE)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if not 1 <= args.hours <= 48 or not 1 <= args.max_calls <= 40 or not 0 < args.cap <= 1:
        raise SystemExit("hours 1-48; max calls 1-40; cap >0 and <=1")
    payload = run(args)
    print(f"  Wire V3: {payload['raw_candidate_count']} reports -> "
          f"{payload['story_count']} stories, {payload['model_calls']} calls, "
          f"{payload['proposal_count']} proposals, 0 publications")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
