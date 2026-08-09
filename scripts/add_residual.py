#!/usr/bin/env python3
"""Record opportunity the model left deliberately unassigned.

    python3 scripts/add_residual.py --team CLE --position QB \
        --pass-att 27.5 --completions 17.0 --pass-yds 178.7 --pass-td 1.0 \
        --note "5% other-QB bucket, Sanders 50 / Watson 45 / other 5"

WHY A RESIDUAL IS NOT A PLAYER

Cleveland was modelled as Sanders 50%, Watson 45%, other 5%. That last five
percent is real opportunity -- the receiving side is reconciled against it --
but it belongs to nobody in particular. Assigning it to a named third
quarterback would invent a projection the model never made.

So it gets a row that reconciles and never appears: is_residual, excluded
from rankings, from the site, and from anything a reader sees. It exists so
the arithmetic is honest about what was left unallocated rather than hiding
it inside somebody else's line.
"""
import argparse
import sqlite3
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--db", default="beatwire.db")
ap.add_argument("--season", type=int, default=2026)
ap.add_argument("--team", required=True)
ap.add_argument("--position", required=True)
ap.add_argument("--note", default="")
for f in ("pass-att", "completions", "pass-yds", "pass-td", "ints",
          "targets", "rec", "rec-yds", "rec-td",
          "rush-att", "rush-yds", "rush-td", "fumbles"):
    ap.add_argument(f"--{f}", type=float, default=0.0)
args = ap.parse_args()

conn = sqlite3.connect(args.db)
pid = f"{args.team.lower()}-{args.position.lower()}-residual"
name = f"{args.team} {args.position} residual"

cols = dict(
    pass_att=args.pass_att, completions=args.completions,
    pass_yds=args.pass_yds, pass_td=args.pass_td, ints=args.ints,
    targets=args.targets, rec=args.rec, recyd=args.rec_yds,
    rec_td=args.rec_td, rush_att=args.rush_att, ruyd=args.rush_yds,
    rush_td=args.rush_td, fumbles=args.fumbles,
)
pts = (cols["pass_yds"] / 25 + cols["pass_td"] * 4 - cols["ints"] * 2
       + (cols["ruyd"] + cols["recyd"]) / 10
       + (cols["rush_td"] + cols["rec_td"]) * 6 - cols["fumbles"] * 2)

conn.execute("""INSERT OR REPLACE INTO projection_staging
 (season, player_id, sleeper_id, player, position, team, ppr, half, standard,
  adjusted, exp_games, floor, ceiling, rank_pos, rec, recyd, ruyd, news_adj,
  trace, pass_att, completions, pass_yds, pass_td, ints, targets, rec_td,
  rush_att, rush_td, fumbles, is_residual)
 VALUES (?,?,'',?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,?,?,?,NULL,?,
         ?,?,?,?,?,?,?,?,?,?,1)""",
 (args.season, pid, name, args.position.upper(), args.team.upper(),
  pts + cols["rec"], pts + cols["rec"] * 0.5, pts,
  cols["rec"], cols["recyd"], cols["ruyd"],
  args.note or "residual",
  cols["pass_att"], cols["completions"], cols["pass_yds"], cols["pass_td"],
  cols["ints"], cols["targets"], cols["rec_td"], cols["rush_att"],
  cols["rush_td"], cols["fumbles"]))
conn.commit()
print(f"  {name}: reconciles, never ranked, never published")
if args.note:
    print(f"  {args.note}")
