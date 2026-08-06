#!/usr/bin/env python3
"""Pull ESPN's own projections and compare them with ours.

    python3 scripts/espn_proj.py --league 284261 --season 2026
    python3 scripts/espn_proj.py --league 284261 --compare

WHY ESPN RATHER THAN A PUBLISHED CSV

A downloaded board is scored with somebody else's rules. Comparing against
one showed every quarterback about 25 points low, and unpicking that took
longer than it should have: their interceptions are worth -1 where ESPN's
are -2, and roughly 15 more points a quarterback come from something not
present in the columns they published.

ESPN scores its projections with YOUR league's settings. `appliedTotal` is
the projection already run through the scoring you actually play. So a point
gap here means a real disagreement about a player rather than an accounting
difference, which is the only way a point comparison is worth anything.

The endpoint is undocumented. Fine for checking our own work in private;
not something to build a product on.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
# defaultPositionId, NOT lineupSlotId. The two overlap at 2 (running back)
# and nowhere else, so an earlier version using slot ids labelled every
# quarterback and receiver "?" while running backs looked fine -- the most
# misleading kind of wrong.
POSITION = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS espn_proj (
  season INTEGER, espn_id INTEGER, player TEXT, name_key TEXT,
  position TEXT, team_id INTEGER, points REAL,
  -- Injury status is a FACT about the world, not a projection: reported by
  -- every outlet, derivable from none of our data. Ricky Pearsall is out for
  -- the year and no amount of modelling his 2025 will discover that.
  injury TEXT, active INTEGER,
  fetched_at TEXT, PRIMARY KEY (season, espn_id));
"""


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def fetch(league, season, limit=600, cookies=None):
    """kona_player_info carries the projection block on each player."""
    url = f"{BASE}/seasons/{season}/segments/0/leagues/{league}?view=kona_player_info"
    # The filter is how ESPN wants the query expressed. Sorting by projected
    # points keeps the top of the board inside the limit.
    filt = {
        "players": {
            "limit": limit,
            "sortDraftRanks": {"sortPriority": 100, "sortAsc": True,
                               "value": "PPR"},
        }
    }
    req = urllib.request.Request(url, headers={
        "x-fantasy-filter": json.dumps(filt),
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Accept": "application/json",
    })
    if cookies:
        req.add_header("Cookie", cookies)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def projection_of(player, season):
    """The season-long projected total, scored with this league's rules.

    statSourceId 1 is projected (0 is actual). statSplitTypeId 0 is the
    season split. `appliedTotal` has already been through league scoring;
    `total` has not, which is the difference that matters here.
    """
    best = None
    for s in player.get("stats") or []:
        if s.get("statSourceId") != 1:
            continue
        if s.get("statSplitTypeId") != 0:
            continue
        if int(s.get("seasonId") or 0) != int(season):
            continue
        v = s.get("appliedTotal")
        if v is not None:
            best = float(v)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--league", type=int, default=284261)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--our-season", type=int, default=2025,
                    help="the season our model projects FROM")
    ap.add_argument("--limit", type=int, default=600)
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--basis", default="ppr", choices=["ppr", "adjusted"],
                    help="ppr is like-for-like: ESPN publishes a healthy "
                         "season and so does our ppr column")
    ap.add_argument("--patterns", action="store_true",
                    help="what we are systematically missing, not who")
    ap.add_argument("--audit", type=int, default=0,
                    help="disagreements WITH the context needed to judge them")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if not args.compare:
        cookies = None
        s2, swid = os.environ.get("ESPN_S2"), os.environ.get("ESPN_SWID")
        if s2 and swid:
            cookies = f"espn_s2={s2}; SWID={swid}"
            print("  using ESPN_S2 / ESPN_SWID from the environment")
        try:
            data = fetch(args.league, args.season, args.limit, cookies)
        except urllib.error.HTTPError as e:
            msg = {401: "league is private; set ESPN_S2 and ESPN_SWID",
                   404: "league or season not found"}.get(e.code, "")
            sys.exit(f"  HTTP {e.code}. {msg}")
        except Exception as e:
            sys.exit(f"  {str(e)[:90]}")

        players = data.get("players") or []
        if not players:
            sys.exit("  no players returned; the filter may need adjusting")

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        n = 0
        for wrap in players:
            p = wrap.get("player") or {}
            pts = projection_of(p, args.season)
            if pts is None:
                pts = 0.0      # keep him: the injury status is the point
            pos = POSITION.get(p.get("defaultPositionId"), "?")
            name = p.get("fullName") or ""
            conn.execute("INSERT OR REPLACE INTO espn_proj "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (args.season, p.get("id"), name, key(name), pos,
                          p.get("proTeamId"), round(pts, 1),
                          p.get("injuryStatus"),
                          1 if p.get("active") else 0, now))
            n += 1
        conn.commit()
        print(f"  stored {n} ESPN projections for {args.season}")
        if n:
            print(f"\n  {'POS':<5}{'PLAYER':<24}{'ESPN':>7}")
            for r in conn.execute("""SELECT player, position, points FROM espn_proj
                                     WHERE season=? ORDER BY points DESC LIMIT 12""",
                                  (args.season,)):
                print(f"  {r['position']:<5}{r['player'][:24]:<24}{r['points']:>7.0f}")
        hurt = conn.execute("""SELECT COUNT(*) n FROM espn_proj WHERE season=?
                               AND injury IS NOT NULL
                               AND injury NOT IN ('ACTIVE','')""",
                            (args.season,)).fetchone()["n"]
        print(f"  {hurt} carry an injury status")
        for r in conn.execute("""SELECT player, position, injury FROM espn_proj
                                 WHERE season=? AND injury IS NOT NULL
                                 AND injury NOT IN ('ACTIVE','')
                                 ORDER BY points DESC LIMIT 8""", (args.season,)):
            print(f"    {r['player'][:24]:<24} {r['position']:<4} {r['injury']}")
        print(f"\n  next: python3 scripts/espn_proj.py --compare --audit 15")
        return

    # ---- compare ---------------------------------------------------------
    try:
        espn = {r["name_key"]: dict(r) for r in conn.execute(
            "SELECT * FROM espn_proj WHERE season=?", (args.season,))}
    except sqlite3.OperationalError:
        sys.exit("  nothing imported yet")
    if not espn:
        sys.exit("  nothing imported yet; run without --compare first")

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p5", str(ROOT / "scripts" / "project5.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    rows = m.build(conn, args.our_season, m.roster(), m.crosswalk(conn))

    print(f"\n  {len(espn)} ESPN projections, scored with league {args.league}"
          f"'s own rules")
    print(f"  Compared on our {args.basis.upper()} column.")
    if args.basis == "ppr":
        print("  ESPN publishes a healthy full season and so does this column,")
        print("  so a gap is a disagreement about the player. Comparing our")
        print("  ADJUSTED column instead put Jahmyr Gibbs and Puka Nacua in the")
        print("  'we rate him too low' bucket -- they were not too low, we were")
        print("  discounting for expected games and ESPN was not.\n")
    else:
        print("  Our adjusted column discounts for expected games; ESPN does")
        print("  not. Expect a systematic offset that is not an error.\n")
    print(f"  {'POS':<5}{'n':>4}{'MEDIAN GAP':>12}{'WITHIN 25':>11}{'RANK CORR':>11}")
    allg, rhos = [], []
    for pos in ("QB", "RB", "WR", "TE"):
        ours = sorted([r for r in rows if r["pos"] == pos],
                      key=lambda x: -x[args.basis])[:60]
        theirs = sorted([v for v in espn.values() if v["position"] == pos],
                        key=lambda x: -x["points"])
        # Rank both boards WITHIN THE MATCHED SET.
        #
        # Comparing our 1-60 against their position in a 200-name list made a
        # player we ranked 57th and they ranked 193rd contribute a distance
        # of 136 -- from a list we only ranked 60 deep. That alone pushed
        # receiver correlation to 0.08 while the two top-20s were nearly
        # identical. The metric was broken, not the model.
        matched = [r for r in ours
                   if key(r["name"]) in espn
                   and espn[key(r["name"])]["position"] == pos]
        gaps = [r[args.basis] - espn[key(r["name"])]["points"] for r in matched]
        theirs_sub = sorted(matched,
                            key=lambda r: -espn[key(r["name"])]["points"])
        tr = {key(r["name"]): i for i, r in enumerate(theirs_sub)}
        pairs = [(i, tr[key(r["name"])]) for i, r in enumerate(matched)]
        if len(pairs) < 8:
            print(f"  {pos:<5}{len(pairs):>4}   too few matched")
            continue
        n = len(pairs)
        rho = 1 - 6*sum((a-b)**2 for a, b in pairs)/(n*(n*n-1))
        print(f"  {pos:<5}{n:>4}{statistics.median(gaps):>+12.0f}"
              f"{sum(1 for g in gaps if abs(g) <= 25):>8}/{n:<3}{rho:>+11.2f}")
        allg += gaps; rhos.append(rho)
    if allg:
        print(f"  {'ALL':<5}{len(allg):>4}{statistics.median(allg):>+12.0f}"
              f"{sum(1 for g in allg if abs(g)<=25):>8}/{len(allg):<3}"
              f"{statistics.mean(rhos):>+11.2f}")
        print("\n  Both boards are scored the same way here, so a point gap is")
        print("  a real disagreement about a player rather than an accounting")
        print("  difference. That was not true of the downloaded CSV.")

    if args.patterns:
        # The question is not which players we get wrong. It is what we get
        # wrong ABOUT players, because a category of error can be fixed once
        # and a list of players cannot.
        #
        # Every disagreement is bucketed by the thing most likely to explain
        # it, and the buckets are ranked by total damage. A bucket carrying a
        # thousand points across forty players is a hole in the model; one
        # carrying three hundred across two is a difference of opinion.
        import csv as _csv
        chart = {}
        rp = ROOT / "rosters" / "nfl.csv"
        if rp.exists():
            for r in _csv.DictReader(rp.open()):
                chart[key(r["name"])] = r
        played = {}
        for r in conn.execute("""SELECT player_name, COUNT(*) g FROM weekly_stats
                                 WHERE season_type='REG' AND season >= ?
                                 GROUP BY player_id""", (args.our_season - 2,)):
            played[key(r["player_name"])] = r["g"]

        ours = {key(r["name"]): r for r in rows}
        buckets = {}
        for k, e in espn.items():
            if e["position"] not in ("QB", "RB", "WR", "TE"):
                continue
            o = ours.get(k)
            mine = o[args.basis] if o else 0.0
            gap = mine - e["points"]
            if abs(gap) < 15:
                continue
            c = chart.get(k, {})
            try:
                slot = int(c.get("depth_order") or 0)
            except ValueError:
                slot = 0
            hist = played.get(k, 0)

            if not o and hist == 0:
                b = "no NFL history: we cannot project him at all"
            elif not o:
                b = "has NFL history but is missing from our board"
            elif e.get("injury") and e["injury"] not in ("ACTIVE", ""):
                b = f"reported {e['injury'].lower().replace('_',' ')}"
            elif slot >= 3 and gap > 0:
                b = "buried on the depth chart, we still rate him"
            elif slot == 1 and gap < 0:
                b = "listed first, we rate him below ESPN"
            elif hist < 17 and gap < 0:
                b = "thin NFL record, ESPN expects more"
            elif gap > 0:
                b = "we are higher, no obvious structural reason"
            else:
                b = "we are lower, no obvious structural reason"
            d = buckets.setdefault(b, {"n": 0, "pts": 0.0, "who": []})
            d["n"] += 1; d["pts"] += abs(gap)
            d["who"].append((abs(gap), e["player"]))

        print(f"\n  WHAT WE ARE SYSTEMATICALLY MISSING\n")
        print(f"  {'n':>4}{'total pts':>11}   pattern")
        for b, d in sorted(buckets.items(), key=lambda x: -x[1]["pts"]):
            print(f"  {d['n']:>4}{d['pts']:>11.0f}   {b}")
            top = sorted(d["who"], reverse=True)[:3]
            print(f"       {', '.join(n for _, n in top)}")
        tot = sum(d["pts"] for d in buckets.values())
        struct = sum(d["pts"] for b, d in buckets.items()
                     if "no obvious" not in b)
        print(f"\n  {struct/tot*100:.0f}% of the disagreement falls into a")
        print(f"  category with a structural cause. That part is fixable in")
        print(f"  the model. The rest is a difference of opinion about players.")

    if args.audit:
        # Why we differ, not just that we do.
        #
        # A list of gaps is useless on its own: nobody can hold the depth
        # chart, injury history and role of five hundred players in their
        # head. Every disagreement here comes with the three things that
        # actually explain it -- where he sits on the chart, how many games
        # we expect, and what his last three seasons looked like -- so a
        # wrong number can be traced to its cause rather than guessed at.
        import csv as _csv
        chart = {}
        rp = ROOT / "rosters" / "nfl.csv"
        if rp.exists():
            for r in _csv.DictReader(rp.open()):
                chart[key(r["name"])] = r

        hist = {}
        for r in conn.execute("""SELECT player_name, season, COUNT(*) g,
                                 ROUND(AVG(fantasy_points_ppr),1) ppg
                                 FROM weekly_stats WHERE season_type='REG'
                                 AND season >= ? GROUP BY player_id, season""",
                              (args.our_season - 2,)):
            hist.setdefault(key(r["player_name"]), []).append(
                (r["season"], r["g"], r["ppg"]))

        rows_by = {key(r["name"]): r for r in rows}
        diffs = []
        for k, e in espn.items():
            if e["position"] not in ("QB", "RB", "WR", "TE"):
                continue
            o = rows_by.get(k)
            mine = o[args.basis] if o else 0.0
            diffs.append((abs(mine - e["points"]), k, e, o, mine))
        diffs.sort(reverse=True)

        print(f"\n  WHERE WE DISAGREE WITH ESPN, AND WHY\n")
        for _, k, e, o, mine in diffs[:args.audit]:
            c = chart.get(k, {})
            slot = f"{c.get('depth_pos','?')}{c.get('depth_order','?')}"
            print(f"  {e['player'][:26]:<26} {e['position']:<3} "
                  f"ours {mine:>5.0f}   ESPN {e['points']:>5.0f}   "
                  f"{mine - e['points']:>+6.0f}")
            print(f"    depth {slot:<6} team {c.get('team','?'):<4} "
                  f"adp {c.get('adp') or '-':<7} "
                  f"our games {o['games']:.1f}" if o else
                  f"    depth {slot:<6}  we do not project him at all")
            h = sorted(hist.get(k, []), reverse=True)[:3]
            if h:
                print("    " + "   ".join(
                    f"{s}: {g}g {pg} ppg" for s, g, pg in h))
            if not o:
                print("    -> not on our board: no roster match, or filtered out")
            elif c.get("depth_order") in ("3", "4", "5", "6", "7"):
                print("    -> he is buried on the depth chart")
            elif h and len(h) >= 2 and h[0][2] < h[1][2] * 0.7:
                print("    -> last season was well below the one before")
            print()

    if args.show:
        print(f"\n  BIGGEST DISAGREEMENTS")
        d = []
        for r in rows:
            k = key(r["name"])
            if k in espn and espn[k]["position"] in ("QB","RB","WR","TE"):
                d.append((abs(r["adjusted"] - espn[k]["points"]), r["name"],
                          r["pos"], r["adjusted"], espn[k]["points"]))
        for gap, name, pos, mine, theirs in sorted(d, reverse=True)[:args.show]:
            print(f"    {name[:24]:<24}{pos:<5}{mine:>7.0f}{theirs:>7.0f}"
                  f"{mine-theirs:>+7.0f}")


if __name__ == "__main__":
    main()
