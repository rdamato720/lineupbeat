#!/usr/bin/env python3
"""Evidence changes the world; the engine recomputes it from the baseline.

    python3 scripts/engine.py --add-event PLAYER_UNAVAILABLE \
        --player "Jahmyr Gibbs" --availability 0.0 \
        --reason "simulated season-ending injury" --source "test"

    python3 scripts/engine.py --run --source-cutoff-at 2026-08-08T10:00:00Z
    python3 scripts/engine.py --supersede evt-abc123 --reason "cleared"
    python3 scripts/engine.py --events
    python3 scripts/engine.py --explain "David Montgomery"

WHY IT RECOMPUTES RATHER THAN CHAINS

The obvious design takes yesterday's state and applies today's event to it.
It works until something is reversed.

A back is ruled out on Monday and his 275 carries spread across three
teammates. On Friday he is cleared. Reversing that means unpicking which
teammate got which carry, at which point in a chain of intervening events,
and putting each one back. Every arithmetic error compounds and none of them
is visible.

So nothing is ever reversed. The baseline is immutable, events carry an
active flag, and every run recomputes from the baseline plus whatever is
active now. Clearing a player means marking one event inactive; the next run
does not include it, and the team returns to where it was because that is
what the arithmetic produces rather than what somebody tried to undo.

It also makes a batch natural. Six injuries on a Sunday morning are six
active events and one run, not six snapshots.

WHAT IS FROZEN WITH A RUN

Everything needed to reproduce it: the model inputs it computed, the team
environment it validated against, the events that caused it, the input
changes each produced, and the reconciliation factors applied at the end.
An old run can be explained without rerunning anything.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

NON_TEAMS = {"FA", "FA/UNK", "UNK", ""}

# Who can absorb what. Carries go to the backfield: a tight end does not pick
# up three percent of one because a back is hurt, and a quarterback's runs
# are his scheme rather than a vacancy anybody fills.
ELIGIBLE = {
    "carry_share":    {"RB", "FB"},
    "rush_td_share":  {"RB", "FB"},
    "target_share":   {"RB", "FB", "WR", "TE"},
    "rec_td_share":   {"RB", "FB", "WR", "TE"},
    "pass_att_share": {"QB"},
}

# Appetite and ceiling, per opportunity type.
#
# One number for all of them was wrong: a back can plausibly take sixty
# percent of a team's carries and twelve percent of its targets, and a slot
# receiver the reverse.
DEFAULTS = {
    "RB": {"carry": (1.0, 0.72), "target": (0.7, 0.18),
           "rush_td": (1.0, 0.75), "rec_td": (0.6, 0.15)},
    "FB": {"carry": (0.3, 0.12), "target": (0.3, 0.06),
           "rush_td": (0.5, 0.20), "rec_td": (0.3, 0.06)},
    "WR": {"carry": (0.05, 0.03), "target": (1.0, 0.32),
           "rush_td": (0.05, 0.03), "rec_td": (1.0, 0.35)},
    "TE": {"carry": (0.02, 0.01), "target": (0.8, 0.28),
           "rush_td": (0.05, 0.03), "rec_td": (0.9, 0.30)},
    "QB": {"carry": (1.0, 0.25), "target": (0.0, 0.0),
           "rush_td": (1.0, 0.35), "rec_td": (0.0, 0.0)},
}
FIELD_KIND = {"carry_share": "carry", "target_share": "target",
              "rush_td_share": "rush_td", "rec_td_share": "rec_td"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_events (
  event_id TEXT PRIMARY KEY,
  season INTEGER, event_type TEXT, player_id TEXT, player TEXT, team TEXT,
  payload_json TEXT, reason TEXT, source TEXT, source_tier TEXT,
  observed_at TEXT, created_at TEXT,
  active INTEGER DEFAULT 1,
  -- When this event started being true, and when it stopped.
  --
  -- "active" answers what is true NOW. A run asks what was true AS OF its
  -- cutoff, and those differ the moment anything is superseded: a player
  -- ruled out at 9:55 and cleared at 10:05 was still out at 10:00, but the
  -- 9:55 event had already been deactivated, so a run at 10:00 saw no
  -- events at all and returned him to baseline.
  --
  -- The interval answers the historical question. active stays as a
  -- convenience for "what is true now" and no longer decides anything.
  effective_start_at TEXT, effective_end_at TEXT,
  supersedes_event_id TEXT, superseded_by TEXT, status TEXT,
  -- Why an event stopped being true, kept rather than printed. Months later
  -- the question is which Friday source cleared Monday's injury, and a
  -- boolean cannot answer it.
  retired_at TEXT, retired_reason TEXT, retired_source TEXT
);

-- The frozen state a run computed. Not the input to the next run -- that is
-- always the baseline plus active events -- but the record that makes an old
-- run explainable and reproducible.
CREATE TABLE IF NOT EXISTS run_model_inputs (
  run_id TEXT, player_id TEXT, player TEXT, team TEXT, position TEXT,
  availability REAL, carry_share REAL, target_share REAL,
  rush_td_share REAL, rec_td_share REAL, pass_att_share REAL,
  catch_rate REAL, yards_per_target REAL, yards_per_carry REAL,
  yards_per_attempt REAL, completion_rate REAL, int_rate REAL,
  pass_td_rate REAL, fumble_rate REAL, is_residual INTEGER,
  -- The controls that actually drove this run's redistribution. A default
  -- changed in October must not make an August run inexplicable.
  confidence TEXT,
  carry_realloc_weight REAL, carry_headroom REAL,
  target_realloc_weight REAL, target_headroom REAL,
  rush_td_realloc_weight REAL, rush_td_headroom REAL,
  rec_td_realloc_weight REAL, rec_td_headroom REAL,
  PRIMARY KEY (run_id, player_id)
);

CREATE TABLE IF NOT EXISTS run_events (
  run_id TEXT, event_id TEXT, action TEXT, applied INTEGER,
  PRIMARY KEY (run_id, event_id)
);

CREATE TABLE IF NOT EXISTS run_input_changes (
  run_id TEXT, player_id TEXT, player TEXT, field TEXT,
  previous REAL, current REAL, event_id TEXT,
  PRIMARY KEY (run_id, player_id, field, event_id)
);

-- What reconciliation had to do, and why. Moving targets does not move catch
-- rate with them, so a team can finish short of its own completions and the
-- reconciler scales it back. A reader asking why a receiver's line moved
-- deserves both halves: the share changed, then the team was scaled by
-- 1.056 to match the passing environment.
CREATE TABLE IF NOT EXISTS run_reconciliation_adjustments (
  run_id TEXT, team TEXT, field TEXT,
  pre_reconcile REAL, target REAL, factor REAL, reason TEXT,
  PRIMARY KEY (run_id, team, field)
);

-- What reconciliation did to each player, not just to each team.
--
-- A team factor explained the result while everyone was scaled by the same
-- number. Under confidence weighting they are not: a high-confidence line
-- absorbs almost nothing and a depth player absorbs a lot, so printing the
-- team factor beside a player would describe a multiplication that never
-- happened to him.
CREATE TABLE IF NOT EXISTS run_player_reconciliation (
  run_id TEXT, team TEXT, player_id TEXT, player TEXT, field TEXT,
  pre_value REAL, adjustment REAL, post_value REAL,
  confidence TEXT, flexibility REAL, team_gap REAL,
  PRIMARY KEY (run_id, player_id, field)
);
"""


