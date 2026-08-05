#!/usr/bin/env python3
"""Season projections with a visible chain of reasoning and honest error bars.

    python3 scripts/project3.py --season 2024 --pool relevant
    python3 scripts/project3.py --explain "Puka Nacua" --season 2024
    python3 scripts/project3.py --sim --season 2024 --top 20
    python3 scripts/project3.py --scorecard 2022,2023
    python3 scripts/project3.py --ablate --backtest 2022,2023

FOUR THINGS v2 DID NOT DO

1. QUARTERBACKS WERE NEVER REGRESSED. v2 ran passing volume and touchdowns
   straight through at last season's rate while regressing every other
   position. QB was duly the worst position in the backtest, by a mile. Fixed
   here: passing touchdown rate per attempt regresses like every other rate,
   because it is just as unstable.

2. NO VISIBLE ARITHMETIC. A projection nobody can interrogate is a number you
   have to take on faith, and most systems cannot show their work because they
   are either black boxes or hand-built. This one carries a trace: baseline,
   then each adjustment, then the total. `--explain` prints it.

3. NO RANGES. "287 points" is what everyone publishes and it hides the thing
   that actually matters, which is spread. A back with a 60-point floor and a
   340-point ceiling is a different asset from one who lands on 280 every
   time. `--sim` runs the season repeatedly and reports the distribution.

4. NO PUBLISHED ERROR. `--scorecard` scores last season's projection against
   what happened, by position, next to the do-nothing baseline. It is
   uncomfortable and it is the most credible thing on the site.
"""

from __future__ import annotations

import argparse
import collections
import csv
import math
import random
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ROOT = Path(__file__).resolve().parent.parent

GAMES = 17
# Most recent season first. Tested rather than assumed: 60/30/10 was
# over-smoothing, and on two backtest windows a heavier recency weight
# predicted better at every tier -- 66.4 mean error on the top forty against
# 68.9. Pure last-season did marginally better overall but worse where it
# matters, so this keeps a little history without burying a breakout year.
#
#   weights          all   top 100   top 40
#   100/0/0         47.4      62.4     68.2
#   75/20/5         47.6      63.2     66.4   <- chosen
#   60/30/10        48.2      63.3     68.9
#   50/30/20        48.5      64.9     68.9
# A partial season is a smaller sample and should count for less. Brock
# Bowers played 17 games in 2024 and 12 in 2025; recency alone put 75% of the
# weight on the injured year and projected him as TE8 against a consensus TE1.
# The per-game rate in an injured season is depressed too, so without this a
# hurt player is penalised twice.
#
# Barely moves the backtest -- 63.4 to 63.2 on the top forty -- which is worth
# saying plainly: this is a face-validity fix, not an accuracy one. It is
# still worth having, because a visibly wrong ranking costs more trust than a
# decimal point of mean error.
# Squared-and-a-half, not square-root. A seven-game season is a quarter of
# the evidence of a full one, not two thirds. This is the "fresh year"
# assumption made concrete: the headline projection asks what a player does
# when he plays, so a season he mostly missed should not define him.
#
# Worth being honest that it moves the needle less than it feels like it
# should -- mean gap to consensus goes from -17 to -15 -- because the larger
# gaps are judgments about health and role, not weighting.
SAMPLE_POWER = 1.5

YEAR_WEIGHTS = [0.75, 0.20, 0.05]

# Measured, not guessed. Year-over-year correlation on 2024 -> 2025, filtered
# by volume so a thirty-carry back does not count like a three-hundred-carry
# one:
#
#   yards per carry     0.06 - 0.13   essentially noise even for workhorses
#   yards per target    0.37 - 0.42
#   catch rate          0.38 - 0.46
#   TD rate, 200+ car   0.64          the MOST repeatable thing here
#
# Worth stating plainly: changing these barely moves anything. All four
# candidate weightings landed within 0.2 points of mean error and within 0.01
# of rank correlation. Volume dominates and efficiency washes out. These are
# set to the halfway point between the old guesses and the measurement --
# defensible either way, and not worth further argument.
REGRESS = {
    # ypc set to the measured value rather than a halfway compromise. Yards
    # per carry correlates at 0.06-0.13 year over year even for 300-carry
    # backs: it is close to noise, and a back coming off 5.5 a carry is
    # overwhelmingly likely to come back toward 4.3. Tested at 0.60, 0.75 and
    # 0.85 -- identical mean error, marginally better rank correlation at the
    # top -- so the honest number costs nothing.
    "td_rate": 0.52, "ypc": 0.85, "ypt": 0.44, "catch_rate": 0.42,
    # New. Passing touchdown rate is no more stable than rushing or receiving
    # touchdown rate, and treating it as fixed is why QB was the worst
    # position in the backtest.
    "pass_td_rate": 0.55, "ypa": 0.30, "int_rate": 0.50,
    # Quarterback rushing touchdowns are a ROLE, not luck, and were being
    # regressed at 0.60 like everything else. Josh Allen scored on 11.8% of
    # his carries in 2024 and 12.5% in 2025; Goff and Stafford scored on 0.0%
    # in both. Those are not players getting lucky, they are teams deciding
    # who runs the ball at the goal line, and that decision persists.
    #
    # Regressing it hard cost the designed runners about sixty points each --
    # Allen, Lamar, Hurts and Daniels were all far below consensus while
    # pocket passers came out slightly high. This is the single biggest reason
    # our quarterback board looked wrong.
    "qb_rush_td_rate": 0.25,
}

# Measured on per-game production for players who stayed healthy in both
# seasons, because that is the question the headline number asks. Raw
# year-over-year retention falls off a cliff after 28 -- median 0.38 at age
# 30 -- but almost all of that is missed games, and missed games are already
# modelled separately in the adjusted column. Applying the raw decline here
# charged older players twice for the same thing: Christian McCaffrey was
# taking a 22% age cut on top of a 10% workload cut and landing 18% below
# consensus, having been 16% above it an hour earlier.
#
# When healthy, backs hold their per-game rate to about 27 and decline gently
# after. That is what this curve now says.
#
#   measured, healthy-only, per game:
#     23  1.05    24  0.95    25  1.04
#     26  1.06    27  1.03    28  0.65 (n=3)
AGE_CURVE = {
    "RB": {22: 0.98, 25: 1.02, 27: 1.00, 29: 0.93, 31: 0.85, 33: 0.75},
    "WR": {22: 0.94, 25: 1.02, 28: 1.00, 30: 0.96, 32: 0.89},
    "TE": {23: 0.90, 26: 1.00, 29: 0.99, 31: 0.95, 33: 0.88},
    "QB": {24: 0.97, 27: 1.00, 33: 1.00, 37: 0.97, 40: 0.90},
}
DEPTH_MULT = {1: 1.00, 2: 0.55, 3: 0.25, 4: 0.10}

# Typical share of a team's volume by depth slot, measured from history rather
# than guessed. This is the fix for the single worst failure mode: a player
# whose ROLE changed. Bhayshul Tuten carried 83 times as a rookie behind Travis
# Etienne; with Etienne gone he is the lead back, and no amount of reading his
# own history will say so. Projecting continuity had him at 61 points against a
# consensus of 205.
#
# So a player's historical share is blended toward the share his depth slot
# normally commands. A back promoted from third string to first gets pulled
# most of the way toward lead-back volume; an established starter barely
# moves, because his own history already says the same thing.
#
# This depends entirely on the depth chart being current. When it is not
# populated -- which is common in early August -- the blend does nothing,
# which is the right failure: no signal, no change.
# MEASURED, from three seasons and ninety-six team-years. Every one of these
# was previously a guess and every guess was too high, which is why the model
# kept promoting committee backs and backups: an RB1 was being handed 55% of a
# team's carries when the real median is 49%, an RB2 25% against a real 21%,
# and a second receiver 18% of targets against a real 14%.
#
# Small differences, compounding. James Conner, Woody Marks, Jacory
# Croskey-Merritt and Zach Charbonnet were all fifty-plus points high, and all
# of them are depth-chart RB1s in what are actually committees.
#
#   slot   RB carries   RB targets   WR targets   TE targets
#     1        0.493       0.091        0.225        0.148
#     2        0.211       0.041        0.143        0.048
#     3        0.073       0.013        0.095        0.018
#     4        0.023       0.006        0.054        0.007
ROLE_PRIOR = {
    # position -> depth slot -> (share of team carries, share of team targets)
    "RB": {1: (0.49, 0.09), 2: (0.21, 0.04), 3: (0.07, 0.01), 4: (0.02, 0.01)},
    "WR": {1: (0.01, 0.225), 2: (0.01, 0.143), 3: (0.01, 0.095), 4: (0.00, 0.054)},
    "TE": {1: (0.00, 0.148), 2: (0.00, 0.048), 3: (0.00, 0.018), 4: (0.00, 0.007)},
    "QB": {1: (0.07, 0.00), 2: (0.02, 0.00), 3: (0.01, 0.00), 4: (0.00, 0.00)},
}

