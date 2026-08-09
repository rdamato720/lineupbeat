#!/usr/bin/env python3
"""Turn a published snapshot into the inputs that produced it.

    python3 scripts/derive_inputs.py
    python3 scripts/derive_inputs.py --check          # do they rebuild v1.0?
    python3 scripts/derive_inputs.py --show DET

WHY INPUTS AND NOT STATS

An event must never edit a final stat line. "Gibbs is out" is not an
instruction to set his rushing yards to zero; it is a change to his
availability, from which a carry share of zero follows, from which zero
yards follow at whatever efficiency he was being projected at.

The difference matters when the carries land somewhere. Moving 275 carries
to Montgomery and Jackson and letting each one's own yards-per-carry produce
the yardage is football. Moving Gibbs's 1,380 yards is arithmetic that
happens to look like football until somebody with a different efficiency
receives them.

So there is a layer between evidence and output:

    team environment  what the offence does in total
    model inputs      each player's share of it, and his efficiency
    raw stats         shares x environment, at those efficiencies
    fantasy points    scoring rules over raw stats

DERIVED, NOT INVENTED

For the baseline these inputs are read back out of Offense v1.0 by division:
if a back has 275 of his team's 457 carries, his carry share is 0.602. Feed
those shares back through and the workbook reappears exactly, which is the
check this script runs. Anything else would mean the engine and the baseline
disagree from the first day.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NON_TEAMS = {"FA", "FA/UNK", "UNK", ""}

SCHEMA = """
-- What a team's offence does in total, frozen per run.
--
-- Summing QB rows is not the same thing: Cleveland's QB rows summed to 522.5
-- attempts while the team was modelled at 550, and the receivers were
-- reconciled against 550. An independent team-level record is what makes
-- that visible instead of invisible.
CREATE TABLE IF NOT EXISTS team_environment (
  season INTEGER, team TEXT,
  pass_att REAL, completions REAL, pass_yds REAL, pass_td REAL,
  non_target_rate REAL,
  rush_att REAL, rush_yds REAL, rush_td REAL,
  source TEXT, updated_at TEXT,
  PRIMARY KEY (season, team)
);

-- The same, frozen with the run it validated.
CREATE TABLE IF NOT EXISTS run_team_environment (
  run_id TEXT, team TEXT,
  pass_att REAL, completions REAL, pass_yds REAL, pass_td REAL,
  non_target_rate REAL, rush_att REAL, rush_yds REAL, rush_td REAL,
  source TEXT,
  PRIMARY KEY (run_id, team)
);

