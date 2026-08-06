#!/usr/bin/env python3
"""Compare our projections against several boards at once.

    python3 scripts/consensus.py ~/Downloads/*.csv
    python3 scripts/consensus.py --espn ~/Downloads/fp.csv
    python3 scripts/consensus.py --espn ~/Downloads/fp.csv --outliers 20

WHY SEVERAL AND NOT ONE

Disagreeing with one board tells you very little. It might be them.

Disagreeing with all of them is a different signal entirely, and it is the
one worth acting on: when ESPN, FantasyPros and everyone else put a player
somewhere and we do not, the burden is ours. That distinction found six real
holes in a day -- backups projected as starters, rookies missing, displaced
veterans never demoted -- and none of them would have been obvious from a
single comparison, because a single comparison cannot tell a bug from a
difference of opinion.

So this reads any number of boards, works out where they agree with each
other, and reports where we sit against that agreement rather than against
any one of them.

WHAT IT DOES NOT DO

It does not fetch anything. Point it at files you already have, one per
board. Their numbers stay on your machine and inform your model; nothing
from them is published, and nothing here scrapes anybody.
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


# What a season of fantasy points looks like. Anything outside this is not a
# points column, whatever its header says.
PLAUSIBLE = {
    "top_min": 180,     # the best player in the league scores at least this
    "top_max": 600,     # and not more than this
    "median_max": 250,  # a median player is nowhere near the top
}


def plausible(values, label=""):
    """Does this column look like fantasy points?

    The first version trusted a header, then fell back to "first mostly
    numeric column", and silently picked passing yards on one board. That
    produced a 545-point quarterback, a spread of 523 between two boards, and
    a comparison that looked like catastrophic disagreement rather than a
    parsing bug.
    """
    v = sorted((x for x in values if x is not None), reverse=True)
    if len(v) < 20:
        return False, "too few rows"
    top, med = v[0], v[len(v) // 2]
    if top < PLAUSIBLE["top_min"]:
        return False, f"top value {top:.0f} is too low for a season"
    if top > PLAUSIBLE["top_max"]:
        return False, f"top value {top:.0f} is too high; likely yards"
    if med > PLAUSIBLE["median_max"]:
        return False, f"median {med:.0f} is implausibly high"
    return True, f"top {top:.0f}, median {med:.0f}"


def sniff(path, verbose=True):
    """Find name, position and points columns, and refuse if unsure."""
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 2:
        return []
    hdr = [h.strip() for h in rows[0]]
    low = [h.lower() for h in hdr]

    def find(*cands, contains=False):
        for c in cands:
            for i, h in enumerate(low):
                if h == c or (contains and c in h):
                    return i
        return None

    i_name = find("player", "name", "player name")
    if i_name is None:
        i_name = find("player", contains=True)
    i_pos = find("fantasy position", "pos", "position")
    if i_name is None:
        return []

    def col(i):
        out = []
        for r in rows[1:]:
            if i >= len(r):
                continue
            try:
                out.append(float(str(r[i]).replace(",", "")))
            except ValueError:
                pass
        return out

    # Try named columns first, then every column, and take the first that
    # actually looks like fantasy points.
    order = []
    for c in ("3d proj", "fpts", "points", "proj", "projection", "ppr", "total"):
        i = find(c)
        if i is not None and i not in order:
            order.append(i)
    order += [i for i in range(len(hdr)) if i not in order and i != i_name]

    i_pts, why = None, "no column looked like fantasy points"
    for i in order:
        ok, msg = plausible(col(i))
        if ok:
            i_pts, why = i, f"'{hdr[i] or i}' ({msg})"
            break
    if i_pts is None:
        if verbose:
            print(f"    rejected: {why}")
        return []
    if verbose:
        print(f"    points column {why}")

    out, seen = [], set()
    for r in rows[1:]:
        if max(i_name, i_pts) >= len(r):
            continue
        try:
            pts = float(str(r[i_pts]).replace(",", ""))
        except ValueError:
            continue
        pos = ""
        if i_pos is not None and i_pos < len(r):
            pos = re.sub(r"[^A-Z]", "", r[i_pos].strip().upper())[:2]
        if pos in ("K", "DE", "DS"):
            continue
        k = key(r[i_name])
        if not k or k in seen:
            continue          # a board listing a player twice is one opinion
        seen.add(k)
        out.append({"key": k, "name": r[i_name].strip(), "pos": pos, "pts": pts})
    return out


def load_espn(conn, season):
    try:
        rows = conn.execute("""SELECT name_key, player, position, points
                               FROM espn_proj WHERE season=?""", (season,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"key": r["name_key"], "name": r["player"],
             "pos": r["position"], "pts": r["points"]} for r in rows]


def spearman(pairs):
    n = len(pairs)
    if n < 5:
        return None
    return 1 - 6 * sum((a - b) ** 2 for a, b in pairs) / (n * (n * n - 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="one CSV per board")
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--espn-season", type=int, default=2026)
    ap.add_argument("--espn", action="store_true",
                    help="include the stored ESPN projections as a board")
    # Published boards give a single realistic number. Our full-season
    # column is a best case that assumes a player holds his role all year, so
    # comparing it against them flags every injured player as a disagreement
    # when the two numbers answer different questions.
    ap.add_argument("--basis", default="adjusted", choices=["ppr", "adjusted"])
    ap.add_argument("--outliers", type=int, default=15)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # Store what each board says, so the model can read the spread later.
    #
    # Only the spread is used -- how far apart they are, not where they land.
    # A projection that took their level would be theirs; one that notices
    # they cannot agree is reading uncertainty nobody else publishes.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS board_points (
            season INTEGER, board TEXT, name_key TEXT, player TEXT,
            position TEXT, points REAL,
            PRIMARY KEY (season, board, name_key));
        CREATE INDEX IF NOT EXISTS idx_board_key ON board_points(name_key, season);
    """)

    boards, seen_sig = {}, {}
    for pat in args.files:
        for f in sorted(glob.glob(str(Path(pat).expanduser()))):
            print(f"  {Path(f).name}")
            rows = sniff(f)
            if not rows:
                continue
            # The same file passed twice is one opinion counted as two, and
            # it silently doubles that source's weight in the consensus.
            sig = (len(rows), round(sum(r["pts"] for r in rows)))
            if sig in seen_sig:
                print(f"    skipped: identical to {seen_sig[sig]}")
                continue
            name = Path(f).stem[:22]
            seen_sig[sig] = name
            boards[name] = rows
            for r in rows:
                conn.execute("INSERT OR REPLACE INTO board_points VALUES "
                             "(?,?,?,?,?,?)",
                             (args.espn_season, name, r["key"], r["name"],
                              r["pos"], r["pts"]))
            conn.commit()
            print(f"    loaded {len(rows)} players")
    if args.espn:
        e = load_espn(conn, args.espn_season)
        if e:
            boards["ESPN"] = e
            for r in e:
                conn.execute("INSERT OR REPLACE INTO board_points VALUES "
                             "(?,?,?,?,?,?)",
                             (args.espn_season, "ESPN", r["key"], r["name"],
                              r["pos"], r["pts"]))
            conn.commit()
            print(f"  ESPN (stored): {len(e)} players")

    if not boards:
        sys.exit("\n  no boards loaded. Pass CSV files, or --espn.")

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p5", str(ROOT / "scripts" / "project5.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    ours = {key(r["name"]): r for r in
            m.build(conn, args.season, m.roster(), m.crosswalk(conn))}
    print(f"  ours: {len(ours)} players\n")

    # --- where the boards agree with each other --------------------------
    #
    # A player every board ranks alike is settled. One they scatter on is
    # contested, and our disagreement there means much less.
    # Key on name AND position. Name alone matched a tight end on one board
    # to a quarterback on another and reported the difference as
    # disagreement.
    agg = defaultdict(dict)
    for name, rows in boards.items():
        for r in rows:
            pos = r["pos"] or (ours.get(r["key"]) or {}).get("pos", "")
            if pos not in POS:
                continue
            agg[(r["key"], pos)][name] = r
    consensus = {}
    for (k, pos), per in agg.items():
        if len(per) < 1:
            continue
        pts = [v["pts"] for v in per.values()]
        any_row = next(iter(per.values()))
        consensus[k] = {
            "name": any_row["name"],
            "pos": pos,
            "mean": statistics.mean(pts),
            "spread": (max(pts) - min(pts)) if len(pts) > 1 else None,
            "n": len(per),
        }

    multi = sum(1 for v in consensus.values() if v["n"] > 1)
    print(f"  {len(consensus)} players across {len(boards)} board"
          f"{'s' if len(boards) != 1 else ''}, {multi} on more than one\n")

    # --- how we sit against the agreement --------------------------------
    print(f"  {'POS':<5}{'n':>5}{'MEDIAN GAP':>12}{'WITHIN 25':>11}{'RANK CORR':>11}")
    allg, rhos = [], []
    for pos in POS:
        mine = sorted([r for k, r in ours.items() if r["pos"] == pos],
                      key=lambda r: -r[args.basis])[:60]
        matched = [r for r in mine
                   if key(r["name"]) in consensus
                   and (consensus[key(r["name"])]["pos"] or pos) == pos]
        if len(matched) < 8:
            print(f"  {pos:<5}{len(matched):>5}   too few matched")
            continue
        gaps = [r[args.basis] - consensus[key(r["name"])]["mean"] for r in matched]
        theirs = sorted(matched, key=lambda r: -consensus[key(r["name"])]["mean"])
        tr = {key(r["name"]): i for i, r in enumerate(theirs)}
        rho = spearman([(i, tr[key(r["name"])]) for i, r in enumerate(matched)])
        print(f"  {pos:<5}{len(matched):>5}{statistics.median(gaps):>+12.0f}"
              f"{sum(1 for g in gaps if abs(g) <= 25):>8}/{len(matched):<3}"
              f"{(rho if rho is not None else 0):>+11.2f}")
        allg += gaps
        if rho is not None:
            rhos.append(rho)
    if allg:
        print(f"  {'ALL':<5}{len(allg):>5}{statistics.median(allg):>+12.0f}"
              f"{sum(1 for g in allg if abs(g) <= 25):>8}/{len(allg):<3}"
              f"{statistics.mean(rhos):>+11.2f}")

    # --- the part worth acting on ----------------------------------------
    #
    # Split disagreements by whether the boards agree with each other. Where
    # they do and we do not, it is probably us. Where they scatter, nobody
    # knows and we are entitled to our own answer.
    if len(boards) > 1:
        agreed, contested = [], []
        for k, c in consensus.items():
            o = ours.get(k)
            if not o or c["n"] < 2 or c["spread"] is None:
                continue
            gap = o[args.basis] - c["mean"]
            if abs(gap) < 20:
                continue
            (agreed if c["spread"] <= 30 else contested).append(
                (abs(gap), c["name"], c["pos"], o[args.basis], c["mean"],
                 c["spread"], c["n"]))

        print(f"\n  THEY AGREE, WE DO NOT  ({len(agreed)})")
        print(f"  The burden is ours on these.\n")
        print(f"  {'PLAYER':<24}{'POS':<5}{'OURS':>7}{'THEIRS':>8}"
              f"{'SPREAD':>8}{'BOARDS':>8}")
        for _, name, pos, mine_, theirs_, sp, n in sorted(agreed, reverse=True)[:args.outliers]:
            print(f"  {name[:24]:<24}{pos:<5}{mine_:>7.0f}{theirs_:>8.0f}"
                  f"{sp:>8.0f}{n:>8}")
        if not agreed:
            print("    none")

        print(f"\n  THEY DISAGREE TOO  ({len(contested)})")
        print(f"  Nobody knows. Our number is as defensible as theirs.\n")
        for _, name, pos, mine_, theirs_, sp, n in sorted(contested, reverse=True)[:8]:
            print(f"  {name[:24]:<24}{pos:<5}{mine_:>7.0f}{theirs_:>8.0f}"
                  f"{sp:>8.0f}{n:>8}")
        if not contested:
            print("    none")
    else:
        print("\n  Only one board loaded, so there is no way to tell a bug from")
        print("  a difference of opinion. Add a second and the split appears.")


if __name__ == "__main__":
    main()