# How far to pull toward the role prior. Applied only when the depth slot and
# the player's own history disagree -- a starter whose usage already looks
# like a starter's is left alone.
ROLE_PULL = 0.55

# Extreme workloads do not repeat. Christian McCaffrey took 436 touches in
# 2025 and the model happily projected 434 again, giving him 399 points --
# roughly sixteen percent above consensus and the sort of number that makes a
# whole board look unserious. Backs above this line come down toward it,
# because almost nobody does it twice in a row.
TOUCH_CEILING = {"RB": 340, "WR": 175, "TE": 140}
TOUCH_PULL = 0.45
# Beat reports split by how long they stay true.
#
# A season projection is a claim about seventeen weeks. "Took first-team reps
# on Tuesday" is a claim about Tuesday, and applying it as a flat multiplier
# across a whole season assumes the observation persists all year, which it
# usually does not -- the player who is up in August is often back down by
# October. So role signals do not touch season numbers.
#
# Season-ending events are different in kind. A torn ACL in August is true in
# December, and ignoring it would be worse than any timescale mismatch.
#
# The role signals are not wasted: they are what weekly projections run on,
# where a Wednesday practice report SHOULD move Sunday's number. That is a
# better fit for them and a sharper differentiator than one number trying to
# do both jobs.
SEASON_ADJ = {
    "ir_placement": -0.85, "surgery": -0.60, "pup_placement": -0.40,
    "retired": -1.00, "released": -0.35,
}
WEEKLY_ADJ = {
    "first_team_reps": 0.08, "starter_named": 0.12, "depth_chart_move": 0.06,
    "snap_share": 0.05, "committee": -0.05, "practice_absent": -0.04,
    "ruled_out": -0.30, "activated": 0.10, "cleared": 0.08,
    "practice_limited": -0.06,
}
ROLE_ADJ = {**SEASON_ADJ, **WEEKLY_ADJ}   # weekly projections use everything
ADJ_CAP = 0.25
# Off by default. On three real backtest windows the market blend made the
# model 3.4 points WORSE and flipped it from beating the do-nothing baseline
# to losing to it. The ADP-to-points curve below is crude enough that it
# poisons good projections. Left in place, and switched off, because the idea
# is sound and the implementation is not -- fit the curve properly and try
# again rather than deleting the mechanism.
MARKET_WEIGHT = 0.0
# Fullbacks are out. Nobody drafts one, no fantasy roster has a slot for one,
# and including them only creates opportunities to be embarrassed -- Patrick
# Ricard and Reggie Gilliam both reached the top 22 running backs on the
# strength of a depth chart row. A position nobody rosters has no upside and
# real downside.
SKILL = {"QB", "RB", "WR", "TE"}

# The feeds spell three teams differently and it produced 88 false "wrong
# team" reports. Normalise everywhere a team code is compared or stored.
TEAM_ALIAS = {"LA": "LAR", "SD": "LAC", "OAK": "LV", "STL": "LAR",
              "WSH": "WAS", "JAC": "JAX", "ARZ": "ARI"}


def norm_team(code):
    c = (code or "").strip().upper()
    return TEAM_ALIAS.get(c, c)

# Points per reception by format. Everything else is identical -- yardage and
# touchdowns do not change -- so the three formats are one projection scored
# three ways rather than three models. Doing it any other way would let them
# drift apart, which is how a site ends up ranking a player differently in
# half-PPR than the arithmetic supports.
FORMATS = {"ppr": 1.0, "half": 0.5, "standard": 0.0}

# The rest of the scoring, checked against a real ESPN league rather than
# assumed. Two of these were wrong on the first pass: interceptions scored at
# -1 when the standard is -2, and fumbles lost were not modelled at all. Both
# land almost entirely on quarterbacks, which is where the model was already
# weakest.
SCORING = {
    "pass_yd": 0.04, "pass_td": 4.0, "interception": -2.0,
    "rush_yd": 0.10, "rush_td": 6.0,
    "rec_yd": 0.10, "rec_td": 6.0,
    "fumble_lost": -2.0,
}

# Season-long spread, as a fraction of the projection. Derived from how far
# players actually landed from their prior-season pace, not invented: skill
# positions swing wildly, quarterbacks much less.
# Season-long spread, as a fraction of the projection. Lowered from the first
# pass, which produced floors nobody would recognise -- a 290-point back with a
# 60-point tenth percentile is not a floor, it is a season-ending injury in
# week two, and that case is already covered separately below. These are
# closer to how far players actually land from a well-built projection.
SPREAD = {"QB": 0.24, "RB": 0.30, "WR": 0.28, "TE": 0.30}

# Probability of missing enough of a season to matter, and how much is lost
# when it happens. Previously a flat 25-80 percent cut, which stacked on top
# of an already-wide distribution and double-counted the same risk.
INJURY_RISK = {"QB": 0.16, "RB": 0.28, "WR": 0.22, "TE": 0.22}
INJURY_COST = (0.55, 0.90)


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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


def blend(own, league, w):
    return league if own is None else own * (1 - w) + league * w


# A named starting quarterback plays. Estimating his availability from last
# season is how Malik Willis -- Miami's starter on a new contract -- came out
# at 9.1 expected games and 63 points, and Jayden Daniels at 11.5 and 191.
# Six starters landed under 200, which is not a rounding error, it is the
# model saying starters are backups.
#
# Quarterback is the position where the depth chart is nearly deterministic:
# you start seventeen games or you start none. So for QBs the slot governs
# availability, with history only nudging it.
QB_SLOT_GAMES = {1: 15.8, 2: 2.5, 3: 0.5, 4: 0.2}


# Availability is a durability trait, not a fact about last season. Weighting
# it 75/20/5 like usage meant one bad year defined a player: a back who missed
# eight games in 2025 and none in the three seasons before was treated as
# fragile. Four seasons, weighted almost flat, is a better read on whether
# someone gets hurt.
AVAIL_WEIGHTS = [0.30, 0.28, 0.24, 0.18]


def expected_games(conn, pid, seasons, pos, slot=None):
    """How many games this player is likely to actually play.

    Projecting everyone for a full seventeen is the quiet assumption that
    ruins volume-based models: almost nobody plays them all, so every number
    comes out high. The old share calculation was wrong in a way that happened
    to damp this, and fixing the shares exposed it.

    Estimated from games played, regressed toward the position norm so one
    lost season does not write a player off.
    """
    played, wsum = 0.0, 0.0
    # Four seasons for availability, not the three used for usage, and nearly
    # evenly weighted.
    avail_seasons = list(seasons) + [min(seasons) - 1]
    for w, s in zip(AVAIL_WEIGHTS, avail_seasons):
        r = conn.execute("""SELECT COUNT(*) g FROM weekly_stats
                            WHERE player_id=? AND season=? AND season_type='REG'""",
                         (pid, s)).fetchone()
        if r and r["g"]:
            played += min(17, r["g"]) * w
            wsum += w
    if pos == "QB" and slot:
        base = QB_SLOT_GAMES.get(slot, 1.0)
        if not wsum:
            return base
        hist = played / wsum
        # Mostly the slot, a little history: a starter with an injury record
        # should come down slightly, not all the way to his backup years.
        return base * 0.8 + min(17.0, hist) * 0.2

    if not wsum:
        return GAMES * 0.85
    rate = played / wsum / 17.0
    # Regress halfway toward the position norm: last year's availability is
    # informative but far from destiny.
    norm = {"QB": 0.82, "RB": 0.78, "WR": 0.82, "TE": 0.82}.get(pos, 0.80)
    return GAMES * (rate * 0.5 + norm * 0.5)


def weighted_usage(conn, pid, seasons):
    acc, wsum = {}, 0.0
    for w, s in zip(YEAR_WEIGHTS, seasons):
        r = conn.execute("""
            SELECT COUNT(*) g, AVG(targets) tgt, AVG(carries) car,
                   AVG(receptions) rec, AVG(receiving_yards) recyd,
                   AVG(rushing_yards) rushyd, AVG(attempts) patt,
                   AVG(passing_yards) passyd, AVG(completions) cmp,
                   SUM(receiving_tds) rectd, SUM(rushing_tds) rushtd,
                   SUM(passing_tds) passtd, SUM(interceptions) ints,
                   SUM(COALESCE(rushing_fumbles_lost,0)
                       + COALESCE(receiving_fumbles_lost,0)
                       + COALESCE(sack_fumbles_lost,0)) fum
            FROM weekly_stats WHERE player_id=? AND season=? AND season_type='REG'
        """, (pid, s)).fetchone()
        if not r or not r["g"]:
            continue
        w = w * ((min(17, r["g"]) / 17.0) ** SAMPLE_POWER)
        for k in r.keys():
            if k == "g" or r[k] is None:
                continue
            per = (r[k] / r["g"]
                   if k in ("rectd", "rushtd", "passtd", "ints", "fum") else r[k])
            acc[k] = acc.get(k, 0.0) + per * w
        acc["g"] = acc.get("g", 0.0) + r["g"] * w
        wsum += w
    return {k: v / wsum for k, v in acc.items()} if wsum else None