def now():
    """One canonical representation: UTC, ISO.

    SQLite compares timestamps as text, so 09:00-04:00 sorts before
    12:00+00:00 despite being an hour later. Every stored timestamp is
    normalised on the way in, which is what makes a cutoff comparison mean
    what it says.
    """
    return datetime.now(timezone.utc).isoformat()


def utc(stamp: str | None) -> str | None:
    """Parse anything ISO-ish and return it as UTC."""
    if not stamp:
        return None
    s = stamp.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        raise SystemExit(f"  not a timestamp: {stamp!r}")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def migrate(conn):
    """Add columns an older database will not have.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists,
    so the engine works perfectly against a fresh database and then fails on
    the real one with "no such column". This is idempotent and runs on every
    start.
    """
    wanted = {
        "model_events": [
            ("active", "INTEGER DEFAULT 1"),
            ("effective_start_at", "TEXT"),
            ("effective_end_at", "TEXT"),
            ("supersedes_event_id", "TEXT"),
            ("superseded_by", "TEXT"),
            ("retired_at", "TEXT"),
            ("retired_reason", "TEXT"),
            ("retired_source", "TEXT"),
        ],
        "model_inputs": [
            ("confidence", "TEXT"),
            ("carry_realloc_weight", "REAL"), ("carry_headroom", "REAL"),
            ("target_realloc_weight", "REAL"), ("target_headroom", "REAL"),
            ("rush_td_realloc_weight", "REAL"), ("rush_td_headroom", "REAL"),
            ("rec_td_realloc_weight", "REAL"), ("rec_td_headroom", "REAL"),
        ],
        "projection_runs": [
            ("created_at", "TEXT"),
        ],
        # An installation from the previous engine has this table without
        # the reconciliation controls, and CREATE TABLE IF NOT EXISTS will
        # not touch it. The insert below names its columns, so a missing one
        # fails loudly here rather than silently misaligning values.
        "run_model_inputs": [
            ("confidence", "TEXT"),
            ("carry_realloc_weight", "REAL"), ("carry_headroom", "REAL"),
            ("target_realloc_weight", "REAL"), ("target_headroom", "REAL"),
            ("rush_td_realloc_weight", "REAL"), ("rush_td_headroom", "REAL"),
            ("rec_td_realloc_weight", "REAL"), ("rec_td_headroom", "REAL"),
        ],
        "run_projections": [
            ("is_residual", "INTEGER DEFAULT 0"),
            ("confidence", "TEXT"),
        ],
    }
    for table, cols in wanted.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue                       # table does not exist yet
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    # An older row has no interval. Its start is when it was observed, and
    # it ended when whatever replaced it began -- or it has not ended.
    have = {r[1] for r in conn.execute("PRAGMA table_info(model_events)")}
    if "effective_start_at" in have:
        conn.execute("""UPDATE model_events
                        SET effective_start_at = observed_at
                        WHERE effective_start_at IS NULL""")
        conn.execute("""UPDATE model_events SET effective_end_at = COALESCE(
                          (SELECT observed_at FROM model_events s
                           WHERE s.event_id = model_events.superseded_by),
                          retired_at)
                        WHERE effective_end_at IS NULL AND active = 0""")
    conn.commit()