-- Each player's share of his team, and how efficiently he uses it.
CREATE TABLE IF NOT EXISTS model_inputs (
  season INTEGER, player_id TEXT, player TEXT, team TEXT, position TEXT,
  availability REAL,          -- 1.0 healthy, 0.0 out
  carry_share REAL,           -- of team rush attempts
  target_share REAL,          -- of team player targets
  rush_td_share REAL,         -- of team rushing TDs, the goal-line role
  rec_td_share REAL,          -- of team receiving TDs
  pass_att_share REAL,        -- of team pass attempts, QBs
  catch_rate REAL,            -- receptions per target
  yards_per_target REAL,
  yards_per_carry REAL,
  yards_per_attempt REAL,     -- passing, QBs
  completion_rate REAL,
  int_rate REAL,
  pass_td_rate REAL,          -- section 7: pass_td = attempts x rate
  fumble_rate REAL,           -- per touch
  realloc_weight REAL,        -- how much freed opportunity this player takes
  headroom REAL,              -- the most he can end up with, as a share
  is_residual INTEGER DEFAULT 0,
  source TEXT, updated_at TEXT,
  PRIMARY KEY (season, player_id)
);
"""

# How much of somebody else's freed opportunity a player will absorb.
#
# A default rather than a football opinion. The intent is that a clear
# second-stringer takes most of it, depth takes some, and nobody ends up
# with a workload the position does not produce -- hence headroom.
DEFAULT_WEIGHT = {"RB": 1.0, "WR": 0.6, "TE": 0.5, "QB": 1.0, "FB": 0.3}
DEFAULT_HEADROOM = {"RB": 0.72, "WR": 0.32, "TE": 0.30, "QB": 1.0, "FB": 0.15}


def live_run(conn, season):
    r = conn.execute("SELECT run_id FROM published_snapshot WHERE season=?",
                     (season,)).fetchone()
    return r["run_id"] if r else None


def derive(conn, season, run_id):
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM run_projections WHERE run_id=?", (run_id,))]
    teams = {}
    for r in rows:
        t = (r["team"] or "").strip().upper()
        if t in NON_TEAMS:
            continue
        d = teams.setdefault(t, {k: 0.0 for k in (
            "pass_att", "completions", "pass_yds", "pass_td",
            "targets", "rec", "rec_yds", "rec_td",
            "rush_att", "rush_yds", "rush_td")})
        for k in d:
            d[k] += float(r.get(k) or 0)

    env = {}
    for t, d in teams.items():
        # The non-target rate is measured, not assumed. It should come out
        # near 3%, and if a team's does not that is worth knowing.
        ntr = (1 - d["targets"] / d["pass_att"]) if d["pass_att"] else 0.03
        env[t] = {**d, "non_target_rate": ntr}
    return rows, env


def player_inputs(r, e):
    """One player's shares and efficiencies, by division."""
    pos = (r["position"] or "").upper()
    f = lambda k: float(r.get(k) or 0)
    safe = lambda a, b: (a / b) if b else 0.0
    touches = f("rush_att") + f("rec")
    return {
        "availability": 1.0,
        "carry_share": safe(f("rush_att"), e["rush_att"]),
        "target_share": safe(f("targets"), e["targets"]),
        "rush_td_share": safe(f("rush_td"), e["rush_td"]),
        "rec_td_share": safe(f("rec_td"), e["rec_td"]),
        "pass_att_share": safe(f("pass_att"), e["pass_att"]),
        "catch_rate": safe(f("rec"), f("targets")),
        "yards_per_target": safe(f("rec_yds"), f("targets")),
        "yards_per_carry": safe(f("rush_yds"), f("rush_att")),
        "yards_per_attempt": safe(f("pass_yds"), f("pass_att")),
        "completion_rate": safe(f("completions"), f("pass_att")),
        "int_rate": safe(f("ints"), f("pass_att")),
        "pass_td_rate": safe(f("pass_td"), f("pass_att")),
        "fumble_rate": safe(f("fumbles"), touches),
        "realloc_weight": DEFAULT_WEIGHT.get(pos, 0.5),
        "headroom": DEFAULT_HEADROOM.get(pos, 0.3),
    }


