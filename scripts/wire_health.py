#!/usr/bin/env python3
"""Source health, automatic pausing, and rollback of the published file.

    python3 scripts/wire_health.py --snapshot     # bank a clean publication file
    python3 scripts/wire_health.py --check        # score every active source
    python3 scripts/wire_health.py --pause SRC --reason "..."
    python3 scripts/wire_health.py --rollback     # restore the last clean snapshot

One bad source must not take the Wire down with it. Each is scored on its own
stored output and paused on its own, and the published file is snapshotted
before anything changes so there is always something clean to go back to.

Pausing writes to data/wire_paused_sources.json rather than editing the
registry, so the reason and the timestamp survive and a human can see what
was paused and why without reading a diff.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import registry as artreg
from wire import si
from wire.store import WireStore

PUBS = Path("data/wire_publications.json")
SNAPDIR = Path("data/wire_snapshots")
PAUSED = Path("data/wire_paused_sources.json")

# Conditions that pause a source outright. Each is a correctness failure
# rather than a quiet day: none of them can be produced by a publisher simply
# not posting.
FATAL = "fatal"
RATIO = "ratio"
RULES = [
    ("wrong-team contamination", FATAL),
    ("paid article text", FATAL),
    ("unapproved firsthand authority", FATAL),
    ("corrupted extraction", FATAL),
    ("unresolved author identity", RATIO),
    ("excessive duplicate or irrelevant content", RATIO),
]


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_paused() -> dict:
    return json.loads(PAUSED.read_text()) if PAUSED.exists() else {}


def save_paused(d: dict) -> None:
    PAUSED.parent.mkdir(parents=True, exist_ok=True)
    PAUSED.write_text(json.dumps(d, indent=1) + "\n")


def snapshot() -> Path | None:
    """Bank the current published file. Never overwrites an earlier one."""
    if not PUBS.exists():
        print(f"  {PUBS} does not exist yet; nothing to snapshot")
        return None
    SNAPDIR.mkdir(parents=True, exist_ok=True)
    dest = SNAPDIR / f"wire_publications.{now().replace(':', '')}.json"
    shutil.copy2(PUBS, dest)
    payload = json.loads(PUBS.read_text())
    print(f"  snapshot {dest}  ({payload.get('count', 0)} published item(s))")
    return dest


def latest_snapshot() -> Path | None:
    if not SNAPDIR.exists():
        return None
    snaps = sorted(SNAPDIR.glob("wire_publications.*.json"))
    return snaps[-1] if snaps else None


def rollback() -> int:
    snap = latest_snapshot()
    if snap is None:
        print("  no snapshot to roll back to")
        return 1
    # The file being replaced is itself banked first: a rollback that
    # destroys the evidence of what went wrong is not a rollback.
    if PUBS.exists():
        bad = SNAPDIR / f"rolled-back.{now().replace(':', '')}.json"
        shutil.copy2(PUBS, bad)
        print(f"  current file preserved at {bad}")
    shutil.copy2(snap, PUBS)
    print(f"  restored {snap} -> {PUBS}")
    return 0


def score(store, sources) -> list[dict]:
    """One health row per active source, from its own stored output."""
    auth = si.load_authors()
    approved = {(t, n)
                for t, e in auth.get("teams", {}).items()
                for n, a in e["authors"].items()
                if a["classification"] == "FIRSTHAND_APPROVED"}
    named_local = {s.reporter_name for s in sources
                   if s.reporter_name and "staff" not in s.reporter_name.lower()}

    rows = []
    for src in sources:
        if not src.active or not src.adapter:
            continue
        team = src.teams[0] if src.teams else ""
        ev = [dict(r) for r in store.evidence()
              if r["source_id"] == src.source_id]
        items = store.conn.execute(
            "SELECT canonical_url, extraction_status FROM wire_source_items "
            "WHERE source_id = ?", (src.source_id,)).fetchall()
        live = [r for r in ev if r["review_status"] in ("PENDING", "APPROVED")]

        wrong_team = [r for r in ev if r["team"] and team and r["team"] != team]
        paid_text = [r for r in ev if src.paid and r["evidence_text"]]
        unapproved_fh = [
            r for r in live
            if r["evidence_class"] == "FIRSTHAND_OBSERVATION"
            and (team, r["source_author_or_channel"]) not in approved
            and r["source_author_or_channel"] not in named_local]
        unresolved = [r for r in live if not r["player_id"]]
        dupes = [r for r in live if r["duplicate_of"]]
        broken = [i for i in items if i["extraction_status"] == "BLOCKED"]

        problems = []
        if wrong_team:
            problems.append((f"wrong-team contamination: {len(wrong_team)} "
                             f"candidate(s)", FATAL))
        if paid_text:
            problems.append((f"paid article text: {len(paid_text)}", FATAL))
        if unapproved_fh:
            problems.append((f"unapproved firsthand authority: "
                             f"{len(unapproved_fh)}", FATAL))
        if items and len(broken) / len(items) > 0.5:
            problems.append((f"corrupted extraction: {len(broken)}/{len(items)}",
                             FATAL))
        if live and len(unresolved) / len(live) > 0.5:
            problems.append((f"unresolved author identity: {len(unresolved)}/"
                             f"{len(live)}", RATIO))
        if live and len(dupes) / len(live) > 0.9:
            problems.append((f"excessive duplicate content: {len(dupes)}/"
                             f"{len(live)}", RATIO))

        rows.append({"source_id": src.source_id, "team": team,
                     "class": src.source_class, "articles": len(items),
                     "candidates": len(live), "problems": problems})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--pause")
    ap.add_argument("--unpause")
    ap.add_argument("--reason", default="")
    ap.add_argument("--auto-pause", action="store_true",
                    help="pause any source with a fatal health problem")
    args = ap.parse_args()

    if args.snapshot:
        snapshot()
        return 0
    if args.rollback:
        return rollback()

    paused = load_paused()
    if args.pause:
        paused[args.pause] = {"paused_at": now(),
                              "reason": args.reason or "manual"}
        save_paused(paused)
        print(f"  paused {args.pause}: {paused[args.pause]['reason']}")
        return 0
    if args.unpause:
        paused.pop(args.unpause, None)
        save_paused(paused)
        print(f"  unpaused {args.unpause}")
        return 0

    store = WireStore()
    rows = score(store, artreg.load())
    bad = [r for r in rows if r["problems"]]
    print(f"  {len(rows)} active source(s); {len(bad)} with health problems")
    for r in rows:
        if not r["problems"]:
            continue
        print(f"    {r['source_id']:<26}{r['team']:<5}"
              f"{r['candidates']:>5} candidates")
        for text, kind in r["problems"]:
            print(f"        [{kind}] {text}")
    if args.auto_pause:
        n = 0
        for r in bad:
            if any(k == FATAL for _, k in r["problems"]):
                paused[r["source_id"]] = {
                    "paused_at": now(),
                    "reason": "; ".join(t for t, _ in r["problems"])}
                n += 1
        save_paused(paused)
        print(f"  auto-paused {n} source(s)")
    if paused:
        print(f"  currently paused: {', '.join(sorted(paused))}")
    snap = latest_snapshot()
    print(f"  rollback snapshot: {snap or 'none banked yet'}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
