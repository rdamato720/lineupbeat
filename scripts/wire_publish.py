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
from wire import public_summary as PS
from wire.store import WireStore

PREVIEW = Path("data/wire_publication_preview.json")
PUBLICATIONS = Path("data/wire_publications.json")
NEVER_PUBLISH = {"PENDING", "HOLD", "ABSTAIN", "NO_FANTASY_IMPACT",
                 "INCONCLUSIVE_TECHNICAL", "HELD_EVIDENCE_CONFLICT"}


def hydrate_publication_store(store: WireStore,
                              mirror_path: Path = PUBLICATIONS) -> int:
    """Make a clean local database match the tracked publication mirror.

    The database is intentionally untracked, so a fresh clone can have a
    valid publication JSON file and an empty SQLite store. Publishing against
    that empty store would replace the historical cards with only the new
    batch. Import the mirror only when the database is completely empty; any
    other mismatch fails closed and requires a human reconciliation.
    """
    mirror = json.loads(mirror_path.read_text())
    publications = mirror.get("publications") or []
    if mirror.get("count") != len(publications):
        raise ValueError("publication mirror count does not match its records")

    stored = store.publications()
    if stored:
        stored_records = {
            row["publication_id"]: {
                "publication_id": row["publication_id"],
                "version": row["version"],
                "published_at": row["published_at"],
                "updated_at": row["updated_at"],
                **json.loads(row["payload"]),
            }
            for row in stored
        }
        mirror_records = {row.get("publication_id"): row
                          for row in publications}
        if stored_records != mirror_records:
            raise ValueError(
                "publication database and tracked mirror disagree; refusing "
                "to publish")
        return 0

    with store.conn:
        for row in publications:
            publication_id = str(row.get("publication_id") or "").strip()
            candidate_id = str(row.get("evidence_candidate_id") or
                               publication_id).strip()
            if not publication_id or not candidate_id:
                raise ValueError("historical publication is missing its id")
            payload = {key: value for key, value in row.items()
                       if key not in {"publication_id", "version",
                                      "published_at", "updated_at"}}
            store.conn.execute(
                "INSERT INTO wire_publications VALUES (?,?,?,?,?,?,?,0)",
                (publication_id, candidate_id, f"wire:{candidate_id}",
                 int(row.get("version") or 1), json.dumps(payload),
                 row.get("published_at", ""), row.get("updated_at", "")))
    return len(publications)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--actor", default="reviewer")
    args = ap.parse_args()

    preview = json.loads(PREVIEW.read_text())
    cards = preview["cards"]
    store = WireStore()
    hydrated = hydrate_publication_store(store)
    if hydrated:
        print(f"  hydrated {hydrated} historical publication(s) from the "
              "tracked mirror")

    approved, refused = [], []
    for c in cards:
        act = c.get("reviewer_action", "")
        if act in NEVER_PUBLISH or not act.startswith("APPROVE"):
            refused.append((c["player"], f"reviewer action {act or 'none'}"))
            continue
        fails = PP.readiness_failures(c)
        summary = str(c.get("public_summary") or "").strip()
        if not summary:
            fails.append("named-human-approved public summary is missing")
        else:
            fails.extend(PS.validate(
                summary, c["player"], c["evidence"],
                c.get("content_type", "REPORTING"),
                bool(c.get("summary_subject_context"))))
        if not str(c.get("public_summary_approved_by") or "").strip():
            fails.append("public summary has no named-human approval")
        if not str(c.get("commentary_approved_by") or "").strip():
            fails.append("Lineup Beat impact has no named-human approval")
        if not str(c.get("approved_at") or "").strip():
            fails.append("finished wording has no approval timestamp")
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
            # The stable id travels with the card. Without it the homepage
            # renderer has only a name, cannot reach the photo resolver or
            # the display join, and falls back to initials for everyone.
            "player_id": c.get("player_id") or "",
            "content_type": c.get("content_type", "REPORTING"),
            "player_name": c["player"], "team": c["team"],
            "position": c["position"],
            "reader_label": c["reader_label"],
            "direction": c["direction"], "mechanism": c["mechanism"],
            "strength": c["strength"], "horizon": c["horizon"],
            "projection_action": c["projection_action"],
            "reporter_found": c["evidence"],
            "public_evidence_summary": c["public_summary"],
            "summary_subject_context": bool(
                c.get("summary_subject_context")),
            "public_evidence_summary_approved_by":
                c["public_summary_approved_by"],
            "public_evidence_summary_approved_at": c["approved_at"],
            "lineupbeat_impact": c["commentary"],
            "lineupbeat_impact_approved_by": c["commentary_approved_by"],
            "lineupbeat_impact_approved_at": c["approved_at"],
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
