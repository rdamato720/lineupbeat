#!/usr/bin/env python3
"""The only way to read projections. Scoring and ranking live here.

    python3 scripts/projection_view.py --position RB --scoring ppr
    python3 scripts/projection_view.py --position RB --scoring standard --top 20
    python3 scripts/projection_view.py --player "Jahmyr Gibbs"
    python3 scripts/projection_view.py --json site/data/projections.json

WHY EVERYTHING GOES THROUGH HERE

Two rules that a front end must not be trusted to remember.

Residual rows exist in the snapshot because a team cannot reconcile without
them -- Cleveland's five percent other-quarterback bucket is real
opportunity, allocated to nobody. They are not people, and a component that
forgets to filter them puts "CLE QB residual" in a ranking. So the filter is
here, on is_residual, not on rank_pos being null: a null rank is a
consequence, and depending on a consequence is how it eventually leaks.

And points are computed, never stored. The workbook's PPR column is a
baseline cross-check; the raw stat line in the immutable snapshot is the
truth. Three stored scoring columns is three models that can disagree, and
the one that disagrees is always the one somebody is reading.

RANKS ARE PER FORMAT

Rank is not a property of a player, it is a property of a player under a
scoring rule. A back can be RB7 in standard, RB5 in half and RB3 in full PPR
from one stat line, and that is correct rather than a contradiction. The
imported rank_pos is a workbook artefact and is ignored.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Not teams. Free agents belong in the universe, not in a positional ranking,
# unless somebody deliberately asks for them.
NON_TEAMS = {"FA", "FA/UNK", "UNK", ""}

FORMATS = ("ppr", "half", "standard")


def score(r, fmt="ppr") -> float:
    """Fantasy points from the raw stat line. Section 4, locked.

    QB and skill positions share one expression because they share one stat
    line: a quarterback simply has no receptions. Two-point conversions are
    deliberately absent.
    """
    g = lambda k: float(r[k] or 0) if r[k] is not None else 0.0
    pts = (g("pass_yds") / 25 + g("pass_td") * 4 - g("ints") * 2
           + (g("rush_yds") + g("rec_yds")) / 10
           + (g("rush_td") + g("rec_td")) * 6
           - g("fumbles") * 2)
    if fmt == "ppr":
        pts += g("rec")
    elif fmt == "half":
        pts += g("rec") * 0.5
    return pts


def live_run(conn, season):
    row = conn.execute("SELECT * FROM published_snapshot WHERE season=?",
                       (season,)).fetchone()
    return row["run_id"] if row else None


def public_rows(conn, season, position=None, scoring="ppr",
                include_free_agents=False):
    """Every publishable row, scored and ranked for one format.

    The single place a residual is dropped and a rank is computed. Anything
    reading projections reads this.
    """
    run_id = live_run(conn, season)
    if not run_id:
        return [], None
    rows = [dict(r) for r in conn.execute(
        """SELECT * FROM run_projections
           WHERE run_id = ? AND (is_residual IS NULL OR is_residual = 0)""",
        (run_id,))]
    if not include_free_agents:
        rows = [r for r in rows
                if (r.get("team") or "").strip().upper() not in NON_TEAMS]

    # Full precision for ranking, one decimal for display.
    #
    # 247.74 and 247.66 both show as 247.7 and they are not the same number.
    # Rounding before the sort makes their order depend on whatever the
    # database happened to return first.
    for r in rows:
        for f in FORMATS:
            r[f"exact_{f}"] = score(r, f)
            r[f"fpts_{f}"] = round(r[f"exact_{f}"], 1)

    # Rank within position, for this format only.
    by_pos = {}
    for r in rows:
        by_pos.setdefault(r["position"], []).append(r)
    for pos, group in by_pos.items():
        # player_id last, so an exact tie resolves the same way every time
        # rather than following row order.
        group.sort(key=lambda x: (-x[f"exact_{scoring}"], str(x["player_id"])))
        for i, r in enumerate(group, 1):
            r["rank"] = i
            r["rank_label"] = f"{pos}{i}"

    out = rows if not position else by_pos.get(position.upper(), [])
    out.sort(key=lambda x: (-x[f"exact_{scoring}"], str(x["player_id"])))
    return out, run_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--position")
    ap.add_argument("--scoring", default="ppr", choices=FORMATS)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--player")
    ap.add_argument("--free-agents", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--compare-formats", action="store_true",
                    help="show how rank moves between scoring rules")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    run_id = live_run(conn, args.season)
    if not run_id:
        sys.exit("  nothing published for this season")
    meta = conn.execute("SELECT * FROM projection_runs WHERE run_id=?",
                        (run_id,)).fetchone()
    pub = conn.execute("SELECT * FROM published_snapshot WHERE season=?",
                       (args.season,)).fetchone()

    if args.player:
        rows, _ = public_rows(conn, args.season, None, args.scoring, True)
        hit = [r for r in rows if args.player.lower() in r["player"].lower()]
        if not hit:
            sys.exit(f"  {args.player} is not in the published snapshot")
        for r in hit:
            print(f"\n  {r['player']}  {r['team']} {r['position']}")
            print(f"  {'':>14}{'POINTS':>9}{'RANK':>8}")
            for f in FORMATS:
                # rank has to be recomputed per format to be meaningful
                fr, _ = public_rows(conn, args.season, r["position"], f,
                                    args.free_agents)
                rank = next((x["rank_label"] for x in fr
                             if x["player_id"] == r["player_id"]), "—")
                print(f"  {f:<14}{r[f'fpts_{f}']:>9.1f}{rank:>8}")
            print(f"\n  raw line")
            for k in ("pass_att", "completions", "pass_yds", "pass_td", "ints",
                      "targets", "rec", "rec_yds", "rec_td",
                      "rush_att", "rush_yds", "rush_td", "fumbles"):
                v = r.get(k)
                if v:
                    print(f"    {k:<14}{float(v):>9.1f}")
        return

    rows, _ = public_rows(conn, args.season, args.position, args.scoring,
                          args.free_agents)
    if not rows:
        sys.exit("  nothing to show")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"season": args.season, "scoring": args.scoring,
                   "modelVersion": meta["model_version"],
                   "scoringVersion": meta["scoring_version"],
                   "runId": run_id,
                   # What the projection knows, and when it went out. They
                   # are not the same thing: a run built on evidence through
                   # 10:00 might finish validating at 10:07, and a reader
                   # asking "does this include the injury" means the first.
                   "sourceCutoffAt": meta["source_cutoff_at"],
                   "publishedAt": pub["published_at"],
                   "asOf": meta["source_cutoff_at"],
                   "players": [{
                       "playerId": r["player_id"], "name": r["player"],
                       "team": r["team"], "position": r["position"],
                       "rank": r["rank"],
                       "targets": r["targets"], "receptions": r["rec"],
                       "receivingYards": r["rec_yds"],
                       "receivingTd": r["rec_td"],
                       "rushingAttempts": r["rush_att"],
                       "rushingYards": r["rush_yds"], "rushingTd": r["rush_td"],
                       "passAttempts": r["pass_att"],
                       "passYards": r["pass_yds"], "passTd": r["pass_td"],
                       "interceptions": r["ints"], "fumblesLost": r["fumbles"],
                       "fantasyPoints": r[f"fpts_{args.scoring}"],
                   } for r in rows]}
        p.write_text(json.dumps(payload, indent=1))
        print(f"  wrote {p}  ({len(rows)} players, {args.scoring})")
        return

    print(f"\n  {args.position or 'all'}, {args.scoring}")
    print(f"  run {run_id}")
    print(f"  published {pub['published_at'][:16]}, model {meta['model_version']}\n")
    if args.compare_formats:
        ranks = {}
        for f in FORMATS:
            fr, _ = public_rows(conn, args.season, args.position, f,
                                args.free_agents)
            for r in fr:
                ranks.setdefault(r["player_id"], {})[f] = r["rank"]
        print(f"  {'#':<4}{'PLAYER':<24}{'TM':<5}"
              f"{'STD':>7}{'HALF':>7}{'PPR':>7}   RANK std/half/ppr")
        for r in rows[:args.top]:
            k = ranks[r["player_id"]]
            moved = "  <-" if len({k[f] for f in FORMATS}) > 1 else ""
            print(f"  {r['rank']:<4}{r['player'][:24]:<24}{(r['team'] or ''):<5}"
                  f"{r['fpts_standard']:>7.1f}{r['fpts_half']:>7.1f}"
                  f"{r['fpts_ppr']:>7.1f}"
                  f"   {k['standard']}/{k['half']}/{k['ppr']}{moved}")
        print(f"\n  A player can be RB7 in standard and RB3 in full PPR from")
        print(f"  one stat line. That is the same projection read two ways,")
        print(f"  not two projections.")
    else:
        print(f"  {'#':<4}{'PLAYER':<24}{'TM':<5}{'PTS':>8}{'TGT':>7}"
              f"{'REC':>6}{'RECYD':>8}{'RUYD':>8}")
        for r in rows[:args.top]:
            print(f"  {r['rank']:<4}{r['player'][:24]:<24}{(r['team'] or ''):<5}"
                  f"{r[f'fpts_{args.scoring}']:>8.1f}"
                  f"{float(r['targets'] or 0):>7.0f}{float(r['rec'] or 0):>6.0f}"
                  f"{float(r['rec_yds'] or 0):>8.0f}"
                  f"{float(r['rush_yds'] or 0):>8.0f}")

    total = conn.execute(
        "SELECT COUNT(*) c FROM run_projections WHERE run_id=?",
        (run_id,)).fetchone()["c"]
    resid = conn.execute(
        "SELECT COUNT(*) c FROM run_projections WHERE run_id=? AND is_residual=1",
        (run_id,)).fetchone()["c"]
    fa = total - resid - len([r for r in public_rows(
        conn, args.season, None, args.scoring, False)[0]])
    print(f"\n  snapshot holds {total} rows: {resid} residual and {fa} free "
          f"agents are in the run for reconciliation and out of this view.")


if __name__ == "__main__":
    main()
