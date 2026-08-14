#!/usr/bin/env python3
"""Delete specific nuggets by id or claim text.

    python3 scripts/purge.py --claim "Carted off during the game."

For the case where something wrong is published and has to come down
before the cause is fixed. Prints what it will remove and requires
--yes to actually do it, because a claim filter is a blunt instrument.
"""
import argparse, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--db", default="beatwire.db")
ap.add_argument("--claim", required=True)
ap.add_argument("--yes", action="store_true")
a = ap.parse_args()

conn = sqlite3.connect(ROOT / a.db)
rows = conn.execute(
    "SELECT id, player_name, team, claim FROM nuggets WHERE claim LIKE ?",
    (f"%{a.claim}%",)).fetchall()
print(f"\n  {len(rows)} matching:")
for r in rows:
    print(f"    [{r[0]}] {r[1]} ({r[2]}): {r[3][:60]}")
if not rows:
    sys.exit(0)
if not a.yes:
    print("\n  nothing deleted. Re-run with --yes.")
    sys.exit(0)
conn.execute("DELETE FROM nuggets WHERE claim LIKE ?", (f"%{a.claim}%",))
conn.commit()
print(f"\n  deleted {len(rows)}")
