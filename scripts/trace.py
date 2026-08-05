#!/usr/bin/env python3
"""Print every intermediate value behind one player's projection.

    python3 scripts/trace.py "James Cook" --season 2025
    python3 scripts/trace.py "James Cook" --season 2025 --compare 1621

Built because three separate edits during one session silently failed to
apply, and each time the model was verified by reading the code rather than by
reading the numbers. This reads the numbers.

It recomputes the projection step by step in the open, so a disagreement with
consensus resolves to a specific line rather than a shrug. Every stage prints
its inputs, its output, and where the value came from, so a wrong answer
points at the stage that produced it.

Deliberately independent of project3.py's internals: it queries the database
directly and does the arithmetic here. If the two disagree, that is itself the
finding -- a trace that imports the thing it is checking can only ever agree
with it.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROSTER = ROOT / "rosters" / "nfl.csv"

YEAR_WEIGHTS = [0.75, 0.20, 0.05]
SAMPLE_POWER = 1.5
REGRESS = {"td_rate": 0.52, "ypc": 0.85, "ypt": 0.44, "catch_rate": 0.42}
# The model weights its position means by opportunity so a fourth-stringer
# does not drag a starter's baseline down. The tracer used a plain average and
# the two disagreed by 38 points on De'Von Achane -- which is exactly the sort
# of thing this tool exists to surface, but only useful once fixed.
OPPORTUNITY_WEIGHTED = True
DEPTH_NORM = {"RB": 0.78, "WR": 0.82, "TE": 0.82, "QB": 0.82, "FB": 0.80}
AVAIL_WEIGHTS = [0.30, 0.28, 0.24, 0.18]
SCORING = {"rush_yd": 0.10, "rec_yd": 0.10, "rush_td": 6.0, "rec_td": 6.0,
           "reception": 1.0}
ROLE_PRIOR = {
    "RB": {1: (0.55, 0.11), 2: (0.25, 0.07), 3: (0.10, 0.04), 4: (0.04, 0.02)},
    "WR": {1: (0.01, 0.24), 2: (0.01, 0.18), 3: (0.01, 0.12), 4: (0.00, 0.06)},
    "TE": {1: (0.00, 0.15), 2: (0.00, 0.07), 3: (0.00, 0.03), 4: (0.00, 0.01)},
}
ROLE_PULL = 0.55


def line(label, value, note=""):
    print(f"    {label:<34} {value:>12}   {note}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--compare", type=float,
                    help="a number from another source, for context")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    who = conn.execute("""SELECT player_id, player_name, position, team
                          FROM weekly_stats WHERE player_name LIKE ?
                          AND season=? ORDER BY week DESC LIMIT 1""",
                       (f"%{args.name}%", args.season)).fetchone()
    if not who:
        sys.exit(f"  no {args.name} in {args.season}")
    pid, pos = who["player_id"], who["position"]
    print(f"\n  {who['player_name']}  {pos}  (played {args.team if False else who['team']} "
          f"in {args.season})")

    # ---- roster --------------------------------------------------------
    ros = None
    if ROSTER.exists():
        for r in csv.DictReader(ROSTER.open()):
            if args.name.lower() in (r.get("name") or "").lower():
                ros = r
                break
    print(f"\n  1. ROSTER")
    if ros:
        line("current team", ros.get("team") or "—")
        line("depth position", ros.get("depth_pos") or "—")
        line("depth order", ros.get("depth_order") or "—",
             "<- drives the role prior")
        line("ADP", ros.get("adp") or "—")
    else:
        line("not found", "—", "<- no current team, no depth slot, no prior")

    # ---- usage ---------------------------------------------------------
    print(f"\n  2. USAGE, weighted across three seasons")
    seasons = [args.season, args.season - 1, args.season - 2]
    acc, wsum, played = {}, 0.0, 0.0
    print(f"    {'season':<8} {'g':>3} {'car':>5} {'tgt':>5} {'ryd':>6} "
          f"{'weight':>7} {'why':<22}")
    for w, s in zip(YEAR_WEIGHTS, seasons):
        r = conn.execute("""SELECT COUNT(*) g, SUM(carries) car, SUM(targets) tgt,
                            SUM(rushing_yards) ry, SUM(receptions) rec,
                            SUM(receiving_yards) recyd, SUM(rushing_tds) rtd,
                            SUM(receiving_tds) rectd
                            FROM weekly_stats WHERE player_id=? AND season=?
                            AND season_type='REG'""", (pid, s)).fetchone()
        if not r or not r["g"]:
            print(f"    {s:<8} {'—':>3}")
            continue
        adj = w * ((min(17, r["g"]) / 17.0) ** SAMPLE_POWER)
        why = f"{w} x ({r['g']}/17)^{SAMPLE_POWER}"
        print(f"    {s:<8} {r['g']:>3} {r['car'] or 0:>5.0f} {r['tgt'] or 0:>5.0f} "
              f"{r['ry'] or 0:>6.0f} {adj:>7.3f} {why:<22}")
        for k in ("car", "tgt", "ry", "rec", "recyd", "rtd", "rectd"):
            acc[k] = acc.get(k, 0.0) + (r[k] or 0) / r["g"] * adj
        played += min(17, r["g"]) * adj
        wsum += adj
    if not wsum:
        sys.exit("  no usable seasons")
    for k in acc:
        acc[k] /= wsum
    line("weighted carries per game", f"{acc.get('car', 0):.2f}")
    line("weighted targets per game", f"{acc.get('tgt', 0):.2f}")

    # ---- team ----------------------------------------------------------
    print(f"\n  3. TEAM VOLUME")
    hist_team = who["team"]
    tot = conn.execute("""SELECT SUM(carries) car, SUM(targets) tgt
                          FROM weekly_stats WHERE season=? AND team=?
                          AND season_type='REG'""",
                       (args.season, hist_team)).fetchone()
    league = conn.execute("""SELECT AVG(c) mc, AVG(t) mt FROM
                             (SELECT SUM(carries) c, SUM(targets) t
                              FROM weekly_stats WHERE season=? AND season_type='REG'
                              AND team IS NOT NULL GROUP BY team)""",
                          (args.season,)).fetchone()
    team_car = (tot["car"] or 0) * 0.75 + (league["mc"] or 0) * 0.25
    team_tgt = (tot["tgt"] or 0) * 0.75 + (league["mt"] or 0) * 0.25
    line(f"{hist_team} carries {args.season}", f"{tot['car'] or 0:.0f}",
         f"league mean {league['mc']:.0f}")
    line("regressed 75/25 to mean", f"{team_car:.0f}")
    line(f"{hist_team} targets {args.season}", f"{tot['tgt'] or 0:.0f}",
         f"league mean {league['mt']:.0f}")
    line("regressed 75/25 to mean", f"{team_tgt:.0f}")

    # ---- share ---------------------------------------------------------
    print(f"\n  4. SHARE")
    own = conn.execute("""SELECT SUM(carries) car, SUM(targets) tgt
                          FROM weekly_stats WHERE player_id=? AND season=?
                          AND season_type='REG'""", (pid, args.season)).fetchone()
    # The denominator the model uses: only players it kept in `raw`.
    kept = conn.execute("""SELECT SUM(car) c, SUM(tgt) t FROM
        (SELECT player_id, SUM(carries) car, SUM(targets) tgt, COUNT(*) g
         FROM weekly_stats WHERE season=? AND team=? AND season_type='REG'
         GROUP BY player_id HAVING g >= 1)""",
        (args.season, hist_team)).fetchone()
    share_c = (own["car"] or 0) / max(0.01, kept["c"] or 1)
    share_t = (own["tgt"] or 0) / max(0.01, kept["t"] or 1)
    line("his carries", f"{own['car'] or 0:.0f}")
    line("team carries (denominator)", f"{kept['c'] or 0:.0f}")
    line("carry share", f"{share_c:.3f}")
    line("his targets", f"{own['tgt'] or 0:.0f}")
    line("target share", f"{share_t:.3f}")

    slot = None
    try:
        slot = int(ros["depth_order"]) if ros and ros.get("depth_order") else None
    except (TypeError, ValueError):
        slot = None
    prior = (ROLE_PRIOR.get(pos, {}) or {}).get(slot) if slot else None
    if prior:
        pc, pt = prior
        career = conn.execute("""SELECT SUM(carries) c, SUM(targets) t, COUNT(*) g
                                 FROM weekly_stats WHERE player_id=?
                                 AND season_type='REG'""", (pid,)).fetchone()
        touches = (career["c"] or 0) + (career["t"] or 0)
        pull = ROLE_PULL
        if career["g"] >= 25 and touches < career["g"] * 6:
            pull = ROLE_PULL * 0.25
        elif career["g"] >= 25:
            pull = ROLE_PULL * 0.75
        before_c, before_t = share_c, share_t
        # Raise only, never demote. See project3.py.
        share_c = share_c*(1-pull) + pc*pull if pc > share_c else share_c
        share_t = share_t*(1-pull) + pt*pull if pt > share_t else share_t
        line(f"role prior for slot {slot}", f"{pc:.3f} / {pt:.3f}",
             f"pull {pull:.2f}")
        line("carry share after prior", f"{share_c:.3f}",
             f"was {before_c:.3f}" + ("   <- LOWERED" if share_c < before_c else ""))
        line("target share after prior", f"{share_t:.3f}",
             f"was {before_t:.3f}" + ("   <- LOWERED" if share_t < before_t else ""))
    else:
        line("role prior", "none", "no depth slot")

    car_s = team_car * share_c
    tgt_s = team_tgt * share_t
    line("season carries", f"{car_s:.0f}")
    line("season targets", f"{tgt_s:.0f}")
    floor_c = acc.get("car", 0) * 17 * 0.85
    if car_s < floor_c:
        line("history floor applied", f"{floor_c:.0f}", "<- share was lower")
        car_s = floor_c

    # ---- availability (separate, never in the headline) ----------------
    print(f"\n  5. AVAILABILITY  (a separate column, NOT in the headline)")
    av_seasons = seasons + [min(seasons) - 1]
    ap_, aw = 0.0, 0.0
    for w, s in zip(AVAIL_WEIGHTS, av_seasons):
        r = conn.execute("""SELECT COUNT(*) g FROM weekly_stats
                            WHERE player_id=? AND season=? AND season_type='REG'""",
                         (pid, s)).fetchone()
        if r and r["g"]:
            line(f"  {s} games played", f"{r['g']}", f"weight {w}")
            ap_ += min(17, r["g"]) * w
            aw += w
    rate = (ap_ / aw / 17.0) if aw else 0.85
    norm = DEPTH_NORM.get(pos, 0.80)
    exp_g = 17 * (rate * 0.5 + norm * 0.5)
    line("four-season availability rate", f"{rate:.3f}")
    line("position norm", f"{norm:.2f}")
    line("expected games", f"{exp_g:.1f}")

    # ---- efficiency -----------------------------------------------------
    print(f"\n  6. EFFICIENCY, each rate regressed toward the position mean")

    def eff(label, own, expr, having, key):
        rows_ = conn.execute(f"""SELECT {expr} v,
            SUM(carries)+SUM(targets) w FROM weekly_stats
            WHERE season=? AND position=? AND season_type='REG'
            GROUP BY player_id HAVING {having}""", (args.season, pos)).fetchall()
        vals = [(r["v"], r["w"]) for r in rows_ if r["v"] is not None and r["w"]]
        if OPPORTUNITY_WEIGHTED and vals:
            mean = sum(v * w for v, w in vals) / sum(w for _, w in vals)
        else:
            mean = sum(v for v, _ in vals) / len(vals) if vals else 0.0
        w = REGRESS[key]
        val = own * (1 - w) + mean * w if own is not None else mean
        line(label, f"{val:.3f}",
             f"his {own:.3f}, mean {mean:.3f}, regressed {w:.0%}"
             if own is not None else f"no sample, using mean {mean:.3f}")
        return val

    own_ypc = acc.get("ry", 0) / acc["car"] if acc.get("car") else None
    own_cr = acc.get("rec", 0) / acc["tgt"] if acc.get("tgt") else None
    own_ypt = acc.get("recyd", 0) / acc["tgt"] if acc.get("tgt") else None
    opp = acc.get("car", 0) + acc.get("tgt", 0)
    own_td = ((acc.get("rtd", 0) + acc.get("rectd", 0)) / opp) if opp else None

    ypc = eff("yards per carry", own_ypc,
              "SUM(rushing_yards)*1.0/SUM(carries)", "SUM(carries)>20", "ypc")
    cr = eff("catch rate", own_cr,
             "SUM(receptions)*1.0/SUM(targets)", "SUM(targets)>15", "catch_rate")
    ypt = eff("yards per target", own_ypt,
              "SUM(receiving_yards)*1.0/SUM(targets)", "SUM(targets)>15", "ypt")
    tdr = eff("TD per opportunity", own_td,
              "(SUM(rushing_tds)+SUM(receiving_tds))*1.0/(SUM(carries)+SUM(targets))",
              "SUM(carries)+SUM(targets)>25", "td_rate")

    # ---- the arithmetic --------------------------------------------------
    print(f"\n  7. THE POINTS, at a full 17 games")
    rec = tgt_s * cr
    recyd = tgt_s * ypt
    ruyd = car_s * ypc
    tds = (tgt_s + car_s) * tdr
    print(f"    {'component':<30} {'calculation':<30} {'points':>8}")
    print(f"    {'receptions':<30} {f'{tgt_s:.0f} tgt x {cr:.3f}':<30} "
          f"{rec:>8.1f}")
    print(f"    {'receiving yards':<30} {f'{tgt_s:.0f} x {ypt:.2f} x 0.1':<30} "
          f"{recyd * 0.1:>8.1f}")
    print(f"    {'rushing yards':<30} {f'{car_s:.0f} x {ypc:.2f} x 0.1':<30} "
          f"{ruyd * 0.1:>8.1f}")
    print(f"    {'touchdowns':<30} {f'{tgt_s + car_s:.0f} opp x {tdr:.4f} x 6':<30} "
          f"{tds * 6:>8.1f}")
    total = rec + recyd * 0.1 + ruyd * 0.1 + tds * 6
    print(f"    {'':<30} {'':<30} {'-'*8}")
    print(f"    {'HEADLINE (full season)':<30} {'':<30} {total:>8.1f}")
    print(f"    {'ADJUSTED':<30} {f'x {exp_g/17:.3f} for {exp_g:.1f} games':<30} "
          f"{total * exp_g/17:>8.1f}")
    print(f"\n    stat line: {rec:.0f} rec, {recyd:.0f} rec yds, "
          f"{ruyd:.0f} rush yds, {tds:.1f} TD")
    if args.compare:
        print(f"    for comparison: {args.compare:.0f}  "
              f"({100*(total/args.compare - 1):+.0f}%)")


if __name__ == "__main__":
    main()