def rebuild(inp, e):
    """Shares times environment, at the player's own efficiency.

    The inverse of the derivation, and the thing the engine will call after
    an event moves a share around. Multiplying by availability is what makes
    "out" mean zero opportunity rather than zero yards.
    """
    a = inp["availability"]
    rush_att = e["rush_att"] * inp["carry_share"] * a
    targets = e["targets"] * inp["target_share"] * a
    rec = targets * inp["catch_rate"]
    pass_att = e["pass_att"] * inp["pass_att_share"] * a
    return {
        "rush_att": rush_att,
        "rush_yds": rush_att * inp["yards_per_carry"],
        "rush_td": e["rush_td"] * inp["rush_td_share"] * a,
        "targets": targets,
        "rec": rec,
        "rec_yds": targets * inp["yards_per_target"],
        "rec_td": e["rec_td"] * inp["rec_td_share"] * a,
        "pass_att": pass_att,
        "completions": pass_att * inp["completion_rate"],
        "pass_yds": pass_att * inp["yards_per_attempt"],
        "pass_td": pass_att * inp["pass_td_rate"],
        "ints": pass_att * inp["int_rate"],
        "fumbles": (rush_att + rec) * inp["fumble_rate"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--show", help="one team's inputs")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    run_id = live_run(conn, args.season)
    if not run_id:
        sys.exit("  nothing published to derive from")
    rows, env = derive(conn, args.season, run_id)

    if args.show:
        t = args.show.upper()
        e = env.get(t)
        if not e:
            sys.exit(f"  no team {t}")
        print(f"\n  {t} ENVIRONMENT\n")
        for k in ("pass_att", "completions", "pass_yds", "pass_td",
                  "non_target_rate", "rush_att", "rush_yds", "rush_td"):
            print(f"    {k:<18}{e[k]:>10.2f}")
        print(f"\n  PLAYERS\n")
        print(f"  {'PLAYER':<24}{'POS':<5}{'CARRY%':>8}{'TGT%':>7}"
              f"{'RUTD%':>7}{'CATCH':>7}{'YPC':>6}{'YPT':>6}")
        mine = [r for r in rows if (r["team"] or "").upper() == t]
        for r in sorted(mine, key=lambda x: -(float(x["rush_att"] or 0)
                                              + float(x["targets"] or 0))):
            i = player_inputs(r, e)
            if i["carry_share"] < .005 and i["target_share"] < .005:
                continue
            print(f"  {r['player'][:24]:<24}{r['position']:<5}"
                  f"{i['carry_share']:>8.1%}{i['target_share']:>7.1%}"
                  f"{i['rush_td_share']:>7.1%}{i['catch_rate']:>7.1%}"
                  f"{i['yards_per_carry']:>6.2f}{i['yards_per_target']:>6.2f}")
        return

    if args.check:
        # Feed the derived inputs back through and see whether the workbook
        # reappears. If it does not, the engine and the baseline disagree
        # from day one and nothing built on top can be trusted.
        worst, worst_who = 0.0, ""
        checked = 0
        for r in rows:
            t = (r["team"] or "").upper()
            if t in NON_TEAMS:
                continue
            e = env[t]
            back = rebuild(player_inputs(r, e), e)
            for k, v in back.items():
                orig = float(r.get(k) or 0)
                d = abs(v - orig)
                if d > worst:
                    worst, worst_who = d, f"{r['player']}.{k} {orig:.3f} -> {v:.3f}"
            checked += 1
        print(f"\n  rebuilt {checked} players from their derived inputs")
        print(f"  worst difference: {worst:.2e}  {worst_who}")
        print(f"\n  {'exact' if worst < 1e-6 else 'NOT EXACT, the engine would drift'}")
        return

    now = datetime.now(timezone.utc).isoformat()
    src = f"derived from {run_id}"
    n = 0
    for t, e in env.items():
        conn.execute("INSERT OR REPLACE INTO team_environment VALUES "
                     "(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (args.season, t, e["pass_att"], e["completions"],
                      e["pass_yds"], e["pass_td"], e["non_target_rate"],
                      e["rush_att"], e["rush_yds"], e["rush_td"], src, now))
    for r in rows:
        t = (r["team"] or "").upper()
        if t in NON_TEAMS:
            continue
        i = player_inputs(r, env[t])
        conn.execute(
            "INSERT OR REPLACE INTO model_inputs VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (args.season, r["player_id"], r["player"], t, r["position"],
             i["availability"], i["carry_share"], i["target_share"],
             i["rush_td_share"], i["rec_td_share"], i["pass_att_share"],
             i["catch_rate"], i["yards_per_target"], i["yards_per_carry"],
             i["yards_per_attempt"], i["completion_rate"], i["int_rate"],
             i["pass_td_rate"], i["fumble_rate"], i["realloc_weight"],
             i["headroom"], r["is_residual"] or 0, src, now))
        n += 1
    conn.commit()
    print(f"  {len(env)} team environments, {n} player input rows")
    print(f"  from {run_id}")
    print(f"\n  next: python3 scripts/derive_inputs.py --check")


if __name__ == "__main__":
    main()
