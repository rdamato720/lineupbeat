#!/usr/bin/env python3
"""Season projections. Points per game, times games played.

    python3 scripts/project5.py --season 2025
    python3 scripts/project5.py --season 2025 --position RB
    python3 scripts/project5.py --season 2025 --publish
    python3 scripts/project5.py --season 2025 --compare ~/Downloads/rankings-ppr.csv

THE WHOLE MODEL

    projection = weighted points per game  x  expected games
                 x  role adjustment, where the depth chart disagrees

That is it. Three inputs, no efficiency regression, no team volume
reconciliation, no age curve, no workload ceiling, no market blend.

An earlier version had all of those. It was measured against a model
consisting only of the first two lines above:

    model                            MAE    top40   rank corr
    just points/game x games        47.7     67.1        0.52
    everything we built             47.5     65.2        0.47

The elaborate version was no more accurate and ranked players slightly
worse. Most of a season's fantasy scoring is opportunity, opportunity is
stable, and points per game already contains it. The machinery was
re-deriving something the input already said.

The one exception is a player whose ROLE changed -- a back who inherited a
backfield, a receiver who moved. His own history describes a job he no
longer has, and nothing in his past can say so. That is what the depth
chart is for and it is the only outside input here.
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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAMES = 17
SKILL = {"QB", "RB", "WR", "TE"}

# Most recent season first. Tested: heavier recency beats a flatter blend at
# every tier, because a player's most recent season is the one describing the
# player he is now.
YEAR_WEIGHTS = [0.75, 0.20, 0.05]

# How hard to discount a partial season. Tested against a published board:
#
#   power   QB gap   rank corr
#   1.5       -35        0.56
#   2.5       -27        0.61   <- chosen
#   4.0       -29        0.53
#   6.0       -27        0.42
#
# 2025 was a bad year for quarterback availability -- Jackson, Daniels,
# Burrow and Herbert all missed time -- and their reduced per-game rates were
# dragging the projection while every published board projects them healthy.
# Discounting a short season harder is the honest version of that judgment:
# it says a season somebody mostly missed describes him less well, rather
# than saying anything about who he is.
SAMPLE_POWER = 2.5

# Availability reads four seasons, nearly flat. Durability is a trait; a
# single bad year is not a verdict on it.
AVAIL_WEIGHTS = [0.30, 0.28, 0.24, 0.18]
# Measured on the top 36 at each position across four seasons -- the players
# somebody actually rosters:
#
#   pos   median g   mean g   fraction of 17   old norm
#   QB      14.5      13.5        0.80           0.82
#   RB      16.0      15.6        0.92           0.78
#   WR      16.0      15.8        0.93           0.82
#   TE      15.0      14.7        0.87           0.82
#
# The old numbers came from every player at the position, including the
# fringe ones who appear for three weeks and vanish. Applying that to a
# starter docked him for injuries that happen to somebody else, and it was
# the whole of a 34-point gap at running back.
AVAIL_NORM = {"QB": 0.80, "RB": 0.92, "WR": 0.93, "TE": 0.87}

# How many games a player at each depth slot actually plays.
#
# This is the single biggest thing the model was missing. Points per game
# times seventeen games assumes everyone starts. Kirk Cousins came out at
# 249 where ESPN has him at 46; Joe Flacco 195 against 10; Mac Jones 193
# against 9. All backups, all projected as though they would play a full
# season, all scattered through the rankings -- receiver rank correlation
# was 0.04, which is no better than shuffling.
#
# A backup quarterback plays two or three games. His per-game rate in those
# games might be fine; the number of games is the point. History cannot say
# this, because last year he was a backup too and the average already
# reflects it -- what it cannot do is say he will be a backup AGAIN.
SLOT_GAMES = {
    "QB": {1: 15.8, 2: 2.5, 3: 0.5, 4: 0.2},
    "RB": {1: 14.5, 2: 12.5, 3: 8.0, 4: 4.0},
    "WR": {1: 15.0, 2: 14.0, 3: 11.0, 4: 6.0},
    "TE": {1: 14.5, 2: 9.0, 3: 4.0, 4: 2.0},
}

# What a depth slot actually scores per game, measured across 96 team-seasons.
# A promoted backup moves toward this; he cannot exceed it by a multiple of
# whatever his bench role happened to produce.
SLOT_PPG = {
    "QB": {1: 15.7, 2: 8.3, 3: 4.0, 4: 2.0},
    "RB": {1: 14.1, 2: 6.3, 3: 3.1, 4: 2.2},
    "WR": {1: 13.8, 2: 10.0, 3: 6.3, 4: 4.3},
    "TE": {1: 9.2, 2: 3.6, 3: 2.2, 4: 1.2},
}
ROLE_PULL = 0.55     # how far toward the slot when promoting
ROLE_GAP = 0.35      # how different before we act at all

SPREAD = {"QB": 0.24, "RB": 0.30, "WR": 0.28, "TE": 0.30}
INJURY_RISK = {"QB": 0.16, "RB": 0.28, "WR": 0.22, "TE": 0.22}
TEAM_ALIAS = {"LA": "LAR", "LVR": "LV", "SD": "LAC", "OAK": "LV",
              "WSH": "WAS", "JAC": "JAX", "ARZ": "ARI", "STL": "LAR"}


def norm_team(c):
    c = (c or "").strip().upper()
    return TEAM_ALIAS.get(c, c)


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def roster():
    out = {}
    p = ROOT / "rosters" / "nfl.csv"
    if not p.exists():
        return out
    for r in csv.DictReader(p.open()):
        try:
            slot = int(r["depth_order"]) if str(r.get("depth_order") or "").strip() else None
        except ValueError:
            slot = None
        out[r["id"]] = {"name": r["name"], "team": norm_team(r.get("team")),
                        "pos": (r.get("position") or "").upper(), "slot": slot,
                        "adp": r.get("adp"), "depth_pos": (r.get("depth_pos") or "").upper()}
    return out


# What a reported status means for a season. Out for the year is out for the
# year -- that is a fact somebody reported, not a judgment we are making.
# Only statuses that describe a SEASON belong in a season projection.
#
# Questionable and doubtful are weekly designations -- they answer "will he
# play Sunday", not "how many games this year". Treating questionable as a
# 15% haircut docked Jahmyr Gibbs, Patrick Mahomes and Malik Nabers a couple
# of games each for a camp designation that will be gone by Week 2.
#
# This is the same day-versus-season split the beat reports needed, and
# getting it wrong in the other direction here was the same mistake.
STATUS_GAMES = {
    "INJURY_RESERVE": 0.0,
    "OUT": 0.0,
    "SUSPENSION": 0.0,
    "NON_FOOTBALL_INJURY": 0.0,
    # Everything else -- questionable, doubtful, day to day -- is about a
    # week and is deliberately absent.
}


def statuses(conn):
    """Reported injury status, keyed by normalised name.

    Ricky Pearsall is out for the season. Nothing in three years of his stats
    can say so, and a model that cannot read a wire will project him as a
    starting receiver every time. This is the one input that has to come from
    outside, and it is a fact rather than an opinion.
    """
    out = {}
    try:
        for r in conn.execute("""SELECT name_key, injury FROM espn_proj
                                 WHERE injury IS NOT NULL AND injury != ''"""):
            out[r["name_key"]] = r["injury"]
    except sqlite3.OperationalError:
        pass
    return out


def crosswalk(conn):
    """gsis id -> BARE sleeper id.

    Not prefixed. The exporter builds its own key as f"nfl-{sleeper_id}", so
    storing "nfl-12345" here produced "nfl-nfl-12345" and matched nothing --
    the export cheerfully reported 382 projections attached and the site
    showed none, because the count came from the projections table and the
    join happened afterwards.
    """
    out = {}
    try:
        for r in conn.execute("""SELECT gsis_id, sleeper_id FROM id_map
                                 WHERE sleeper_id IS NOT NULL AND sleeper_id != ''"""):
            out[r["gsis_id"]] = str(r["sleeper_id"])
    except sqlite3.OperationalError:
        pass
    return out


def build(conn, season, ros, xw, status=None):
    """One pass. Points per game, games, role, reported status."""
    rows = []
    seen = set()
    status = status if status is not None else statuses(conn)
    for rec in conn.execute("""SELECT DISTINCT player_id FROM weekly_stats
                               WHERE season=? AND season_type='REG'""", (season,)):
        pid = rec["player_id"]
        info = conn.execute("""SELECT player_name, position, team FROM weekly_stats
                               WHERE player_id=? AND season=? ORDER BY week DESC
                               LIMIT 1""", (pid, season)).fetchone()
        pos = info["position"]
        if pos not in SKILL:
            continue

        rid = xw.get(pid)
        meta = ros.get(f"nfl-{rid}") if rid else None
        if not meta:
            meta = next((v for v in ros.values()
                         if key(v["name"]) == key(info["player_name"])), None)
        if ros and not meta:
            continue                      # not on a 2026 roster
        if meta and meta.get("depth_pos") == "FB":
            continue                      # nobody rosters a fullback

        # --- points per game, three seasons -----------------------------
        pts = played = wsum = 0.0
        line = collections.defaultdict(float)
        for w, s in zip(YEAR_WEIGHTS, (season, season - 1, season - 2)):
            g = conn.execute("""SELECT COUNT(*) g, AVG(fantasy_points_ppr) p,
                                AVG(receptions) rec, AVG(receiving_yards) recyd,
                                AVG(rushing_yards) ruyd, AVG(passing_yards) pyd
                                FROM weekly_stats WHERE player_id=? AND season=?
                                AND season_type='REG'""", (pid, s)).fetchone()
            if not g or not g["g"]:
                continue
            adj = w * ((min(17, g["g"]) / 17.0) ** SAMPLE_POWER)
            pts += (g["p"] or 0) * adj
            played += min(17, g["g"]) * adj
            wsum += adj
            for k in ("rec", "recyd", "ruyd", "pyd"):
                line[k] += (g[k] or 0) * adj
        if wsum < 0.12:
            continue
        ppg = pts / wsum
        base = ppg
        for k in line:
            line[k] /= wsum

        # --- role, only where the chart clearly disagrees ----------------
        note = ""
        slot = meta.get("slot") if meta else None
        if slot and pos in SLOT_PPG:
            target = SLOT_PPG[pos].get(slot)
            if target:
                moved = bool(meta.get("team")
                             and meta["team"] != norm_team(info["team"]))
                gap = (target - ppg) / max(target, 0.01)
                promoting = target > ppg
                # Promote freely; demote on the chart's word.
                #
                # Demotion used to require the player's own usage to be thin
                # as well, which meant a healthy veteran who had been replaced
                # never came down: Arizona drafted Jeremiyah Love, the chart
                # correctly listed James Conner third, and he still projected
                # as RB12 because his 2024 was fine.
                #
                # His 2024 describes a job he no longer has. The chart is the
                # only input that can know that, and it updates three times a
                # day. The earlier caution came from one mis-scraped row, and
                # guarding against that by ignoring every row was the wrong
                # trade -- a stale demotion is visible and fixable, a veteran
                # ranked twelfth who is third on his own team is neither.
                if moved or abs(gap) >= ROLE_GAP:
                    pull = 0.75 if moved else ROLE_PULL
                    ppg = ppg * (1 - pull) + target * pull
                    note = "role"

        # --- expected games, four seasons -------------------------------
        ap = aw = 0.0
        for w, s in zip(AVAIL_WEIGHTS, (season, season-1, season-2, season-3)):
            g = conn.execute("""SELECT COUNT(*) g FROM weekly_stats
                                WHERE player_id=? AND season=? AND season_type='REG'""",
                             (pid, s)).fetchone()
            if g and g["g"]:
                ap += min(17, g["g"]) * w
                aw += w
        rate = (ap / aw / 17.0) if aw else 0.82
        exp_g = GAMES * (rate * 0.5 + AVAIL_NORM.get(pos, 0.80) * 0.5)

        # Depth slot caps it. A quarterback listed second plays a couple of
        # games whatever his history says, and history cannot know he is
        # second again this year.
        if slot and pos in SLOT_GAMES:
            cap = SLOT_GAMES[pos].get(slot)
            if cap is not None:
                exp_g = min(exp_g, cap) if slot > 1 else max(exp_g, cap * 0.75)

        st = status.get(key(info["player_name"]))
        mult = STATUS_GAMES.get(st) if st else None
        if mult is not None:
            exp_g *= mult
            note = (note + " / " + st.lower().replace("_", " ")).strip(" /")

        # The full-season number means "he holds this role all year", not
        # "he plays seventeen games whatever his role is".
        #
        # Those are different seasons. A backup quarterback playing seventeen
        # games only happens if the starter goes down, and projecting that as
        # his headline put Kirk Cousins, Spencer Rattler and Marcus Mariota
        # among the better quarterbacks in the league -- a median of +45
        # against ESPN at the position.
        #
        # Starters get the full seventeen. Everyone else gets the games his
        # slot actually plays, which is the honest version of his best case.
        full_g = GAMES
        if slot and slot > 1 and pos in SLOT_GAMES:
            full_g = min(GAMES, SLOT_GAMES[pos].get(slot, GAMES))

        scale = ppg / max(base, 0.01)
        seen.add(pid)
        rows.append({
            "id": pid, "name": (meta or {}).get("name") or info["player_name"],
            "pos": pos, "team": (meta or {}).get("team") or norm_team(info["team"]),
            "ppr": ppg * full_g, "adjusted": ppg * exp_g, "games": exp_g,
            "rec": line["rec"] * full_g * scale,
            "recyd": line["recyd"] * full_g * scale,
            "ruyd": line["ruyd"] * full_g * scale,
            "note": note, "sleeper": rid,
        })

    # ---- rookies -------------------------------------------------------
    #
    # Everything above starts from weekly_stats, so a player who has never
    # taken an NFL snap is invisible. That is not a rounding error: eleven of
    # the twenty biggest disagreements with ESPN were players we did not
    # project at all, including Arizona's starting running back.
    #
    # With no history there is exactly one thing to go on, and it is the same
    # thing that tells us a veteran has been replaced: where he sits on the
    # chart. A rookie listed first gets what a first-string player at his
    # position scores. It will be wrong for the ones who break out and wrong
    # for the ones who bust, but it is not wrong by three hundred points,
    # which is what leaving them out was.
    have = {key(r["name"]) for r in rows}
    for rid, meta in ros.items():
        pos = meta.get("pos")
        slot = meta.get("slot")
        if pos not in SLOT_PPG or not slot or slot > 4:
            continue
        if key(meta["name"]) in have:
            continue
        if meta.get("depth_pos") == "FB":
            continue
        ppg = SLOT_PPG[pos].get(slot)
        if not ppg:
            continue
        # A rookie has no durability record, so use the position norm alone
        # rather than pretending a number we do not have.
        exp_g = min(GAMES * AVAIL_NORM.get(pos, 0.80),
                    SLOT_GAMES[pos].get(slot, GAMES))
        st = status.get(key(meta["name"]))
        rmult = STATUS_GAMES.get(st) if st else None
        if rmult is not None:
            exp_g *= rmult
        full_g = GAMES if slot == 1 else min(
            GAMES, SLOT_GAMES[pos].get(slot, GAMES))
        rows.append({
            "id": rid, "name": meta["name"], "pos": pos,
            "team": meta.get("team") or "",
            "ppr": ppg * full_g, "adjusted": ppg * exp_g, "games": exp_g,
            "rec": 0.0, "recyd": 0.0, "ruyd": 0.0,
            "note": "no NFL history", "sleeper": rid.replace("nfl-", ""),
        })
    return rows


def simulate(r, runs=2000):
    sd = r["ppr"] * SPREAD.get(r["pos"], .3)
    risk = INJURY_RISK.get(r["pos"], .24)
    v = []
    for _ in range(runs):
        x = random.gauss(r["ppr"], sd)
        if random.random() < risk:
            x *= random.uniform(.55, .9)
        v.append(max(0.0, x))
    v.sort()
    return v[int(.10 * (len(v)-1))], v[int(.90 * (len(v)-1))]


SCHEMA = """
CREATE TABLE IF NOT EXISTS projections (
  season INTEGER, player_id TEXT, sleeper_id TEXT, player TEXT,
  position TEXT, team TEXT, ppr REAL, half REAL, standard REAL,
  adjusted REAL, exp_games REAL, floor REAL, ceiling REAL,
  rank_pos INTEGER, rec REAL, recyd REAL, ruyd REAL, news_adj REAL,
  trace TEXT, PRIMARY KEY (season, player_id));
