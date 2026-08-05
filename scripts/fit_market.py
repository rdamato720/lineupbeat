#!/usr/bin/env python3
"""Fit the ADP-to-points curve, then test whether blending it in helps.

    python3 scripts/fit_market.py --seasons 2022,2023,2024
    python3 scripts/fit_market.py --seasons 2022,2023,2024 --test

WHY

"Bake in what the fantasy sites think" already has an answer that does not
require scraping anybody: average draft position. Thousands of real drafters,
every one of whom read the sites, each making a decision with something at
stake. It is the wisdom of the crowd rather than one outlet's take, it is
published free for commercial use, and it is already imported.

An earlier attempt to blend it in made the model measurably worse. That test
deserves to be redone, because the ADP-to-points curve used then was invented
rather than fitted -- a made-up conversion will poison a good signal and look
like the signal is bad.

This fits the curve on historical ADP against what actually happened, then
scores three things against each other: the model alone, ADP alone, and a
blend. Whichever wins, wins. If ADP alone beats the model that is worth
knowing too, and would say something uncomfortable and useful about how much
of this work was necessary.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://fantasyfootballcalculator.com/api/v1/adp"
POS = ("QB", "RB", "WR", "TE")


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def fetch_adp(year, fmt="ppr", teams=12):
    url = f"{API}/{fmt}?teams={teams}&year={year}"
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode()).get("players") or []


def fit(pairs):
    """Least squares on log(adp) -> points. Simple, and the shape is right:
    value falls fast at the top of a draft and flattens out."""
    xs = [math.log(max(1.0, a)) for a, _ in pairs]
    ys = [p for _, p in pairs]
    n = len(xs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if not denom:
        return my, 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return my - slope * mx, slope


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--seasons", default="2022,2023,2024")
    ap.add_argument("--test", action="store_true",
                    help="score model vs ADP vs blend")
    ap.add_argument("--format", default="ppr")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    seasons = [int(s) for s in args.seasons.split(",")]

    data = {}          # pos -> [(adp, actual points)]
    by_season = {}     # season -> {name_key: adp}
    for year in seasons:
        try:
            rows = fetch_adp(year, args.format)
        except Exception as exc:
            print(f"  {year}: {str(exc)[:60]}")
            continue
        actual = {}
        for r in conn.execute("""SELECT player_name, position,
                                 SUM(fantasy_points_ppr) pts, COUNT(*) g
                                 FROM weekly_stats WHERE season=? AND season_type='REG'
                                 GROUP BY player_id""", (year,)):
            actual[key(r["player_name"])] = (r["pts"] or 0, r["position"], r["g"])
        seen = {}
        matched = 0
        for r in rows:
            if not r.get("adp"):
                continue
            k = key(r.get("name"))
            seen[k] = float(r["adp"])
            a = actual.get(k)
            if not a or a[1] not in POS or a[2] < 6:
                continue
            data.setdefault(a[1], []).append((float(r["adp"]), a[0]))
            matched += 1
        by_season[year] = seen
        print(f"  {year}: {len(rows)} in ADP, {matched} matched to outcomes")

    if not data:
        sys.exit("\n  no data — the ADP API may be unreachable from here")

    print(f"\n  FITTED CURVES   points = a + b * ln(adp)\n")
    print(f"  {'pos':<5}{'n':>5}{'a':>9}{'b':>9}   {'adp 5':>7}{'adp 24':>8}"
          f"{'adp 60':>8}{'adp 120':>9}")
    curves = {}
    for pos in POS:
        v = data.get(pos, [])
        if len(v) < 25:
            print(f"  {pos:<5}{len(v):>5}   too few")
            continue
        a, b = fit(v)
        curves[pos] = (a, b)
        pred = lambda x: max(0.0, a + b * math.log(x))
        print(f"  {pos:<5}{len(v):>5}{a:>9.1f}{b:>9.1f}   "
              f"{pred(5):>7.0f}{pred(24):>8.0f}{pred(60):>8.0f}{pred(120):>9.0f}")

    out = ROOT / "adp_curve.json"
    out.write_text(json.dumps({k: list(v) for k, v in curves.items()}, indent=2))
    print(f"\n  wrote {out}")

    if not args.test:
        print("  rerun with --test to score model vs ADP vs a blend")
        return

    # ---- the actual question -------------------------------------------
    import importlib.util
    spec = importlib.util.spec_from_file_location("p4", ROOT / "scripts" / "project4.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    meta, xw = m.load_roster(), m.crosswalk(conn)

    print(f"\n  SCORING  (mean absolute error against what happened)\n")
    print(f"  {'season':<9}{'model':>9}{'ADP alone':>12}{'blend 50/50':>13}"
          f"{'best':>10}")
    tot = {"model": [], "adp": [], "blend": []}
    for year in seasons:
        if year not in by_season:
            continue
        proj = {p["name"]: p for p in
                m.build(conn, year, meta, xw, require_roster=False)}
        actual = {}
        for r in conn.execute("""SELECT player_name, position,
                                 SUM(fantasy_points_ppr) pts, COUNT(*) g
                                 FROM weekly_stats WHERE season=? AND season_type='REG'
                                 GROUP BY player_id HAVING g>=6""", (year + 1,)):
            actual[key(r["player_name"])] = (r["pts"] or 0, r["position"])

        e_m, e_a, e_b = [], [], []
        for name, p in proj.items():
            k = key(name)
            act = actual.get(k)
            adp_v = by_season.get(year + 1, {}).get(k) or by_season[year].get(k)
            if not act or not adp_v or p["pos"] not in curves:
                continue
            a, b = curves[p["pos"]]
            implied = max(0.0, a + b * math.log(max(1.0, adp_v)))
            e_m.append(abs(p["adjusted"] - act[0]))
            e_a.append(abs(implied - act[0]))
            e_b.append(abs((p["adjusted"] + implied) / 2 - act[0]))
        if not e_m:
            continue
        mm, aa, bb = (statistics.mean(e_m), statistics.mean(e_a),
                      statistics.mean(e_b))
        best = min((mm, "model"), (aa, "ADP"), (bb, "blend"))[1]
        print(f"  {year:<9}{mm:>9.1f}{aa:>12.1f}{bb:>13.1f}{best:>10}")
        tot["model"] += e_m; tot["adp"] += e_a; tot["blend"] += e_b

    if tot["model"]:
        mm = statistics.mean(tot["model"]); aa = statistics.mean(tot["adp"])
        bb = statistics.mean(tot["blend"])
        print(f"  {'ALL':<9}{mm:>9.1f}{aa:>12.1f}{bb:>13.1f}"
              f"{min((mm,'model'),(aa,'ADP'),(bb,'blend'))[1]:>10}")
        print("\n  If the blend wins, add it with the fitted curve above and")
        print("  re-run the scorecard. If ADP alone wins, that is worth sitting")
        print("  with: it would mean the crowd knows more than the model, and")
        print("  the honest product is the crowd plus our own reporting rather")
        print("  than a projection built from scratch.")


if __name__ == "__main__":
    main()
