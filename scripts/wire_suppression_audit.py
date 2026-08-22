#!/usr/bin/env python3
"""A deterministic stratified sample of rejected fantasy-position candidates.

    python3 scripts/wire_suppression_audit.py --from ... --to ... --out data/wire_suppression_audit.json

The reconciliation says how many were stopped and where. It cannot say
whether any of them should have been, and a rejection rate is not evidence of
a correct rejection. This pulls the rows out so a person can read them.

Selection is deterministic -- sorted by candidate id, never sampled at random
-- so the same corpus produces the same sample and a fix can be measured
against the same rows it was written for.

Every candidate whose text mentions an actionable development is included in
full regardless of quota: those are the rows where a false suppression
actually costs a reader something.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire.store import WireStore

FANTASY = {"QB", "RB", "WR", "TE"}
QUOTA = {"QB": 20, "RB": 30, "WR": 30, "TE": 20}

# The developments a fantasy manager acts on. A rejected row that mentions one
# of these is worth a human's time whatever the quota says.
ACTIONABLE = re.compile(
    r"\b(injur\w+|hurt|sidelined|did not (?:practice|participate)|"
    r"non-?participant|limited|missed practice|absent|返|return\w*|"
    r"back (?:at|to) practice|first[- ]team|1s\b|starter|starting|start\w*|"
    r"route\w*|target\w*|carr\w+|touches|snap\w*|red[- ]zone|goal[- ]line|"
    r"depth chart|promot\w+|demot\w+|released|waived|signed|activated|"
    r"designated|reps\b|PUP|IR\b|questionable|doubtful|ruled out)\b", re.I)


def parse(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", required=True)
    ap.add_argument("--to", dest="hi", required=True)
    ap.add_argument("--recon", default="data/wire_backfill_reconcile.json")
    ap.add_argument("--out", default="data/wire_suppression_audit.json")
    args = ap.parse_args()
    lo, hi = parse(args.lo), parse(args.hi)

    recon = json.loads(Path(args.recon).read_text())
    assigned = recon["assignment"]

    store = WireStore()
    arts = {}
    for a in store.conn.execute(
            "SELECT canonical_url, published_at, source_id, headline, author "
            "FROM wire_source_items"):
        arts[a["canonical_url"]] = dict(a)

    rows = []
    for r in store.evidence():
        if r["review_status"] != "PENDING":
            continue
        cat = assigned.get(r["candidate_id"])
        if cat is None or cat == "sent to Claude":
            continue
        pos = (r["position"] or "").upper()
        if pos not in FANTASY:
            continue
        rows.append((dict(r), cat))

    rows.sort(key=lambda t: t[0]["candidate_id"])
    print(f"  rejected candidates at a fantasy position: {len(rows)}")
    print("  by category:", Counter(c for _, c in rows).most_common())

    picked, seen = [], set()

    def take(r, cat, why):
        if r["candidate_id"] in seen:
            return
        seen.add(r["candidate_id"])
        a = arts.get(r["source_url"], {})
        picked.append({
            "candidate_id": r["candidate_id"],
            "selected_because": why,
            "player": r["player_name"], "team": r["team"],
            "position": (r["position"] or "").upper(),
            "source_id": r["source_id"],
            "reporter": r["source_author_or_channel"] or a.get("author") or "",
            "publication": a.get("source_id", ""),
            "published_at": a.get("published_at", r["published_at"]),
            "url": r["source_url"],
            "evidence_class": r["evidence_class"],
            "classification_reasons": r["classification_reasons"],
            "rejection_stage": cat,
            "rejection_reason": (r["exclusion_reason"]
                                 or str(r["classification_reasons"]) or cat),
            "evidence_text": r["evidence_text"],
            "mentions_actionable": bool(ACTIONABLE.search(r["evidence_text"] or "")),
            "human_agrees": None,
            "human_note": "",
        })

    # 1. Everything actionable, whatever the quota.
    for r, cat in rows:
        if ACTIONABLE.search(r["evidence_text"] or ""):
            take(r, cat, "mentions an actionable development")
    print(f"  actionable rows taken in full: {len(picked)}")

    # 2. Then fill the per-position quota, spreading across reasons.
    by_pos = defaultdict(lambda: defaultdict(list))
    for r, cat in rows:
        by_pos[(r["position"] or "").upper()][cat].append((r, cat))
    for pos, want in QUOTA.items():
        have = sum(1 for p in picked if p["position"] == pos)
        cats = sorted(by_pos[pos])
        i = 0
        while have < want and cats:
            cat = cats[i % len(cats)]
            pool = by_pos[pos][cat]
            got = False
            while pool:
                r, c = pool.pop(0)
                if r["candidate_id"] not in seen:
                    take(r, c, f"quota fill, {pos}, {cat}")
                    have += 1; got = True; break
            if not got:
                cats.remove(cat)
                i = 0
                continue
            i += 1

    print(f"\n  sample: {len(picked)}")
    print("  by position:", Counter(p["position"] for p in picked).most_common())
    print("  by rejection stage:")
    for k, v in Counter(p["rejection_stage"] for p in picked).most_common():
        print(f"    {v:>5}  {k}")
    print(f"  mentioning an actionable development: "
          f"{sum(1 for p in picked if p['mentions_actionable'])}")

    Path(args.out).write_text(json.dumps(
        {"window": {"from": lo.isoformat(), "to": hi.isoformat()},
         "rejected_fantasy_candidates": len(rows),
         "sample_size": len(picked),
         "sample": picked}, indent=1) + "\n")
    print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
