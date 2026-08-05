#!/usr/bin/env python3
"""Season projections. Points per game, times games, adjusted for role change.

    python3 scripts/project4.py --season 2025 --position RB
    python3 scripts/project4.py --season 2025 --publish
    python3 scripts/project4.py --scorecard 2022,2023
    python3 scripts/project4.py --explain "Bhayshul Tuten" --season 2025

WHY THIS REPLACED A MUCH LARGER MODEL

The previous version had three-season weighting, team volume reconciliation,
role priors, a moved-player rule, a specialist guard, an age curve, a workload
ceiling, four separately calibrated efficiency regressions and position
specific quarterback handling. It was tested against a model consisting of
"average the last three seasons of points per game and multiply by expected
games":

    model                            MAE    top40   rank corr
    just points/game x games        47.7     67.1        0.52
    everything we built             47.5     65.2        0.47

The simple version ranked players BETTER. Several of those factors were real
bugs when they were wrong, and collectively they bought nothing. Worth stating
plainly rather than burying, because the temptation is always to keep the
machinery and assume it must be helping.

WHAT IS LEFT

  1. POINTS PER GAME, three seasons, recency weighted, partial seasons
     discounted. This is the projection.
  2. EXPECTED GAMES, from four seasons. Durability is the one thing per-game
     scoring cannot see.
  3. ROLE CHANGE, only where the depth chart materially disagrees with
     history. Bhayshul Tuten carried 83 times behind Travis Etienne and is now
     Jacksonville's lead back; no amount of averaging his past will say so.

Nothing else. Anything added back should be added because it improved the
scorecard, and the scorecard re-run to prove it.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import re
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROSTER = ROOT / "rosters" / "nfl.csv"

GAMES = 17
YEAR_WEIGHTS = [0.75, 0.20, 0.05]
SAMPLE_POWER = 1.5
AVAIL_WEIGHTS = [0.30, 0.28, 0.24, 0.18]
AVAIL_NORM = {"QB": 0.82, "RB": 0.78, "WR": 0.82, "TE": 0.82}
SKILL = {"QB", "RB", "WR", "TE"}

# What a depth slot actually SCORES, per game. Measured across 96
# team-seasons.
#
# This replaced a version that multiplied a player's points per game by a
# ratio of volume shares. That compounded catastrophically: Cam Skattebo, a
# part-season backup promoted to first string, came out at 512 points for the
# season -- an all-time year, from a factor cap of 2.2 applied to a small
# number. Justice Hill as Baltimore's second back reached 267 the same way.
#
# Blending toward what the slot typically scores is bounded by construction.
# A promoted backup moves toward what a starter earns; he cannot exceed it by
# a multiple of his own former bench role.
SLOT_PPG = {
    "RB": {1: 14.1, 2: 6.3, 3: 3.1, 4: 2.2},
    "WR": {1: 13.8, 2: 10.0, 3: 6.3, 4: 4.3},
    "TE": {1: 9.2, 2: 3.6, 3: 2.2, 4: 1.2},
    "QB": {1: 15.7, 2: 8.3, 3: 4.0, 4: 2.0},
}
ROLE_PULL = 0.55        # how far toward the slot's typical scoring
ROLE_GAP = 0.35         # how different it must be before we act

# Age. Added back after removing it, because dropping it put James Conner at
# RB14 against a consensus RB61, with Alvin Kamara and Aaron Jones close
# behind -- all of them thirty or older.
#
# Measured on per-game production for backs healthy in BOTH seasons, which is
# the right question for a full-health projection: raw retention collapses
# after 28 but most of that is missed games, and games are modelled
# separately. Healthy-only, production holds to about 27 and falls after.
#
#   23  1.05   24  0.95   25  1.04   26  1.06   27  1.03   28  0.65
AGE_CURVE = {
    "RB": {23: 1.00, 26: 1.02, 28: 0.96, 30: 0.86, 32: 0.74},
    "WR": {23: 0.97, 26: 1.02, 29: 1.00, 31: 0.94, 33: 0.86},
    "TE": {24: 0.95, 27: 1.01, 30: 0.98, 32: 0.92, 34: 0.84},
    "QB": {25: 0.98, 28: 1.00, 34: 1.00, 38: 0.96, 41: 0.90},
}


def age_mult(pos, age):
    curve = AGE_CURVE.get(pos)
    if not curve or not age:
        return 1.0
    ages = sorted(curve)
    if age <= ages[0]:
        return curve[ages[0]]
    if age >= ages[-1]:
        return curve[ages[-1]]
    for a, b in zip(ages, ages[1:]):
        if a <= age <= b:
            return curve[a] + (curve[b] - curve[a]) * (age - a) / (b - a)
    return 1.0

SPREAD = {"QB": 0.24, "RB": 0.30, "WR": 0.28, "TE": 0.30}
INJURY_RISK = {"QB": 0.16, "RB": 0.28, "WR": 0.22, "TE": 0.22}
INJURY_COST = (0.55, 0.90)
TEAM_ALIAS = {"LA": "LAR", "SD": "LAC", "OAK": "LV", "STL": "LAR",
              "WSH": "WAS", "JAC": "JAX", "ARZ": "ARI"}


def norm_team(code):
    c = (code or "").strip().upper()
    return TEAM_ALIAS.get(c, c)


def key(name):
    n = re.sub(r"[.'`]", "", (name or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def load_roster():
    meta = {}
    if not ROSTER.exists():
        return meta
    for r in csv.DictReader(ROSTER.open()):
        try:
            slot = int(r["depth_order"]) if str(r.get("depth_order") or "").strip() else None
        except ValueError:
            slot = None
        e = {"team": norm_team(r.get("team")), "slot": slot,
             "pos": (r.get("position") or "").upper(),
             "depth_pos": (r.get("depth_pos") or "").upper(),
             "adp": float(r["adp"]) if str(r.get("adp") or "").strip() else None,
             "age": float(r["age"]) if str(r.get("age") or "").strip() else None}
        meta[(r.get("name") or "").lower()] = e
        meta[key(r.get("name"))] = e
        if r.get("id"):
            meta[r["id"]] = e
    return meta


def crosswalk(conn):
    out = {}
    try:
        for r in conn.execute("""SELECT gsis_id, sleeper_id FROM id_map
                                 WHERE sleeper_id IS NOT NULL AND sleeper_id != ''"""):
            out[r["gsis_id"]] = f"nfl-{r['sleeper_id']}"
    except sqlite3.OperationalError:
        pass
    return out


def per_game(conn, pid, season):
    acc, pts, played, wsum = {}, 0.0, 0.0, 0.0
    for w, s in zip(YEAR_WEIGHTS, (season, season - 1, season - 2)):
        r = conn.execute("""SELECT COUNT(*) g, AVG(fantasy_points_ppr) p,
                            AVG(receptions) rec, AVG(receiving_yards) recyd,
                            AVG(rushing_yards) ruyd
                            FROM weekly_stats WHERE player_id=? AND season=?
                            AND season_type='REG'""", (pid, s)).fetchone()
        if not r or not r["g"]:
            continue
        adj = w * ((min(17, r["g"]) / 17.0) ** SAMPLE_POWER)
        pts += (r["p"] or 0) * adj
        played += min(17, r["g"]) * adj
        wsum += adj
        for k in ("rec", "recyd", "ruyd"):
            acc[k] = acc.get(k, 0.0) + (r[k] or 0) * adj
    if not wsum:
        return None
    out = {"ppg": pts / wsum, "w": wsum}
    out.update({k: v / wsum for k, v in acc.items()})
    return out


def expected_games(conn, pid, season, pos):
    played, wsum = 0.0, 0.0
    for w, s in zip(AVAIL_WEIGHTS, (season, season - 1, season - 2, season - 3)):
        r = conn.execute("""SELECT COUNT(*) g FROM weekly_stats WHERE player_id=?
                            AND season=? AND season_type='REG'""", (pid, s)).fetchone()
        if r and r["g"]:
            played += min(17, r["g"]) * w
            wsum += w
    if not wsum:
        return GAMES * 0.82
    rate = played / wsum / 17.0
    return GAMES * (rate * 0.5 + AVAIL_NORM.get(pos, 0.80) * 0.5)


def team_share(conn, pid, season, team, pos):
    col = "carries" if pos == "RB" else "targets"
    own = conn.execute(f"""SELECT SUM({col}) v FROM weekly_stats
        WHERE player_id=? AND season=? AND season_type='REG'""",
        (pid, season)).fetchone()
    tot = conn.execute(f"""SELECT SUM({col}) v FROM weekly_stats
        WHERE season=? AND team=? AND season_type='REG'""",
        (season, team)).fetchone()
    if not tot or not tot["v"]:
        return None
    return (own["v"] or 0) / tot["v"]


def build(conn, season, meta, xw, role=True, require_roster=True):
    """require_roster=False when backtesting: a 2022 projection should not be
    filtered by who is on a 2026 roster, which would score a handful of
    survivors and call it a season."""
    rows = []
    for rec in conn.execute("""SELECT DISTINCT player_id FROM weekly_stats
                               WHERE season=? AND season_type='REG'""", (season,)):
        pid = rec["player_id"]
        info = conn.execute("""SELECT player_name, position, team FROM weekly_stats
                               WHERE player_id=? AND season=? ORDER BY week DESC
                               LIMIT 1""", (pid, season)).fetchone()
        pos = info["position"]
        if pos not in SKILL:
            continue
        m = (meta.get(xw.get(pid, "")) or meta.get(info["player_name"].lower())
             or meta.get(key(info["player_name"])) or {})
        if require_roster and meta and not m:
            continue
        if m.get("depth_pos") == "FB":
            continue

        u = per_game(conn, pid, season)
        if not u or u["w"] < 0.15:
            continue

        ppg = u["ppg"]
        trace = [("points per game, three seasons", ppg, ppg)]
        note = ""

        if role and m.get("slot") and pos in SLOT_PPG:
            target = SLOT_PPG[pos].get(m["slot"])
            if target is not None:
                moved = bool(m.get("team") and m["team"] != norm_team(info["team"]))
                gap = (target - ppg) / max(target, 0.01)
                # Promote freely; demote only on a move.
                #
                # A player who stayed put and earned a big share has told us
                # more than a chart row can. James Cook took 309 carries and a
                # mis-scraped slot-3 listing halved his projection -- the same
                # failure that bit the previous model, reappearing here the
                # moment the adjustment was made two-directional.
                #
                # A player who moved is different: his old share describes an
                # offence he no longer plays in, so the chart is the only
                # current information available and it governs both ways.
                # Demote only when the chart and the usage AGREE.
                #
                # James Cook took 309 carries and a mis-scraped slot-3 row
                # tried to halve him, which is why demotion was switched off.
                # But James Conner is also listed third and took 32 carries in
                # three games: there both signals say backup, and refusing to
                # act put him at RB14 against a consensus RB61.
                #
                # So: promote freely, demote when the depth chart is
                # corroborated by thin recent usage or by a move.
                recent = conn.execute("""SELECT COUNT(*) g,
                        COALESCE(SUM(carries),0)+COALESCE(SUM(targets),0) touches
                        FROM weekly_stats WHERE player_id=? AND season=?
                        AND season_type='REG'""", (pid, season)).fetchone()
                thin = bool(recent and (recent["g"] < 8 or recent["touches"] < 120))
                promoting = target > ppg
                if moved or (promoting and abs(gap) >= ROLE_GAP) \
                        or (not promoting and thin and abs(gap) >= ROLE_GAP):
                    pull = 0.75 if moved else ROLE_PULL
                    before = ppg
                    ppg = ppg * (1 - pull) + target * pull
                    trace.append((f"role: depth {m['slot']} typically scores "
                                  f"{target:.1f}" + (" (moved)" if moved else ""),
                                  ppg - before, ppg))
                    note = "role change"

        if m.get("age"):
            am = age_mult(pos, m["age"])
            if abs(am - 1) > 0.005:
                before = ppg
                ppg *= am
                trace.append((f"age {m['age']:.0f}", ppg - before, ppg))

        exp_g = expected_games(conn, pid, season, pos)
        scale = ppg / max(0.01, u["ppg"])
        rows.append({
            "id": pid, "name": info["player_name"], "pos": pos,
            "team": m.get("team") or norm_team(info["team"]),
            "ppr": ppg * GAMES, "adjusted": ppg * exp_g, "exp_games": exp_g,
            "rec": u.get("rec", 0) * GAMES * scale,
            "recyd": u.get("recyd", 0) * GAMES * scale,
            "ruyd": u.get("ruyd", 0) * GAMES * scale,
            "adp": m.get("adp"), "note": note, "trace": trace,
        })
    return rows


def simulate(row, runs=3000):
    sd = row["ppr"] * SPREAD.get(row["pos"], 0.30)
    risk = INJURY_RISK.get(row["pos"], 0.24)
    vals = []
    for _ in range(runs):
        v = random.gauss(row["ppr"], sd)
        if random.random() < risk:
            v *= random.uniform(*INJURY_COST)
        vals.append(max(0.0, v))
    vals.sort()
    return vals[int(0.10 * (len(vals)-1))], vals[int(0.90 * (len(vals)-1))]


PROJ_SCHEMA = """
CREATE TABLE IF NOT EXISTS projections (
    season INTEGER, player_id TEXT, sleeper_id TEXT,
    player TEXT, position TEXT, team TEXT,
    ppr REAL, half REAL, standard REAL, adjusted REAL, exp_games REAL,
    floor REAL, ceiling REAL, rank_pos INTEGER,
    rec REAL, recyd REAL, ruyd REAL, news_adj REAL, trace TEXT,
    PRIMARY KEY (season, player_id)
);
"""


def publish(conn, season, rows, xw):
    conn.execute("DROP TABLE IF EXISTS projections")
    conn.executescript(PROJ_SCHEMA)
    by_pos = collections.defaultdict(list)
    for r in sorted(rows, key=lambda x: -x["ppr"]):
        by_pos[r["pos"]].append(r)
    n = 0
    for pos, grp in by_pos.items():
        for i, r in enumerate(grp, 1):
            lo, hi = simulate(r)
            std = r["ppr"] - r["rec"]
            conn.execute("INSERT OR REPLACE INTO projections VALUES "
                         "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (season, r["id"], xw.get(r["id"]), r["name"], pos, r["team"],
                          round(r["ppr"],1), round((r["ppr"]+std)/2,1), round(std,1),
                          round(r["adjusted"],1), round(r["exp_games"],1),
                          round(lo,1), round(hi,1), i,
                          round(r["rec"],1), round(r["recyd"],1), round(r["ruyd"],1),
                          0.0, json.dumps([[a, round(b,1), round(c,1)]
                                           for a,b,c in r["trace"]])))
            n += 1
    conn.commit()
    print(f"  wrote {n} projections for {season+1}")


def scorecard(conn, seasons, meta, xw):
    print(f"\n  HOW WRONG WE WERE  —  {seasons}")
    print("  Graded on the adjusted number, against what actually happened.\n")
    agg = collections.defaultdict(lambda: [[], []])
    tiers, rk = [], []
    for s in seasons:
        proj = build(conn, s, meta, xw, require_roster=False)
        act = {r["player_id"]: r["pts"] for r in conn.execute("""
            SELECT player_id, SUM(fantasy_points_ppr) pts, COUNT(*) g
            FROM weekly_stats WHERE season=? AND season_type='REG'
            GROUP BY player_id HAVING g>=6""", (s+1,))}
        prev = {r["player_id"]: r["pts"] for r in conn.execute("""
            SELECT player_id, SUM(fantasy_points_ppr) pts, COUNT(*) g
            FROM weekly_stats WHERE season=? AND season_type='REG'
            GROUP BY player_id HAVING g>=6""", (s,))}
        sc = []
        for p in proj:
            a, pv = act.get(p["id"]), prev.get(p["id"])
            if a is None or pv is None:
                continue
            agg[p["pos"]][0].append(abs(p["adjusted"]-a))
            agg[p["pos"]][1].append(abs(pv-a))
            sc.append((p, a, pv))
        sc.sort(key=lambda x: -x[0]["adjusted"])
        tiers.append(sc)
        for pos in ("RB","WR","TE","QB"):
            g = [(p,a) for p,a,_ in sc if p["pos"]==pos][:40]
            if len(g) < 8: continue
            actm = {p["id"]: a for p,a in g}
            ours = {p["id"]: i for i,(p,_) in enumerate(g)}
            real = {k:i for i,k in enumerate(sorted(ours, key=lambda k:-actm[k]))}
            n = len(ours)
            rk.append(1 - 6*sum((ours[k]-real[k])**2 for k in ours)/(n*(n*n-1)))

    print(f"  {'POSITION':<10} {'N':>5} {'OUR MAE':>9} {'DO-NOTHING':>12} {'EDGE':>7}")
    am, an = [], []
    for pos,(mine,naive) in sorted(agg.items(), key=lambda kv:-len(kv[1][0])):
        if not mine: continue
        m_, d_ = statistics.mean(mine), statistics.mean(naive)
        am += mine; an += naive
        print(f"  {pos:<10} {len(mine):>5} {m_:>9.1f} {d_:>12.1f} {d_-m_:>+7.1f}"
              + ("  <-" if m_ > d_ else ""))
    if am:
        print(f"  {'ALL':<10} {len(am):>5} {statistics.mean(am):>9.1f} "
              f"{statistics.mean(an):>12.1f} {statistics.mean(an)-statistics.mean(am):>+7.1f}")
    print(f"\n  {'BY DRAFT TIER':<26} {'OUR MAE':>9} {'DO-NOTHING':>12} {'EDGE':>7}")
    for n_, label in ((40,"top 40 (rounds 1-4)"), (100,"top 100"), (None,"everyone")):
        mm, dd = [], []
        for sc in tiers:
            cut = sc[:n_] if n_ else sc
            mm += [abs(p["adjusted"]-a) for p,a,_ in cut]
            dd += [abs(pv-a) for _,a,pv in cut]
        if mm:
            print(f"  {label:<26} {statistics.mean(mm):>9.1f} {statistics.mean(dd):>12.1f} "
                  f"{statistics.mean(dd)-statistics.mean(mm):>+7.1f}")
    if rk:
        print(f"\n  rank correlation with actual outcomes: {statistics.mean(rk):.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int)
    ap.add_argument("--position")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--scorecard")
    ap.add_argument("--explain")
    ap.add_argument("--no-role", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    meta = load_roster()
    xw = crosswalk(conn)
    if not meta:
        print("  no roster file — teams and depth slots unavailable")

    if args.scorecard:
        scorecard(conn, [int(s) for s in args.scorecard.split(",")], meta, xw)
        return
    if not args.season:
        sys.exit("  pass --season")

    rows = build(conn, args.season, meta, xw, role=not args.no_role)

    if args.explain:
        hit = [r for r in rows if args.explain.lower() in r["name"].lower()]
        if not hit:
            sys.exit(f"  no player matching '{args.explain}'")
        r = hit[0]
        print(f"\n  {r['name']}  {r['pos']} {r['team']}\n")
        for label, delta, run in r["trace"]:
            if "points per game" in label:
                print(f"    {label:<40} {run:>8.2f} per game")
            else:
                print(f"    {label:<40} {delta:>+8.2f} -> {run:>6.2f} per game")
        print(f"    {'-'*54}")
        print(f"    {'full season (x17)':<40} {r['ppr']:>8.1f}")
        print(f"    {'adjusted (x' + format(r['exp_games'],'.1f') + ' games)':<40}"
              f" {r['adjusted']:>8.1f}")
        return

    if args.publish:
        publish(conn, args.season, rows, xw)
        return

    if args.position:
        rows = [r for r in rows if r["pos"] == args.position.upper()]
    rows.sort(key=lambda r: -r["ppr"])
    print(f"\n  {'#':<4}{'PLAYER':<24}{'TM':<5}{'FULL':>7}{'ADJ':>7}{'G':>6}  NOTE")
    for i, r in enumerate(rows[:args.top], 1):
        print(f"  {i:<4}{r['name'][:24]:<24}{r['team']:<5}{r['ppr']:>7.0f}"
              f"{r['adjusted']:>7.0f}{r['exp_games']:>6.1f}  {r['note']}")
    print(f"\n  {len(rows)} players")


if __name__ == "__main__":
    main()