"""


def publish(conn, season, rows):
    conn.execute("DROP TABLE IF EXISTS projections")
    conn.executescript(SCHEMA)
    by = collections.defaultdict(list)
    for r in sorted(rows, key=lambda x: -x["adjusted"]):
        by[r["pos"]].append(r)
    n = 0
    for pos, grp in by.items():
        for i, r in enumerate(grp, 1):
            lo, hi = simulate(r)
            std = r["ppr"] - r["rec"]
            conn.execute("INSERT OR REPLACE INTO projections VALUES "
                         "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (season, r["id"], r["sleeper"], r["name"], pos, r["team"],
                          round(r["ppr"],1), round((r["ppr"]+std)/2,1), round(std,1),
                          round(r["adjusted"],1), round(r["games"],1),
                          round(lo,1), round(hi,1), i, round(r["rec"],1),
                          round(r["recyd"],1), round(r["ruyd"],1), 0.0, "[]"))
            n += 1
    conn.commit()
    print(f"  published {n} projections for {season+1}")


def compare(rows, path):
    """Score against a published board. Rank agreement is the number that
    matters: nobody compares point totals across sites, they compare order."""
    ref = {}
    with open(Path(path).expanduser()) as fh:
        for r in csv.DictReader(fh):
            name = r.get("Player") or r.get("PLAYER") or ""
            pos = (r.get("Fantasy Position") or r.get("POS") or "").upper()
            try:
                pts = float(str(r.get("3D Proj") or r.get("FPTS") or "").replace(",", ""))
            except ValueError:
                continue
            if name and pos in SKILL:
                ref[key(name)] = {"pts": pts, "pos": pos, "name": name}

    print(f"\n  {len(ref)} skill players on the reference board\n")
    print(f"  {'POS':<5}{'n':>4}{'MEDIAN GAP':>12}{'WITHIN 25':>11}{'RANK CORR':>11}")
    allg, allr = [], []
    for pos in ("QB", "RB", "WR", "TE"):
        ours = sorted([r for r in rows if r["pos"] == pos],
                      key=lambda x: -x["adjusted"])
        theirs = sorted([v for v in ref.values() if v["pos"] == pos],
                        key=lambda x: -x["pts"])
        # Rank both boards within the matched set; see espn_proj.py.
        matched = [r for r in ours[:60]
                   if key(r["name"]) in ref and ref[key(r["name"])]["pos"] == pos]
        gaps = [r["ppr"] - ref[key(r["name"])]["pts"] for r in matched]
        theirs_sub = sorted(matched, key=lambda r: -ref[key(r["name"])]["pts"])
        trank = {key(r["name"]): i for i, r in enumerate(theirs_sub)}
        pairs = [(i, trank[key(r["name"])]) for i, r in enumerate(matched)]
        if len(pairs) < 8:
            print(f"  {pos:<5}{len(pairs):>4}   too few matched")
            continue
        n = len(pairs)
        rho = 1 - 6*sum((a-b)**2 for a, b in pairs)/(n*(n*n-1))
        within = sum(1 for g in gaps if abs(g) <= 25)
        print(f"  {pos:<5}{n:>4}{statistics.median(gaps):>+12.0f}"
              f"{within:>8}/{n:<3}{rho:>+11.2f}")
        allg += gaps; allr.append(rho)
    if allg:
        print(f"  {'ALL':<5}{len(allg):>4}{statistics.median(allg):>+12.0f}"
              f"{sum(1 for g in allg if abs(g)<=25):>8}/{len(allg):<3}"
              f"{statistics.mean(allr):>+11.2f}")
    print("\n  Rank correlation is the one to watch. A systematic point offset")
    print("  is invisible to a reader; a scrambled order is not.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--position")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--compare")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    ros = roster()
    if not ros:
        print("  no roster file; teams and depth slots unavailable")
    rows = build(conn, args.season, ros, crosswalk(conn))
    if not rows:
        sys.exit("  nothing projected. Is weekly_stats imported?")

    if args.compare:
        compare(rows, args.compare)
        return
    if args.publish:
        publish(conn, args.season, rows)
        return

    if args.position:
        rows = [r for r in rows if r["pos"] == args.position.upper()]
    rows.sort(key=lambda r: -r["ppr"])
    print(f"\n  {'#':<4}{'PLAYER':<24}{'POS':<5}{'TM':<5}{'PROJ':>6}{'ADJ':>6}"
          f"{'G':>6}  NOTE")
    for i, r in enumerate(rows[:args.top], 1):
        print(f"  {i:<4}{r['name'][:24]:<24}{r['pos']:<5}{r['team']:<5}"
              f"{r['ppr']:>6.0f}{r['adjusted']:>6.0f}{r['games']:>6.1f}  {r['note']}")
    print(f"\n  {len(rows)} players")


if __name__ == "__main__":
    main()
