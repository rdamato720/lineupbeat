#!/usr/bin/env python3
"""Run every provider over the locked corpus and grade them.

    python3 scripts/wire_semantic_eval.py --providers rules
    python3 scripts/wire_semantic_eval.py --providers rules,claude,openai

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

CORPUS = Path("data/wire_eval_corpus.json")
OUT = Path("data/wire_semantic_eval.json")


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
        if a.decision == "INTERPRET":
            g["errors"].append("false_positive")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="rules")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--include-unlabelled", action="store_true")
    args = ap.parse_args()

    corpus = json.loads(CORPUS.read_text())
    items = [x for x in corpus["items"]
             if x["kind"] == "GOLD" or args.include_unlabelled]
    if args.limit:
        items = items[:args.limit]
    reg = pl.load()

    report = {"corpus": str(CORPUS), "graded_items": 0, "providers": {}}
    for pname in args.providers.split(","):
        pname = pname.strip()
        cls = REGISTRY.get(pname)
        if cls is None:
            print(f"  unknown provider {pname!r}")
            continue
        prov = cls()
        avail = getattr(prov, "available", lambda: True)()
        results, graded = [], []
        t0 = time.time()
        for item in items:
            a = prov.evaluate(item["text"], item["metadata"], item["players"])
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
            "correct": sum(1 for g in gd if g["correct"]),
            "precision": f"{prec_num}/{prec_den}",
            "recall": f"{rec_num}/{rec_den}",
            "errors": dict(err),
            "abstain": f"{len(abst)}/{len(graded)}",
            "no_fantasy_impact": f"{len(nofi)}/{len(graded)}",
            "median_latency_ms": int(statistics.median(lat)),
            "p95_latency_ms": int(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)]),
            "cost_usd_total": round(cost, 4),
            "cost_per_1000_segments": round(cost / max(1, len(graded)) * 1000, 2),
            "wall_seconds": round(wall, 1),
        }
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

    OUT.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(f"\n  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
