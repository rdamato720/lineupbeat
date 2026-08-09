#!/usr/bin/env python3
"""The engine regression suite. Required before any deployment.

    python3 scripts/test_engine.py
    python3 scripts/test_engine.py --only g
    python3 scripts/test_engine.py --keep      # leave the db for inspection

Each test rebuilds from the baseline so they cannot contaminate each other,
and each asserts rather than prints: a test that only shows numbers is a
demonstration, and a demonstration passes whatever happens.

  A  sequential     two events accumulate from the baseline
  B  reversal       retiring an event returns every field, and moves no
                    other team
  C  batch          two teams, one run, one live pointer, all ten
                    constraints across all 32 teams
  D  cutoff         sourceCutoffAt excludes later evidence, including an
                    Eastern timestamp against a UTC cutoff
  E  failure        a rejected candidate freezes its state and stays
                    inspectable, and never publishes
  F  migration      an older database gains its columns without losing rows
  G  supersession   what was true AS OF the cutoff, not what is true now
  H  empty          zero events produce zero movement and no reconciliation
  I  residual       a residual created by an event disappears with it, while
                    the baseline residual survives
  J  yards          reconciling carries carries the yards, so a player keeps
                    his own rate

Ten tests, 71 assertions. Several exist because a specific bug reached a
package: J because a rewrite dropped a value the reconciler depended on, I
because a fix for free agents was written as "anything missing", G because
"active now" and "active then" are different questions.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Set by main(). The suite creates events, publishes runs and restores from
# a snapshot, so pointing it at the live database means a crash halfway
# through leaves synthetic events in the real rankings. It takes --db so the
# release wrapper can hand it a disposable copy.
DB = ROOT / "beatwire.db"
SNAP = ROOT / ".test_baseline.db"

FAILURES = []


def check(label, ok, detail=""):
    print(f"    {'pass' if ok else 'FAIL':<5} {label}" +
          (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def run(*args, expect=0):
    # Every engine invocation gets the same database the suite is using.
    # Without this the scripts default to beatwire.db and the isolation is
    # only half there.
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / args[0]),
                        "--db", str(DB)] + list(args[1:]),
                       capture_output=True, text=True)
    if expect is not None and r.returncode != expect:
        print(r.stdout[-2000:])
        print(r.stderr[-1000:])
        raise SystemExit(f"  {args[0]} exited {r.returncode}, expected {expect}")
    return r.stdout


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def restore():
    """Every test starts from the same published baseline."""
    shutil.copy2(SNAP, DB)


def team_totals(c, run_id, team):
    r = c.execute("""SELECT SUM(rush_att) ra, SUM(rush_td) rt, SUM(rec) rc,
                            SUM(rec_yds) ry, SUM(targets) tg
                     FROM run_projections WHERE run_id=? AND team=?""",
                  (run_id, team)).fetchone()
    return dict(r)


def live(c):
    """What the site would read: the pointer, not the newest run.

    Asking projection_runs for the most recent published row answers a
    different question, and gives the wrong answer the moment a rollback
    points at an older one.
    """
    r = c.execute("SELECT run_id FROM published_snapshot WHERE season=2026"
                  ).fetchone()
    return r["run_id"] if r else None


def player(c, run_id, name):
    r = c.execute("""SELECT * FROM run_projections
                     WHERE run_id=? AND lower(player)=lower(?)""",
                  (run_id, name)).fetchone()
    if not r:
        return None
    d = dict(r)
    # There is no stored points column: the whole point of the view layer is
    # that points are computed from the raw line, so the test computes them
    # the same way rather than reading a number nobody stores.
    import projection_view as pv
    for f in ("ppr", "half", "standard"):
        d[f] = pv.score(d, f)
    return d


def full_team(c, run_id, team):
    """Every raw field for every player, for an exact before/after."""
    out = {}
    for r in c.execute("SELECT * FROM run_projections WHERE run_id=? AND team=?",
                       (run_id, team)):
        d = dict(r)
        out[d["player_id"]] = {k: d[k] for k in (
            "pass_att", "completions", "pass_yds", "pass_td", "ints",
            "targets", "rec", "rec_yds", "rec_td",
            "rush_att", "rush_yds", "rush_td", "fumbles")}
    return out


def iso(minutes_ago=0):
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes_ago)).isoformat()


# --------------------------------------------------------------------------

def test_a():
    print("\n  A. SEQUENTIAL: two events, both applied from the baseline\n")
    restore()
    c = conn()
    base_run = live(c)
    base_pacheco = player(c, base_run, "Braelon Allen")
    c.close()

    run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
        "--player", "Breece Hall", "--availability", "0.0",
        "--reason", "test A first event", "--source", "suite",
        "--observed-at", iso(30))
    run("engine.py", "--run", "--source-cutoff-at", iso(0))

    c = conn()
    r1 = live(c)
    after_one = player(c, r1, "Braelon Allen")
    # Not "player X gained": whether a named backup gains depends on his
    # headroom, and a back already at his ceiling correctly sends the
    # overflow to the residual instead. What must be true is that the
    # opportunity went somewhere and the team still adds up.
    # Everyone except the injured player, including a residual bucket that
    # did not exist in the base run. An inner join on player_id misses that
    # case entirely, and the residual is exactly where the overflow goes
    # when the backup is already at his ceiling.
    def rest_carries(rid):
        return c.execute("""SELECT COALESCE(SUM(rush_att),0) s
            FROM run_projections WHERE run_id=? AND team=?
              AND lower(player) != lower(?)""",
            (rid, "NYJ", "Breece Hall")).fetchone()["s"]
    before_rest, after_rest = rest_carries(base_run), rest_carries(r1)
    check("the freed carries landed on somebody",
          after_rest > before_rest + 1,
          f"{before_rest:.1f} -> {after_rest:.1f} across the rest of the team")
    e = c.execute("SELECT * FROM run_team_environment WHERE run_id=? AND team=?",
                  (r1, "NYJ")).fetchone()
    t1 = team_totals(c, r1, "NYJ")
    check("and the team still adds up", abs(t1["ra"] - e["rush_att"]) < 0.5,
          f"{t1['ra']:.1f} vs {e['rush_att']:.1f}")
    c.close()

    # second event, a different Detroit player
    run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
        "--player", "Braelon Allen", "--availability", "0.5",
        "--reason", "test A second event", "--source", "suite",
        "--observed-at", iso(10))
    run("engine.py", "--run", "--source-cutoff-at", iso(0))

    c = conn()
    r2 = live(c)
    check("a new run published", r2 != r1)
    ev = c.execute("SELECT COUNT(*) n FROM run_events WHERE run_id=? "
                   "AND applied=1", (r2,)).fetchone()["n"]
    check("both events applied to the second run", ev == 2, f"{ev} applied")

    after_two = player(c, r2, "Braelon Allen")
    # The second event halves this player. If the run had started from the
    # baseline rather than from baseline-plus-both-events, the first event's
    # redistribution would be absent and the team would not reconcile.
    check("both events are reflected, not just the second",
          abs(after_two["rush_att"] - base_pacheco["rush_att"]) > 0.5,
          f"baseline {base_pacheco['rush_att']:.0f}, "
          f"after both {after_two['rush_att']:.0f}")
    check("Gibbs still out in the second run",
          player(c, r2, "Breece Hall")["rush_att"] < 0.01)
    t = team_totals(c, r2, "NYJ")
    e = c.execute("SELECT * FROM run_team_environment WHERE run_id=? AND team=?",
                  (r2, "NYJ")).fetchone()
    check("DET still reconciles", abs(t["ra"] - e["rush_att"]) < 1,
          f"{t['ra']:.1f} vs {e['rush_att']:.1f}")
    c.close()


def test_b():
    print("\n  B. REVERSAL: retire the event, the team returns\n")
    restore()
    c = conn()
    base_run = live(c)
    before = team_totals(c, base_run, "NYJ")
    before_full = full_team(c, base_run, "NYJ")
    c.close()

    out = run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
              "--player", "Breece Hall", "--availability", "0.0",
              "--reason", "test B", "--source", "suite",
              "--observed-at", iso(30))
    eid = out.split()[0]
    run("engine.py", "--run", "--source-cutoff-at", iso(0))

    c = conn()
    r1 = live(c)
    check("Gibbs zeroed", player(c, r1, "Breece Hall")["ppr"] < 0.01)
    c.close()

    run("engine.py", "--supersede", eid,
        "--reason", "cleared, returned to full participation",
        "--source", "suite")
    run("engine.py", "--run", "--source-cutoff-at", iso(0))

    c = conn()
    r2 = live(c)
    # Every raw field for every player, not two point totals. Baseline plus
    # zero active events is deterministic, so this should return to where it
    # started within floating point.
    after_full = full_team(c, r2, "NYJ")
    worst, who = 0.0, ""
    for pid, fields in before_full.items():
        now_ = after_full.get(pid)
        if not now_:
            worst, who = 9e9, f"{pid} vanished"
            break
        for k, v in fields.items():
            d = abs((v or 0) - (now_.get(k) or 0))
            if d > worst:
                worst, who = d, f"{pid}.{k}"
    check("every Jets raw field returns to baseline", worst < 0.01,
          f"worst drift {worst:.2e} at {who}")
    check("no player appeared or disappeared",
          set(before_full) == set(after_full),
          f"{len(before_full)} -> {len(after_full)}")

    # And nothing else in the league moved either. A reversal that quietly
    # re-reconciles thirty-one other teams is not a reversal.
    drift, where = 0.0, ""
    for row in c.execute("""SELECT a.player_id, a.team,
               a.rush_att-b.rush_att da, a.rec-b.rec dr,
               a.rec_yds-b.rec_yds dy, a.targets-b.targets dt
           FROM run_projections a JOIN run_projections b
             ON a.player_id=b.player_id
           WHERE a.run_id=? AND b.run_id=? AND a.team!='NYJ'""",
           (r2, base_run)):
        for k in ("da", "dr", "dy", "dt"):
            if abs(row[k] or 0) > drift:
                drift, where = abs(row[k]), f"{row['team']} {row['player_id']}.{k}"
    check("no other team moved during the reversal", drift < 1e-9,
          f"worst {drift:.2e} at {where}")
    rr = c.execute("SELECT retired_reason, retired_source, retired_at "
                   "FROM model_events WHERE event_id=?", (eid,)).fetchone()
    check("retirement reason persisted", bool(rr and rr["retired_reason"]),
          (rr["retired_reason"] if rr else ""))
    c.close()


def test_c():
    print("\n  C. BATCH: two teams, one run, one snapshot\n")
    restore()
    c = conn()
    base = live(c)
    det_before = team_totals(c, base, "NYJ")
    buf_before = team_totals(c, base, "DAL")
    ctl_before = team_totals(c, base, "PHI")
    c.close()

    cut = iso(0)
    run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
        "--player", "Breece Hall", "--availability", "0.0",
        "--reason", "test C, team one", "--source", "suite",
        "--observed-at", iso(20))
    run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
        "--player", "CeeDee Lamb", "--availability", "0.0",
        "--reason", "test C, team two", "--source", "suite",
        "--observed-at", iso(15))
    run("engine.py", "--run", "--source-cutoff-at", cut)

    c = conn()
    r = live(c)
    n = c.execute("SELECT COUNT(*) n FROM run_events WHERE run_id=? "
                  "AND applied=1", (r,)).fetchone()["n"]
    check("both events on one run", n == 2, f"{n}")
    check("Detroit changed",
          abs(player(c, r, "Breece Hall")["ppr"]) < 0.01)
    check("Buffalo changed",
          abs(player(c, r, "CeeDee Lamb")["ppr"]) < 0.01)
    ctl_after = team_totals(c, r, "PHI")
    check("an untouched team did not move",
          abs(ctl_after["ra"] - ctl_before["ra"]) < 0.01
          and abs(ctl_after["rc"] - ctl_before["rc"]) < 0.01)
    ptr = c.execute("SELECT COUNT(*) n FROM published_snapshot "
                    "WHERE season=?", (2026,)).fetchone()["n"]
    check("exactly one live snapshot", ptr == 1, f"{ptr} pointer rows")
    kept = c.execute("SELECT COUNT(*) n FROM projection_runs "
                     "WHERE status='published'").fetchone()["n"]
    check("earlier runs kept for rollback", kept >= 1, f"{kept} runs retained")

    # Every constraint, computed here rather than trusted from the
    # validator. If production validation is ever weakened by accident this
    # is what notices.
    envs = c.execute("SELECT * FROM run_team_environment WHERE run_id=?",
                     (r,)).fetchall()
    CATCH = ("RB", "FB", "WR", "TE")
    bad = []
    for e in envs:
        if e["team"] in ("FA", "FA/UNK", "UNK", ""):
            continue
        def s(field, positions=None):
            q = f"SELECT COALESCE(SUM({field}),0) v FROM run_projections " \
                f"WHERE run_id=? AND team=?"
            args_ = [r, e["team"]]
            if positions:
                q += " AND position IN (" + ",".join("?" * len(positions)) + ")"
                args_ += list(positions)
            return c.execute(q, args_).fetchone()["v"]

        targetable = e["pass_att"] * (1 - e["non_target_rate"])
        for label, got, want in (
                ("QB attempts",   s("pass_att", ("QB",)),    e["pass_att"]),
                ("QB completions", s("completions", ("QB",)), e["completions"]),
                ("QB pass yards", s("pass_yds", ("QB",)),    e["pass_yds"]),
                ("QB pass TDs",   s("pass_td", ("QB",)),     e["pass_td"]),
                ("targets",       s("targets", CATCH),       targetable),
                ("receptions",    s("rec", CATCH),           e["completions"]),
                ("rec yards",     s("rec_yds", CATCH),       e["pass_yds"]),
                ("rec TDs",       s("rec_td", CATCH),        e["pass_td"]),
                ("rush attempts", s("rush_att"),             e["rush_att"]),
                ("rush TDs",      s("rush_td"),              e["rush_td"])):
            if abs(got - want) > max(0.6, abs(want) * 0.004):
                bad.append(f"{e['team']} {label} {got:.1f}/{want:.1f}")
    check(f"all {len(envs)} teams satisfy all ten constraints",
          not bad, str(bad[:3]))
    c.close()


def test_d():
    print("\n  D. CUTOFF: later evidence is genuinely excluded\n")
    restore()
    early, late = iso(20), iso(5)
    mid, after = iso(12), iso(0)

    run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
        "--player", "Breece Hall", "--availability", "0.0",
        "--reason", "test D early", "--source", "suite",
        "--observed-at", early)
    run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
        "--player", "CeeDee Lamb", "--availability", "0.0",
        "--reason", "test D late", "--source", "suite",
        "--observed-at", late)

    run("engine.py", "--run", "--source-cutoff-at", mid)
    c = conn()
    r1 = live(c)
    n1 = c.execute("SELECT COUNT(*) n FROM run_events WHERE run_id=? "
                   "AND applied=1", (r1,)).fetchone()["n"]
    check("cutoff before the second event applies only the first", n1 == 1,
          f"{n1} applied")
    check("the early event took effect",
          player(c, r1, "Breece Hall")["ppr"] < 0.01)
    check("the late event did not",
          player(c, r1, "CeeDee Lamb")["ppr"] > 1)
    c.close()

    # An Eastern timestamp against a UTC cutoff. Lexically "09:00-04:00"
    # sorts before "12:00+00:00" while actually being an hour later, so this
    # is the case a text comparison gets wrong.
    from datetime import timezone as _tz
    eastern = (datetime.now(timezone.utc) + timedelta(hours=2))\
        .astimezone(_tz(timedelta(hours=-4))).isoformat()
    run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
        "--player", "Braelon Allen", "--availability", "0.0",
        "--reason", "test D eastern", "--source", "suite",
        "--observed-at", eastern, "--force")
    run("engine.py", "--run", "--source-cutoff-at", iso(0))
    c = conn()
    rtz = live(c)
    n = c.execute("SELECT COUNT(*) n FROM run_events WHERE run_id=? "
                  "AND applied=1", (rtz,)).fetchone()["n"]
    check("an Eastern timestamp two hours ahead is excluded by a UTC cutoff",
          n == 2, f"{n} applied, expected the two earlier events only")
    c.close()

    run("engine.py", "--run", "--source-cutoff-at", after)
    c = conn()
    r2 = live(c)
    n2 = c.execute("SELECT COUNT(*) n FROM run_events WHERE run_id=? "
                   "AND applied=1", (r2,)).fetchone()["n"]
    check("a later cutoff applies both", n2 == 2, f"{n2} applied")
    check("the late event now takes effect",
          player(c, r2, "CeeDee Lamb")["ppr"] < 0.01)
    c.close()


def test_e():
    print("\n  E. FAILED CANDIDATE: still fully inspectable\n")
    restore()
    c = conn()
    before = live(c)
    c.close()

    # A break reconciliation cannot paper over.
    #
    # Scaling the rushing budget does not work: the reconciler scales the
    # players to match it and the run passes, which is the reconciler doing
    # its job. Completions are different -- they come from each quarterback's
    # own rate and reconciliation never touches them, so an environment that
    # claims a different number is a genuine contradiction.
    c = conn()
    c.execute("""UPDATE team_environment SET completions = completions + 40
                 WHERE team='NYJ' AND season=2026""")
    c.commit()
    c.close()

    run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
        "--player", "Breece Hall", "--availability", "0.0",
        "--reason", "test E", "--source", "suite", "--observed-at", iso(10))
    out = run("engine.py", "--run", "--source-cutoff-at", iso(0), expect=1)
    check("the candidate failed", "FAILED validation" in out)

    c = conn()
    check("the live pointer did not move", live(c) == before)
    failed = c.execute("""SELECT run_id FROM projection_runs
                          WHERE status='failed' ORDER BY created_at DESC
                          LIMIT 1""").fetchone()
    check("the failure is recorded", failed is not None)
    rid = failed["run_id"] if failed else None
    if rid:
        for table, label in (("run_model_inputs", "model inputs"),
                             ("run_team_environment", "team environment"),
                             ("run_events", "events"),
                             ("run_input_changes", "input changes")):
            n = c.execute(f"SELECT COUNT(*) n FROM {table} WHERE run_id=?",
                          (rid,)).fetchone()["n"]
            check(f"failed run froze its {label}", n > 0, f"{n} rows")
        n = c.execute("SELECT COUNT(*) n FROM run_projections WHERE run_id=?",
                      (rid,)).fetchone()["n"]
        check("but wrote no stat rows", n == 0)
    c.close()

    if rid:
        out = run("engine.py", "--explain", "Braelon Allen", "--run-id", rid)
        check("--explain works on the failed run",
              "Braelon Allen" in out and rid in out)
        check("and says it was not published", "not published" in out
              or "failed" in out)


def test_f():
    print("\n  F. MIGRATION: an older database gains its columns\n")
    restore()
    c = conn()
    # Rebuild model_events without the audit columns, as an older install
    # would have it, and put a row in it.
    c.executescript("""
        DROP TABLE IF EXISTS model_events;
        CREATE TABLE model_events (
          event_id TEXT PRIMARY KEY, season INTEGER, event_type TEXT,
          player_id TEXT, player TEXT, team TEXT, payload_json TEXT,
          reason TEXT, source TEXT, source_tier TEXT, observed_at TEXT,
          created_at TEXT, status TEXT);
        INSERT INTO model_events VALUES
          ('evt-old', 2026, 'PLAYER_UNAVAILABLE', 'x', 'Someone', 'NYJ',
           '{}', 'from before the upgrade', 'old', 'A',
           '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'active');
    """)
    c.commit()
    cols_before = {r[1] for r in c.execute("PRAGMA table_info(model_events)")}
    check("older schema lacks the audit columns",
          "retired_reason" not in cols_before)
    c.close()

    run("engine.py", "--events")

    c = conn()
    cols_after = {r[1] for r in c.execute("PRAGMA table_info(model_events)")}
    for col in ("active", "retired_at", "retired_reason", "retired_source",
                "supersedes_event_id", "superseded_by"):
        check(f"{col} added", col in cols_after)
    row = c.execute("SELECT * FROM model_events WHERE event_id='evt-old'"
                    ).fetchone()
    check("the existing event survived", row is not None
          and row["reason"] == "from before the upgrade")
    c.close()

    # The same for run_model_inputs, which an earlier engine created without
    # the reconciliation controls. CREATE TABLE IF NOT EXISTS will not touch
    # it, and the insert names its columns, so a missing one fails loudly.
    c = conn()
    c.executescript("""
        DROP TABLE IF EXISTS run_model_inputs;
        CREATE TABLE run_model_inputs (
          run_id TEXT, player_id TEXT, player TEXT, team TEXT, position TEXT,
          availability REAL, carry_share REAL, target_share REAL,
          rush_td_share REAL, rec_td_share REAL, pass_att_share REAL,
          catch_rate REAL, yards_per_target REAL, yards_per_carry REAL,
          yards_per_attempt REAL, completion_rate REAL, int_rate REAL,
          pass_td_rate REAL, fumble_rate REAL, is_residual INTEGER,
          PRIMARY KEY (run_id, player_id));
        INSERT INTO run_model_inputs
          (run_id, player_id, player, team, position, availability)
          VALUES ('run-old', 'p-old', 'Older Frozen Row', 'NYJ', 'RB', 1.0);
    """)
    c.commit()
    before = {r[1] for r in c.execute("PRAGMA table_info(run_model_inputs)")}
    check("older run_model_inputs lacks the controls",
          "confidence" not in before)
    c.close()

    run("engine.py", "--events")

    c = conn()
    after = {r[1] for r in c.execute("PRAGMA table_info(run_model_inputs)")}
    for col in ("confidence", "carry_realloc_weight", "carry_headroom",
                "target_realloc_weight", "target_headroom",
                "rush_td_realloc_weight", "rush_td_headroom",
                "rec_td_realloc_weight", "rec_td_headroom"):
        check(f"run_model_inputs.{col} added", col in after)
    old = c.execute("SELECT * FROM run_model_inputs WHERE run_id='run-old'"
                    ).fetchone()
    check("the older frozen row survived",
          old is not None and old["player"] == "Older Frozen Row")
    c.close()

    # And a real run must still write into the migrated table.
    run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
        "--player", "Breece Hall", "--availability", "0.0",
        "--reason", "post-migration write", "--source", "suite",
        "--observed-at", iso(5))
    run("engine.py", "--run", "--source-cutoff-at", iso(0))
    c = conn()
    r = live(c)
    n = c.execute("""SELECT COUNT(*) n FROM run_model_inputs
                     WHERE run_id=? AND confidence IS NOT NULL""",
                  (r,)).fetchone()["n"]
    check("a run writes the new columns after migration", n > 0, f"{n} rows")
    c.close()


def test_g():
    print("\n  G. HISTORICAL SUPERSESSION: as of the cutoff, not as of now\n")
    restore()
    c = conn()
    base = live(c)
    base_pts = player(c, base, "Breece Hall")["ppr"]
    c.close()

    out_at = iso(65)     # ruled out
    clear_at = iso(55)   # cleared ten minutes later
    between = iso(60)    # a run whose evidence stops between the two
    after = iso(50)

    o = run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
            "--player", "Breece Hall", "--availability", "0.0",
            "--reason", "test G, ruled out", "--source", "suite",
            "--observed-at", out_at)
    first = o.split()[0]
    run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
        "--player", "Breece Hall", "--availability", "1.0",
        "--reason", "test G, cleared", "--source", "suite",
        "--observed-at", clear_at, "--supersedes", first)

    # A run that knows only what was known at 10:00 must still have him out,
    # even though he has since been cleared.
    run("engine.py", "--run", "--source-cutoff-at", between)
    c = conn()
    r1 = live(c)
    p1 = player(c, r1, "Breece Hall")
    check("as of the earlier cutoff he is still out", p1["ppr"] < 0.01,
          f"{p1['ppr']:.1f} PPR")
    n = c.execute("SELECT COUNT(*) n FROM run_events WHERE run_id=? "
                  "AND applied=1", (r1,)).fetchone()["n"]
    check("only the earlier event applied", n == 1, f"{n}")
    c.close()

    # A later cutoff sees the clearance and returns him.
    run("engine.py", "--run", "--source-cutoff-at", after)
    c = conn()
    r2 = live(c)
    p2 = player(c, r2, "Breece Hall")
    check("as of the later cutoff he is back",
          abs(p2["ppr"] - base_pts) < 0.5,
          f"{base_pts:.1f} -> {p2['ppr']:.1f}")
    c.close()


def test_h():
    print("\n  H. EMPTY RUN: zero events, zero change\n")
    restore()
    c = conn()
    base = live(c)
    before = {}
    for r in c.execute("SELECT * FROM run_projections WHERE run_id=?", (base,)):
        d = dict(r)
        before[d["player_id"]] = {k: d[k] for k in (
            "pass_att", "completions", "pass_yds", "pass_td", "ints",
            "targets", "rec", "rec_yds", "rec_td",
            "rush_att", "rush_yds", "rush_td", "fumbles")}
    c.close()

    run("engine.py", "--run", "--source-cutoff-at", iso(0))

    c = conn()
    r = live(c)
    check("a new run published", r != base)
    worst, who, moved = 0.0, "", 0
    for pid, fields in before.items():
        row = c.execute("SELECT * FROM run_projections WHERE run_id=? "
                        "AND player_id=?", (r, pid)).fetchone()
        if not row:
            worst, who = 9e9, f"{pid} vanished"
            break
        for k, v in fields.items():
            d = abs((v or 0) - (row[k] or 0))
            if d > 1e-9:
                moved += 1
            if d > worst:
                worst, who = d, f"{pid}.{k}"
    check("every raw stat identical to the baseline", worst < 1e-9,
          f"{moved} cells moved, worst {worst:.2e} at {who}")
    n = c.execute("SELECT COUNT(*) n FROM run_reconciliation_adjustments "
                  "WHERE run_id=?", (r,)).fetchone()["n"]
    check("no reconciliation ran at all", n == 0, f"{n} adjustments")
    c.close()


def test_i():
    print("\n  I. TEMPORARY RESIDUAL: created by an event, gone with it\n")
    restore()
    c = conn()
    base = live(c)
    base_rows = c.execute("SELECT COUNT(*) n FROM run_projections WHERE run_id=?",
                          (base,)).fetchone()["n"]
    before = {}
    for r in c.execute("SELECT * FROM run_projections WHERE run_id=?", (base,)):
        d = dict(r)
        before[d["player_id"]] = {k: d[k] for k in (
            "pass_att", "completions", "pass_yds", "pass_td", "ints",
            "targets", "rec", "rec_yds", "rec_td",
            "rush_att", "rush_yds", "rush_td", "fumbles")}
    c.close()

    # Rule out every back on the team.
    #
    # A residual only forms when the freed opportunity exceeds what the
    # remaining eligible players can hold, so the condition has to be
    # constructed rather than hoped for: with nobody left who can take a
    # carry, all of them must land in the residual.
    c = conn()
    backs = [r["player"] for r in c.execute(
        """SELECT player FROM model_inputs WHERE team='NYJ'
           AND position IN ('RB','FB')
           ORDER BY carry_share DESC""")]
    c.close()
    if not backs:
        check("a backfield to empty", False, "no NYJ backs found")
        return
    print(f"    ruling out all {len(backs)} NYJ backs")

    ids = []
    for i, who in enumerate(backs):
        out = run("engine.py", "--add-event", "PLAYER_UNAVAILABLE",
                  "--player", who, "--availability", "0.0",
                  "--reason", f"test I, back {i}", "--source", "suite",
                  "--observed-at", iso(40 - i), "--force")
        ids.append(out.split()[0])
    run("engine.py", "--run", "--source-cutoff-at", iso(0))

    c = conn()
    r1 = live(c)
    res = c.execute("""SELECT * FROM run_projections
                       WHERE run_id=? AND team='NYJ' AND is_residual=1""",
                    (r1,)).fetchall()
    check("the injury run created a temporary residual", len(res) > 0,
          f"{len(res)}: {[x['player'] for x in res]}")
    e = c.execute("SELECT * FROM run_team_environment WHERE run_id=? AND team='NYJ'",
                  (r1,)).fetchone()
    tt = team_totals(c, r1, "NYJ")
    check("and the team still reconciles",
          abs(tt["ra"] - e["rush_att"]) < 0.6,
          f"{tt['ra']:.1f} vs {e['rush_att']:.1f}")
    c.close()

    # Retire both. Nothing about NYJ is true any more.
    for eid in ids:
        run("engine.py", "--supersede", eid, "--reason", "test I cleared",
            "--source", "suite", "--observed-at", iso(5))
    run("engine.py", "--run", "--source-cutoff-at", iso(0))

    c = conn()
    r2 = live(c)
    res2 = c.execute("""SELECT * FROM run_projections
                        WHERE run_id=? AND is_residual=1 AND team='NYJ'""",
                     (r2,)).fetchall()
    check("the temporary residual is gone", len(res2) == 0,
          f"{[x['player'] for x in res2]}")
    n2 = c.execute("SELECT COUNT(*) n FROM run_projections WHERE run_id=?",
                   (r2,)).fetchone()["n"]
    check("player count back to baseline", n2 == base_rows,
          f"{base_rows} -> {n2}")

    worst, who = 0.0, ""
    for pid, fields in before.items():
        row = c.execute("SELECT * FROM run_projections WHERE run_id=? "
                        "AND player_id=?", (r2, pid)).fetchone()
        if not row:
            worst, who = 9e9, f"{pid} vanished"
            break
        for k, v in fields.items():
            d = abs((v or 0) - (row[k] or 0))
            if d > worst:
                worst, who = d, f"{pid}.{k}"
    check("every raw stat back to baseline", worst < 0.01,
          f"worst {worst:.2e} at {who}")

    # and the Cleveland baseline residual, which is not temporary, survives
    cle = c.execute("""SELECT COUNT(*) n FROM run_projections
                       WHERE run_id=? AND is_residual=1 AND team='CLE'""",
                    (r2,)).fetchone()["n"]
    check("the baseline CLE residual is still there", cle == 1, f"{cle}")
    c.close()


def test_j():
    """A unit check, not an integration test.

    Yards per carry is a property of a player, and reconciliation moves
    carries. If the yards do not move with them the rate silently changes,
    which is a modelling error rather than a rounding one -- and it happened,
    because the yard-scaling read a value the caller was supposed to stash
    and a rewrite elsewhere dropped that line.

    Integration tests missed it: in the residual scenario the carry budget
    already balances, so rushing-attempt reconciliation never runs.
    """
    print("\n  J. RECONCILING CARRIES CARRIES THE YARDS\n")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "eng_unit", str(ROOT / "scripts" / "engine.py"))
    eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eng)

    # Two backs at 5.0 yards a carry, 100 carries between them, against a
    # budget of 110.
    inputs = {
        "a": {"player_id": "a", "player": "Back A", "team": "XX",
              "position": "RB", "availability": 1.0, "is_residual": 0},
        "b": {"player_id": "b", "player": "Back B", "team": "XX",
              "position": "RB", "availability": 1.0, "is_residual": 0},
    }
    blank = {k: 0.0 for k in ("pass_att", "completions", "pass_yds",
                              "pass_td", "ints", "targets", "rec", "rec_yds",
                              "rec_td", "rush_att", "rush_yds", "rush_td",
                              "fumbles")}
    stats = {
        "a": {**blank, "rush_att": 60.0, "rush_yds": 300.0},
        "b": {**blank, "rush_att": 40.0, "rush_yds": 200.0},
    }
    env = {"XX": {"pass_att": 0.0, "completions": 0.0, "pass_yds": 0.0,
                  "pass_td": 0.0, "non_target_rate": 0.03,
                  "rush_att": 110.0, "rush_td": 0.0,
                  "targets": 0.0, "rec_td": 0.0}}

    before = {k: stats[k]["rush_yds"] / stats[k]["rush_att"] for k in stats}
    eng.reconcile(stats, inputs, env, {"XX"})

    total = sum(s["rush_att"] for s in stats.values())
    check("carries reconcile to the budget", abs(total - 110.0) < 1e-9,
          f"{total:.2f}")
    for k in stats:
        ypc = stats[k]["rush_yds"] / stats[k]["rush_att"]
        check(f"{inputs[k]['player']} keeps his yards per carry",
              abs(ypc - before[k]) < 1e-9,
              f"{before[k]:.3f} -> {ypc:.3f}, "
              f"{stats[k]['rush_att']:.1f} carries "
              f"{stats[k]['rush_yds']:.1f} yards")
    tot_yds = sum(s["rush_yds"] for s in stats.values())
    check("team yards scaled with the carries",
          abs(tot_yds - 550.0) < 1e-6, f"{tot_yds:.1f}, expected 550.0")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="run against this database instead of the "
                                 "project's. The release wrapper passes a "
                                 "disposable copy.")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()

    global DB, SNAP
    if args.db:
        DB = Path(args.db).expanduser().resolve()
        SNAP = DB.with_suffix(".baseline")

    if not DB.exists():
        sys.exit("  no database. Import the workbook and publish a baseline "
                 "first.")

    # A release artifact is replayed from a copy. The suite migrates the
    # schema and publishes runs, so pointing it at a preserved baseline.db
    # would edit the thing being preserved, and it would stop matching its
    # own manifest -- which is what --verify exists to notice.
    if DB.name == "baseline.db" and (DB.parent / "manifest.json").exists():
        msg = ("  that is a frozen release artifact. Copy it first:"
               "\n    cp " + str(DB) + " /tmp/replay.db"
               "\n    python3 scripts/test_engine.py --db /tmp/replay.db")
        sys.exit(msg)


    # The engine migrates on start; the suite reads tables directly, so it
    # has to be migrated before the first query rather than after.
    sys.path.insert(0, str(ROOT / "scripts"))
    import engine as eng
    c2 = sqlite3.connect(DB)
    c2.executescript(eng.SCHEMA)
    eng.migrate(c2)
    c2.close()

    shutil.copy2(DB, SNAP)
    print(f"\n  baseline snapshotted; each test restores from it")

    tests = {"a": test_a, "b": test_b, "c": test_c, "d": test_d,
             "e": test_e, "f": test_f, "g": test_g, "h": test_h,
             "i": test_i, "j": test_j}
    for k, fn in tests.items():
        if args.only and args.only.lower() != k:
            continue
        fn()

    restore()
    if not args.keep:
        SNAP.unlink(missing_ok=True)

    print()
    if FAILURES:
        print(f"  {len(FAILURES)} failures:")
        for f in FAILURES:
            print(f"    {f}")
        return 1
    print(f"  all tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
