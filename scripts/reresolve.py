#!/usr/bin/env python3
"""Give unresolved mentions another try against a fresher roster.

    python3 scripts/reresolve.py --sport nfl
    python3 scripts/reresolve.py --sport nfl --dry-run
    python3 scripts/reresolve.py --sport nfl --days 14

WHY THIS HAS TO EXIST

Resolution happens once, at extraction, and the answer is stored. That is
right for cost -- nothing re-runs the model -- but it means a mention is
matched against whatever roster existed at that moment, forever.

Stefon Diggs signed with Washington on a Tuesday. Beat writers had it within
minutes and the wire carried five reports about him that night. Sleeper's
roster caught up the following morning. By then the damage was done and
permanent: five claims stored as unresolved, so he had no position, so the
skill filter dropped him, so his video never appeared -- on the one day
anybody was looking for it.

Every player who moves hits this. The wire always knows before the roster
does, which is the whole point of reading beat writers, and it is exactly
when the news matters most.

So: after a roster refresh, take everything still unresolved, and try again.
No model call, no cost, just the matcher against a roster that now knows who
these people are.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beatwire.registry import Registry          # noqa: E402
from beatwire.resolve import Resolver           # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--days", type=int, default=21,
                    help="how far back to retry; older ones are settled")
    ap.add_argument("--min-conf", type=float, default=0.92,
                    help="how sure the matcher must be to overwrite")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    reg = Registry(args.sport)
    resolver = Resolver(reg.players)
    print(f"  roster has {len(reg.players):,} players")

    rows = conn.execute(
        """SELECT id, player_name, team, published_at FROM nuggets
           WHERE (player_id IS NULL OR player_id = '')
           AND published_at > datetime('now', ?)
           ORDER BY published_at DESC""",
        (f"-{args.days} days",)).fetchall()
    print(f"  {len(rows)} unresolved in the last {args.days} days\n")
    if not rows:
        return

    fixed, still = [], 0
    for r in rows:
        player, conf = resolver.resolve(r["player_name"], r["team"] or None)
        # Be much stricter than extraction was.
        #
        # This writes over a claim that is currently honest about not knowing
        # who it means. A first pass at 0.6 offered Braeden Daniels -> CJ
        # Daniels, Dee Williams -> Kyle Williams, Hassan Haskins -> Jaylinn
        # Hawkins: surname collisions, all confidently wrong, and each one
        # would have put a stranger's news on a player's page.
        #
        # Unresolved is a visible, correctable state. A wrong match is not.
        if player and conf >= args.min_conf:
            fixed.append((r, player, conf))
        else:
            still += 1

    for r, p, conf in fixed[:25]:
        print(f"    {r['player_name'][:22]:<22} -> {p.name[:22]:<22} "
              f"{p.team or '':<4} {p.position or '':<3} {conf:.2f}")
    if len(fixed) > 25:
        print(f"    … and {len(fixed) - 25} more")

    print(f"\n  {len(fixed)} now resolve at {args.min_conf:.2f}+, "
          f"{still} still do not")
    print(f"  Anything below that stays unresolved on purpose: this is")
    print(f"  overwriting a claim that is currently honest about not knowing.")
    if not fixed:
        return
    if args.dry_run:
        print(f"  Dry run. Re-run without --dry-run to write them.")
        return

    for r, p, conf in fixed:
        conn.execute("""UPDATE nuggets SET player_id=?, player_name=?,
                        confidence=? WHERE id=?""",
                     (p.id, p.name, conf, r["id"]))
    conn.commit()
    print(f"  Updated. Re-export to put them on the site.")


if __name__ == "__main__":
    main()
