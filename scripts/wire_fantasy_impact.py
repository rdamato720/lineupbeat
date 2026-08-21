#!/usr/bin/env python3
"""Generate Lineup Beat fantasy commentary from approved Wire evidence.

    python3 scripts/wire_fantasy_impact.py --generate
    python3 scripts/wire_fantasy_impact.py --generate --include-pending
    python3 scripts/wire_fantasy_impact.py --show --limit 10

Every record starts PENDING and cannot publish itself. The reporter's
evidence and our reading of it are stored apart and approved apart, because
one can be right while the other is wrong.

Generation is deterministic: rules and templates, no model. That is not a
placeholder. A template can only say what it is handed, which is what makes
"never invent a fact" a property the validator can check rather than a hope.
If this is ever swapped for a model, the record already carries `generator`
and `prompt_version` and the same validate() must still pass.

This script never reads or writes projection files.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import fantasy as fz
from wire import semantic as sem
from wire import semantic_validate as sv
from wire.providers.claude import ClaudeSemanticProvider
from wire import players as pl
from wire.store import WireStore

# Only these classes may support an interpretation. Analysis is a writer's
# opinion and UNCERTAIN is the classifier saying it does not know; neither is
# a fact to build on.
SUPPORTING = {"FIRSTHAND_OBSERVATION", "DIRECT_QUOTATION"}


def eligible_rows(store, include_pending: bool) -> list:
    want = {"APPROVED"} | ({"PENDING"} if include_pending else set())
    out = []
    for r in store.evidence():
        if r["review_status"] not in want:
            continue
        if r["evidence_class"] not in SUPPORTING:
            continue
        if r["exclusion_reason"]:
            continue          # OL, defence, non-fantasy position
        if r["duplicate_of"]:
            continue          # a republished claim is not a second report
        if not r["player_id"]:
            continue          # identity must be exact
        out.append(dict(r))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--include-pending", action="store_true",
                    help="dark launch: treat PENDING evidence as supporting")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--interpreter", default="claude",
                    choices=["claude", "rules"],
                    help="rules is the measured baseline and may not produce "
                         "publishable commentary")
    args = ap.parse_args()

    store = WireStore()
    reg = pl.load()

    if args.show:
        rows = store.impacts()
        print(f"  {len(rows)} fantasy-impact record(s)")
        for r in rows[:args.limit]:
            print(f"\n  {r['player_name']} ({r['team']} {r['position']})  "
                  f"{r['fantasy_impact']}/{r['impact_strength']}/"
                  f"{r['impact_horizon']}  {r['role_signal']}")
            print(f"    action {r['projection_action']}  status "
                  f"{r['review_status']}  sources {r['source_count']} "
                  f"({r['independent_source_count']} independent)")
            print(f"    {r['lineupbeat_commentary']}")
        return 0

    # Claude is the interpreter. The rules engine keeps segmentation, name
    # detection, registry validation, relay detection, ownership, authority,
    # deduplication and the deterministic response check -- everything except
    # deciding what a passage means. When Claude is unavailable the evidence
    # stays pending; it is never quietly reinterpreted by the baseline,
    # because the baseline is the thing that produced the errors under review.
    if args.interpreter == "claude":
        prov = ClaudeSemanticProvider()
        if not prov.available():
            print("  Claude is unavailable (no ANTHROPIC_API_KEY).")
            print("  Evidence is retained and left PENDING for manual review.")
            print("  The rules engine will NOT generate commentary in its place.")
            return 4

    rows = eligible_rows(store, args.include_pending)
    by_player: dict = defaultdict(list)
    for r in rows:
        by_player[r["player_id"]].append(r)

    made = new = refused = 0
    reasons: dict = {}
    suppressed: list = []
    for pid, group in sorted(by_player.items()):
        imp = fz.build(group, reg, registry_version=reg.version)
        if isinstance(imp, dict) and imp.get("suppressed"):
            suppressed.append(imp)
            reasons[imp["reason"]] = reasons.get(imp["reason"], 0) + 1
            continue
        if imp is None:
            refused += 1
            continue
        problems = fz.validate(imp, group, reg)
        if problems:
            refused += 1
            for p in problems:
                key = p.split("(")[0].strip()
                reasons[key] = reasons.get(key, 0) + 1
            continue
        made += 1
        if store.upsert_impact(imp.to_record()):
            new += 1

    Path("data/wire_fantasy_suppressed.json").write_text(
        json.dumps({"suppressed": len(suppressed), "items": suppressed},
                   indent=1) + "\n")
    invalidated = store.invalidate_impacts_without_evidence()
    print(f"  {len(rows)} supporting evidence row(s) across "
          f"{len(by_player)} player(s)")
    print(f"  {made} impact record(s) ({new} new, {made - new} updated)")
    total_considered = made + len(suppressed)
    print(f"  {len(suppressed)} suppressed as NO_FANTASY_IMPACT "
          f"({100 * len(suppressed) / max(1, total_considered):.0f}% of "
          f"{total_considered} considered)")
    print(f"  {refused} refused by validation")
    for k, v in sorted(reasons.items(), key=lambda x: -x[1])[:6]:
        print(f"      {v:>4}  {k}")
    if invalidated:
        print(f"  {invalidated} invalidated (supporting evidence all gone)")
    print("  all PENDING; no commentary can publish without separate approval")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
