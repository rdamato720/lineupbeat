#!/usr/bin/env python3
"""Run non-publishing Pass B over stored Pass A results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import evidence_integrity as integrity
from wire import independent_review as review
from wire import players
from wire.providers.openai_review import OpenAIIndependentReviewer

SOURCE = Path("data/wire_backfill.json")
OUTPUT = Path("data/wire_independent_review.json")


def validated_identity(result: dict, registry) -> tuple[dict | None, str]:
    candidate = result.get("candidate") or {}
    pid = candidate.get("player_id") or result.get("supplied_identity", {}).get("player_id")
    player = registry.by_id.get(pid or "")
    if player is None:
        return None, "player id does not resolve in wire_players"
    for key, actual in (("player_name", player.full_name),
                        ("team", player.team), ("position", player.position)):
        supplied = candidate.get(key)
        if supplied and ((players.norm(supplied) != players.norm(actual))
                         if key == "player_name" else supplied != actual):
            return None, f"candidate {key} disagrees with registry"
    return {"player_id": player.player_id, "player_name": player.full_name,
            "team": player.team, "position": player.position,
            "registry_version": registry.version,
            "registry_check": "VERIFIED"}, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--output", type=Path, default=OUTPUT)
    ap.add_argument("--cap", type=float, default=15.0)
    ap.add_argument("--max-calls", type=int, default=15)
    args = ap.parse_args()
    state = json.loads(args.source.read_text())
    registry = players.load()
    provider = OpenAIIndependentReviewer()
    if not provider.available():
        print("OpenAI unavailable; nothing reviewed", file=sys.stderr)
        return 4
    reviewed, spend, calls = [], 0.0, 0
    for result in state.get("results", []):
        identity, identity_error = validated_identity(result, registry)
        evidence = (result.get("candidate") or {}).get("evidence_text", "")
        evidence_sha = integrity.sha256_text(evidence)
        if identity is None:
            reviewed.append({**result, "supplied_identity": None,
                             "identity_error": identity_error,
                             "independent_reviewer": review.enforce(
                                 {"verdict": "HUMAN_REVIEW"},
                                 identity_resolved=False, integrity_ok=False)})
            continue
        if spend >= args.cap or calls >= args.max_calls:
            break
        payload = provider.evaluate(evidence, identity, result["assessment"])
        calls += 1
        spend += payload.get("cost_usd", 0)
        request = {"evidence_text": evidence, "identity": identity,
                   "assessment": result["assessment"]}
        record = integrity.build_record(
            evidence,
            generator_evidence_sha=result.get(
                "generator_input_evidence_sha256", evidence_sha),
            reviewer_evidence_sha=evidence_sha,
            human_evidence_sha=evidence_sha,
            generator_request_sha=result.get("generator_request_sha256", ""),
            reviewer_request_sha=integrity.request_sha256(request))
        reviewed.append({**result, "supplied_identity": identity,
                         "evidence_integrity": record,
                         "independent_reviewer": review.enforce(
                             payload, identity_resolved=True,
                             integrity_ok=not record["blocks_automatic_approval"],
                             proposed_assessment=result["assessment"])})
    out = {"run_status": "VALID", "source": str(args.source),
           "prompt_version": review.PROMPT_VERSION,
           "schema_version": review.SCHEMA_VERSION,
           "cost_usd": round(spend, 4), "items": reviewed,
           "provider": "openai", "model": provider.model,
           "calls": calls, "max_calls": args.max_calls,
           "publications_applied": 0}
    args.output.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {args.output}: {len(reviewed)} items, ${spend:.4f}, 0 published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