def team_totals(conn, seasons):
    out = {}
    for w, s in zip(YEAR_WEIGHTS, seasons):
        for r in conn.execute("""
            SELECT team, SUM(targets) tgt, SUM(carries) car, COUNT(DISTINCT week) wk
            FROM weekly_stats WHERE season=? AND season_type='REG' AND team IS NOT NULL
            GROUP BY team""", (s,)):
            if not r["wk"]:
                continue
            d = out.setdefault(r["team"], {"tgt": 0.0, "car": 0.0, "w": 0.0})
            d["tgt"] += (r["tgt"] or 0) / r["wk"] * 17 * w
            d["car"] += (r["car"] or 0) / r["wk"] * 17 * w
            d["w"] += w
    vals_t = [d["tgt"] / d["w"] for d in out.values() if d["w"]]
    vals_c = [d["car"] / d["w"] for d in out.values() if d["w"]]
    if vals_t:
        mt, mc = statistics.mean(vals_t), statistics.mean(vals_c)
        for d in out.values():
            if d["w"]:
                d["tgt"] = (d["tgt"] / d["w"]) * 0.75 + mt * 0.25
                d["car"] = (d["car"] / d["w"]) * 0.75 + mc * 0.25
    return out


def pos_means(rows):
    """League baselines, weighted by opportunity.

    A plain average across every player at a position is dominated by people
    who barely play: 53 of 127 running backs saw under five touches a game,
    and their touchdown rates pulled the RB mean down by a third. Every
    starter was then regressed toward a number built mostly from
    fourth-stringers, which systematically underprojected exactly the players
    anyone cares about. Weighting by opportunity means the baseline describes
    football as it is actually played.
    """
    out = {}
    for pos in {r["pos"] for r in rows if r["pos"]}:
        grp = [r for r in rows if r["pos"] == pos]

        def weight(r):
            u = r["u"]
            return max(0.5, u.get("tgt", 0) + u.get("car", 0)
                       + u.get("patt", 0) * 0.5)

        def m(fn):
            pairs = [(x, weight(r)) for r in grp if (x := fn(r)) is not None]
            if not pairs:
                return 0.0
            tot = sum(w for _, w in pairs)
            return sum(v * w for v, w in pairs) / tot if tot else 0.0
        out[pos] = {
            "ypt": m(lambda r: r["u"].get("recyd", 0) / r["u"]["tgt"] if r["u"].get("tgt") else None),
            "ypc": m(lambda r: r["u"].get("rushyd", 0) / r["u"]["car"] if r["u"].get("car") else None),
            "catch_rate": m(lambda r: r["u"].get("rec", 0) / r["u"]["tgt"] if r["u"].get("tgt") else None),
            "td_rate": m(lambda r: (r["u"].get("rectd", 0) + r["u"].get("rushtd", 0))
                         / (r["u"].get("tgt", 0) + r["u"].get("car", 0))
                         if (r["u"].get("tgt") or r["u"].get("car")) else None),
            "ypa": m(lambda r: r["u"].get("passyd", 0) / r["u"]["patt"] if r["u"].get("patt") else None),
            "pass_td_rate": m(lambda r: r["u"].get("passtd", 0) / r["u"]["patt"] if r["u"].get("patt") else None),
            "int_rate": m(lambda r: r["u"].get("ints", 0) / r["u"]["patt"] if r["u"].get("patt") else None),
        }
    return out


def scheme_mult(conn, team, season, pos):
    """A coach who throws more creates targets and destroys carries.

    Absent context is a no-op, not a crash: import_context.py is a separate
    hundred-megabyte download per season and the projection has to work
    without it.
    """
    try:
        r = conn.execute("""SELECT proe FROM team_context WHERE team=? AND season=?""",
                         (team, season)).fetchone()
    except sqlite3.OperationalError:
        return 1.0, 1.0
    if not r or r["proe"] is None:
        return 1.0, 1.0
    # PROE is in percentage points. A team +10 over expectation throws roughly
    # 10% more than neutral, which has to come out of the run game.
    edge = max(-12.0, min(12.0, r["proe"])) / 100.0
    return 1.0 + edge * 0.8, 1.0 - edge * 0.8


def _key(name):
    """Normalise a name for matching.

    Suffixes are the whole problem: the stats feed says "Kenneth Walker III"
    and the roster says "Kenneth Walker", so the join failed silently for 49
    players -- including several starters. They then carried no current team,
    no depth slot and no role prior, which is precisely why Walker projected
    82 points below consensus while sitting on the wrong roster.
    """
    import re as _re
    n = _re.sub(r"[.'`]", "", (name or "").lower())
    n = _re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def load_meta(conn, horizon="season"):
    """Roster facts plus beat reports, filtered by how far out we are looking.

    `season` applies only events that stay true for months. `weekly` applies
    everything, because a practice report is exactly what should move a
    week-ahead number.
    """
    table = SEASON_ADJ if horizon == "season" else ROLE_ADJ
    meta = {}
    roster = ROOT / "rosters" / "nfl.csv"
    if roster.exists():
        for r in csv.DictReader(roster.open()):
            entry = {
                "order": r.get("depth_order") or None,
                "adp": f(r.get("adp")),
                # Where he plays NOW. Historical stats carry last season's
                # team, so without this a back who signed in March is still
                # reconciled against his old offence's target share.
                "team": norm_team(r.get("team")) or None,
                "exp": int(r["years_exp"]) if str(r.get("years_exp") or "").strip() else None,
                "age": float(r["age"]) if str(r.get("age") or "").strip() else None,
                "pid": r.get("id"),
            }
            # Indexed three ways so a miss on one does not lose the player.
            # A previous edit built this dict and never stored it, which meant
            # `meta` was empty of roster data entirely -- no current teams, no
            # depth slots, no ADP -- and every feature depending on it was
            # silently doing nothing while the code around it looked right.
            meta[(r.get("name") or "").lower()] = entry
            meta[_key(r.get("name"))] = entry
            if r.get("id"):
                meta[r["id"]] = entry

    try:
        for r in conn.execute("""
            SELECT player_name, event, COUNT(*) c FROM nuggets
            WHERE player_id IS NOT NULL
              AND published_at > datetime('now','-21 days')
            GROUP BY player_name, event"""):
            w = table.get(r["event"])
            if not w:
                continue
            d = meta.setdefault(_key(r["player_name"]), {})
            d["adj"] = max(-0.9, min(ADJ_CAP, d.get("adj", 0.0) + w))
            d.setdefault("why", []).append(r["event"])
    except sqlite3.OperationalError:
        pass
    return meta


def in_pool(conn, pid, name, pos, meta, season, rules):
    if pos not in SKILL:
        return False, ""
    m = meta.get(name.lower()) or meta.get(_key(name)) or {}
    if rules["adp"] and m.get("adp"):
        return True, "drafted"
    if rules["depth"] and m.get("order"):
        try:
            if int(m["order"]) <= (2 if pos in ("QB", "TE") else 3):
                return True, "depth chart"
        except (TypeError, ValueError):
            pass
    if rules["snaps"]:
        r = conn.execute("""SELECT AVG(offense_pct) p, COUNT(*) g FROM snap_counts
                            WHERE player_id=? AND season=?""", (pid, season)).fetchone()
        if r and r["g"] and (r["p"] or 0) >= 0.25:
            return True, "snap share"
    if rules["news"] and m.get("adj") is not None:
        return True, "in the news"
    if rules["rookie"] and m.get("exp") == 0:
        return True, "rookie"
    return False, ""


def adp_to_ppr(adp, pos):
    base = {"QB": 300, "RB": 300, "WR": 290, "TE": 220}.get(pos)
    return None if not (adp and base) else max(20.0, base * (1.0 - 0.055 * (adp ** 0.62)))


# Measured on three backtest windows and 929 players. Kept what earned its
# place, switched off what did not, and said why:
#
#   team    +3.0  the model. Remove it and the entire edge disappears.
#   depth   +0.9  real, modest.
#   news     ??   UNTESTED, not disproven. The backtest scores 2022-2024 and
#                 our nugget store only holds the last three weeks of camp
#                 reports, so historical projections were adjusted with news
#                 from a different year -- that is noise, not evidence. Kept
#                 on because it is the differentiator and it has never had a
#                 fair trial. Score it forward: save this season's projections
#                 now and check them in January.
#   age     0.0   did nothing at all.
#   market  0.0   did nothing once its weight went to zero.
#   scheme  -0.2  marginally harmful as implemented.
#
# Age and scheme stay in the code because both are sound ideas measured
# badly -- age needs real birth dates rather than the roster's blank column,
# and scheme needs personnel groupings the public feed does not carry.
ALL_FLAGS = {"team": True, "age": True, "depth": True, "news": True,
             "market": False, "scheme": False}


