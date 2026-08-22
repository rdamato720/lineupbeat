#!/usr/bin/env python3
"""Run the independent reviewer over generated assessments. Dark launch.

    python3 scripts/wire_independent_review.py --run --cap 5

It reads data/wire_backfill.json, asks a second, adversarially-framed model
call about each interpretation, and writes its verdicts beside them. It has
no path to wire_publications.json, no path to the review decisions file, and
no write to wire_evidence. The only thing it produces is an opinion for a
person to read.

The comparison it exists to make is four-way: what the generator decided,
what the deterministic validator said, what the reviewer says, and -- later,
and only from a human -- what was actually decided.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import independent_review as ir
from wire import evidence_integrity as eint
from wire import players as pl
from wire.providers.claude import ClaudeSemanticProvider, redact
from wire.store import WireStore

BACKFILL = Path("data/wire_backfill.json")
OUT = Path("data/wire_independent_review.json")


def review_one(provider, evidence: str, proposed: dict,
               identity: dict | None = None) -> dict:
    """One reviewer call. Never raises: a dead provider is an ABSTAIN."""
    t0 = time.time()
    prompt = ir.build_prompt(evidence, proposed, identity)
    try:
        payload, usage = provider._call(
            prompt, system=ir.SYSTEM, schema=ir.RESPONSE_SCHEMA,
            tool="record_review")
    except Exception as e:
        return {"verdict": ir.ABSTAIN,
                "disagreement_summary": f"provider unavailable: {redact(e)[:140]}",
                "latency_ms": int((time.time() - t0) * 1000), "cost_usd": 0.0}

    out = dict(payload)
    ti = usage.get("input_tokens", 0)
    to = usage.get("output_tokens", 0)
    out["tokens_in"], out["tokens_out"] = ti, to
    out["cost_usd"] = ti * 3e-6 + to * 15e-6
    out["latency_ms"] = int((time.time() - t0) * 1000)
    out["schema_version"] = ir.REVIEW_SCHEMA_VERSION
    out["prompt_version"] = ir.REVIEW_PROMPT_VERSION
    out["model"] = provider.model
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--cap", type=float, default=5.0)
    args = ap.parse_args()

    bf = json.loads(BACKFILL.read_text())
    results = bf.get("results", [])

    # The stored rows are the authority on what the evidence says.
    store = WireStore()
    store_text, ident = {}, {}
    for row in store.evidence():
        store_text[row["candidate_id"]] = row["evidence_text"]
        ident[row["candidate_id"]] = {"player_id": row["player_id"] or "",
                                      "team": row["team"] or ""}
    reg = pl.load()
    registry_version = reg.version or "unknown"
    print(f"  {len(results)} generated assessment(s) to review")

    provider = ClaudeSemanticProvider()
    if not provider.available():
        print("  no usable ANTHROPIC_API_KEY; nothing reviewed")
        return 1
    if not args.run:
        print("  --run not given; nothing called")
        return 0

    spend = 0.0
    reviewed = []
    for i, r in enumerate(results, 1):
        a = r.get("assessment") or {}
        c = r.get("candidate") or {}
        if spend >= args.cap:
            print(f"  cap ${args.cap:.2f} reached after {i - 1}")
            break
        # The evidence the GENERATOR read, taken from the stored row, and
        # hashed on both sides. Reading the results file's copy is what got
        # the reviewer 220 characters last time.
        evidence = store_text.get(c.get("candidate_id", "")) \
            or c.get("evidence_text") or ""
        integ = eint.check(
            evidence,
            generator_input=(r.get("evidence_integrity") or {}).get(
                "generator_input_evidence_sha256") and evidence or evidence,
            reviewer_input=evidence)
        if not integ["evidence_complete"]:
            reviewed.append({"candidate_id": c.get("candidate_id", ""),
                             "player": c.get("player_name", ""),
                             "team": c.get("team", ""),
                             "position": c.get("position", ""),
                             "evidence_integrity": integ,
                             "review": {"verdict": ir.HUMAN_REVIEW,
                                        "disagreement_summary":
                                            "evidence is incomplete: "
                                            + "; ".join(integ["incompleteness_reasons"]),
                                        "cost_usd": 0.0, "latency_ms": 0}})
            continue
        proposed = {
            "player_name": c.get("player_name") or a.get("claim_subject_player_name", ""),
            "team": c.get("team", ""), "position": c.get("position", ""),
            "fantasy_mechanism": a.get("fantasy_mechanism", ""),
            "direction": a.get("direction", ""),
            "impact_strength": a.get("impact_strength", ""),
            "impact_horizon": a.get("impact_horizon", ""),
            "fantasy_commentary": a.get("fantasy_commentary", ""),
        }
        # Identity is settled here, in code, from the registry alone -- not
        # from the candidate row and never by the model. A model asked to
        # judge a roster answers from training data, and training data is not
        # the 2026 season: the previous run rejected a real development
        # because it believed Kyler Murray was not a Viking.
        cid_ = c.get("candidate_id", "")
        pid = ident.get(cid_, {}).get("player_id", "")
        who = reg.by_id.get(pid)
        if who is None:
            identity_check = ("NOT IN REGISTRY -- routed to human review "
                              "without a model call")
            reviewed.append({
                "candidate_id": cid_, "player": c.get("player_name", ""),
                "team": c.get("team", ""), "position": c.get("position", ""),
                "evidence_integrity": integ,
                "supplied_identity": {"player_id": pid,
                                      "registry_version": registry_version,
                                      "registry_check": identity_check},
                "review": {"verdict": ir.HUMAN_REVIEW,
                           "disagreement_summary": identity_check,
                           "cost_usd": 0.0, "latency_ms": 0,
                           "blocks_automatic_publication": True,
                           "enforcement_reasons": [identity_check]}})
            continue
        identity = {
            # Every field from wire_players, so the block cannot disagree
            # with itself the way a candidate-sourced one can.
            "player_id": who.player_id,
            "player_name": who.full_name,
            "team": who.team,
            "position": who.position,
            "registry_version": registry_version,
            "registry_check": "VERIFIED against wire_players before the call",
            "source_team": c.get("team", ""),
            "canonical_url": r.get("source_url", ""),
        }
        v = review_one(provider, evidence, proposed, identity)
        # Deterministic rules over the top of the model's opinion.
        v = ir.enforce(v, identity_resolved=True)
        spend += v.get("cost_usd", 0.0)
        reviewed.append({"candidate_id": c.get("candidate_id", ""),
                         "evidence_integrity": integ,
                         "supplied_identity": identity,
                         "player": proposed["player_name"],
                         "team": proposed["team"],
                         "position": proposed["position"],
                         "generator": proposed,
                         "generator_decision": a.get("decision", ""),
                         "validator": a.get("validation_status")
                         or ("ABSTAIN" if a.get("abstention_reason") else "PASS"),
                         "review": v})
        if i % 10 == 0:
            print(f"    {i}/{len(results)}  ${spend:.2f}  "
                  + ", ".join(f"{k} {n}" for k, n in
                              Counter(x["review"]["verdict"]
                                      for x in reviewed).most_common()))

    counts = Counter(x["review"]["verdict"] for x in reviewed)
    print(f"\n  reviewed {len(reviewed)} at ${spend:.4f}")
    for k in ir.VERDICTS:
        print(f"    {k:<14}{counts.get(k, 0)}")

    OUT.write_text(json.dumps({
        "note": "DARK LAUNCH. Advisory only; publishes nothing.",
        "schema_version": ir.REVIEW_SCHEMA_VERSION,
        "prompt_version": ir.REVIEW_PROMPT_VERSION,
        "model": provider.model,
        "window": bf.get("window", {}),
        "reviewed": len(reviewed),
        "cost_usd": round(spend, 4),
        "verdicts": {k: counts.get(k, 0) for k in ir.VERDICTS},
        "registry_version": registry_version,
        "auto_approvals_blocked_by_claim_subject": sum(
            1 for x in reviewed
            if any("claim-subject conflict: auto-approval blocked" in y
                   for y in (x["review"].get("enforcement_reasons") or []))),
        "stale_roster_objections": sum(
            1 for x in reviewed
            if any("stale roster objection" in y
                   for y in (x["review"].get("enforcement_reasons") or []))),
        "identity_unresolved": sum(
            1 for x in reviewed
            if any("registry identity unresolved" in y
                   for y in (x["review"].get("enforcement_reasons") or []))),
        "evidence_integrity_blocked": sum(
            1 for x in reviewed
            if (x.get("evidence_integrity") or {}).get("blocks_automatic_approval")),
        "items": reviewed,
    }, indent=1) + "\n")
    print(f"  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
