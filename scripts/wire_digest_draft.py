#!/usr/bin/env python3
"""Build one review-only curated digest from standalone trusted reports."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import digest, players
from wire.providers.openai import MODEL
import wire_mobile_draft as capture

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/wire_digest_dark_batch.json"
STATE = ROOT / "wire-digest-state.json"
X_DB = ROOT / "wire-digest-x.db"


def now_utc():
    return datetime.now(timezone.utc)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": "wire-digest-state-v1", "report_ids": []}
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != "wire-digest-state-v1":
        raise ValueError("digest state is invalid")
    return payload


def save_state(path: Path, payload: dict) -> None:
    payload["report_ids"] = sorted(set(payload.get("report_ids") or []))[-10_000:]
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=1) + "\n")
    temp.replace(path)


def run(args, provider=None) -> dict:
    generated = now_utc()
    cutoff = generated - timedelta(hours=args.hours)
    registry = players.load()
    capture.BEAT_DB = args.x_db
    raw = capture.article_candidates(registry, cutoff) + capture.x_candidates(registry, cutoff)
    all_reports = digest.collect(raw, max_reports=2000)
    state = load_state(args.state)
    seen = set(state["report_ids"])
    reports = [row for row in all_reports if row["report_id"] not in seen][:args.max_reports]
    calls, cost, accepted, rejected, model_summary = 0, 0.0, [], [], ""
    if reports:
        provider = provider or digest.OpenAIDigestProvider(model=args.model)
        provider.authenticate()
        result, meta = provider.draft(reports)
        calls, cost = 1, float(meta["cost_usd"])
        accepted, rejected = digest.validate(result, reports)
        model_summary = str(result.get("summary") or "")
        state["report_ids"].extend(row["report_id"] for row in reports)
        save_state(args.state, state)
    payload = {
        "schema_version": "wire-digest-dark-batch-v1",
        "generated_at": generated.replace(microsecond=0).isoformat(),
        "window": {"hours": args.hours, "from": cutoff.isoformat(), "to": generated.isoformat()},
        "dark_launch": True, "published": False, "model": args.model,
        "model_calls": calls, "cost_usd": round(cost, 6),
        "limits": {"max_reports": args.max_reports, "cap_usd": args.cap},
        "raw_candidate_count": len(raw), "standalone_report_count": len(all_reports),
        "reviewed_report_count": len(reports), "proposal_count": len(accepted),
        "validation_rejection_count": len(rejected), "model_summary": model_summary,
        "proposals": accepted, "validation_rejections": rejected,
        "cap_crossed": cost > args.cap,
    }
    args.output.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--max-reports", type=int, default=60)
    parser.add_argument("--cap", type=float, default=.20)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--x-db", type=Path, default=X_DB)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if not 1 <= args.hours <= 48 or not 10 <= args.max_reports <= 100 or not 0 < args.cap <= 1:
        raise SystemExit("hours 1-48; reports 10-100; cap >0 and <=1")
    payload = run(args)
    print(f"  Wire digest: {payload['reviewed_report_count']} standalone reports, "
          f"{payload['model_calls']} batch call, ${payload['cost_usd']:.4f}, "
          f"{payload['proposal_count']} updates, 0 publications")
    if payload["cap_crossed"]:
        print("  observed cap crossed by the single batch response")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