def build(conn, season, flags, meta, rules=None):
    seasons = [season, season - 1, season - 2]
    ids = [r["player_id"] for r in conn.execute(
        "SELECT DISTINCT player_id FROM weekly_stats WHERE season=? AND season_type='REG'",
        (season,))]

    # gsis id -> "nfl-{sleeper_id}", the key the roster uses.
    xwalk = {}
    try:
        for x in conn.execute("""SELECT gsis_id, sleeper_id FROM id_map
                                 WHERE sleeper_id IS NOT NULL AND sleeper_id != ''"""):
            xwalk[x["gsis_id"]] = f"nfl-{x['sleeper_id']}"
    except sqlite3.OperationalError:
        pass

    # If the roster failed to load at all, do not silently drop everyone.
    roster_loaded = sum(1 for v in meta.values()
                        if isinstance(v, dict) and v.get("team")) > 200
    dropped_no_roster = []

    raw = []
    for pid in ids:
        u = weighted_usage(conn, pid, seasons)
        if not u or u.get("g", 0) < 4:
            continue
        info = conn.execute("""SELECT player_name, position, team FROM weekly_stats
                               WHERE player_id=? AND season=? ORDER BY week DESC LIMIT 1""",
                            (pid, season)).fetchone()
        pos = info["position"]
        # Everyone is kept for now. The pool filter runs at the END, after
        # team shares are computed -- filtering first meant a team's target
        # denominator only summed the pooled players, so every share was
        # divided by a fraction of the real total and the projections
        # exploded. Shares are a property of the whole roster.
        # Skill positions always. The current nflverse feed carries every
        # player on every roster, so without this `--pool all` cheerfully
        # projects nose tackles.
        if pos not in SKILL:
            continue

        # And he has to actually be on a team. Stefon Diggs, Keenan Allen and
        # Russell Wilson all played in 2025 and are on nobody's roster now;
        # publishing a number next to a retired or unsigned player is the kind
        # of error that makes every other number look unchecked.
        on_roster = (meta.get(xwalk.get(pid, ""))
                     or meta.get(info["player_name"].lower())
                     or meta.get(_key(info["player_name"])))
        if roster_loaded and not on_roster:
            dropped_no_roster.append(info["player_name"])
            continue
        why_pool = ""
        if rules:
            keep, why_pool = in_pool(conn, pid, info["player_name"], pos, meta, season, rules)
        else:
            keep = True
        # Current team beats historical team. The roster is refreshed every
        # run; the stats table is a record of where he used to play.
        # Current team by ID first. Name matching kept missing players who
        # changed teams -- Geno Smith projected on Las Vegas while the roster
        # had him on the Jets -- and a projection on the wrong team is the
        # single most visible error we can publish. The crosswalk maps the
        # stats id to a Sleeper id, which is what the roster is keyed on.
        cur = None
        sid = xwalk.get(pid)
        if sid:
            cur = (meta.get(sid) or {}).get("team")
        if not cur:
            cur = ((meta.get(info["player_name"].lower())
                    or meta.get(_key(info["player_name"])) or {}).get("team"))
        cur = norm_team(cur) if cur else None
        raw.append({"id": pid, "name": info["player_name"], "pos": pos,
                    "team": cur or norm_team(info["team"]),
                    "prev_team": norm_team(info["team"]),
                    "moved": bool(cur and cur != info["team"]),
                    "u": u, "pool": why_pool, "_keep": keep})

    if dropped_no_roster:
        print(f"  dropped {len(dropped_no_roster)} players not on a 2026 "
              f"roster (e.g. {', '.join(dropped_no_roster[:3])})")
    if not roster_loaded:
        print("  WARNING: roster metadata looks empty, so the not-on-a-roster "
              "check is off.\n  Current teams, depth slots and ADP are "
              "probably all missing too.")

    means = pos_means(raw)
    teams = team_totals(conn, seasons) if flags["team"] else {}
    # Shares come from the baseline season alone, not the three-year weighted
    # usage. Using the weighted number dragged a player's earlier usage on a
    # DIFFERENT team into this team's denominator -- a receiver who arrived in
    # free agency brought two prior seasons of another club's targets with
    # him. That inflated every denominator and quietly shrank everyone's
    # share, which is why the top of the board kept coming out low.
    # SEASON TOTALS, not per-game averages. Summing averages badly inflates a
    # team's denominator: a receiver who appeared in four games at 3.5 targets
    # contributed 3.5 to a total he only took fourteen targets from. Atlanta's
    # real volume is 30.5 targets a game; summing averages gave 42.3, so every
    # share was divided by 1.39 and every player came out roughly a third
    # light. This was the single largest source of underprojection.
    tgt_tot, car_tot, own = {}, {}, {}
    for r in raw:
        s1 = conn.execute("""
            SELECT SUM(targets) tgt, SUM(carries) car
            FROM weekly_stats WHERE player_id=? AND season=? AND season_type='REG'
        """, (r["id"], season)).fetchone()
        own[r["id"]] = {"tgt": (s1["tgt"] or 0.0) if s1 else 0.0,
                        "car": (s1["car"] or 0.0) if s1 else 0.0}
        hist = r.get("prev_team") or r["team"]
        tgt_tot[hist] = tgt_tot.get(hist, 0.0) + own[r["id"]]["tgt"]
        car_tot[hist] = car_tot.get(hist, 0.0) + own[r["id"]]["car"]

    out = []
    for r in raw:
        u, pos = r["u"], r["pos"] or "WR"
        m = means.get(pos, next(iter(means.values())))
        meta_p = (meta.get(xwalk.get(r["id"], ""))
                  or meta.get(r["name"].lower())
                  or meta.get(_key(r["name"]))
                  or {})
        trace = []

        tgt_pg, car_pg = u.get("tgt", 0.0), u.get("car", 0.0)
        _slot = None
        try:
            _slot = int(meta_p["order"]) if meta_p.get("order") else None
        except (TypeError, ValueError):
            _slot = None
        exp_g = expected_games(conn, r["id"], seasons, pos, _slot)
        avail = exp_g / GAMES

        if flags["team"] and r["team"] in teams:
            tt = teams[r["team"]]
            # Share from this season, then blended toward the three-year rate
            # so a one-year spike is not taken entirely at face value.
            o = own.get(r["id"], {"tgt": 0.0, "car": 0.0})
            # A player who changed teams has no share of his new offence's
            # history. Carry his share from the team he actually played for,
            # which assumes the role travels with him -- crude, and much
            # closer than pretending he was there all along.
            src_team = r.get("prev_team") if r.get("moved") else r["team"]
            share_t = o["tgt"] / max(0.01, tgt_tot.get(src_team, 1))
            share_c = o["car"] / max(0.01, car_tot.get(src_team, 1))
            # Blend the player's own share toward what his depth slot
            # normally commands. This is what lets a promoted backup be
            # projected as a starter rather than as last year's backup.
            slot = None
            try:
                slot = int(meta_p["order"]) if meta_p.get("order") else None
            except (TypeError, ValueError):
                slot = None
            prior = (ROLE_PRIOR.get(pos, {}) or {}).get(slot) if slot else None
            if prior and flags["depth"]:
                pc, pt = prior
                # How much history does this player have arguing against the
                # promotion? A second-year back with 83 career carries has
                # almost none, and the depth chart is the better evidence --
                # that is the Bhayshul Tuten case the prior exists for.
                #
                # A fullback with three seasons of fullback usage has a great
                # deal, and listing him RB1 does not make him a lead back.
                # Hunter Luepke went from 37 points to 158 on exactly that,
                # which is the sort of number a reader screenshots.
                #
                # So the pull weakens as the contradicting sample grows.
                career = conn.execute("""
                    SELECT SUM(carries) c, SUM(targets) t, COUNT(*) g
                    FROM weekly_stats WHERE player_id=? AND season_type='REG'
                """, (r["id"],)).fetchone()
                touches = ((career["c"] or 0) + (career["t"] or 0)) if career else 0
                games = (career["g"] or 0) if career else 0
                pull = ROLE_PULL
                # A first-string listing on thin evidence is exactly the case
                # the prior exists for: Omarion Hampton played ten games as a
                # rookie and is now the Chargers' starter. Give it full weight.
                if slot == 1 and games < 20:
                    pull = 0.80
                elif games >= 25 and touches < games * 6:
                    # A long record of light usage. Trust it over the chart.
                    pull = ROLE_PULL * 0.25
                elif games >= 25:
                    pull = ROLE_PULL * 0.75
                # Which is better evidence: last season's usage, or this
                # season's depth chart?
                #
                # It depends entirely on whether he moved. A back who stayed
                # put and took 309 carries has told us more than a chart row
                # can -- that was James Cook, buried by a mis-scraped slot.
                # But a back who signed somewhere new carries a share of an
                # offence he no longer plays in, and there the chart is the
                # only current information we have. Kenny Gainwell arrived in
                # Tampa as the backup and was ranked eleventh on the strength
                # of what he did in Philadelphia.
                #
                # So: stayed put means raise-only, because his usage is real.
                # Moved means the slot governs in both directions, because
                # his usage describes somewhere else.
                if r.get("moved"):
                    # His old share is not evidence about his new job. David
                    # Montgomery split carries with Jahmyr Gibbs in Detroit;
                    # that tells you nothing about how Houston will use him.
                    # Kenneth Walker shared a Seattle backfield and walks into
                    # Kansas City with no real competition. Blending in the
                    # old number just imports the wrong offence's committee.
                    #
                    # So for a player who moved, the share comes entirely from
                    # his slot on the new depth chart, applied to the new
                    # team's volume. Efficiency still comes from his own
                    # history, because yards per carry is a player trait and
                    # role is not.
                    # ... unless a long career says he is a specialist. The
                    # moved rule skipped the light-usage check entirely and
                    # put two fullbacks -- Patrick Ricard and Reggie Gilliam,
                    # both listed in their new teams' running back group --
                    # inside the top 22. A player with five seasons and fifty
                    # touches a year is not about to lead a backfield because
                    # a chart row places him there.
                    if games >= 25 and touches < games * 6:
                        share_c = min(share_c * 1.3, pc * 0.35)
                        share_t = min(share_t * 1.3, pt * 0.35)
                    else:
                        share_c, share_t = pc, pt
                else:
                    share_c = share_c * (1 - pull) + pc * pull if pc > share_c else share_c
                    share_t = share_t * (1 - pull) + pt * pull if pt > share_t else share_t

            tgt_s = tt["tgt"] * share_t
            car_s = tt["car"] * share_c
            # Guard: never project less volume than the weighted history
            # implies, scaled to a full season. Reconciliation should
            # redistribute a pie, not shrink it.
            #
            # This whole block is a deliberate trade. Single-season shares
            # improved the top 100 from +3.8 to +6.4 and the top 40 from +11.5
            # to +12.2, while costing about a point across all six hundred
            # players. For a draft tool that is the right side of the trade --
            # nobody agonises over the three hundredth ranked player -- but it
            # is a trade, not a free win, and someone reading this later
            # should know it was made on purpose.
            # Floor only, never a ceiling: a promoted player must be allowed
            # to exceed what his own history implies.
            tgt_s = max(tgt_s, tgt_pg * GAMES * 0.85)
            car_s = max(car_s, car_pg * GAMES * 0.85)
            # Scale to the games he is actually likely to play.
            # Availability is deliberately NOT applied here. The headline
            # projection answers "what does he do this season", and a fresh
            # season should not carry last season's injuries. Availability is
            # applied afterwards, once, to produce a separate number.
        else:
            tgt_s, car_s = tgt_pg * GAMES, car_pg * GAMES

        ypt = blend(u.get("recyd", 0) / tgt_pg if tgt_pg else None, m["ypt"], REGRESS["ypt"])
        ypc = blend(u.get("rushyd", 0) / car_pg if car_pg else None, m["ypc"], REGRESS["ypc"])
        cr = blend(u.get("rec", 0) / tgt_pg if tgt_pg else None, m["catch_rate"], REGRESS["catch_rate"])
        opp = tgt_pg + car_pg
        tdr = blend(((u.get("rectd", 0) + u.get("rushtd", 0)) / opp) if opp else None,
                    m["td_rate"], REGRESS["td_rate"])

        # Receptions are held separately so the same projection can be scored
        # in every format without recomputing anything.
        rec_component = tgt_s * cr
        fum_season = u.get("fum", 0.0) * GAMES
        base_ppr = (rec_component
                    + tgt_s * ypt * SCORING["rec_yd"]
                    + car_s * ypc * SCORING["rush_yd"]
                    + (tgt_s + car_s) * tdr * SCORING["rec_td"]
                    + fum_season * SCORING["fumble_lost"])

        if pos == "QB":
            # Quarterbacks are close to all-or-nothing: you start seventeen
            # games or you start none. Multiplying a six-game backup's
            # per-game rate by seventeen invents a season he was never going
            # to play, and QB was duly the worst position in the backtest by a
            # wide margin. Expected starts are estimated from how much he
            # actually played, regressed toward the middle.
            games_played = u.get("g", 0.0)
            start_rate = min(1.0, games_played / 15.0)
            # Volume as well as availability. A quarterback who threw 120
            # times in relief last year will throw 500 as a starter, and his
            # own history cannot say so. Blend toward a starter's typical
            # attempt volume in proportion to how thin his sample is.
            if _slot == 1:
                thin = 1.0 - min(1.0, games_played / 13.0)
                if thin > 0:
                    league_patt = 33.0        # attempts per game, starter
                    u = dict(u)
                    u["patt"] = u.get("patt", 0.0) * (1 - thin) + league_patt * thin
            # A part-time starter is more likely to be part-time again than to
            # jump to a full season, but less extreme than last year suggests.
            # Full season for the headline, same as every other position.
            # Availability is applied once at the end.
            exp_games = GAMES
            patt_pg = u.get("patt", 0.0)
            ypa = blend(u.get("passyd", 0) / patt_pg if patt_pg else None,
                        m["ypa"], REGRESS["ypa"])
            ptdr = blend(u.get("passtd", 0) / patt_pg if patt_pg else None,
                         m["pass_td_rate"], REGRESS["pass_td_rate"])
            # A rushing quarterback's own rate, barely regressed.
            qb_tdr = blend(u.get("rushtd", 0) / car_pg if car_pg else None,
                           m["td_rate"], REGRESS["qb_rush_td_rate"])
            intr = blend(u.get("ints", 0) / patt_pg if patt_pg else None,
                         m["int_rate"], REGRESS["int_rate"])
            patt_s = patt_pg * exp_games
            qb_car = car_s * (exp_games / GAMES)
            qb_fum = u.get("fum", 0.0) * exp_games
            base_ppr = (patt_s * ypa * SCORING["pass_yd"]
                        + patt_s * ptdr * SCORING["pass_td"]
                        + patt_s * intr * SCORING["interception"]
                        + qb_car * ypc * SCORING["rush_yd"]
                        + qb_car * qb_tdr * SCORING["rush_td"]
                        + qb_fum * SCORING["fumble_lost"])
            rec_component = 0.0

        trace.append(("baseline usage x regressed efficiency", base_ppr, base_ppr))
        ppr = base_ppr

        def step(label, mult):
            nonlocal ppr
            before = ppr
            ppr *= mult
            if abs(ppr - before) > 0.5:
                trace.append((label, ppr - before, ppr))

        if flags["scheme"] and r["team"]:
            tm, cm = scheme_mult(conn, r["team"], season, pos)
            step("coaching scheme (PROE)", tm if pos in ("WR", "TE", "QB") else cm)
        if flags["age"]:
            step("age curve", age_mult(pos, meta_p.get("age")))
        # NO second depth multiplier. There used to be one here, a flat cut of
        # up to 30% for anyone not listed first, applied on top of the role
        # prior that already handles depth through share. It cost James Cook
        # 56.7 points -- a 300-carry back marked down because one row in a
        # 6,000-row feed listed him third.
        #
        # Depth belongs in exactly one place: the share prior, which can only
        # raise. Two mechanisms for the same signal is how a fix gets applied
        # to one of them and quietly not the other, which is what happened.
        if flags["news"] and meta_p.get("adj"):
            step(f"beat reports ({', '.join(meta_p.get('why', [])[:2])})",
                 1 + meta_p["adj"])

        note = ""
        if flags["market"] and meta_p.get("adp"):
            implied = adp_to_ppr(meta_p["adp"], pos)
            if implied:
                before = ppr
                ppr = ppr * (1 - MARKET_WEIGHT) + implied * MARKET_WEIGHT
                if abs(ppr - before) > 0.5:
                    trace.append(("market (ADP) blend", ppr - before, ppr))
                gap = before - implied
                if abs(gap) > 35:
                    note = "model high vs ADP" if gap > 0 else "model low vs ADP"

        if rules and not r.get("_keep"):
            continue
        # Two numbers, deliberately. Every other projection site publishes an
        # "if healthy" full-season line; ours is risk-adjusted, because
        # regressing availability toward the position norm measurably predicts
        # actual outcomes better (47.0 mean error against 47.4 at every tier
        # tested). But a reader comparing us to anyone else would read the
        # lower number as an error rather than a different question being
        # answered, so both are published: `healthy` for comparison, `ppr` for
        # what we actually expect to happen.
        # `ppr` IS the full-season number now -- availability was never
        # applied. `adjusted` is the same projection scaled by expected games,
        # published beside it rather than folded into it.
        adjusted = ppr * avail

        # Extreme workload regression, applied before scoring.
        ceiling = TOUCH_CEILING.get(pos)
        if ceiling:
            touches = tgt_s + car_s
            if touches > ceiling:
                over = touches - ceiling
                keep = 1 - (over / touches) * TOUCH_PULL
                tgt_s *= keep
                car_s *= keep
                rec_component *= keep
                base_ppr *= keep
                ppr *= keep

        # Scale the reception component by format. Every adjustment above was
        # multiplicative on the whole projection, so the ratio holds.
        scale = ppr / base_ppr if base_ppr else 1.0
        by_format = {
            fmt: (ppr - rec_component * scale * (1.0 - pts))
            for fmt, pts in FORMATS.items()
        }

        out.append({**r, "ppr": ppr, "healthy": ppr, "adjusted": adjusted,
                    "avail": avail,
                    "fmt": by_format,
                    "base": base_ppr, "trace": trace,
                    "adp": meta_p.get("adp"), "note": note,
                    "rec": tgt_s * cr, "recyd": tgt_s * ypt,
                    "ruyd": car_s * ypc, "tgt": tgt_s, "car": car_s})
    return out


