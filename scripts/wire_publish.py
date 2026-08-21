#!/usr/bin/env python3
"""Publish reviewer-approved Wire cards. Nothing else, ever.

    python3 scripts/wire_publish.py --dry-run
    python3 scripts/wire_publish.py --publish --actor ralph

The only route from evidence to a reader. Three independent conditions must
all hold for a card to pass, and each is checked here rather than trusted
from upstream:

    a reviewer approved it by name, with wording they authored or accepted
    the readiness checks pass on that finished wording
    the record is not PENDING, HOLD, ABSTAIN or NO_FANTASY_IMPACT

Claude's output alone authorises nothing. A card whose only support is a
model assessment does not appear here, because no reviewer decision names it.
No projection is read or written by this script.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wire_publication_preview as PP
from wire.store import WireStore

PREVIEW = Path("data/wire_publication_preview.json")
NEVER_PUBLISH = {"PENDING", "HOLD", "ABSTAIN", "NO_FANTASY_IMPACT",
                 "INCONCLUSIVE_TECHNICAL", "HELD_EVIDENCE_CONFLICT"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--actor", default="reviewer")
    args = ap.parse_args()

    preview = json.loads(PREVIEW.read_text())
    cards = preview["cards"]
    store = WireStore()

    approved, refused = [], []
    for c in cards:
        act = c.get("reviewer_action", "")
        if act in NEVER_PUBLISH or not act.startswith("APPROVE"):
            refused.append((c["player"], f"reviewer action {act or 'none'}"))
            continue
        fails = PP.readiness_failures(c)
        if fails:
            refused.append((c["player"], "; ".join(fails)))
            continue
        approved.append(c)

    # Anything the reviewer held is named here too, so the refusal list is a
    # record rather than an absence.
    for h in preview.get("held_back", []):
        refused.append((h["player"], h["why"]))

    print(f"  {len(approved)} card(s) cleared to publish, "
          f"{len(refused)} refused")
    for c in approved:
        print(f"    PUBLISH  {c['player']:<20}{c['team']} {c['position']:<4}"
              f"{c['reader_label']}")
    for who, why in refused:
        print(f"    refuse   {who:<20}{why[:70]}")

    if args.dry_run or not args.publish:
        print("  dry run; nothing written")
        return 0

    ids = []
    for c in approved:
        payload = {
            "player_name": c["player"], "team": c["team"],
            "position": c["position"],
            "reader_label": c["reader_label"],
            "direction": c["direction"], "mechanism": c["mechanism"],
            "strength": c["strength"], "horizon": c["horizon"],
            "projection_action": c["projection_action"],
            "reporter_found": c["evidence"],
            "lineupbeat_impact": c["commentary"],
            "source": c["source"], "author": c["author"],
            "published_date": c["date"], "url": c["url"],
            "source_ownership": c["ownership"],
            "reviewer_action": c["reviewer_action"],
            "evidence_candidate_id": c["evidence_candidate_id"],
        }
        cid = c["evidence_candidate_id"] or c["player"].lower().replace(" ", "-")
        pub_id = store.publish(cid, payload,
                               fingerprint=f"wire:{cid}", actor=args.actor)
        ids.append(pub_id)

    n, changed = store.export_publications()
    print(f"\n  wire_publications.json: {n} item(s)"
          f"{' (written)' if changed else ' (unchanged)'}")
    for i in ids:
        print(f"    published id {i}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