def baseline_inputs(conn, season):
    """Offense v1.0 as derived. Never modified by an event."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM model_inputs WHERE season=?", (season,))]


def environments(conn, season):
    env = {}
    for r in conn.execute("SELECT * FROM team_environment WHERE season=?",
                          (season,)):
        d = dict(r)
        d["targets"] = d["pass_att"] * (1 - d["non_target_rate"])
        d["rec_td"] = d["pass_td"]
        env[d["team"]] = d
    return env


def active_events(conn, season, cutoff=None):
    """What was in effect as of the cutoff, not what is active now.

    The cutoff was stored on the run and ignored by the computation, so a
    run could claim its evidence stopped at 10:00 while including something
    seen at 10:05. sourceCutoffAt has to mean what it says: this is the
    information in the rankings, and nothing later.

    Ordering is fully deterministic down to the event id, so the same
    evidence always produces the same allocation.
    """
    if cutoff:
        # An event applies if it had begun and had not yet ended. Filtering
        # on `active` instead answers a different question and gets the
        # supersession case exactly backwards.
        return [dict(r) for r in conn.execute(
            """SELECT * FROM model_events
               WHERE season=?
                 AND COALESCE(effective_start_at, observed_at) <= ?
                 AND (effective_end_at IS NULL OR effective_end_at > ?)
               ORDER BY COALESCE(effective_start_at, observed_at),
                        created_at, event_id""",
            (season, cutoff, cutoff))]
    return [dict(r) for r in conn.execute(
        """SELECT * FROM model_events WHERE season=? AND active=1
           ORDER BY COALESCE(effective_start_at, observed_at),
                    created_at, event_id""", (season,))]


def weights_for(p, field):
    kind = FIELD_KIND.get(field)
    pos = (p.get("position") or "").upper()
    d = DEFAULTS.get(pos, {}).get(kind, (0.3, 0.15))
    w = p.get(f"{kind}_realloc_weight")
    h = p.get(f"{kind}_headroom")
    return (w if w is not None else d[0], h if h is not None else d[1])


def redistribute(freed, field, candidates, leaving_id, moves):
    ok = ELIGIBLE.get(field, set())
    pool = [c for c in candidates
            if c["player_id"] != leaving_id and not c.get("is_residual")
            and c["availability"] > 0
            and (c.get("position") or "").upper() in ok
            and weights_for(c, field)[0] > 0]
    remaining = freed
    if not pool:
        return remaining
    for _ in range(12):
        if remaining <= 1e-9:
            break
        live = []
        for c in pool:
            w, h = weights_for(c, field)
            held = (c[field] or 0) + moves.get((c["player_id"], field), 0)
            if held < h:
                live.append((c, w, h, held))
        if not live:
            break
        # Weighted by the role a player already holds: the obvious recipient
        # is the man already doing some of the job.
        tot = sum(w * (0.05 + held) for _, w, _, held in live)
        if tot <= 0:
            break
        placed = 0.0
        for c, w, h, held in live:
            want = remaining * (w * (0.05 + held)) / tot
            take = min(want, max(0.0, h - held))
            if take > 0:
                moves[(c["player_id"], field)] = \
                    moves.get((c["player_id"], field), 0) + take
                placed += take
        remaining -= placed
        if placed <= 1e-12:
            break
    return max(0.0, remaining)


def compute_state(conn, season, cutoff=None):
    """Baseline plus every active event, recomputed from scratch.

    Deterministic: the same events in the same order always produce the same
    state, which is what makes an old run reproducible and a reversal exact.
    """
    inputs = {p["player_id"]: dict(p) for p in baseline_inputs(conn, season)}
    for p in inputs.values():
        p.setdefault("is_residual", 0)
    events = active_events(conn, season, cutoff)
    changes, touched = [], set()

    for ev in events:
        target = inputs.get(ev["player_id"])
        if not target:
            continue
        payload = json.loads(ev["payload_json"] or "{}")
        team = target["team"]
        if team in NON_TEAMS:
            continue
        touched.add(team)
        teammates = [p for p in inputs.values() if p["team"] == team]

        old = target["availability"]
        new = float(payload.get("availability", old))
        if abs(new - old) < 1e-9:
            continue
        changes.append({"player_id": target["player_id"],
                        "player": target["player"], "field": "availability",
                        "previous": old, "current": new,
                        "event_id": ev["event_id"]})
        target["availability"] = new

        moves, leftover = {}, {}
        for field in ("carry_share", "target_share", "rush_td_share",
                      "rec_td_share"):
            freed = (target[field] or 0) * (old - new)
            if freed <= 1e-9:
                continue
            rest = redistribute(freed, field, teammates,
                                target["player_id"], moves)
            if rest > 1e-9:
                leftover[field] = rest

        for (pid, field), add in moves.items():
            p = inputs[pid]
            before = p[field] or 0
            p[field] = before + add
            changes.append({"player_id": pid, "player": p["player"],
                            "field": field, "previous": before,
                            "current": p[field], "event_id": ev["event_id"]})

        if leftover:
            rid = f"{team.lower()}-realloc-residual"
            res = inputs.get(rid)
            if not res:
                res = {k: (0.0 if isinstance(v, (int, float)) else None)
                       for k, v in target.items()}
                res.update({"player_id": rid, "player": f"{team} unallocated",
                            "team": team, "position": "RB",
                            "availability": 1.0, "is_residual": 1,
                            "season": season})
                inputs[rid] = res
            for field, amount in leftover.items():
                before = res[field] or 0
                res[field] = before + amount
                changes.append({"player_id": rid, "player": res["player"],
                                "field": field, "previous": before,
                                "current": res[field],
                                "event_id": ev["event_id"]})

    return inputs, changes, touched, events


def validate(stats, inputs, env, season, teams=None):
    """Both sides of the offence, each against the stored environment.

    Checking the receivers against the sum of the QB rows means an error
    that moves both moves neither. Cleveland's QB rows summed to 522.5
    attempts while the team was modelled at 550 and the receivers were
    reconciled to 550: consistent with each other, wrong against the model,
    and invisible to a check that compares them to one another.

    So the environment is authoritative and everything is measured against
    it. The QB side and the receiving side are checked separately, which is
    what makes a disagreement between them detectable at all.
    """
    problems = []
    check = teams or env.keys()
    for team in check:
        e = env.get(team)
        if not e or team in NON_TEAMS:
            continue
        ids = [pid for pid, p in inputs.items() if p["team"] == team]
        pos = {pid: (inputs[pid].get("position") or "").upper() for pid in ids}
        s = lambda f, want=None: sum(
            stats[i][f] for i in ids
            if i in stats and (want is None or pos[i] in want))

        # The targetable share comes from the environment, not a constant.
        targetable = e["pass_att"] * (1 - e["non_target_rate"])
        CATCHERS = {"RB", "FB", "WR", "TE"}

        for label, got, want in (
                # the quarterbacks
                ("QB pass attempts", s("pass_att", {"QB"}), e["pass_att"]),
                ("QB completions",   s("completions", {"QB"}), e["completions"]),
                ("QB passing yards", s("pass_yds", {"QB"}), e["pass_yds"]),
                ("QB passing TDs",   s("pass_td", {"QB"}), e["pass_td"]),
                # the people they throw to
                ("player targets",   s("targets", CATCHERS), targetable),
                ("receptions",       s("rec", CATCHERS), e["completions"]),
                ("receiving yards",  s("rec_yds", CATCHERS), e["pass_yds"]),
                ("receiving TDs",    s("rec_td", CATCHERS), e["pass_td"]),
                # the ground
                ("rush attempts",    s("rush_att"), e["rush_att"]),
                ("rush TDs",         s("rush_td"), e["rush_td"])):
            if abs(got - want) > max(0.6, abs(want) * 0.004):
                problems.append(f"{team} {label}: {got:.1f} vs {want:.1f}")
    return problems


# How much reconciliation a player absorbs, by how sure we are about him.
#
# A uniform factor was wrong. Offense v1.0 was built by auditing the players
# who matter and letting depth absorb the residual, and a flat scaling
# discards exactly that judgment: an injury to a fourth receiver should not
# move Amon-Ra St. Brown's line by the same proportion as a rookie nobody
# has an opinion about.
#
# Flexibility is the inverse of confidence. High confidence barely moves.
FLEXIBILITY = {
    "high": 0.15, "medium-high": 0.40, "medium": 1.00,
    "low-medium": 2.00, "low": 3.50,
}


def confidence_of(p, stats, team_total):
    """A stored confidence, or one inferred from how big a role he holds.

    The workbook does not carry a confidence column, so until one exists it
    is inferred: the players carrying most of an offence are the ones that
    were audited individually, and the tail is what absorbed the residual.
    """
    c = (p.get("confidence") or "").lower()
    if c in FLEXIBILITY:
        return c
    share = 0.0
    if team_total > 0:
        s = stats.get(p["player_id"])
        if s:
            share = (s.get("targets", 0) + s.get("rush_att", 0)) / team_total
    if p.get("is_residual"):
        return "low"
    if share >= 0.18:
        return "high"
    if share >= 0.10:
        return "medium-high"
    if share >= 0.04:
        return "medium"
    if share >= 0.01:
        return "low-medium"
    return "low"


def reconcile(stats, inputs, env, teams):
    """Close each team's gap, weighted by confidence.

    The correction is distributed in proportion to a player's flexibility
    times what he currently holds, so the same total adjustment lands mostly
    on the players nobody had a strong opinion about. High-confidence lines
    move a fraction as far.
    """
    adjustments, per_player = [], []
    for team in teams:
        e = env.get(team)
        if not e or team in NON_TEAMS:
            continue
        ids = [pid for pid, p in inputs.items() if p["team"] == team]
        team_total = sum(stats[i].get("targets", 0) + stats[i].get("rush_att", 0)
                         for i in ids if i in stats)
        conf = {i: confidence_of(inputs[i], stats, team_total)
                for i in ids if i in stats}
        flex = {i: FLEXIBILITY[conf[i]] for i in ids if i in stats}
        for i in ids:
            if i in stats:
                inputs[i]["confidence"] = conf[i]

        for field, target, why in (
                ("rec", e["completions"], "receptions to QB completions"),
                ("rec_yds", e["pass_yds"], "receiving yards to passing yards"),
                ("rec_td", e["pass_td"], "receiving TDs to passing TDs"),
                ("rush_att", e["rush_att"], "carries to the team budget"),
                ("rush_td", e["rush_td"], "rushing TDs to the team budget")):
            have = sum(stats[i][field] for i in ids if i in stats)
            if have <= 1e-9 or abs(have - target) < 1e-9:
                continue
            gap = target - have
            pre_player = {i: stats[i][field] for i in ids if i in stats}
            # weight = what he holds, times how willing he is to move
            weights = {i: stats[i][field] * flex[i] for i in ids if i in stats}
            wsum = sum(weights.values())
            if wsum <= 1e-12:
                k = target / have
                for i in ids:
                    if i in stats:
                        stats[i][field] *= k
            else:
                for i in ids:
                    if i in stats:
                        stats[i][field] += gap * weights[i] / wsum
                        if stats[i][field] < 0:
                            stats[i][field] = 0.0
                # a negative clamp can reopen the gap; close it proportionally
                again = sum(stats[i][field] for i in ids if i in stats)
                if again > 1e-9 and abs(again - target) > 1e-9:
                    k2 = target / again
                    for i in ids:
                        if i in stats:
                            stats[i][field] *= k2
            if field == "rush_att":
                # Yards follow the carries that produced them, at the
                # player's own rate.
                #
                # This used to read a "_ra0" key the caller was expected to
                # set, and a rewrite elsewhere dropped that line -- so
                # carries moved and yards did not, silently changing every
                # affected player's yards per carry. A function that depends
                # on a caller remembering to stash a value will eventually
                # be called by somebody who does not.
                #
                # pre_player already holds exactly this, captured above.
                for i in ids:
                    if i in stats and pre_player.get(i):
                        stats[i]["rush_yds"] *= stats[i][field] / pre_player[i]
            adjustments.append({"team": team, "field": field, "pre": have,
                                "target": target, "factor": target / have,
                                "reason": why})
            for i in ids:
                if i in stats and abs(stats[i][field] - pre_player[i]) > 1e-9:
                    per_player.append({
                        "team": team, "player_id": i,
                        "player": inputs[i]["player"], "field": field,
                        "pre": pre_player[i],
                        "adjustment": stats[i][field] - pre_player[i],
                        "post": stats[i][field],
                        "confidence": conf[i], "flexibility": flex[i],
                        "gap": gap})
    return adjustments, per_player


def freeze(conn, run_id, inputs, env, changes, events, applied,
           adjustments, per_player):
    """Write every immutable record for this run, exactly once.

    One insertion path per table. The previous version inserted run_events
    and run_input_changes in cmd_run and then again here, which was harmless
    only because those inserts were INSERT OR REPLACE. With plain inserts --
    which is what "immutable" has to mean -- the second write is a primary
    key violation, and it should be.

    Called after reconciliation and before the validation verdict, so a
    failed candidate is exactly as inspectable as a published one.
    """
    for p in inputs.values():
        conn.execute(
            """INSERT INTO run_model_inputs
               (run_id, player_id, player, team, position, availability,
                carry_share, target_share, rush_td_share, rec_td_share,
                pass_att_share, catch_rate, yards_per_target,
                yards_per_carry, yards_per_attempt, completion_rate,
                int_rate, pass_td_rate, fumble_rate, is_residual, confidence,
                carry_realloc_weight, carry_headroom,
                target_realloc_weight, target_headroom,
                rush_td_realloc_weight, rush_td_headroom,
                rec_td_realloc_weight, rec_td_headroom)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                       ?,?,?,?,?,?,?,?)""",
            (run_id, p["player_id"], p["player"], p["team"], p.get("position"),
             p.get("availability"), p.get("carry_share"), p.get("target_share"),
             p.get("rush_td_share"), p.get("rec_td_share"),
             p.get("pass_att_share"), p.get("catch_rate"),
             p.get("yards_per_target"), p.get("yards_per_carry"),
             p.get("yards_per_attempt"), p.get("completion_rate"),
             p.get("int_rate"), p.get("pass_td_rate"), p.get("fumble_rate"),
             1 if p.get("is_residual") else 0,
             p.get("confidence"),
             *[weights_for(p, f)[i]
               for f in ("carry_share", "target_share",
                         "rush_td_share", "rec_td_share")
               for i in (0, 1)]))
    for team, e in env.items():
        conn.execute(
            """INSERT INTO run_team_environment
               (run_id, team, pass_att, completions, pass_yds, pass_td,
                non_target_rate, rush_att, rush_yds, rush_td, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, team, e["pass_att"], e["completions"], e["pass_yds"],
             e["pass_td"], e["non_target_rate"], e["rush_att"],
             e.get("rush_yds"), e["rush_td"], e.get("source")))
    for c in changes:
        conn.execute(
            "INSERT INTO run_input_changes VALUES (?,?,?,?,?,?,?)",
            (run_id, c["player_id"], c["player"], c["field"],
             c["previous"], c["current"], c["event_id"]))
    for ev in events:
        conn.execute(
            "INSERT INTO run_events VALUES (?,?,?,?)",
            (run_id, ev["event_id"], ev["event_type"],
             1 if ev["event_id"] in applied else 0))
    for a in adjustments:
        conn.execute(
            "INSERT INTO run_reconciliation_adjustments VALUES (?,?,?,?,?,?,?)",
            (run_id, a["team"], a["field"], a["pre"], a["target"],
             a["factor"], a["reason"]))
    for a in per_player:
        conn.execute(
            "INSERT INTO run_player_reconciliation VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, a["team"], a["player_id"], a["player"], a["field"],
             a["pre"], a["adjustment"], a["post"], a["confidence"],
             a["flexibility"], a["gap"]))