def simulate(row, runs=4000):
    """Season outcomes, not a point estimate."""
    pos = row["pos"] or "WR"
    sd = row["ppr"] * SPREAD.get(pos, 0.45)
    risk = INJURY_RISK.get(pos, 0.28)
    vals = []
    for _ in range(runs):
        v = random.gauss(row["ppr"], sd)
        if random.random() < risk:
            v *= random.uniform(*INJURY_COST)   # games missed
        vals.append(max(0.0, v))
    vals.sort()
    q = lambda p: vals[int(p * (len(vals) - 1))]
    return {"p10": q(0.10), "p25": q(0.25), "med": q(0.50),
            "p75": q(0.75), "p90": q(0.90), "vals": vals}


PROJ_SCHEMA = """
CREATE TABLE IF NOT EXISTS projections (
    season INTEGER, player_id TEXT, sleeper_id TEXT,
    player TEXT, position TEXT, team TEXT,
    ppr REAL, half REAL, standard REAL, adjusted REAL, exp_games REAL,
    floor REAL, ceiling REAL, rank_pos INTEGER,
    rec REAL, recyd REAL, ruyd REAL, tgt REAL, car REAL,
    news_adj REAL, trace TEXT,
    PRIMARY KEY (season, player_id)
);
"""


def roster_is_verified(conn, max_age_hours=36):
    """Refuse to publish on an unchecked roster.

    The guard writes a verdict every time it runs; this reads the most recent
    one. Publishing projections built on a roster nobody verified is how a
    player ends up listed on a team he left in March, and that costs more
    trust than the projections earn.
    """
    from datetime import datetime, timezone
    try:
        r = conn.execute("""SELECT checked_at, verdict, team_disagreements
                            FROM roster_checks ORDER BY checked_at DESC
                            LIMIT 1""").fetchone()
    except sqlite3.OperationalError:
        return False, "roster has never been checked — run scripts/roster_guard.py"
    if not r:
        return False, "roster has never been checked — run scripts/roster_guard.py"
    age = (datetime.now(timezone.utc)
           - datetime.fromisoformat(r["checked_at"])).total_seconds() / 3600
    if age > max_age_hours:
        return False, (f"last roster check was {age:.0f} hours ago — "
                       f"re-run scripts/roster_guard.py")
    # "check" blocks too. You asked for certainty, and the point of the gate
    # is not to never publish -- it is to never publish without looking. Read
    # the disagreements, then --force if they are benign.
    if r["verdict"] in ("do not publish", "check"):
        return False, (f"roster check says do not publish "
                       f"({r['team_disagreements']} disagreements)")
    return True, f"roster verified {age:.0f}h ago, verdict '{r['verdict']}'"


