#!/usr/bin/env python3
"""What would a projection-list gate actually cost and save?

    python3 scripts/gate_compare.py --n 800

Three gates, over the same recent items, counted rather than argued about:

  now         mentions any rostered skill player          3,000 names
  projected   mentions a player in the published board      632 names
  proposed    projected, OR any roster transaction

The middle one is tighter and has a hole in it. A player being waived is
frequently not on the roster any more, and a player being signed was never
on it -- so the items that change somebody's role most are exactly the ones
a name-based gate cannot see. "The Seahawks plan to waive RB Kenny McIntosh"
matters because of the back standing behind him, and it names nobody the
board projects.

The third gate is the second plus a transaction override, which keeps the
saving and closes the hole. This prints what each would have done so the
choice is made on numbers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# A roster move is news about whoever is left, whatever name it carries.
TRANSACTION = re.compile(
    r"\b(sign(ed|ing|s)?|waiv(e|ed|ing)|releas(e|ed|ing)|"
    r"trad(e|ed|ing)|claim(ed)?|activat(e|ed)|"
    r"injured reserve|\bIR\b|\bPUP\b|suspend(ed)?|"
    r"placed on|designated|cut\b|agreed to terms|extension)", re.I)

# Positions the wire shows.
SKILL = {"QB", "RB", "WR", "TE", "FB"}


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    return " ".join(re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n).split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args()

    from beatwire.registry import Registry
    from beatwire.resolve import Resolver
    from beatwire.extract import mentions_any_player
    from beatwire.models import RawItem

    reg = Registry(args.sport)
    resolver = Resolver(reg.players, reg.profile.position_groups)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # The published board, if there is one.
    projected = set()
    try:
        run = conn.execute("SELECT run_id FROM published_snapshot WHERE season=?",
                           (args.season,)).fetchone()
        if run:
            for r in conn.execute(
                    """SELECT player FROM run_projections WHERE run_id=?
                       AND (is_residual IS NULL OR is_residual=0)""",
                    (run["run_id"],)):
                projected.add(key(r["player"]))
    except sqlite3.OperationalError:
        pass
    if not projected:
        sys.exit("  no published projections to gate on")
    print(f"\n  {len(projected)} projected players against "
          f"{len(reg.players)} on the roster")

    # surnames, so a bare "Metchie" still matches
    proj_surnames = {k.split()[-1] for k in projected if len(k.split()) > 1}

    rows = conn.execute(
        """SELECT item_id, title, body, source_id FROM items
           WHERE source_id LIKE ? ORDER BY fetched_at DESC LIMIT ?""",
        (f"{args.sport}-%", args.n)).fetchall()
    if not rows:
        sys.exit("  no items")

    def mentions_projected(text):
        words = set(re.findall(r"[A-Za-z'\-]{3,}", text.lower()))
        return bool(words & proj_surnames)

    now_pass = proj_pass = prop_pass = 0
    lost, rescued = [], []
    for r in rows:
        text = ((r["title"] or "") + "\n" + (r["body"] or "")).strip()
        item = RawItem(source_id=r["source_id"], sport=args.sport, url="",
                       title=r["title"] or "", body=r["body"] or "",
                       published_at=dt.datetime.now(dt.timezone.utc))
        team = None
        for s in reg.sources:
            if s.id == r["source_id"]:
                team = resolver.source_team_hint(s)
                break
        a = mentions_any_player(item, resolver, team, skill_only=True)
        b = mentions_projected(text)
        txn = bool(TRANSACTION.search(text))
        cpass = b or txn

        now_pass += a
        proj_pass += b
        prop_pass += cpass
        if a and not b:
            (rescued if txn else lost).append((r, text))

    n = len(rows)
    print(f"\n  OVER {n} RECENT ITEMS\n")
    print(f"    {'gate':<12}{'passes':>8}{'rate':>8}{'vs now':>10}")
    print(f"    {'now':<12}{now_pass:>8}{now_pass/n:>8.0%}{'':>10}")
    print(f"    {'projected':<12}{proj_pass:>8}{proj_pass/n:>8.0%}"
          f"{proj_pass-now_pass:>+10}")
    print(f"    {'proposed':<12}{prop_pass:>8}{prop_pass/n:>8.0%}"
          f"{prop_pass-now_pass:>+10}")

    per = 0.00108
    print(f"\n  at ${per*1000:.2f} per thousand extractions, over 20 runs a day")
    for label, cnt in (("now", now_pass), ("projected", proj_pass),
                       ("proposed", prop_pass)):
        # scale the sample to the real hourly volume of ~2,975 items
        scaled = cnt / n * 2975
        print(f"    {label:<12} {scaled:>6.0f} an hour   "
              f"${scaled*per:>5.2f}/hr   ${scaled*per*20:>6.2f}/day")

    print(f"\n  {len(rescued)} items the transaction override rescues\n")
    for r, text in rescued[:args.show]:
        print(f"    {r['source_id'][-20:]:<20} {' '.join(text.split())[:88]}")

    print(f"\n  {len(lost)} items dropped that the current gate passes\n")
    for r, text in lost[:args.show]:
        print(f"    {r['source_id'][-20:]:<20} {' '.join(text.split())[:88]}")
    print(f"\n  Read that last list carefully. It is the coverage the tighter")
    print(f"  gate costs, and it is the only part of this that cannot be")
    print(f"  measured in dollars.")


if __name__ == "__main__":
    main()