def cmd_run(conn, args):
    import derive_inputs as di
    season = args.season
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    # Through utc(), not straight from the argument. SQLite compares these
    # as text, so 09:00-04:00 sorts before 12:00+00:00 while actually being
    # an hour later -- an event after the cutoff would be silently included.
    cutoff = utc(args.source_cutoff_at) if args.source_cutoff_at else now()

    # The candidate exists before anything is computed, so a run that fails
    # still carries its events and its input changes. The pending JSON file
    # could not do that: a failed run left it behind and the next event
    # attached itself to the wrong thing.
    conn.execute(
        """INSERT INTO projection_runs
           (run_id, season, created_at, status, source_cutoff_at, note)
           VALUES (?,?,?,?,?,?)""",
        (run_id, season, now(), "candidate", cutoff, args.note or ""))
    conn.commit()
    print(f"\n  candidate {run_id}")

    inputs, changes, touched, events = compute_state(conn, season, cutoff)
    applied = {c["event_id"] for c in changes}
    env = environments(conn, season)

    print(f"  {len(events)} active events, {len(applied)} applied, "
          f"{len(changes)} input changes")
    print(f"  teams affected: {sorted(touched) or 'none'}")

    stats = {}
    for p in inputs.values():
        e = env.get(p["team"])
        if e:
            stats[p["player_id"]] = di.rebuild(p, e)

    # Free agents, and only free agents.
    #
    # This was written for them and implemented as "anything the rebuild did
    # not produce", which is a different rule. A reallocation residual --
    # "NYJ unallocated", created when an injury freed more opportunity than
    # anybody's headroom could absorb -- is also missing from the recomputed
    # state once that injury is retired, and the broad version copied it
    # forward. The opportunity outlived the event that created it, and the
    # team still reconciled, so nothing complained.
    #
    # So: carry a player only if he has no team environment to be rebuilt
    # from. Never carry a residual. And if a player on a real NFL team
    # cannot be rebuilt, say so rather than reaching for last week's number.
    prev = conn.execute("SELECT run_id FROM published_snapshot WHERE season=?",
                        (season,)).fetchone()
    if prev:
        carried, orphans = 0, []
        for r in conn.execute(
                "SELECT * FROM run_projections WHERE run_id=?",
                (prev["run_id"],)):
            pid = r["player_id"]
            if pid in stats:
                continue
            team = (r["team"] or "").strip().upper()
            if r["is_residual"]:
                # It exists because an event said so. If the event is gone,
                # so is it.
                continue
            if team in NON_TEAMS:
                stats[pid] = {k: (r[k] or 0.0) for k in (
                    "pass_att", "completions", "pass_yds", "pass_td", "ints",
                    "targets", "rec", "rec_yds", "rec_td",
                    "rush_att", "rush_yds", "rush_td", "fumbles")}
                inputs[pid] = {"player_id": pid, "player": r["player"],
                               "team": r["team"], "position": r["position"],
                               "availability": 1.0, "is_residual": 0}
                carried += 1
            else:
                orphans.append(f"{r['player']} ({team})")
        if carried:
            print(f"  {carried} free agents carried forward unchanged")
        if orphans:
            conn.execute("UPDATE projection_runs SET status='failed', note=? "
                         "WHERE run_id=?",
                         (f"{len(orphans)} rostered players could not be "
                          f"rebuilt: {orphans[:3]}", run_id))
            conn.commit()
            print(f"\n  FAILED: {len(orphans)} players on real teams could "
                  f"not be rebuilt from model inputs")
            for o in orphans[:5]:
                print(f"    {o}")
            print(f"\n  Copying their previous line forward would publish a "
                  f"number nothing produced.")
            return 1

    adjustments, per_player = reconcile(stats, inputs, env, touched)
    if adjustments:
        print(f"\n  reconciliation")
        for a in adjustments[:8]:
            print(f"    {a['team']} {a['field']:<9} {a['pre']:>8.1f} "
                  f"-> {a['target']:>8.1f}  x{a['factor']:.4f}")

    # Freeze first, decide second. A candidate that fails is the one you
    # most want to inspect.
    freeze(conn, run_id, inputs, env, changes, events, applied,
           adjustments, per_player)
    conn.commit()

    problems = validate(stats, inputs, env, season)
    if problems:
        conn.execute("UPDATE projection_runs SET status=?, note=? "
                     "WHERE run_id=?",
                     ("failed", "; ".join(problems[:4]), run_id))
        conn.commit()
        print(f"\n  FAILED validation, {len(problems)} problems")
        for p in problems[:6]:
            print(f"    {p}")
        print(f"\n  Nothing published. Inspect it with:")
        print(f"    python3 scripts/engine.py --explain PLAYER "
              f"--run-id {run_id}")
        return 1

    # Write the run's stat rows, then move the pointer. In that order: the
    # rows must exist before anything can be told to read them.
    for pid, s in stats.items():
        p = inputs[pid]
        # No points column: they are computed from the raw line by the
        # view layer, so there is nowhere for a stored figure to disagree.
        conn.execute(
            """INSERT INTO run_projections
               (run_id, season, player_id, player, team, position,
                pass_att, completions, pass_yds, pass_td, ints,
                targets, rec, rec_yds, rec_td,
                rush_att, rush_yds, rush_td, fumbles,
                rank_pos, confidence, reason_codes, is_residual)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, season, pid, p["player"], p["team"], p.get("position"),
             s["pass_att"], s["completions"], s["pass_yds"], s["pass_td"],
             s["ints"], s["targets"], s["rec"], s["rec_yds"], s["rec_td"],
             s["rush_att"], s["rush_yds"], s["rush_td"], s["fumbles"],
             None, p.get("confidence"), None,
             1 if p.get("is_residual") else 0))

    conn.execute("""INSERT INTO published_snapshot VALUES (?,?,?,?)
                    ON CONFLICT(season) DO UPDATE SET
                      run_id=excluded.run_id,
                      published_at=excluded.published_at,
                      previous_run_id=excluded.previous_run_id""",
                 (season, run_id, now(), prev["run_id"] if prev else None))
    conn.execute("UPDATE projection_runs SET status='published', "
                 "completed_at=? WHERE run_id=?", (now(), run_id))
    conn.commit()
    print(f"\n  published {run_id}, {len(stats)} players")
    return 0


def cmd_add_event(conn, args):
    pid = None
    row = conn.execute(
        "SELECT player_id, player, team FROM model_inputs "
        "WHERE season=? AND lower(player)=lower(?)",
        (args.season, args.player)).fetchone()
    if not row:
        sys.exit(f"  no player called {args.player!r} in the baseline")
    pid, name, team = row["player_id"], row["player"], row["team"]

    # An accidental availability of -1 or 5 must never reach the engine: it
    # would free negative opportunity, or more than a player holds, and the
    # reconciler would dutifully spread the nonsense across a real team.
    payload = {}
    if args.availability is not None:
        if not (0.0 <= args.availability <= 1.0):
            sys.exit(f"  availability must be between 0 and 1, "
                     f"got {args.availability}")
        payload["availability"] = args.availability
    if not payload:
        sys.exit("  the event carries no change; pass --availability")
    if args.team and args.team.upper() != (team or "").upper():
        sys.exit(f"  {name} is on {team}, not {args.team.upper()}")
    if args.observed_at:
        utc(args.observed_at)          # raises if it is not a timestamp
    # Two active availability events for the same player is not a state the
    # engine should resolve by ordering. Whichever came second is the current
    # belief, and the first should be retired explicitly rather than left to
    # win or lose on a sort.
    if "availability" in payload and not args.supersedes:
        clash = conn.execute(
            """SELECT * FROM model_events WHERE season=? AND player_id=?
               AND active=1 AND json_extract(payload_json,'$.availability')
               IS NOT NULL""", (args.season, pid)).fetchall()
        if clash and not args.force:
            print(f"  {name} already has an active availability event:")
            for x in clash:
                print(f"    {x['event_id']}  {x['observed_at'][:19]}  "
                      f"{x['reason']}")
            sys.exit("  pass --supersedes EVENT_ID to replace it, or --force "
                     "if both are genuinely true")

    if args.supersedes:
        prior = conn.execute("SELECT * FROM model_events WHERE event_id=?",
                             (args.supersedes,)).fetchone()
        if not prior:
            sys.exit(f"  cannot supersede {args.supersedes}: no such event")
        if prior["player_id"] != pid and not args.force:
            sys.exit(f"  {args.supersedes} is about {prior['player']}, not "
                     f"{name}. Pass --force if that is deliberate.")
    # Canonical UTC on the way in, so every comparison downstream is
    # chronological rather than lexical.
    observed = utc(args.observed_at) if args.observed_at else now()
    eid = f"evt-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO model_events
           (event_id, season, event_type, player_id, player, team,
            payload_json, reason, source, source_tier, observed_at,
            created_at, active, effective_start_at, effective_end_at,
            supersedes_event_id, superseded_by, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,NULL,?,NULL,'active')""",
        (eid, args.season, args.add_event, pid, name, team,
         json.dumps(payload), args.reason or "", args.source or "manual",
         args.source_tier or "A", observed, now(), observed,
         args.supersedes))
    if args.supersedes:
        # The old event stops being true when the new one starts, not when
        # somebody typed the command. A run with a cutoff between the two
        # must still see the old one.
        conn.execute("""UPDATE model_events
                        SET active=0, superseded_by=?, status='superseded',
                            effective_end_at=?
                        WHERE event_id=?""",
                     (eid, observed, args.supersedes))
    conn.commit()
    print(f"  {eid}  {name} ({team})  {args.add_event}  {payload}")
    if args.supersedes:
        print(f"  supersedes {args.supersedes}, which is now inactive")
    print(f"  nothing recomputed yet: run --run")


