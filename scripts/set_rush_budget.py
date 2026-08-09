#!/usr/bin/env python3
"""Record what each team is allowed to rush for.

    python3 scripts/set_rush_budget.py --derive     # from the live snapshot
    python3 scripts/set_rush_budget.py --show

WHY THIS HAS TO EXIST BEFORE THE ENGINE CAN PUBLISH

If a starting back is ruled out, the engine cannot simply delete his 180
carries. They belong to the team, and somebody else takes them. Without a
budget to reconcile against, "he is out" quietly becomes "this team runs the
ball 180 fewer times", which is not what happened and nothing would catch it.

The passing side already reconciles because the workbook was built that way.
Rushing was not, so the budget is derived from the current validated
snapshot: whatever Offense v1.0 allocated is what the team is entitled to.
That is a starting point rather than a truth, and it can be replaced with a
modelled budget later.
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone

ap = argparse.ArgumentParser()
ap.add_argument("--db", default="beatwire.db")
ap.add_argument("--season", type=int, default=2026)
ap.add_argument("--derive", action="store_true")
ap.add_argument("--show", action="store_true")
args = ap.parse_args()

conn = sqlite3.connect(args.db)
conn.row_factory = sqlite3.Row

if args.show:
    rows = conn.execute("SELECT * FROM team_rush_budget WHERE season=? "
                        "ORDER BY team", (args.season,)).fetchall()
    if not rows:
        sys.exit("  no budgets stored. Run with --derive")
    print(f"\n  {len(rows)} teams\n")
    print(f"  {'TM':<5}{'RUSH ATT':>10}{'RUSH TD':>10}  SOURCE")
    for r in rows:
        print(f"  {r['team']:<5}{r['rush_att']:>10.1f}{r['rush_td']:>10.1f}"
              f"  {r['source']}")
    print(f"\n  total {sum(r['rush_att'] for r in rows):,.0f} carries across "
          f"the league")
    sys.exit()

if not args.derive:
    sys.exit("  pass --derive or --show")

live = conn.execute("SELECT run_id FROM published_snapshot WHERE season=?",
                    (args.season,)).fetchone()
if not live:
    sys.exit("  nothing published to derive from")

# Residuals count: they are opportunity the model allocated, and leaving them
# out would set every budget slightly too low.
rows = conn.execute(
    """SELECT team, SUM(rush_att) att, SUM(rush_td) td
       FROM run_projections WHERE run_id = ? AND team NOT IN
       ('FA','FA/UNK','UNK','') GROUP BY team""", (live["run_id"],)).fetchall()

now = datetime.now(timezone.utc).isoformat()
for r in rows:
    conn.execute("INSERT OR REPLACE INTO team_rush_budget VALUES (?,?,?,?,?,?)",
                 (args.season, r["team"], r["att"], r["td"],
                  f"derived from {live['run_id']}", now))
conn.commit()
print(f"  derived {len(rows)} team budgets from the live snapshot")
print(f"  total {sum(r['att'] for r in rows):,.0f} carries, "
      f"{sum(r['td'] for r in rows):,.1f} rushing TDs")
print(f"\n  These are what Offense v1.0 allocated, not an independently")
print(f"  modelled budget. They exist so a role change has to move carries")
print(f"  rather than delete them.")
