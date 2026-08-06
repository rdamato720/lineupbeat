#!/usr/bin/env python3
"""Calibrate our constants against consensus STAT LINES, not points.

    python3 scripts/calibrate_volume.py
    python3 scripts/calibrate_volume.py --apply

WHY VOLUME AND NOT POINTS

An earlier pass fitted our slot rates to consensus point totals. It helped,
but a points total is the end of a long chain -- team volume, share, role,
efficiency, availability -- and a gap in it says only that something in the
chain is wrong.

A stat line says which link. Their Jahmyr Gibbs is 274 carries and 71
catches; ours is whatever our team totals and shares produce. Comparing
those directly tells us whether our Detroit backfield is too big, whether
our RB1 share is too high, or whether our yards per carry is the problem.
Points hide all three. Carries and targets separate them.

WHAT IT CAN AND CANNOT WRITE

project5 projects points per game directly; it has no share constants to
update, because it does not model shares. So the share section here is
diagnostic: it tells you what the market thinks a depth slot commands, which
is worth knowing and which you can act on by hand, but it cannot write
itself into a parameter that does not exist.

What it can check directly is SLOT_PPG, team volume and efficiency, which
are the constants that do exist.

WHAT THIS CHANGES AND WHAT IT DOES NOT

It corrects constants: how much volume a team generates, what share a depth
slot commands, how hard efficiency regresses. Those are parameters of our
model, and calibrating them against where the market lands is the same kind
of act as calibrating them against history -- we are choosing which evidence
to fit, not adopting anybody's answer for a player.

It never touches an individual. If the market likes one back more than we
do, that stays a disagreement. Only a pattern across many players moves
anything, because only a pattern is evidence about a constant.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POS = ("QB", "RB", "WR", "TE")
MIN_N = 8            # below this, a "pattern" is a coincidence


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--fp-season", type=int, default=2026)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        ref = {r["name_key"]: dict(r) for r in conn.execute(
            "SELECT * FROM fp_projections WHERE season=?", (args.fp_season,))}
    except sqlite3.OperationalError:
        sys.exit("  no fp_projections table. Import first.")
    if not ref:
        sys.exit("  nothing stored. Run fp_projections.py first.")

    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p5", str(ROOT / "scripts" / "project5.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    roster = m.roster()
    ours = {key(r["name"]): r for r in
            m.build(conn, args.season, roster, m.crosswalk(conn))}

    slot_of = {}
    for rid, meta in roster.items():
        if meta.get("slot") and meta.get("name"):
            slot_of[key(meta["name"])] = (meta["pos"], meta["slot"])

    print(f"\n  {len(ref)} consensus stat lines, {len(ours)} of ours\n")

    # ---- 1. team volume -------------------------------------------------
    #
    # Sum carries and targets across each team. Ours comes from last year's
    # totals; theirs is what the market expects this year. A team that lost
    # its line, or gained a quarterback, shows up here and nowhere else in
    # our inputs.
    tv_ours, tv_ref = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0])
    for k, r in ref.items():
        o = ours.get(k)
        tm = r["team"]
        if not o or not tm or o["pos"] not in ("RB", "WR", "TE"):
            continue
        tv_ref[tm][0] += r["rush_att"]
        tv_ref[tm][1] += r["rec"]
        # our own stat line, scaled the same way the projection was
        tv_ours[tm][0] += o.get("ruyd", 0) / 4.3      # yards -> rough carries
        tv_ours[tm][1] += o.get("rec", 0)

    ratios = []
    for tm in sorted(set(tv_ref) & set(tv_ours)):
        a, b = tv_ours[tm][1], tv_ref[tm][1]          # receptions, the cleaner one
        if a > 20 and b > 20:
            ratios.append((b / a, tm, a, b))
    if len(ratios) >= 8:
        med = statistics.median(r[0] for r in ratios)
        print(f"  TEAM RECEIVING VOLUME")
        print(f"    across {len(ratios)} teams, the market expects "
              f"{med:.2f}x our receptions")
        far = sorted(ratios, key=lambda x: -abs(x[0] - med))[:5]
        for ratio, tm, a, b in far:
            print(f"      {tm:<4} ours {a:>5.0f}  theirs {b:>5.0f}  "
                  f"{ratio:.2f}x")
        if abs(med - 1) < 0.05:
            print(f"    -> our team volume is close; nothing to correct")
        else:
            print(f"    -> we are systematically "
                  f"{'low' if med > 1 else 'high'} on team passing volume")
    else:
        print(f"  TEAM VOLUME: too few teams matched to say")

    # ---- 2. what a slot actually commands --------------------------------
    #
    # The share of his own team's work that each depth slot gets. Ours comes
    # from ROLE_PRIOR, measured historically. Theirs is what the market
    # expects, which prices a listed backup above his historical average
    # because a backup on a real offence carries upside an average buries.
    print(f"\n  SHARE OF TEAM, BY DEPTH SLOT")
    print(f"  {'':<4}{'slot':<6}{'n':>4}{'ours':>9}{'market':>9}{'change':>9}")
    fitted = {}
    for pos in ("RB", "WR", "TE"):
        by_slot = defaultdict(list)
        for k, r in ref.items():
            sl = slot_of.get(k)
            if not sl or sl[0] != pos or sl[1] > 4:
                continue
            tm = r["team"]
            tot = tv_ref[tm][0] if pos == "RB" else tv_ref[tm][1]
            mine = r["rush_att"] if pos == "RB" else r["rec"]
            if tot > 30 and mine >= 0:
                by_slot[sl[1]].append(mine / tot)
        cur_tiers = m.ROLE_PRIOR.get(pos) if hasattr(m, "ROLE_PRIOR") else None
        for slot in sorted(by_slot):
            vals = by_slot[slot]
            if len(vals) < MIN_N:
                print(f"  {pos:<4}{slot:<6}{len(vals):>4}"
                      f"{'—':>9}{'—':>9}{'too few':>9}")
                continue
            market = statistics.median(vals)
            cur = None
            if isinstance(cur_tiers, dict):
                c = cur_tiers.get(slot)
                cur = c if isinstance(c, (int, float)) else (c or [None])[0]
            if cur is None:
                print(f"  {pos:<4}{slot:<6}{len(vals):>4}{'—':>9}"
                      f"{market:>9.3f}{'no prior':>9}")
                continue
            new = round(cur + (market - cur) * 0.5, 3)
            fitted[(pos, slot)] = new
            print(f"  {pos:<4}{slot:<6}{len(vals):>4}{cur:>9.3f}"
                  f"{market:>9.3f}{new - cur:>+9.3f}")

    # ---- 3. efficiency ---------------------------------------------------
    print(f"\n  EFFICIENCY")
    for label, num_f, den_f, ours_num, ours_den in (
            ("yards per carry", "rush_yds", "rush_att", "ruyd", None),
            ("yards per catch", "rec_yds", "rec", "recyd", "rec")):
        pairs = []
        for k, r in ref.items():
            o = ours.get(k)
            if not o or r[den_f] < 30:
                continue
            theirs = r[num_f] / r[den_f]
            if ours_den:
                mine_den = o.get(ours_den, 0)
                if mine_den < 10:
                    continue
                mine = o.get(ours_num, 0) / mine_den
            else:
                continue
            if 1 < theirs < 25 and 1 < mine < 25:
                pairs.append((mine, theirs))
        if len(pairs) >= MIN_N:
            mo = statistics.median(p[0] for p in pairs)
            mt = statistics.median(p[1] for p in pairs)
            print(f"    {label:<18} ours {mo:>5.2f}   market {mt:>5.2f}   "
                  f"{mt - mo:>+5.2f}   n={len(pairs)}")
        else:
            print(f"    {label:<18} too few matched")

    if not fitted:
        print(f"\n  Nothing had enough behind it to move.")
        return
    print(f"\n  These shares are diagnostic. project5 projects points per")
    print(f"  game directly and has no share constant to write them into --")
    print(f"  they tell you what the market believes a slot commands, which")
    print(f"  is worth knowing and is not something this can apply for you.")
    return

    src = (ROOT / "scripts" / "project5.py").read_text()
    block = re.search(r"ROLE_PRIOR = \{.*?\n\}", src, re.S)
    if not block:
        print("  no ROLE_PRIOR in project5.py; shares not written")
        return
    print(f"  {len(fitted)} shares fitted. Review before writing:")
    for (pos, slot), v in sorted(fitted.items()):
        print(f"    {pos}{slot}: {v}")


if __name__ == "__main__":
    main()
