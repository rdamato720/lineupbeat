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
# Quarterback is winner-take-all: one man plays and the rest hold a
# clipboard, so a QB2 genuinely appears in two or three games.
#
# Every other position is share-based. A WR2 and a WR3 both dress and play
# all seventeen; they simply see fewer targets, and SLOT_PPG already says so.
# Capping their games as well charged them twice for the same thing and was
# the whole reason receiver ordering sat at 0.78 while everything else was
# near 0.90.
#
# The small reductions at RB3 and below are healthy scratches, which are
# real but nothing like a backup quarterback's situation.
SLOT_GAMES = {
    "QB": {1: 15.8, 2: 2.5, 3: 0.5, 4: 0.2},
    "RB": {1: 17.0, 2: 16.0, 3: 13.0, 4: 9.0},
    "WR": {1: 17.0, 2: 17.0, 3: 16.0, 4: 12.0},
    "TE": {1: 17.0, 2: 16.0, 3: 12.0, 4: 8.0},
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
# How much of a pass-catcher's projection comes from his ROLE rather than
# his production.
#
# Weighted opportunity rating -- 1.5 x target share plus 0.7 x air yards
# share -- is more stable year to year than points are, for anyone catching
# passes. Measured 2024 -> 2025:
#
#            points/game   target share   wopr
#   WR          0.76           0.83        0.83
#   TE          0.74           0.81        0.85
#   RB          0.82           0.75        0.75
#
# And it predicts better. Mean error in points per game against 2025:
#
#          ppg alone   wopr alone   blend
#   WR       2.82         2.68       2.61
#   TE       2.22         1.99       2.04
#   RB       2.77         3.58       2.97
#
# So receivers take half, tight ends take most, and running backs take none
# -- carries already carry the signal there and target share actively
# misleads. This is the one place the model looks at role rather than output,
# and it is worth it because a share of a passing game is a more durable fact
# about a player than the points that came out of it.
WOPR_WEIGHT = {"WR": 0.50, "TE": 0.70, "RB": 0.0, "QB": 0.0}

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
# What a reported status costs, in the ADJUSTED column only. The full-season
# number is a best case by design and never sees any of this.
#
# And "out" in August is not "out for the year". A player carrying a
# preseason designation misses camp and maybe some early weeks; every
# published board still projects him for most of a season, because he will
# probably be back. Zeroing him said his season was over on the strength of
# a practice report in July.
#
# Injured reserve and retirement do end a season. A game-status designation
# does not.
STATUS_GAMES = {
    "INJURY_RESERVE": 0.0,
    "RETIRED": 0.0,
    "OUT": 0.80,
    "SUSPENSION": 0.65,
    "NON_FOOTBALL_INJURY": 0.75,
    # Everything else -- questionable, doubtful, day to day -- is about a
    # week and is deliberately absent.
}


def contested(conn, season):
    """Where the market itself does not know, keyed by normalised name.

    A depth chart has no "probably" in it. Minnesota lists one quarterback
    first and so does Buffalo, and only one of those jobs is settled --
    Minnesota signed Kyler Murray and Cleveland has Shedeur Sanders, and
    nobody knows how either room shakes out.

    Published boards carry that uncertainty in a form nothing else does. A
    settled starter sits within a few points across every board; a contested
    job scatters by a hundred and fifty. That spread is the industry saying
    it does not know, and a projection that commits to the depth chart in
    those cases is more confident than anybody who follows the sport.

    So: how far apart are the boards on this player, as a fraction of what
    they think he is worth. Returns nothing at all when fewer than two
    boards are stored, because one board cannot disagree with itself.
    """
    out = {}
    try:
        rows = conn.execute("""SELECT name_key,
            MIN(points) lo, MAX(points) hi, AVG(points) mid, COUNT(*) n
            FROM board_points WHERE season=? GROUP BY name_key
            HAVING n >= 2""", (season,)).fetchall()
    except sqlite3.OperationalError:
        return {}
    for r in rows:
        if not r["mid"] or r["mid"] < 20:
            continue
        out[r["name_key"]] = (r["hi"] - r["lo"]) / r["mid"]
    return out


def wopr_curve(conn, season):
    """Fit points per game against weighted opportunity, per position.

    Fitted rather than assumed: the conversion differs by position and by
    season as passing volume moves, and a made-up constant would poison a
    signal that measurably works.
    """
    out = {}
    for pos in ("WR", "TE", "RB", "QB"):
        rows = conn.execute("""SELECT AVG(wopr) w, AVG(fantasy_points_ppr) p,
            COUNT(*) g FROM weekly_stats WHERE season=? AND position=?
            AND season_type='REG' GROUP BY player_id
            HAVING g >= 8 AND w > 0""", (season, pos)).fetchall()
        pts = [(r["w"], r["p"]) for r in rows if r["w"] and r["p"]]
        if len(pts) < 12:
            continue
        mx = statistics.mean(x for x, _ in pts)
        my = statistics.mean(y for _, y in pts)
        den = sum((x - mx) ** 2 for x, _ in pts)
        if den:
            b = sum((x - mx) * (y - my) for x, y in pts) / den
            out[pos] = (my - b * mx, b)
    return out


def snap_share(conn, season):
    """Weighted offensive snap share, which is how much a player is actually
    on the field.

    The depth chart says a receiver is third and says nothing about whether
    that means sixty snaps a game or twelve. In an offence that runs three
    wide most downs a WR3 plays nearly every snap; a second running back in a
    committee might be at 25% while nominally one rung higher. Slot is a
    proxy for this and snap share is the thing itself.

    Weighted the same way as everything else, so a player who took over a
    role late in the season is read as having that role.
    """
    out = {}
    try:
        for w, s in zip(YEAR_WEIGHTS, (season, season - 1, season - 2)):
            for r in conn.execute("""SELECT player_id, AVG(offense_pct) pct,
                                     COUNT(*) g FROM snap_counts
                                     WHERE season=? AND player_id IS NOT NULL
                                     AND offense_pct IS NOT NULL
                                     GROUP BY player_id""", (s,)):
                if not r["pct"]:
                    continue
                adj = w * ((min(17, r["g"]) / 17.0) ** SAMPLE_POWER)
                a, b = out.get(r["player_id"], (0.0, 0.0))
                out[r["player_id"]] = (a + r["pct"] * adj, b + adj)
    except sqlite3.OperationalError:
        return {}
    return {k: (v / w) for k, (v, w) in out.items() if w > 0.05}


def injury_weeks(conn, season):
    """How many weeks a player was ruled out, from official reports.

    A player listed Out for eleven weeks is a different fact from one ruled
    out on a Friday, and only the first belongs in a season projection. The
    ESPN status field cannot tell them apart -- it carries today's
    designation and no history.
    """
    out = {}
    try:
        for r in conn.execute("""SELECT gsis_id, COUNT(*) n FROM injuries
                                 WHERE season=? AND report_status='Out'
                                 GROUP BY gsis_id""", (season,)):
            out[r["gsis_id"]] = r["n"]
    except sqlite3.OperationalError:
        pass
    return out


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
    snaps = snap_share(conn, season)
    curve = wopr_curve(conn, season)
    # Official reports beat a guess at availability. Games actually missed is
    # a better number than a position-wide norm, for anyone who has any.
    missed = injury_weeks(conn, season)
    # How uncertain the market is about each player, if we have boards stored.
    unsure = contested(conn, season + 1)
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
        games_seen = 0
        line = collections.defaultdict(float)
        for w, s in zip(YEAR_WEIGHTS, (season, season - 1, season - 2)):
            g = conn.execute("""SELECT COUNT(*) g, AVG(fantasy_points_ppr) p,
                                AVG(receptions) rec, AVG(receiving_yards) recyd,
                                AVG(rushing_yards) ruyd, AVG(passing_yards) pyd,
                                AVG(wopr) wopr
                                FROM weekly_stats WHERE player_id=? AND season=?
                                AND season_type='REG'""", (pid, s)).fetchone()
            if not g or not g["g"]:
                continue
            games_seen += g["g"]
            adj = w * ((min(17, g["g"]) / 17.0) ** SAMPLE_POWER)
            pts += (g["p"] or 0) * adj
            played += min(17, g["g"]) * adj
            wsum += adj
            for k in ("rec", "recyd", "ruyd", "pyd", "wopr"):
                line[k] += (g[k] or 0) * adj
        # Enough of a record to project from?
        #
        # This used to test the weight, which quietly broke when SAMPLE_POWER
        # went from 1.5 to 2.5: an eight-game season weighs 0.114 at 2.5 and
        # 0.29 at 1.5, so a threshold tuned for one silently dropped sixty
        # three players under the other. Cam Skattebo played eight games at
        # sixteen points and was thrown out for a flat slot rate.
        #
        # Count games instead. Whether we have a record is a fact about how
        # much football a player has played, and it should not move when a
        # weighting constant does.
        if games_seen < 4:
            continue
        ppg = pts / wsum
        trace = [("points per game, three seasons", ppg)]
        # Blend in what his ROLE usually scores, where role predicts better
        # than production does.
        wq = WOPR_WEIGHT.get(pos, 0.0)
        if wq and pos in curve and line.get("wopr"):
            a, b = curve[pos]
            from_role = a + b * (line["wopr"] / wsum)
            if from_role > 0:
                ppg = ppg * (1 - wq) + from_role * wq
                trace.append((f"blended {wq:.0%} toward role "
                              f"(wopr says {from_role:.1f})", ppg))
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
                # Demote only when the chart says he is NOT the starter.
                #
                # SLOT_PPG[QB][1] is 15.7 -- what an AVERAGE starting
                # quarterback scores. Josh Allen scores 23. Treating that gap
                # as something to correct pulled every elite player down
                # toward the average of his own slot: Allen went from about
                # 390 to 296, and the whole top of the board compressed into
                # a single indistinguishable band.
                #
                # A slot-1 listing confirms a player's role; it says nothing
                # about how good he is at it. Only a slot BELOW first is
                # evidence against his own production, which was the James
                # Conner case this rule was built for.
                demotable = moved or (slot and slot > 1)
                if promoting or demotable:
                    pull = 0.75 if moved else ROLE_PULL
                    ppg = ppg * (1 - pull) + target * pull
                    trace.append((f"role: slot {slot} typically scores "
                                  f"{target:.1f}" + (" (moved)" if moved else ""),
                                  ppg))
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
        # Weeks officially ruled out last season, if we have them. This is
        # the same question the availability average answers, measured rather
        # than inferred from how many games appear in the box scores -- a
        # player can dress and not play, and the box score cannot tell.
        wk = missed.get(pid)
        if wk:
            reported = max(0.0, (17 - wk) / 17.0)
            rate = rate * 0.5 + reported * 0.5
        exp_g = GAMES * (rate * 0.5 + AVAIL_NORM.get(pos, 0.80) * 0.5)

        # Hedge where the market is split.
        #
        # A spread of 150 points across boards on the same player is not
        # noise, it is a job nobody has called yet. Committing to our depth
        # chart there projects a starter's season for a man who may take
        # forty percent of the snaps. Pull toward the middle of his slot and
        # the one below it, in proportion to how unsure everyone is.
        spread = unsure.get(key(info["player_name"]))
        if spread and spread > 0.35 and slot and pos in SLOT_PPG:
            tiers = SLOT_PPG[pos]
            here = tiers.get(slot)
            below = tiers.get(slot + 1, (here or 0) * 0.4)
            if here:
                # 0.35 spread barely moves; 1.0 or more is a coin flip.
                hedge = min(0.55, (spread - 0.35) * 0.7)
                ppg = ppg * (1 - hedge) + ((here + below) / 2) * hedge
                trace.append((f"market split ({spread:.0%} spread): hedged "
                              f"{hedge:.0%} toward the slot below", ppg))
                note = (note + " / contested").strip(" /")

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
            trace.append((f"reported {st.lower().replace('_',' ')}", ppg))

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
        # Snap share overrides the slot where we have it. A quarterback who
        # was on the field for 8% of snaps is a backup whatever the chart
        # says; one at 90% is the starter whatever his listed order.
        sh = snaps.get(pid)
        if sh is not None and pos == "QB":
            # offense_pct is a FRACTION (0-1), not a percentage. Dividing by
            # a hundred gave a starting quarterback 0.0095, clamped to the
            # 0.05 floor, and projected Josh Allen for 0.85 games and
            # seventeen points. Every quarterback in the league came out at
            # 15-17, which read as catastrophic disagreement with every
            # board rather than as one stray divisor.
            #
            # Guard rather than assume: if a future import ever stores it as
            # 0-100, this still behaves.
            frac = sh / 100.0 if sh > 1.5 else sh
            full_g = GAMES * min(1.0, max(0.05, frac))

        scale = ppg / max(base, 0.01)
        seen.add(pid)
        rows.append({
            "id": pid, "name": (meta or {}).get("name") or info["player_name"],
            "pos": pos, "team": (meta or {}).get("team") or norm_team(info["team"]),
            "ppr": ppg * full_g, "adjusted": ppg * exp_g, "games": exp_g,
            "rec": line["rec"] * full_g * scale,
            "recyd": line["recyd"] * full_g * scale,
            "ruyd": line["ruyd"] * full_g * scale,
            "note": note, "sleeper": rid, "trace": trace,
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
            "trace": [(f"no NFL history; slot {slot} scores", ppg)],
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
    ap.add_argument("--explain", help="every step behind one player's number")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    ros = roster()
    if not ros:
        print("  no roster file; teams and depth slots unavailable")
    rows = build(conn, args.season, ros, crosswalk(conn))
    if not rows:
        sys.exit("  nothing projected. Is weekly_stats imported?")

    if args.explain:
        hit = [r for r in rows if args.explain.lower() in r["name"].lower()]
        if not hit:
            sys.exit(f"  no player matching '{args.explain}'")
        r = hit[0]
        print(f"\n  {r['name']}  {r['pos']} {r['team']}\n")
        for label, val in r.get("trace", []):
            print(f"    {label:<44} {val:>8.2f} per game")
        print(f"    {'-' * 54}")
        print(f"    {'full season':<44} {r['ppr']:>8.1f}")
        print(f"    {'expected games':<44} {r['games']:>8.1f}")
        print(f"    {'adjusted':<44} {r['adjusted']:>8.1f}")
        if r["note"]:
            print(f"\n    note: {r['note']}")
        return

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
