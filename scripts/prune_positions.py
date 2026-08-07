#!/usr/bin/env python3
"""Drop claims about players the wire does not show, carefully.

    python3 scripts/prune_positions.py            # report only
    python3 scripts/prune_positions.py --apply

WHY CAREFULLY

The skill filter stops NEW extraction. It does nothing about what is already
stored, and fifty-seven percent of the database is about linemen and
defenders nobody sees.

But deleting is not reversible, and the failure that matters is not leaving
a lineman in -- it is dropping a receiver out. So this errs, everywhere it
can, toward keeping:

  A player we cannot match to a roster is KEPT. Somebody who signed this
  morning is unresolvable until the roster catches up, and that is exactly
  the news worth having. Stefon Diggs was unmatched for a day.

  A player whose position we do not know is KEPT.

  A claim that also names a skill player anywhere in its source is KEPT,
  because merged claims can carry more than one man.

  Anything within the last three days is KEPT regardless, because the roster
  may simply not have caught up yet.

What that leaves is what it should: a defensive tackle, matched, positioned,
and older than the window in which we might still be wrong about him.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = {"QB", "RB", "WR", "TE"}
# Positions we are confident are not shown. Anything not on either list is
# treated as unknown and kept.
NON_SKILL = {"C", "G", "OG", "OT", "T", "OL", "LS", "FB",
             "DE", "DT", "DL", "NT", "LB", "CB", "DB", "S", "SS", "FS",
             "K", "P"}


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--days-safe", type=int, default=3,
                    help="never touch anything newer than this")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    pos_by_id, pos_by_name = {}, {}
    rp = ROOT / "rosters" / "nfl.csv"
    if not rp.exists():
        sys.exit("  no roster file; refusing to guess")
    for r in csv.DictReader(rp.open()):
        p = (r.get("position") or "").upper()
        pos_by_id[r["id"]] = p
        pos_by_name[key(r["name"])] = p
    print(f"  roster: {len(pos_by_id):,} players")

    rows = conn.execute("""SELECT id, player_id, player_name, published_at,
                           attributions FROM nuggets""").fetchall()
    print(f"  nuggets: {len(rows):,}\n")

    # Which source items mention a skill player at all? A merged claim can
    # carry several men, and one receiver is enough to keep the item.
    skill_urls = set()
    for r in rows:
        p = pos_by_id.get(r["player_id"] or "") or pos_by_name.get(
            key(r["player_name"]), "")
        if p in SKILL:
            try:
                for a in json.loads(r["attributions"] or "[]"):
                    if a.get("url"):
                        skill_urls.add(a["url"])
            except json.JSONDecodeError:
                pass

    drop, keep_why = [], Counter()
    for r in rows:
        pid, name = r["player_id"] or "", r["player_name"] or ""
        p = pos_by_id.get(pid) or pos_by_name.get(key(name), "")

        if not pid:
            keep_why["unmatched player"] += 1
            continue
        if not p:
            keep_why["position unknown"] += 1
            continue
        if p in SKILL:
            keep_why["skill player"] += 1
            continue
        if p not in NON_SKILL:
            keep_why["position not on either list"] += 1
            continue
        try:
            urls = {a.get("url") for a in json.loads(r["attributions"] or "[]")}
        except json.JSONDecodeError:
            urls = set()
        if urls & skill_urls:
            keep_why["source also names a skill player"] += 1
            continue
        drop.append((r["id"], name, p))

    recent = conn.execute(
        """SELECT COUNT(*) n FROM nuggets
           WHERE published_at > datetime('now', ?)""",
        (f"-{args.days_safe} days",)).fetchone()["n"]
    safe_ids = {r["id"] for r in conn.execute(
        """SELECT id FROM nuggets WHERE published_at > datetime('now', ?)""",
        (f"-{args.days_safe} days",))}
    held = [d for d in drop if d[0] in safe_ids]
    drop = [d for d in drop if d[0] not in safe_ids]

    print("  KEPT")
    for why, n in keep_why.most_common():
        print(f"    {n:>6,}  {why}")
    if held:
        print(f"    {len(held):>6,}  non-skill but newer than "
              f"{args.days_safe} days")

    print(f"\n  WOULD DROP  {len(drop):,}")
    by_pos = Counter(p for _, _, p in drop)
    for p, n in by_pos.most_common(10):
        print(f"    {n:>6,}  {p}")
    print("\n  a sample:")
    for _, name, p in drop[:8]:
        print(f"    {name[:26]:<26} {p}")

    if not args.apply:
        print(f"\n  Nothing deleted. Re-run with --apply.")
        print(f"  Every rule above errs toward keeping: the failure that")
        print(f"  matters is dropping a receiver, not leaving a guard in.")
        return

    if not drop:
        print("\n  nothing to drop")
        return
    conn.executemany("DELETE FROM nuggets WHERE id=?",
                     [(d[0],) for d in drop])
    conn.commit()
    left = conn.execute("SELECT COUNT(*) n FROM nuggets").fetchone()["n"]
    print(f"\n  dropped {len(drop):,}, {left:,} remain")
    print(f"  next: python3 -m beatwire.cli export --sports nfl --limit 4000")


if __name__ == "__main__":
    main()