def cmd_supersede(conn, args):
    """Retire an event. The next run recomputes without it.

    This is how a reversal works. Nothing is unpicked: the event stops being
    true, and the arithmetic that produced the redistribution simply does
    not happen next time.

    The reason is stored rather than printed. In February the question will
    be which source cleared Monday's injury and when, and a boolean cannot
    answer it.
    """
    row = conn.execute("SELECT * FROM model_events WHERE event_id=?",
                       (args.supersede,)).fetchone()
    if not row:
        sys.exit(f"  no event {args.supersede}")
    if not args.reason:
        sys.exit("  --reason is required: an event that stops being true "
                 "needs to say why")
    # Ends when the clearance happened, not when it was recorded. Pass
    # --observed-at to say when that was.
    ended = utc(args.observed_at) if args.observed_at else now()
    conn.execute("""UPDATE model_events SET active=0, status='retired',
                    retired_at=?, retired_reason=?, retired_source=?,
                    effective_end_at=?
                    WHERE event_id=?""",
                 (now(), args.reason, args.source or "manual", ended,
                  args.supersede))
    conn.commit()
    print(f"  retired {args.supersede}: {row['player']} {row['event_type']}")
    print(f"  {args.reason}  [{args.source or 'manual'}]")
    print(f"  run --run to recompute without it")


