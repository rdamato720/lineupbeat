#!/usr/bin/env python3
"""Run every provider over the locked corpus and grade them.

    python3 scripts/wire_semantic_eval.py --providers rules
    python3 scripts/wire_semantic_eval.py --providers rules,openai

Precision and recall are both reported, and that is the point. Grading only
the interpretations a layer chose to emit rewards suppression: a provider
that abstains on everything scores a perfect error rate and is useless. So
false suppression -- a GOLD item that should have been interpreted and was
not -- counts against a provider exactly as a wrong interpretation does.

Numerators and denominators are printed, not just percentages, because "0%"
over three items is not a measurement.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import players as pl
from wire import semantic as sem
from wire import semantic_validate as sv
from wire.providers import REGISTRY
from wire.providers.openai import redact as redact_openai

CORPUS = Path("data/wire_eval_corpus.json")
OUT = Path("data/wire_semantic_eval.json")

# Promotion thresholds are declared before the provider is run.  They are
# deliberately round policy boundaries, not numbers fitted to one result.
MIN_ACCURACY = 0.95
MIN_PRECISION = 1.00
MIN_RECALL = 0.90
MAX_ABSTENTION_RATE = 0.15
ZERO_TOLERANCE_ERRORS = {
    "false_positive", "wrong_player", "wrong_direction", "wrong_unit",
    "unsupported_role", "wrong_classification", "forbidden_mechanism",
    "identity_refusal_bypassed",
}


def live_limit_errors(provider_names: list[str], item_count: int,
                      cap: float | None,
                      max_calls: int | None) -> list[str]:
    """Preflight live-provider limits before the first paid request."""
    if "openai" not in provider_names:
        return []
    errors = []
    if cap is None or not math.isfinite(cap) or cap <= 0:
        errors.append("OpenAI evaluation requires an explicit positive --cap")
    if max_calls is None or max_calls <= 0:
        errors.append(
            "OpenAI evaluation requires an explicit positive --max-calls")
    elif max_calls < item_count:
        errors.append(
            f"--max-calls {max_calls} cannot cover the selected "
            f"{item_count}-item corpus; 0 API calls made")
    return errors


def last(name: str) -> str:
    n = pl.norm(name or "").split()
    return n[-1] if n else ""


def grade(item: dict, a: sem.SemanticAssessment) -> dict:
    """One graded result against a hand-written expectation."""
    exp = item["expected"]
    g = {"id": item["id"], "errors": [], "correct": False,
         "decision": a.decision, "mechanism": a.fantasy_mechanism,
         "direction": a.direction,
         "subject": a.claim_subject_player_name or ""}
    if exp is None:
        g["graded"] = False
        return g
    g["graded"] = True

    want_dec = exp["decision"]
    # An unregistered player is not a required interpretation. Refusing him is
    # identity validation working, not failing, and counting it as a failure
    # also wrongly inflated the recall denominator to 14.
    if item.get("identity_outcome") == "CORRECT_REGISTRY_REFUSAL":
        g["identity_outcome"] = "CORRECT_REGISTRY_REFUSAL"
    # ABSTAIN on an item that should have been interpreted is a false
    # suppression, not a free pass.
    if want_dec == "INTERPRET":
        if a.decision != "INTERPRET":
            g["errors"].append("false_suppression")
        else:
            want_sub = exp.get("subject")
            alt = item.get("also_valid_subject") or ""
            chose_alt = alt and last(a.claim_subject_player_name) == last(alt)
            ok_subject = (not want_sub
                          or last(a.claim_subject_player_name) == last(want_sub)
                          or chose_alt)
            if not ok_subject:
                g["errors"].append("wrong_player")
            # A passage with two valid subjects has two valid answers. Grading
            # the alternate subject against the primary subject's mechanism
            # marked a correct reading wrong: Claude picked Brian Thomas and
            # was scored against Parker Washington's absence.
            exp_mech = exp.get("mechanism")
            exp_dir = exp.get("direction")
            if chose_alt:
                alt_exp = item.get("also_valid_expected") or {}
                exp_mech = alt_exp.get("mechanism")
                exp_dir = alt_exp.get("direction")
            if exp_mech and a.fantasy_mechanism != exp_mech:
                g["errors"].append("unsupported_role")
            if exp_dir and a.direction != exp_dir:
                g["errors"].append("wrong_direction")
    else:
        if item.get("identity_outcome") == "CORRECT_REGISTRY_REFUSAL":
            if a.decision != "ABSTAIN":
                g["errors"].append("identity_refusal_bypassed")
        elif a.decision != want_dec:
            if a.decision == "INTERPRET":
                g["errors"].append("false_positive")
            elif a.decision == "ABSTAIN":
                # An abstention is safe, but it is not the requested semantic
                # answer and may not be counted as correct.  This is the
                # non-interpretation half of "do not reward abstaining".
                g["errors"].append("unnecessary_abstention")
            else:
                g["errors"].append("wrong_decision")
        if exp.get("classification") and \
                a.evidence_classification != exp["classification"]:
            g["errors"].append("wrong_classification")

    if item.get("forbidden_mechanism") and \
            a.fantasy_mechanism == item["forbidden_mechanism"]:
        g["errors"].append("forbidden_mechanism")
    unit = {"FIRST_TEAM_REPS", "SECOND_TEAM_REPS", "THIRD_TEAM_REPS"}
    if (exp.get("mechanism") in unit and a.fantasy_mechanism in unit
            and a.fantasy_mechanism != exp["mechanism"]):
        g["errors"].append("wrong_unit")
    g["correct"] = not g["errors"]
    return g


def promotion_gate(summary: dict, graded: list[dict]) -> dict:
    """Apply the predeclared OpenAI promotion policy to one locked run."""
    accuracy = summary["correct_num"] / max(1, summary["correct_den"])
    precision = summary["precision_num"] / max(1, summary["precision_den"])
    recall = summary["recall_num"] / max(1, summary["recall_den"])
    abstention = summary["abstain_num"] / max(1, summary["abstain_den"])
    errors = Counter(e for g in graded for e in g.get("errors", []))
    zero_tolerance = {e: errors[e] for e in ZERO_TOLERANCE_ERRORS if errors[e]}
    unexpected_validation = [
        {"id": g["id"], "failures": g.get("validation_failures", [])}
        for g in graded
        if g.get("validation_failures")
        and g.get("identity_outcome") != "CORRECT_REGISTRY_REFUSAL"
    ]
    checks = {
        "provider_available": bool(summary.get("available")),
        "locked_corpus_complete": (
            summary["correct_den"] == summary["locked_gold_items"]),
        "accuracy_at_least_95_percent": accuracy >= MIN_ACCURACY,
        "precision_100_percent": precision >= MIN_PRECISION,
        "recall_at_least_90_percent": recall >= MIN_RECALL,
        "abstention_at_most_15_percent": abstention <= MAX_ABSTENTION_RATE,
        "zero_tolerance_errors_zero": not zero_tolerance,
        "unexpected_validation_failures_zero": not unexpected_validation,
    }
    if "cap_usd" in summary:
        checks["observed_spend_within_cap"] = (
            summary.get("cost_usd_total", 0) <= summary["cap_usd"])
        checks["call_limit_not_exceeded"] = (
            summary.get("calls", 0) <= summary.get("max_calls", 0))
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "zero_tolerance_errors": zero_tolerance,
        "unexpected_validation_failures": unexpected_validation,
        "thresholds": {
            "accuracy": MIN_ACCURACY, "precision": MIN_PRECISION,
            "recall": MIN_RECALL, "max_abstention_rate": MAX_ABSTENTION_RATE,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="rules")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--include-unlabelled", action="store_true")
    ap.add_argument("--cap", type=float,
                    help="required observed-spend ceiling for OpenAI")
    ap.add_argument("--max-calls", type=int,
                    help="required hard request-count ceiling for OpenAI")
    args = ap.parse_args()

    corpus = json.loads(CORPUS.read_text())
    items = [x for x in corpus["items"]
             if x["kind"] == "GOLD" or args.include_unlabelled]
    if args.limit:
        items = items[:args.limit]
    provider_names = [x.strip() for x in args.providers.split(",") if x.strip()]
    limit_errors = live_limit_errors(
        provider_names, len(items), args.cap, args.max_calls)
    if limit_errors:
        for error in limit_errors:
            print(f"  {error}", file=sys.stderr)
        return 2
    reg = pl.load()

    report = {"corpus": str(CORPUS), "graded_items": 0, "providers": {}}
    gate_failed = False
    for pname in provider_names:
        cls = REGISTRY.get(pname)
        if cls is None:
            print(f"  unknown provider {pname!r}")
            continue
        prov = cls()
        avail = getattr(prov, "available", lambda: True)()
        results, graded = [], []
        calls, observed_spend = 0, 0.0
        stopped_at_cap = False
        provider_error = ""
        if pname == "openai" and avail:
            try:
                prov.authenticate()
            except Exception as exc:
                avail = False
                provider_error = redact_openai(
                    f"{type(exc).__name__}: {exc}")[:400]
        t0 = time.time()
        selected_items = [] if pname == "openai" and not avail else items
        if pname == "openai" and not avail and not provider_error:
            provider_error = "OpenAI unavailable; key missing or invalid"
        for item in selected_items:
            if pname == "openai" and calls >= args.max_calls:
                # The preflight normally makes this unreachable. Keep the
                # boundary here so later item-selection changes cannot spend
                # an extra request.
                break
            if pname == "openai" and observed_spend >= args.cap:
                stopped_at_cap = True
                break
            if pname == "openai":
                calls += 1
            try:
                a = prov.evaluate(item["text"], item["metadata"],
                                  item["players"])
            except Exception as exc:
                if pname != "openai":
                    raise
                provider_error = redact_openai(
                    f"{type(exc).__name__}: {exc}")[:400]
                break
            if pname == "openai":
                observed_spend += a.cost_usd
            a = sv.enforce(a, item["text"], item["players"], reg,
                           item["metadata"])
            g = grade(item, a)
            g["validation_failures"] = a.validation_failures
            g["abstention_reason"] = a.abstention_reason
            g["latency_ms"] = a.latency_ms
            g["cost_usd"] = a.cost_usd
            results.append({"item": item["id"], "assessment": a.to_dict()})
            graded.append(g)
        wall = time.time() - t0

        gd = [g for g in graded if g["graded"]]
        n = len(gd)
        err = Counter(e for g in gd for e in g["errors"])
        interp = [g for g in gd if g["decision"] == "INTERPRET"]
        abst = [g for g in graded if g["decision"] == "ABSTAIN"]
        nofi = [g for g in graded if g["decision"] == "NO_FANTASY_IMPACT"]
        should_interp = [g for g in gd
                         if next(x for x in items if x["id"] == g["id"])
                         ["expected"]["decision"] == "INTERPRET"]
        got_right = [g for g in should_interp if g["correct"]]
        lat = [g["latency_ms"] for g in graded] or [0]
        cost = sum(g["cost_usd"] for g in graded)

        prec_den = len(interp)
        prec_num = sum(1 for g in interp if g["correct"])
        rec_den = len(should_interp)
        rec_num = len(got_right)

        summary = {
            "available": bool(avail),
            "model": prov.model,
            "graded": n,
            "locked_gold_items": corpus["gold_items"],
            "correct": sum(1 for g in gd if g["correct"]),
            "correct_num": sum(1 for g in gd if g["correct"]),
            "correct_den": n,
            "precision": f"{prec_num}/{prec_den}",
            "precision_num": prec_num,
            "precision_den": prec_den,
            "recall": f"{rec_num}/{rec_den}",
            "recall_num": rec_num,
            "recall_den": rec_den,
            "errors": dict(err),
            "abstain": f"{len(abst)}/{len(graded)}",
            "abstain_num": len(abst),
            "abstain_den": len(graded),
            "no_fantasy_impact": f"{len(nofi)}/{len(graded)}",
            "median_latency_ms": int(statistics.median(lat)),
            "p95_latency_ms": int(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)]),
            "cost_usd_total": round(cost, 4),
            "cost_per_1000_segments": round(cost / max(1, len(graded)) * 1000, 2),
            "wall_seconds": round(wall, 1),
        }
        if pname == "openai":
            summary.update({
                "calls": calls,
                "max_calls": args.max_calls,
                "cap_usd": args.cap,
                "stopped_at_cap": stopped_at_cap,
                "selected_items": len(items),
                "completed_items": len(graded),
                "provider_error": provider_error,
            })
            summary["promotion_gate"] = promotion_gate(summary, graded)
            gate_failed = gate_failed or not summary["promotion_gate"]["passed"]
        report["providers"][pname] = {"summary": summary, "graded": graded,
                                      "results": results}
        report["graded_items"] = n

        print(f"\n  === {pname} ({prov.model}) "
              f"{'' if avail else '-- NOT AVAILABLE, no key'}")
        print(f"    graded            {summary['correct']}/{n} correct")
        print(f"    precision         {summary['precision']}  "
              f"(interpretations that were right)")
        print(f"    recall            {summary['recall']}  "
              f"(items needing an interpretation that got a right one)")
        print(f"    abstain           {summary['abstain']}")
        print(f"    no-fantasy-impact {summary['no_fantasy_impact']}")
        for k, v in sorted(err.items(), key=lambda x: -x[1]):
            print(f"      {k:<24}{v}/{n}")
        print(f"    latency median {summary['median_latency_ms']}ms  "
              f"p95 {summary['p95_latency_ms']}ms")
        print(f"    cost per 1000 segments ${summary['cost_per_1000_segments']}")
        if pname == "openai":
            print(f"    calls              {calls}/{args.max_calls}")
            print(f"    observed spend     ${cost:.4f}/${args.cap:.2f} cap")
            if stopped_at_cap:
                print("    cost cap reached; no further request was sent")
            if provider_error:
                print(f"    provider failure   {provider_error}")
        if "promotion_gate" in summary:
            gate = summary["promotion_gate"]
            print(f"    promotion gate    "
                  f"{'PASS' if gate['passed'] else 'FAIL'}")
            for name, passed in gate["checks"].items():
                print(f"      {'PASS' if passed else 'FAIL':<5} {name}")

    OUT.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(f"\n  wrote {OUT}")
    return 5 if gate_failed else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
