#!/usr/bin/env python3
"""What does the industry's number imply about a player?

    python3 scripts/implied.py "~/Downloads/board.csv" --espn
    python3 scripts/implied.py "~/Downloads/board.csv" --espn --pos RB
    python3 scripts/implied.py "~/Downloads/board.csv" --espn --min-gap 40

A ranking is a compressed opinion. When every board puts Blake Corum at 167
and we have him at 100, they are not disputing our arithmetic -- they know
something about his role that our inputs do not contain, and the number is
the only form we get it in.

So this runs our own model backwards. Given their points total, what would a
player have to do to earn it? How many points per game, and what depth slot
does that correspond to? Then it says so in English: "we have him as an RB2,
their number implies something between RB2 and RB1."

That translation is the point. "They have him higher" is not actionable.
"They expect him at sixty percent of a lead back's workload" is something
you can check against a beat report, and either accept or dismiss.

Nothing here changes a projection. It tells you where our picture of a
player disagrees with everybody's, and what the disagreement is ABOUT.
"""

from __future__ import annotations

import argparse
import csv
import glob
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POS = ("QB", "RB", "WR", "TE")


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def describe_slot(pos, ppg, slots):
    """Which depth role does this scoring rate correspond to?

    Slot rates are the measured average for each rung, so a rate between two
    of them means the industry expects a role between two rungs -- which is
    exactly what a committee backfield or an ascending second receiver is.
    """
    tiers = slots.get(pos) or {}
    if not tiers:
        return "unknown"
    ranked = sorted(tiers.items(), key=lambda kv: -kv[1])
    top_slot, top_ppg = ranked[0]
    if ppg >= top_ppg * 1.25:
        return f"well above a {pos}{top_slot}"
    for (s1, p1), (s2, p2) in zip(ranked, ranked[1:]):
        if p2 <= ppg <= p1:
            frac = (ppg - p2) / max(p1 - p2, .01)
            if frac > 0.75:
                return f"about a {pos}{s1}"
            if frac < 0.25:
                return f"about a {pos}{s2}"
            return f"between a {pos}{s2} and a {pos}{s1}"
    if ppg >= top_ppg:
        return f"about a {pos}{top_slot}"
    return f"below a {pos}{ranked[-1][0]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--espn-season", type=int, default=2026)
    ap.add_argument("--espn", action="store_true")
    ap.add_argument("--pos", help="one position only")
    ap.add_argument("--min-gap", type=float, default=35,
                    help="points of disagreement worth explaining")
    ap.add_argument("--max-spread", type=float, default=40,
                    help="how closely the boards must agree to count")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    sys.path.insert(0, str(ROOT / "scripts"))
    from consensus import sniff                      # reuse the validated one
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p5", str(ROOT / "scripts" / "project5.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    boards, sigs = {}, {}
    for pat in args.files:
        for f in sorted(glob.glob(str(Path(pat).expanduser()))):
            rows = sniff(f, verbose=False)
            if not rows:
                print(f"  {Path(f).name}: unreadable")
                continue
            sig = (len(rows), round(sum(r["pts"] for r in rows)))
            if sig in sigs:
                continue
            sigs[sig] = 1
            boards[Path(f).stem[:20]] = rows
    if args.espn:
        try:
            e = [{"key": r["name_key"], "name": r["player"],
                  "pos": r["position"], "pts": r["points"]}
                 for r in conn.execute(
                     "SELECT * FROM espn_proj WHERE season=?", (args.espn_season,))]
            if e:
                boards["ESPN"] = e
        except sqlite3.OperationalError:
            pass
    if not boards:
        sys.exit("  no boards. Pass CSV files, or --espn.")

    ours = {key(r["name"]): r for r in
            m.build(conn, args.season, m.roster(), m.crosswalk(conn))}

    agg = defaultdict(dict)
    for bname, rows in boards.items():
        for r in rows:
            pos = r["pos"] or (ours.get(r["key"]) or {}).get("pos", "")
            if pos in POS:
                agg[(r["key"], pos)][bname] = r["pts"]

    # How much of his own team does each side give him?
    #
    # Two very different things look identical in a points gap. Either they
    # expect him to get a different slice of the offence, or they expect the
    # offence itself to produce less. De'Von Achane's role is not in dispute;
    # what the industry knows and our inputs cannot is that Miami is worse
    # this year than last year's volume implies.
    #
    # Comparing shares separates them. Same share, fewer points means the
    # pie shrank. Different share means the role changed.
    team_ours, team_theirs = defaultdict(float), defaultdict(float)
    for (k, pos), per in agg.items():
        o = ours.get(k)
        if not o or len(per) < 2 or not o.get("team"):
            continue
        team_ours[o["team"]] += o["ppr"]
        team_theirs[o["team"]] += statistics.mean(per.values())

    print(f"\n  {len(boards)} board{'s' if len(boards) != 1 else ''}: "
          f"{', '.join(boards)}")
    print(f"  Explaining gaps over {args.min_gap:.0f} points where the boards "
          f"agree within {args.max_spread:.0f}.\n")

    found = []
    for (k, pos), per in agg.items():
        if args.pos and pos != args.pos.upper():
            continue
        o = ours.get(k)
        if not o or len(per) < 2:
            continue
        pts = list(per.values())
        spread = max(pts) - min(pts)
        if spread > args.max_spread:
            continue                       # they disagree; nothing to learn
        theirs = statistics.mean(pts)
        gap = theirs - o["ppr"]
        if abs(gap) < args.min_gap:
            continue
        found.append((abs(gap), k, pos, o, theirs, spread))

    if not found:
        print("  Nothing over the threshold. Lower --min-gap to see more.")
        return

    for _, k, pos, o, theirs, spread in sorted(found, reverse=True)[:args.top]:
        # Run our arithmetic backwards from their number.
        games = o["games"] or 1
        their_ppg = theirs / m.GAMES
        our_ppg = o["ppr"] / m.GAMES
        mine_role = describe_slot(pos, our_ppg, m.SLOT_PPG)
        their_role = describe_slot(pos, their_ppg, m.SLOT_PPG)
        direction = "higher" if theirs > o["ppr"] else "lower"

        print(f"  {o['name']}  {pos} {o['team']}")
        print(f"    ours {o['ppr']:.0f}   industry {theirs:.0f}"
              f"   ({spread:.0f} apart across {len(boards)} boards)")
        print(f"    we have him at {our_ppg:.1f} a game, {mine_role}")
        print(f"    theirs implies {their_ppg:.1f} a game, {their_role}")

        # Same slice of a smaller pie, or a different slice?
        tm = o.get("team")
        our_share = (o["ppr"] / team_ours[tm]) if team_ours.get(tm) else None
        their_share = (theirs / team_theirs[tm]) if team_theirs.get(tm) else None
        share_moved = (our_share is not None and their_share is not None
                       and abs(their_share - our_share) / max(our_share, .001) > 0.15)
        team_moved = (team_ours.get(tm) and team_theirs.get(tm)
                      and abs(team_theirs[tm] - team_ours[tm])
                          / team_ours[tm] > 0.08)

        if our_share is not None and their_share is not None:
            print(f"    share of his own team: ours {our_share:.0%}, "
                  f"theirs {their_share:.0%}")

        if o["note"] and "out" in o["note"]:
            print(f"    -> we are discounting an injury they expect him back "
                  f"from")
        elif team_moved and not share_moved:
            pct = (team_theirs[tm] - team_ours[tm]) / team_ours[tm]
            print(f"    -> HIS ROLE IS NOT IN DISPUTE. The industry marks the "
                  f"whole {tm} offence {abs(pct):.0%} "
                  f"{'up' if pct > 0 else 'down'} from what last year's volume "
                  f"implies -- team quality, which our inputs cannot see.")
        elif share_moved:
            print(f"    -> they expect a different SLICE of the same offence: "
                  f"a role change the depth chart does not show")
        elif abs(their_ppg - our_ppg) < 1.5:
            need = theirs / max(our_ppg, .01)
            print(f"    -> same rate, different availability: their number "
                  f"needs {need:.1f} games, we expect {games:.1f}")
        else:
            print(f"    -> same role and same offence; they simply expect "
                  f"{direction} production")
        if o["note"]:
            print(f"    our note: {o['note']}")
        print()

    print(f"  {len(found)} players over the threshold, showing "
          f"{min(len(found), args.top)}.")
    print(f"\n  Nothing here changes a projection. Each line is the industry")
    print(f"  telling you what it believes about a player, in the only form")
    print(f"  a ranking can carry it.")


if __name__ == "__main__":
    main()
