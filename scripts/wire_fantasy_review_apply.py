#!/usr/bin/env python3
"""Apply reviewer decisions from the review page.

    python3 scripts/wire_fantasy_review_apply.py decisions.json --reviewer ralph

The original generated commentary is never overwritten. An edit is stored
alongside it, so what the generator said and what a person changed it to
remain separately visible -- which is the only way to tell later whether the
generator got better.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire.store import WireStore

# A quotation the model transcribed inexactly says nothing about whether its
# football reading was right, so it is recorded as inconclusive rather than
# counted as a substantive abstention.
INCONCLUSIVE = "INCONCLUSIVE_TECHNICAL"

ACTIONS = {"APPROVE", "APPROVE_WITH_EDIT", INCONCLUSIVE, "REJECT_UNSUPPORTED",
           "REJECT_OVERSTATED", "REJECT_NOT_FANTASY_RELEVANT",
           "REJECT_WRONG_HORIZON", "REJECT_WRONG_STRENGTH",
           "REJECT_WRONG_PLAYER", "REJECT_WRONG_DIRECTION",
           "REJECT_WRONG_UNIT", "REJECT_DUPLICATE", "REJECT"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("decisions")
    ap.add_argument("--reviewer", default="reviewer")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    payload = json.loads(Path(args.decisions).read_text())
    decisions = payload.get("decisions", payload)
    store = WireStore()
    store._fantasy_schema()
    cols = {r["name"] for r in
            store.conn.execute("PRAGMA table_info(wire_fantasy_impact)")}
    for extra in ("original_commentary", "edited_commentary", "reviewer",
                  "reviewer_action", "provider", "model", "schema_version",
                  "prompt_version"):
        if extra not in cols:
            store.conn.execute(
                f"ALTER TABLE wire_fantasy_impact ADD COLUMN {extra} TEXT")
    store.conn.commit()

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    applied = skipped = 0
    for fid, d in decisions.items():
        if fid.startswith("suppressed:"):
            skipped += 1
            continue
        act = d.get("action", "")
        if act not in ACTIONS:
            skipped += 1
            continue
        row = store.conn.execute(
            "SELECT lineupbeat_commentary, original_commentary FROM "
            "wire_fantasy_impact WHERE fantasy_impact_id = ?", (fid,)).fetchone()
        if not row:
            skipped += 1
            continue
        original = row["original_commentary"] or row["lineupbeat_commentary"]
        reason = d.get("reason", "") or (act if act.startswith("REJECT") else "")
        status = (INCONCLUSIVE if act == INCONCLUSIVE
                  else "APPROVED" if act.startswith("APPROVE")
                  else "REJECTED")
        edited = d.get("edited_text", "") if act == "APPROVE_WITH_EDIT" else ""
        if args.dry_run:
            print(f"  would set {fid[:12]} -> {status} ({act})")
            applied += 1
            continue
        store.conn.execute(
            "UPDATE wire_fantasy_impact SET review_status=?, reviewed_at=?, "
            "reviewer=?, reviewer_action=?, reviewer_note=?, "
            "original_commentary=?, edited_commentary=?, "
            "lineupbeat_commentary=?, updated_at=? "
            "WHERE fantasy_impact_id=?",
            (status, now, args.reviewer, act, reason, original, edited,
             edited or original, now, fid))
        applied += 1
    if not args.dry_run:
        store.conn.commit()
    print(f"  {applied} decision(s) applied, {skipped} skipped")
    print("  original generated commentary preserved in original_commentary")
    print("  nothing published; approval records a decision, not a publication")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
