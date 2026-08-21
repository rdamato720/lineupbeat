#!/usr/bin/env python3
"""Build a deterministic, stratified review queue.

    python3 scripts/wire_review_queue.py --build
    python3 scripts/wire_review_queue.py --show --stratum FIRSTHAND_OBSERVATION

Reviewing 6,000 candidates is not review, it is a second job. What decides
whether this pipeline is trustworthy is a sample that covers every way it can
be wrong: each classification, every author it proposes to trust, and each
exclusion rule that is supposed to be holding something back.

The sample is deterministic -- ordered by candidate id, no randomness -- so
the same database produces the same queue and a disagreement about a verdict
can be traced to a specific row rather than re-sampled away. The rule and the
ids are written to data/wire_review_queue.json so the review can be repeated.

Nothing here approves, publishes or changes a candidate. It selects.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import registry as artreg
from wire import si
from wire.store import WireStore

OUT = Path("data/wire_review_queue.json")

PER_CLASS = 20
PER_AUTHOR = 20
PER_ADAPTER = 20

CLASSES = ["FIRSTHAND_OBSERVATION", "DIRECT_QUOTATION",
           "ANALYSIS_OR_OPINION", "UNCERTAIN"]


def spread(rows: list, want: int, keys) -> list:
    """Take `want` rows, spreading across the given keys before topping up.

    A stratum of twenty that is twenty paragraphs of one article by one
    author about one player tests one thing twenty times. This takes one row
    per distinct key in id order, then a second from each, and so on.
    """
    buckets: dict = defaultdict(list)
    for r in sorted(rows, key=lambda x: x["candidate_id"]):
        buckets[tuple(k(r) for k in keys)].append(r)
    picked, i = [], 0
    while len(picked) < want:
        added = False
        for key in sorted(buckets):
            if i < len(buckets[key]):
                picked.append(buckets[key][i])
                added = True
                if len(picked) >= want:
                    break
        if not added:
            break
        i += 1
    return picked


def build(store) -> dict:
    rows = [dict(r) for r in store.evidence()]
    si_rows = [r for r in rows if r["source_id"].startswith("si_")]
    authors = si.load_authors()

    approved = {(t, n)
                for t, e in authors.get("teams", {}).items()
                for n, a in e["authors"].items()
                if a["classification"] == si.FIRSTHAND_APPROVED}

    strata: dict = {}
    for klass in CLASSES:
        pool = [r for r in si_rows if r["evidence_class"] == klass]
        strata[f"si_class:{klass}"] = spread(
            pool, PER_CLASS,
            [lambda r: r["source_id"], lambda r: r["source_author_or_channel"],
             lambda r: r["position"]])

    # Every candidate attributed to an author we propose to trust. These are
    # the rows where a wrong classification would do the most damage.
    for team, name in sorted(approved):
        pool = [r for r in si_rows
                if r["source_author_or_channel"] == name
                and r["team"] == team]
        if pool:
            strata[f"si_author:{team}:{name}"] = spread(
                pool, PER_AUTHOR,
                [lambda r: r["evidence_class"], lambda r: r["player_name"]])

    # The exclusion rules, sampled from what they actually caught. An
    # exclusion nobody reads is a rule nobody has tested.
    ex = [dict(r) for r in store.exclusions()]
    by_reason: dict = defaultdict(list)
    for e in ex:
        key = e["reason"].split("(")[0].strip()
        if key.startswith("canonical url is a"):
            key = "wrong team"
        elif key.startswith("author") and "not in the registry" in e["reason"]:
            key = "unknown author"
        elif key.startswith("author is classified"):
            key = "analysis-only author"
        by_reason[key].append(e)
    for reason, items in by_reason.items():
        strata[f"exclusion:{reason}"] = sorted(
            items, key=lambda x: x["canonical_url"])[:PER_CLASS]

    # Local adapters, one stratum each.
    srcs = {s.source_id: s for s in artreg.load()}
    local: dict = defaultdict(list)
    for r in rows:
        s = srcs.get(r["source_id"])
        if s and s.adapter and s.adapter != artreg.SI_TEAM_PAGE:
            local[s.adapter].append(r)
    for adapter, pool in local.items():
        strata[f"local_adapter:{adapter}"] = spread(
            pool, PER_ADAPTER,
            [lambda r: r["source_id"], lambda r: r["evidence_class"]])

    return {
        "generated_from": "wire_evidence + wire_exclusions",
        "sampling_rule": {
            "deterministic": True,
            "order": "candidate_id ascending; exclusions by canonical_url",
            "per_classification": PER_CLASS,
            "per_approved_author": PER_AUTHOR,
            "per_local_adapter": PER_ADAPTER,
            "spread_keys": ["source_id", "author", "position",
                            "evidence_class", "player_name"],
            "note": "round-robin across the spread keys before topping up, so "
                    "a stratum is not one article sampled twenty times",
        },
        "strata": {k: {"count": len(v),
                       "candidate_ids": [x.get("candidate_id")
                                         or x.get("canonical_url") for x in v]}
                   for k, v in sorted(strata.items())},
        "total": sum(len(v) for v in strata.values()),
    }, strata


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--show")
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args()
    store = WireStore()
    manifest, strata = build(store)

    if args.show:
        rows = strata.get(args.show)
        if rows is None:
            print(f"  no stratum {args.show!r}. available:")
            for k in sorted(strata):
                print(f"    {k}  ({len(strata[k])})")
            return 1
        for r in rows[:args.limit]:
            if "evidence_class" in r:
                print(f"\n  [{r['evidence_class']}] {r['classification_confidence']:.2f}  "
                      f"{r['player_name']} ({r['team']} {r['position']})")
                print(f"    {r['source_id']}  {r['source_author_or_channel']}")
                print(f"    {r['evidence_text'][:230]}")
                print(f"    why: {r['classification_reasons']}")
            else:
                print(f"\n  EXCLUDED  {r['reason']}")
                print(f"    {r['headline'][:80]}")
                print(f"    {r['canonical_url'][:100]}")
        return 0

    if args.build:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"  {manifest['total']} candidates in {len(manifest['strata'])} strata")
    for k, v in manifest["strata"].items():
        print(f"    {v['count']:>4}  {k}")
    if args.build:
        print(f"  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
