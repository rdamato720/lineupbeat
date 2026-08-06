#!/usr/bin/env python3
"""Calibrate the model's slot rates against what the industry implies.

    python3 scripts/calibrate.py "~/Downloads/board.csv" --espn
    python3 scripts/calibrate.py "~/Downloads/board.csv" --espn --apply

WHY

SLOT_PPG says a second running back scores 6.3 points a game, and that is
true: it is the measured average of every RB2 across four seasons. But an
average includes the ones who never got a snap, and projecting forward is a
different question from describing the past. The industry consistently
prices a listed RB2 higher than his historical average, and it is right to
-- a backup on a real offence carries upside the average buries.

So this asks a narrow question. Taking every player where the boards agree
with each other, what slot rate would have made our projections land closest
to theirs? That number is calibration, not imitation: it corrects a constant
in our model rather than adopting anybody's number for a player.

The distinction matters. Copying a board means their projection is inside
every number we publish. Fitting a constant means we looked at where the
market lands, concluded our parameter was off, and changed the parameter.
Every projection still comes out of our own arithmetic.

WHAT IT DOES NOT DO

It does not touch individual players. If the industry likes one back more
than we do, that stays a disagreement. Only a pattern across many players at
the same depth slot moves anything, because only a pattern is evidence about
the constant rather than about a person.
"""

from __future__ import annotations

import argparse
import glob
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POS = ("QB", "RB", "WR", "TE")
MIN_PLAYERS = 6          # below this a "pattern" is a coincidence


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--espn-season", type=int, default=2026)
    ap.add_argument("--espn", action="store_true")
    ap.add_argument("--max-spread", type=float, default=45,
                    help="how closely the boards must agree to count")
    ap.add_argument("--apply", action="store_true",
                    help="write the fitted rates into project5.py")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    sys.path.insert(0, str(ROOT / "scripts"))
    from consensus import sniff
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p5", str(ROOT / "scripts" / "project5.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    boards, sigs = {}, set()
    for pat in args.files:
        for f in sorted(glob.glob(str(Path(pat).expanduser()))):
            rows = sniff(f, verbose=False)
            if not rows:
                continue
            sig = (len(rows), round(sum(r["pts"] for r in rows)))
            if sig in sigs:
                continue
            sigs.add(sig)
            boards[Path(f).stem[:20]] = rows
    if args.espn:
        try:
            e = [{"key": r["name_key"], "name": r["player"],
                  "pos": r["position"], "pts": r["points"]}
                 for r in conn.execute("SELECT * FROM espn_proj WHERE season=?",
                                       (args.espn_season,))]
            if e:
                boards["ESPN"] = e
        except sqlite3.OperationalError:
            pass
    if len(boards) < 2:
        sys.exit("  need at least two boards; one cannot show agreement")

    roster = m.roster()
    ours = {key(r["name"]): r for r in
            m.build(conn, args.season, roster, m.crosswalk(conn))}

    # slot for each player, from the roster we already trust
    slot_of = {}
    for rid, meta in roster.items():
        if meta.get("slot") and meta.get("name"):
            slot_of[key(meta["name"])] = (meta["pos"], meta["slot"])

    agg = defaultdict(dict)
    for bname, rows in boards.items():
        for r in rows:
            pos = r["pos"] or (ours.get(r["key"]) or {}).get("pos", "")
            if pos in POS:
                agg[(r["key"], pos)][bname] = r["pts"]

    # For each position and slot, what per-game rate does the industry imply?
    implied = defaultdict(list)
    for (k, pos), per in agg.items():
        if len(per) < 2:
            continue
        pts = list(per.values())
        if max(pts) - min(pts) > args.max_spread:
            continue                      # they disagree; no signal
        sl = slot_of.get(k)
        if not sl or sl[0] != pos or sl[1] > 4:
            continue
        o = ours.get(k)
        if not o:
            continue
        implied[(pos, sl[1])].append(statistics.mean(pts) / m.GAMES)

    print(f"\n  {len(boards)} boards. A slot moves only with "
          f"{MIN_PLAYERS}+ players behind it.\n")
    print(f"  {'':<4}{'slot':<6}{'n':>4}{'ours':>8}{'implied':>10}"
          f"{'median':>9}{'change':>9}")

    fitted = {}
    for pos in POS:
        tiers = m.SLOT_PPG.get(pos) or {}
        for slot in sorted(tiers):
            vals = implied.get((pos, slot)) or []
            cur = tiers[slot]
            if len(vals) < MIN_PLAYERS:
                print(f"  {pos:<4}{slot:<6}{len(vals):>4}{cur:>8.1f}"
                      f"{'—':>10}{'—':>9}{'too few':>9}")
                continue
            # Median, not mean: one board loving one player should not drag a
            # constant that governs sixty of them.
            med = statistics.median(vals)
            # Move partway. The industry is evidence, not an oracle, and our
            # own measurement of what a slot historically scores is evidence
            # too. Splitting the difference keeps both.
            new = round(cur + (med - cur) * 0.5, 1)
            fitted[(pos, slot)] = new
            print(f"  {pos:<4}{slot:<6}{len(vals):>4}{cur:>8.1f}"
                  f"{med:>10.1f}{new:>9.1f}{new - cur:>+9.1f}")

    if not fitted:
        print("\n  Nothing had enough players behind it to move.")
        return

    print(f"\n  The 'implied' column is what the industry's numbers work out")
    print(f"  to for players at that slot. We move halfway: their board is")
    print(f"  evidence about a constant, and so is our own measurement of")
    print(f"  what the slot historically scored.")

    if not args.apply:
        print(f"\n  Nothing written. Re-run with --apply to update the model.")
        return

    src = (ROOT / "scripts" / "project5.py").read_text()
    import re as _re
    block = _re.search(r"SLOT_PPG = \{.*?\n\}", src, _re.S)
    if not block:
        sys.exit("  could not find SLOT_PPG")
    lines = ["SLOT_PPG = {"]
    for pos in POS:
        tiers = m.SLOT_PPG.get(pos) or {}
        parts = []
        for slot in sorted(tiers):
            parts.append(f"{slot}: {fitted.get((pos, slot), tiers[slot])}")
        lines.append(f'    "{pos}": {{{", ".join(parts)}}},')
    lines.append("}")
    src = src.replace(block.group(0), "\n".join(lines), 1)
    (ROOT / "scripts" / "project5.py").write_text(src)
    print(f"\n  Wrote {len(fitted)} fitted rates into project5.py.")
    print(f"  Re-run the comparison to see whether the gap actually closed --")
    print(f"  a calibration that does not is a calibration to reverse.")


if __name__ == "__main__":
    main()
