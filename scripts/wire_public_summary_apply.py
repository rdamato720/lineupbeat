#!/usr/bin/env python3
"""Attach reviewer-approved public sentences to existing publications.

    python3 scripts/wire_public_summary_apply.py --check
    python3 scripts/wire_public_summary_apply.py --apply --actor ralph

The five sentences below are the reviewer's words, supplied in review and
recorded here verbatim. They are not generated, and this script will not
invent one for a publication that has none -- a card without an approved
summary fails the build rather than falling back to the passage.

Future summaries may be drafted by Claude, but they pass the same validator
and the same review before they reach a card.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import public_summary as ps
from wire.store import WireStore

APPROVED = {
    "Chris Blair": "Blair received most of Jahan Dotson's first-team "
                   "red-zone snaps during one joint practice.",
    "Mack Hollins": "Hollins has regularly participated in two-minute "
                    "drills with multiple Patriots offensive units.",
    "Makai Lemon": "Lemon was officially listed as a non-participant with a "
                   "hamstring issue but still completed some receiver drills.",
    "DeVonta Smith": "Smith returned to limited practice work after "
                     "previously sitting out 11-on-11 periods with a "
                     "hamstring issue.",
    "Sam LaPorta": "LaPorta missed one practice, and no reason for his "
                   "absence was reported.",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--actor", default="reviewer")
    args = ap.parse_args()

    store = WireStore()
    rows = list(store.conn.execute(
        "SELECT publication_id, payload FROM wire_publications"))
    changed = failures = 0

    for r in rows:
        pub = json.loads(r["payload"])
        who = pub.get("player_name", "")
        text = APPROVED.get(who)
        if not text:
            continue
        bad = ps.validate(text, who, pub.get("reporter_found", ""))
        mark = "ok  " if not bad else "FAIL"
        print(f"  [{mark}] {who:<16}{len(text):>4}ch  "
              + ("; ".join(bad) if bad else text[:64] + "..."))
        if bad:
            failures += 1
            continue
        if args.apply:
            pub["public_evidence_summary"] = text
            pub["public_evidence_summary_approved_by"] = args.actor
            pub["public_evidence_summary_approved_at"] = (
                datetime.now(timezone.utc).replace(microsecond=0).isoformat())
            # The passage stays exactly as it was. The summary is what the
            # card shows; the evidence is what the record keeps.
            store.conn.execute(
                "UPDATE wire_publications SET payload = ? "
                "WHERE publication_id = ?", (json.dumps(pub), r["publication_id"]))
            changed += 1

    if args.apply:
        store.conn.commit()
        n, wrote = store.export_publications()
        print(f"\n  {changed} publication(s) updated; wire_publications.json "
              f"{n} item(s){' written' if wrote else ' unchanged'}")
    if failures:
        print(f"\n  {failures} sentence(s) failed validation; nothing applied "
              f"for those")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