def cmd_events(conn, args):
    rows = conn.execute(
        "SELECT * FROM model_events WHERE season=? ORDER BY created_at DESC",
        (args.season,)).fetchall()
    if not rows:
        print("  no events")
        return
    print(f"\n  {len(rows)} events\n")
    for r in rows:
        mark = "active  " if r["active"] else "inactive"
        p = json.loads(r["payload_json"] or "{}")
        print(f"    {mark} {r['event_id']}  {r['player'][:22]:<22} "
              f"{r['team']:<4} {r['event_type']:<22} {p}")
        if r["superseded_by"]:
            print(f"             superseded by {r['superseded_by']}")


def cmd_explain(conn, args):
    """Why a player's line is what it is, from the frozen record."""
    if args.run_id:
        rid = args.run_id
        run = conn.execute("SELECT * FROM projection_runs WHERE run_id=?",
                           (rid,)).fetchone()
        if not run:
            sys.exit(f"  no run {rid}")
    else:
        # The pointer, not the newest published run. After a rollback those
        # are different, and the question "why does this player look like
        # this" is always about what people are being served.
        ptr = conn.execute(
            "SELECT run_id FROM published_snapshot WHERE season=?",
            (args.season,)).fetchone()
        if not ptr:
            sys.exit("  nothing is published")
        rid = ptr["run_id"]
        run = conn.execute("SELECT * FROM projection_runs WHERE run_id=?",
                           (rid,)).fetchone()

    row = conn.execute(
        "SELECT * FROM run_projections WHERE run_id=? AND lower(player)=lower(?)",
        (rid, args.explain)).fetchone()
    inp = conn.execute(
        "SELECT * FROM run_model_inputs WHERE run_id=? AND lower(player)=lower(?)",
        (rid, args.explain)).fetchone()
    if not row and not inp:
        sys.exit(f"  {args.explain} not in {rid}")
    who = row or inp
    print(f"\n  {who['player']} ({who['team']}) in {rid}  [{run['status']}]")
    if run["source_cutoff_at"]:
        print(f"  evidence through {run['source_cutoff_at'][:19]}")
    if row:
        # Computed, not read: there is no stored points column, which is
        # what stops a stale figure disagreeing with the stat line beside it.
        import projection_view as pv
        print(f"  {pv.score(row, 'ppr'):.1f} PPR   "
              f"{pv.score(row, 'half'):.1f} half   "
              f"{pv.score(row, 'standard'):.1f} standard")
    else:
        print(f"  this run was not published, so there is no stat line; the")
        print(f"  inputs and adjustments below are why it failed")
    print()

    ch = conn.execute(
        """SELECT c.*, e.reason, e.source, e.event_type
           FROM run_input_changes c LEFT JOIN model_events e
             ON e.event_id = c.event_id
           WHERE c.run_id=? AND lower(c.player)=lower(?)""",
        (rid, args.explain)).fetchall()
    if ch:
        print("  what changed about him")
        for c in ch:
            print(f"    {c['field']:<16} {c['previous']:.4f} -> "
                  f"{c['current']:.4f}")
            print(f"      because: {c['reason'] or c['event_type']}")
    else:
        print("  no input changes: he is at his baseline")

    # who, not row: a failed candidate has no stat line and that is exactly
    # the case this is for.
    pr = conn.execute(
        """SELECT * FROM run_player_reconciliation
           WHERE run_id=? AND lower(player)=lower(?)""",
        (rid, args.explain)).fetchall()
    if pr:
        print(f"\n  what reconciliation did to him specifically")
        for a in pr:
            print(f"    {a['field']:<9} {a['pre_value']:>8.2f} "
                  f"{a['adjustment']:+8.2f} -> {a['post_value']:>8.2f}")
            print(f"      confidence {a['confidence']}, flexibility "
                  f"{a['flexibility']:.2f}; the team needed "
                  f"{a['team_gap']:+.1f}")
    adj = conn.execute(
        """SELECT * FROM run_reconciliation_adjustments
           WHERE run_id=? AND team=?""", (rid, who["team"])).fetchall()
    if adj:
        print(f"\n  and what {who['team']} as a whole needed")
        for a in adj:
            print(f"    {a['field']:<9} {a['pre_reconcile']:>9.1f} -> "
                  f"{a['target']:>9.1f}  {a['reason']}")
        print(f"\n  The team figure is what had to close, not a factor")
        print(f"  applied to him: under confidence weighting a protected")
        print(f"  line absorbs almost none of it.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--add-event")
    ap.add_argument("--player")
    ap.add_argument("--availability", type=float)
    ap.add_argument("--reason")
    ap.add_argument("--source")
    ap.add_argument("--source-tier")
    ap.add_argument("--observed-at")
    ap.add_argument("--supersedes")
    ap.add_argument("--supersede")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--source-cutoff-at")
    ap.add_argument("--note")
    ap.add_argument("--events", action="store_true")
    ap.add_argument("--explain")
    ap.add_argument("--run-id", help="explain this run, passed or failed")
    ap.add_argument("--team", help="asserted team, checked against the roster")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    migrate(conn)

    if args.add_event:
        return cmd_add_event(conn, args)
    if args.supersede:
        return cmd_supersede(conn, args)
    if args.events:
        return cmd_events(conn, args)
    if args.explain:
        return cmd_explain(conn, args)
    if args.run:
        return cmd_run(conn, args)
    ap.print_help()


if __name__ == "__main__":
    sys.exit(main() or 0)