def write_projections(conn, season, meta, rules, force=False):
    """Persist projections so the site can read them.

    Keyed to Sleeper ids via the crosswalk, because that is what the news
    pipeline and the roster already use. Without that join the projection is a
    separate product that happens to share a screen; with it, a player page
    can show what the beat said and what it did to his number.
    """
    ok, why = roster_is_verified(conn)
    print(f"  {'OK' if ok else 'BLOCKED'}  {why}")

    # The role prior is the single biggest correction in the model and it is
    # entirely dependent on the depth chart. Silence here would be dangerous:
    # without depth data every promoted player is projected as last year's
    # backup, which is the failure that produced Bhayshul Tuten at 61 points
    # against a consensus of 205.
    import csv as _csv
    _r = ROOT / "rosters" / "nfl.csv"
    if _r.exists():
        _rows = list(_csv.DictReader(_r.open()))
        _skill = [x for x in _rows if (x.get("position") or "").upper() in SKILL]
        _dep = [x for x in _skill if (x.get("depth_order") or "").strip()]
        _pct = 100 * len(_dep) / max(1, len(_skill))
        print(f"  depth chart {_pct:.0f}% populated "
              f"({len(_dep)}/{len(_skill)} skill players)")
        if _pct < 50:
            print("  WARNING: role priors will barely fire. Players whose role")
            print("  changed this offseason will be projected as last season's")
            print("  backups. Consider waiting for Sleeper to publish depth")
            print("  charts before treating these numbers as publishable.")
    if not ok and not force:
        sys.exit("  publish blocked. Fix the roster, or pass --force if you "
                 "know\n  what you are doing and can explain it afterwards.")

    import json as _json
    # Schema changed when the three scoring formats landed. Rebuilding is
    # correct here: projections are derived, so there is nothing to migrate,
    # and a stale schema fails at insert time with a column-count error that
    # says nothing about the cause.
    conn.execute("DROP TABLE IF EXISTS projections")
    conn.executescript(PROJ_SCHEMA)
    rows = build(conn, season, ALL_FLAGS, meta, rules)

    xwalk = {}
    try:
        for r in conn.execute("SELECT gsis_id, sleeper_id FROM id_map "
                              "WHERE sleeper_id IS NOT NULL AND sleeper_id != ''"):
            xwalk[r["gsis_id"]] = r["sleeper_id"]
    except sqlite3.OperationalError:
        print("  no id_map — run scripts/import_snaps.py to build the crosswalk")

    by_pos = {}
    for r in sorted(rows, key=lambda x: x["ppr"], reverse=True):
        by_pos.setdefault(r["pos"], []).append(r)

    conn.execute("DELETE FROM projections WHERE season=?", (season,))
    n, linked = 0, 0
    for pos, group in by_pos.items():
        for i, r in enumerate(group, 1):
            s = simulate(r, 1500)
            sid = xwalk.get(r["id"])
            if sid:
                linked += 1
            trace = _json.dumps([[lab, round(d, 1), round(run, 1)]
                                 for lab, d, run in r["trace"]])
            conn.execute("INSERT OR REPLACE INTO projections VALUES "
                         "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (season, r["id"], sid, r["name"], pos, r["team"],
                          round(r["fmt"]["ppr"], 1), round(r["fmt"]["half"], 1),
                          round(r["fmt"]["standard"], 1),
                          round(r["adjusted"], 1), round(r["avail"] * GAMES, 1),
                          round(s["p10"], 1), round(s["p90"], 1), i,
                          round(r["rec"], 1), round(r["recyd"], 1), round(r["ruyd"], 1),
                          round(r["tgt"], 1), round(r["car"], 1),
                          round((meta.get(r["name"].lower())
                                 or meta.get(_key(r["name"])) or {}).get("adj", 0.0), 3),
                          trace))
            n += 1
    conn.commit()
    print(f"  wrote {n} projections for {season + 1} ({linked} linked to Sleeper ids)")
    print(f"  next: python3 -m beatwire.cli export --sports nfl --limit 4000")


SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS projection_log (
    taken_at   TEXT NOT NULL,
    season     INTEGER NOT NULL,
    player_id  TEXT NOT NULL,
    player     TEXT, position TEXT, team TEXT,
    ppr        REAL,      -- with every adjustment
    half       REAL,
    standard   REAL,
    ppr_nonews REAL,      -- identical model, news switched off
    news_adj   REAL,
    news_why   TEXT,
    PRIMARY KEY (taken_at, season, player_id)
);
"""


def snapshot(conn, season, meta, label=None):
    """Freeze today's projection so it can be scored in January.

    Two numbers per player: with the beat adjustments and without. That pair
    is the only honest way to find out whether the news layer helps, because
    the backtest cannot answer it -- historical seasons have no archived camp
    reports to adjust with, so scoring 2023 with 2026 news measures nothing.
    Save both now, compare both later, and let the season decide.
    """
    from datetime import datetime, timezone
    conn.executescript(SNAPSHOT_SCHEMA)
    taken = label or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Record the season being PROJECTED, not the one used as a baseline.
    # Storing the baseline meant score_log compared a 2026 projection against
    # 2025 results -- scoring the model against its own input, which produced
    # a flatteringly small error and told us nothing.
    target = season + 1

    with_news = {r["id"]: r for r in build(conn, season, ALL_FLAGS, meta, None)}
    without = {r["id"]: r for r in
               build(conn, season, {**ALL_FLAGS, "news": False}, meta, None)}

    n = 0
    for pid, r in with_news.items():
        m = meta.get(r["name"].lower(), {})
        conn.execute("INSERT OR REPLACE INTO projection_log VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (taken, target, pid, r["name"], r["pos"], r["team"],
                      r["ppr"], without.get(pid, {}).get("ppr"),
                      m.get("adj", 0.0), ", ".join(m.get("why", [])[:4])))
        n += 1
    conn.commit()
    moved = sum(1 for r in with_news.values()
                if abs(r["ppr"] - without.get(r["id"], {}).get("ppr", r["ppr"])) > 1)
    print(f"  saved {n} projections as '{taken}'")
    print(f"  baseline {season}, projecting {target}")
    print(f"  {moved} of them were moved by beat reports")
    print("\n  Score it after the season with:")
    print(f"    python3 scripts/project3.py --score-log {taken}")


def score_log(conn, taken):
    """Did the news layer help? Answered against real outcomes."""
    rows = conn.execute("SELECT * FROM projection_log WHERE taken_at=?",
                        (taken,)).fetchall()
    if not rows:
        sys.exit(f"  no snapshot '{taken}'")
    season = rows[0]["season"]
    actual = {r["player_id"]: r["pts"] for r in conn.execute("""
        SELECT player_id, SUM(fantasy_points_ppr) pts, COUNT(*) g
        FROM weekly_stats WHERE season=? AND season_type='REG'
        GROUP BY player_id HAVING g>=6""", (season,))}
    if not actual:
        sys.exit(f"  no {season} results yet — that is expected. This snapshot "
                 f"projects {season}, so it can only be scored once that\n"
                 f"  season has been played and imported.")

    both, moved = [], []
    for r in rows:
        a = actual.get(r["player_id"])
        if a is None or r["ppr_nonews"] is None:
            continue
        both.append((abs(r["ppr"] - a), abs(r["ppr_nonews"] - a)))
        if abs(r["ppr"] - r["ppr_nonews"]) > 1:
            moved.append((abs(r["ppr"] - a), abs(r["ppr_nonews"] - a), r))

    print(f"\n  DID THE BEAT REPORTS HELP?   snapshot {taken}, season {season}\n")
    print(f"  {'':<26} {'WITH NEWS':>10} {'WITHOUT':>10} {'EDGE':>7}")
    w = statistics.mean(x[0] for x in both); o = statistics.mean(x[1] for x in both)
    print(f"  {'all ' + str(len(both)) + ' players':<26} {w:>10.1f} {o:>10.1f} {o-w:>+7.1f}")
    if moved:
        mw = statistics.mean(x[0] for x in moved); mo = statistics.mean(x[1] for x in moved)
        print(f"  {'the ' + str(len(moved)) + ' we moved':<26} {mw:>10.1f} {mo:>10.1f} {mo-mw:>+7.1f}")
        print("\n  The second row is the real test. Across everyone the news layer")
        print("  is diluted by players it never touched; on the ones it moved,")
        print("  it either helped or it did not.")
        moved.sort(key=lambda x: x[1] - x[0], reverse=True)
        print("\n  best calls:")
        for mw_, mo_, r in moved[:5]:
            print(f"    {r['player'][:22]:<22} {r['news_why'][:30]:<30} {mo_-mw_:>+7.1f}")
        print("  worst calls:")
        for mw_, mo_, r in moved[-3:]:
            print(f"    {r['player'][:22]:<22} {r['news_why'][:30]:<30} {mo_-mw_:>+7.1f}")


def scorecard(conn, seasons, meta):
    print(f"\n  HOW WRONG WE WERE  —  {seasons}")
    print("  Graded on the ADJUSTED number, since that is the one claiming to")
    print("  predict a real season. The headline full-season number answers a")
    print("  different question and cannot be scored this way.\n")
    print(f"  {'POSITION':<10} {'N':>5} {'OUR MAE':>9} {'DO-NOTHING':>12} {'EDGE':>7}")
    agg = collections.defaultdict(lambda: [[], []])
    for s in seasons:
        proj = build(conn, s, ALL_FLAGS, meta, None)
        actual = {r["player_id"]: r["pts"] for r in conn.execute("""
            SELECT player_id, SUM(fantasy_points_ppr) pts, COUNT(*) g
            FROM weekly_stats WHERE season=? AND season_type='REG'
            GROUP BY player_id HAVING g>=6""", (s + 1,))}
        prev = {r["player_id"]: r["pts"] for r in conn.execute("""
            SELECT player_id, SUM(fantasy_points_ppr) pts, COUNT(*) g
            FROM weekly_stats WHERE season=? AND season_type='REG'
            GROUP BY player_id HAVING g>=6""", (s,))}
        for p in proj:
            a = actual.get(p["id"])
            if a is None or p["id"] not in prev:
                continue
            agg[p["pos"]][0].append(abs(p["adjusted"] - a))
            agg[p["pos"]][1].append(abs(prev[p["id"]] - a))
    allm, alln = [], []
    for pos, (mine, naive) in sorted(agg.items(), key=lambda kv: -len(kv[1][0])):
        if not mine:
            continue
        m, n = statistics.mean(mine), statistics.mean(naive)
        allm += mine; alln += naive
        flag = "" if m < n else "  <-"
        print(f"  {pos:<10} {len(mine):>5} {m:>9.1f} {n:>12.1f} {n-m:>+7.1f}{flag}")
    if allm:
        print(f"  {'ALL':<10} {len(allm):>5} {statistics.mean(allm):>9.1f} "
              f"{statistics.mean(alln):>12.1f} "
              f"{statistics.mean(alln)-statistics.mean(allm):>+7.1f}")
    print("\n  MAE in season-long PPR points. 'Do-nothing' is reusing the prior")
    print("  season's total. A position marked <- is one where we are worse")
    print("  than doing nothing, and should be said out loud rather than hidden.")

    # The headline number averages over three hundred players, most of whom
    # nobody drafts. Where the model actually earns its keep is the top of the
    # board, and that deserves its own line rather than being buried.
    tiers = []
    for s in seasons:
        proj = build(conn, s, ALL_FLAGS, meta, None)
        act = {r["player_id"]: r["pts"] for r in conn.execute("""
            SELECT player_id, SUM(fantasy_points_ppr) pts, COUNT(*) g
            FROM weekly_stats WHERE season=? AND season_type='REG'
            GROUP BY player_id HAVING g>=6""", (s + 1,))}
        prv = {r["player_id"]: r["pts"] for r in conn.execute("""
            SELECT player_id, SUM(fantasy_points_ppr) pts, COUNT(*) g
            FROM weekly_stats WHERE season=? AND season_type='REG'
            GROUP BY player_id HAVING g>=6""", (s,))}
        sc = sorted([(p, act[p["id"]], prv[p["id"]]) for p in proj
                     if p["id"] in act and p["id"] in prv],
                    key=lambda x: -x[0]["adjusted"])
        tiers.append(sc)
    print(f"\n  {'BY DRAFT TIER':<26} {'OUR MAE':>9} {'DO-NOTHING':>12} {'EDGE':>7}")
    for n, label in ((40, "top 40 (rounds 1-4)"), (100, "top 100"), (None, "everyone")):
        mm, dd = [], []
        for sc in tiers:
            cut = sc[:n] if n else sc
            mm += [abs(p["adjusted"] - a) for p, a, _ in cut]
            dd += [abs(pv - a) for _, a, pv in cut]
        if mm:
            m, d = statistics.mean(mm), statistics.mean(dd)
            print(f"  {label:<26} {m:>9.1f} {d:>12.1f} {d-m:>+7.1f}")
    print("\n  The top tier is the one that matters: nobody agonises over the")
    print("  three hundredth best player, and a model that is average overall")
    print("  but sharp at the top is exactly the right shape.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int)
    ap.add_argument("--position")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--pool", default="relevant", choices=["all", "relevant", "drafted"])
    ap.add_argument("--format", default="ppr", choices=["ppr", "half", "standard"],
                    help="scoring format for display and CSV")
    ap.add_argument("--explain", help="print the full chain for one player")
    ap.add_argument("--sim", action="store_true", help="show ranges, not point estimates")
    ap.add_argument("--scorecard", help="score seasons, e.g. 2022,2023")
    ap.add_argument("--snapshot", action="store_true",
                    help="freeze today's projection, with and without news, "
                         "so the news layer can be scored after the season")
    ap.add_argument("--label", help="name for the snapshot, default today")
    ap.add_argument("--score-log", help="score a saved snapshot by its label")
    ap.add_argument("--force", action="store_true",
                    help="publish even if the roster check has not passed")
    ap.add_argument("--publish", action="store_true",
                    help="write projections into the db for the site to read")
    ap.add_argument("--backtest", help="same, terse")
    ap.add_argument("--ablate", action="store_true")
    ap.add_argument("--horizon", default="season", choices=["season", "weekly"],
                    help="season: only season-ending beat reports apply. "
                         "weekly: every role signal applies.")
    ap.add_argument("--enable", default="",
                    help="switch a disabled factor back on, e.g. --enable scheme,age")
    ap.add_argument("--market-weight", type=float,
                    help="blend toward ADP, 0-1. Default 0: it measured worse.")
    ap.add_argument("--csv")
    args = ap.parse_args()

    if args.market_weight is not None:
        global MARKET_WEIGHT
        MARKET_WEIGHT = args.market_weight

    for k in [x.strip() for x in args.enable.split(",") if x.strip()]:
        if k in ALL_FLAGS:
            ALL_FLAGS[k] = True
        else:
            sys.exit(f"  unknown factor '{k}'. Known: {', '.join(ALL_FLAGS)}")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    meta = load_meta(conn, args.horizon)

    if args.score_log:
        score_log(conn, args.score_log)
        return

    if args.publish:
        if not args.season:
            sys.exit("  pass --season")
        rules = {"all": None,
                 "relevant": {"adp": True, "depth": True, "snaps": True,
                              "news": True, "rookie": True},
                 "drafted": {"adp": True, "depth": False, "snaps": False,
                             "news": False, "rookie": False}}[args.pool]
        write_projections(conn, args.season, meta, rules, force=args.force)
        return

    if args.snapshot:
        if not args.season:
            sys.exit("  pass --season")
        snapshot(conn, args.season, meta, args.label)
        return

    if args.scorecard:
        scorecard(conn, [int(s) for s in args.scorecard.split(",")], meta)
        return

    if args.ablate or args.backtest:
        seasons = [int(s) for s in (args.backtest or "2022,2023").split(",")]
        # Honour --pool. Scoring a narrow pool against a broad one is not a
        # comparison, and a small pool makes the mean absolute error jump
        # around on a handful of players.
        rules = {"all": None,
                 "relevant": {"adp": True, "depth": True, "snaps": True,
                              "news": True, "rookie": True},
                 "drafted": {"adp": True, "depth": False, "snaps": False,
                             "news": False, "rookie": False}}[args.pool]
        variants = [("full model", ALL_FLAGS)]
        if args.ablate:
            variants += [(f"without {k}", {**ALL_FLAGS, k: False}) for k in ALL_FLAGS]
        print(f"\n  {'variant':<24} {'MAE':>8} {'vs naive':>10}")
        for label, flags in variants:
            maes, naives = [], []
            for s in seasons:
                proj = build(conn, s, flags, meta, rules)
                actual = {r["player_id"]: r["pts"] for r in conn.execute("""
                    SELECT player_id, SUM(fantasy_points_ppr) pts, COUNT(*) g
                    FROM weekly_stats WHERE season=? AND season_type='REG'
                    GROUP BY player_id HAVING g>=6""", (s + 1,))}
                prev = {r["player_id"]: r["pts"] for r in conn.execute("""
                    SELECT player_id, SUM(fantasy_points_ppr) pts, COUNT(*) g
                    FROM weekly_stats WHERE season=? AND season_type='REG'
                    GROUP BY player_id HAVING g>=6""", (s,))}
                pr = [(p, actual[p["id"]]) for p in proj if p["id"] in actual and p["id"] in prev]
                if pr:
                    maes.append(statistics.mean(abs(p["adjusted"] - a) for p, a in pr))
                    naives.append(statistics.mean(abs(prev[p["id"]] - a) for p, a in pr))
            if maes:
                print(f"  {label:<24} {statistics.mean(maes):>8.1f} "
                      f"{statistics.mean(naives)-statistics.mean(maes):>+10.1f}")
        print("\n  Positive 'vs naive' is good. In the ablation a factor whose")
        print("  removal improves the number is doing harm and should come out.")
        return

    if not args.season:
        sys.exit("  pass --season")

    rules = {"all": None,
             "relevant": {"adp": True, "depth": True, "snaps": True, "news": True, "rookie": True},
             "drafted": {"adp": True, "depth": False, "snaps": False, "news": False, "rookie": False}}[args.pool]
    rows = build(conn, args.season, ALL_FLAGS, meta, rules)

    if args.explain:
        hit = [r for r in rows if args.explain.lower() in r["name"].lower()]
        if not hit:
            sys.exit(f"  no player matching '{args.explain}'")
        r = hit[0]
        print(f"\n  {r['name']}  {r['pos']} {r['team']}"
              f"{'   ADP ' + str(r['adp']) if r['adp'] else ''}\n")
        for label, delta, running in r["trace"]:
            if label.startswith("baseline"):
                print(f"    {label:<44} {running:>8.1f}")
            else:
                print(f"    {label:<44} {delta:>+8.1f}  -> {running:>7.1f}")
        print(f"    {'':<44} {'':>8}     {'-'*7}")
        print(f"    {'PROJECTED PPR':<44} {'':>8}     {r['ppr']:>7.1f}")
        s = simulate(r)
        print(f"\n    adjusted for expected games                  "
              f"{r['adjusted']:>7.1f}")
        print(f"    expected games (4-season durability)         "
              f"{r['avail'] * GAMES:>7.1f}")
        print(f"\n    range   p10 {s['p10']:.0f}   p25 {s['p25']:.0f}   "
              f"median {s['med']:.0f}   p75 {s['p75']:.0f}   p90 {s['p90']:.0f}")
        print(f"    volume  {r['tgt']:.0f} targets, {r['car']:.0f} carries")
        return

    if args.position:
        rows = [r for r in rows if (r["pos"] or "").upper() == args.position.upper()]
    fmt = args.format
    for r in rows:
        r["shown"] = r["fmt"][fmt]
    rows.sort(key=lambda r: r["shown"], reverse=True)

    if args.sim:
        pos_pool = collections.defaultdict(list)
        for r in rows:
            pos_pool[r["pos"]].append(r)
        print(f"\n  {'':<3} {'PLAYER':<22} {'POS':<4} {'FLOOR':>6} {'PROJ':>7} "
              f"{'CEIL':>6} {'TOP-12':>7} {'BUST':>6}")
        for i, r in enumerate(rows[:args.top], 1):
            s = simulate(r)
            peers = sorted((simulate(x, 400)["med"] for x in pos_pool[r["pos"]]), reverse=True)
            cutoff = peers[min(11, len(peers) - 1)]
            top12 = sum(1 for v in s["vals"] if v >= cutoff) / len(s["vals"])
            bust = sum(1 for v in s["vals"] if v < r["ppr"] * 0.6) / len(s["vals"])
            print(f"  {i:<3} {r['name'][:22]:<22} {(r['pos'] or ''):<4} "
                  f"{s['p10']:>6.0f} {r['ppr']:>7.0f} {s['p90']:>6.0f} "
                  f"{top12*100:>6.0f}% {bust*100:>5.0f}%")
        print("\n  Floor is the 10th percentile, ceiling the 90th. Bust is landing")
        print("  under 60 percent of the projection. Ranges assume position-level")
        print("  variance and injury risk, both measured rather than assumed.")
        return

    head = {"ppr": "PPR", "half": "HALF", "standard": "STD"}[fmt]
    print(f"\n  {'':<3} {'PLAYER':<22} {'POS':<4} {'TM':<4} {'REC':>5} {'RECYD':>7} "
          f"{'RUYD':>7} {head:>7}  NOTE")
    for i, r in enumerate(rows[:args.top], 1):
        print(f"  {i:<3} {r['name'][:22]:<22} {(r['pos'] or ''):<4} {(r['team'] or ''):<4} "
              f"{r['rec']:>5.0f} {r['recyd']:>7.0f} {r['ruyd']:>7.0f} {r['shown']:>7.1f}"
              f"  {r['note']}")
    counts = collections.Counter(r["pos"] for r in rows)
    print(f"\n  {len(rows)} players  " + "  ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print("  --explain \"Name\" for the arithmetic, --sim for ranges, "
          "--scorecard for our error rate.")


if __name__ == "__main__":
    main()
