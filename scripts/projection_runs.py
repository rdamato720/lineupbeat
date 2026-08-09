#!/usr/bin/env python3
"""Validation gates and immutable snapshots for the projection engine.

    python3 scripts/projection_runs.py --validate            # check, publish nothing
    python3 scripts/projection_runs.py --publish             # only if clean
    python3 scripts/projection_runs.py --live                # what is published now
    python3 scripts/projection_runs.py --history             # every run, with outcome

THE RULE THIS ENFORCES

A new piece of information must never overwrite the live projections. It
creates inputs for a run; only a fully reconciled, validated snapshot can
become the published set. If a run fails, the previous one stays live and
nothing about it changes.

That is not a preference, it is the whole architecture. Everything else in
the system -- the evidence pipeline, the daily engine, the site -- depends on
"published" meaning something that was checked.

HOW IT WORKS

Runs are immutable. Each one gets a run_id, writes its rows once, and is
never edited. Publishing is a single row in a pointer table naming which run
is live, so it is atomic: either the pointer moves or it does not, and there
is no window where the site sees half a run.

The baseline is preserved. Offense v1.0 is the first run and stays in the
database forever; later runs are new snapshots beside it, not replacements.

BLOCKING CHECKS, from section 19.1

Ten of them, and any one failing stops publication. Six are arithmetic about
a single row -- no NaN, nothing negative, completions within attempts. Four
are the offense-wide reconciliation: for every team, player targets plus the
non-targeted bucket equal QB attempts, receptions equal completions,
receiving yards equal passing yards, receiving TDs equal passing TDs.

Those four are the ones that catch a real modelling error. A single-row check
passes happily while two backs share 140% of a backfield.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ROOT = Path(__file__).resolve().parent.parent

# 3% of attempts are throwaways, spikes and similar. A modelling constant,
# not a backtested optimum -- section 12.
NON_TARGET_RATE = 0.03

# Not teams. Free agents belong in the player universe and nowhere near a
# reconciliation: FA/UNK reconciling to nothing would fail every check.
NON_TEAMS = {"FA", "FA/UNK", "UNK", ""}

# What "equal" means for a float. The workbook reconciles to 1e-13; anything
# under a hundredth of a unit is arithmetic, not disagreement.
TOL = 0.01

SCHEMA = """
CREATE TABLE IF NOT EXISTS projection_runs (
  run_id TEXT PRIMARY KEY,
  season INTEGER,
  model_version TEXT,
  scoring_version TEXT,
  started_at TEXT,
  completed_at TEXT,
  status TEXT,                 -- running | validated | failed | published
  input_hash TEXT,
  source_cutoff_at TEXT,
  validation_json TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS run_projections (
  run_id TEXT, season INTEGER, player_id TEXT, player TEXT,
  team TEXT, position TEXT,
  pass_att REAL, completions REAL, pass_yds REAL, pass_td REAL, ints REAL,
  targets REAL, rec REAL, rec_yds REAL, rec_td REAL,
  rush_att REAL, rush_yds REAL, rush_td REAL, fumbles REAL,
  rank_pos INTEGER, confidence TEXT, reason_codes TEXT,
  is_residual INTEGER DEFAULT 0,
  PRIMARY KEY (run_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_run_proj ON run_projections(run_id, position);

-- Which run the site reads. One row. Moving it is the publish.
CREATE TABLE IF NOT EXISTS published_snapshot (
  season INTEGER PRIMARY KEY,
  run_id TEXT,
  published_at TEXT,
  previous_run_id TEXT
);

-- What changed between one published run and the next, per player.
-- What each team is allowed to rush for. Without this a back losing 180
-- carries can simply have them vanish, and the team quietly runs the ball
-- 180 fewer times than the model said it would.
CREATE TABLE IF NOT EXISTS team_rush_budget (
  season INTEGER, team TEXT, rush_att REAL, rush_td REAL,
  source TEXT, updated_at TEXT,
  PRIMARY KEY (season, team)
);

-- The constraints a run was validated against, frozen with it.
--
-- team_rush_budget is one mutable row per team, which is fine for holding
-- the current baseline and useless for reproducing an old run: change a
-- budget in October and nobody can say what Run A was checked against in
-- August. So every run copies the budgets it used.
CREATE TABLE IF NOT EXISTS run_team_budgets (
  run_id TEXT, team TEXT, rush_att REAL, rush_td REAL, source TEXT,
  PRIMARY KEY (run_id, team)
);

CREATE TABLE IF NOT EXISTS projection_changes (
  run_id TEXT, player_id TEXT, player TEXT, field TEXT,
  previous REAL, current REAL, reason_code TEXT, source_event_ids TEXT,
  PRIMARY KEY (run_id, player_id, field)
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def new_run_id(season, model_version="offense-v1.0"):
    """Timestamp plus randomness.

    Second-level resolution alone collides: two runs in the same second is
    not hypothetical when a manual run and a scheduled one overlap, and the
    loser would silently overwrite an immutable snapshot.
    """
    import uuid
    return (f"{season}-{model_version}-"
            f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-"
            f"{uuid.uuid4().hex[:8]}")


# --------------------------------------------------------------------------
# scoring, section 4. One raw stat line, three views.
# --------------------------------------------------------------------------

def score(row: dict, fmt: str = "ppr") -> float:
    """Fantasy points from raw stats.

    Stored once, computed three ways. The spec is explicit that three
    separate stat models must not exist, and this is what enforces it: there
    is nowhere for a half-PPR projection to disagree with a PPR one, because
    they are the same numbers read differently.

    QB scoring is locked, and two-point conversions are deliberately zero.
    """
    g = lambda k: float(row.get(k) or 0)
    pts = (g("pass_yds") / 25 + g("pass_td") * 4 - g("ints") * 2
           + (g("rush_yds") + g("rec_yds")) / 10
           + (g("rush_td") + g("rec_td")) * 6
           - g("fumbles") * 2)
    if fmt == "ppr":
        pts += g("rec")
    elif fmt == "half":
        pts += g("rec") * 0.5
    return pts


# --------------------------------------------------------------------------
# validation, section 19.1
# --------------------------------------------------------------------------

def validate(rows: list[dict], rush_budget: dict | None = None) -> dict:
    """Every blocking check. Any failure stops publication.

    Returns a summary rather than raising, because the run record has to
    store WHY it failed -- a run that dies with a traceback tells the next
    person nothing.
    """
    blocking, review = [], []

    NUMERIC = ("pass_att", "completions", "pass_yds", "pass_td", "ints",
               "targets", "rec", "rec_yds", "rec_td",
               "rush_att", "rush_yds", "rush_td", "fumbles")

    # ---- per row -----------------------------------------------------
    bad_num, negative, over_att, over_tgt, dupes = [], [], [], [], []
    seen = set()
    for r in rows:
        who = r.get("player") or r.get("player_id")
        for f in NUMERIC:
            v = r.get(f)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                bad_num.append(f"{who}.{f}")
                continue
            if math.isnan(v) or math.isinf(v):
                bad_num.append(f"{who}.{f}")
            elif v < 0:
                # A yard or two of negative rushing is a real football
                # outcome and appears in the workbook. A negative attempt or
                # reception count is not.
                # A yard or two of negative rushing is a legitimate expected
                # value. Negative yards on zero carries is not: nobody loses
                # ground on a run they never took.
                if f == "rush_yds" and v > -5:
                    if float(r.get("rush_att") or 0) <= 0:
                        negative.append(f"{who}.rush_yds={v:.1f} on zero carries")
                    else:
                        review.append(f"{who}.{f}={v:.1f}")
                else:
                    negative.append(f"{who}.{f}={v}")
        if float(r.get("completions") or 0) > float(r.get("pass_att") or 0) + TOL:
            over_att.append(who)
        if float(r.get("rec") or 0) > float(r.get("targets") or 0) + TOL:
            over_tgt.append(who)
        pid = r.get("player_id")
        if pid in seen:
            dupes.append(who)
        seen.add(pid)

    if bad_num:
        blocking.append(f"{len(bad_num)} NaN/null/infinite values: {bad_num[:4]}")
    if negative:
        blocking.append(f"{len(negative)} negative values: {negative[:4]}")
    if over_att:
        blocking.append(f"{len(over_att)} with completions above attempts: {over_att[:4]}")
    if over_tgt:
        blocking.append(f"{len(over_tgt)} with receptions above targets: {over_tgt[:4]}")
    if dupes:
        blocking.append(f"{len(dupes)} duplicate player rows: {dupes[:4]}")

    # ---- per team, the reconciliation --------------------------------
    #
    # The checks that catch a real modelling error. A per-row check passes
    # happily while two backs share 140% of one backfield.
    teams = {}
    for r in rows:
        t = (r.get("team") or "").strip().upper()
        if t in NON_TEAMS:
            continue
        d = teams.setdefault(t, {"qb_att": 0.0, "qb_cmp": 0.0, "qb_yds": 0.0,
                                 "qb_td": 0.0, "tgt": 0.0, "rec": 0.0,
                                 "ryds": 0.0, "rtd": 0.0,
                                 "rush_att": 0.0, "rush_td": 0.0})
        if (r.get("position") or "").upper() == "QB":
            d["qb_att"] += float(r.get("pass_att") or 0)
            d["qb_cmp"] += float(r.get("completions") or 0)
            d["qb_yds"] += float(r.get("pass_yds") or 0)
            d["qb_td"] += float(r.get("pass_td") or 0)
        d["rush_att"] += float(r.get("rush_att") or 0)
        d["rush_td"] += float(r.get("rush_td") or 0)
        d["tgt"] += float(r.get("targets") or 0)
        d["rec"] += float(r.get("rec") or 0)
        d["ryds"] += float(r.get("rec_yds") or 0)
        d["rtd"] += float(r.get("rec_td") or 0)

    off = {"targets": [], "receptions": [], "yards": [], "tds": []}
    for t, d in sorted(teams.items()):
        if d["qb_att"] <= 0:
            continue
        expect_tgt = d["qb_att"] * (1 - NON_TARGET_RATE)
        if abs(d["tgt"] - expect_tgt) > max(0.5, expect_tgt * 0.002):
            off["targets"].append(f"{t} {d['tgt']:.1f} vs {expect_tgt:.1f}")
        if abs(d["rec"] - d["qb_cmp"]) > max(0.5, d["qb_cmp"] * 0.002):
            off["receptions"].append(f"{t} {d['rec']:.1f} vs {d['qb_cmp']:.1f}")
        if abs(d["ryds"] - d["qb_yds"]) > max(1.0, d["qb_yds"] * 0.002):
            off["yards"].append(f"{t} {d['ryds']:.0f} vs {d['qb_yds']:.0f}")
        if abs(d["rtd"] - d["qb_td"]) > max(TOL, d["qb_td"] * 0.01):
            off["tds"].append(f"{t} {d['rtd']:.1f} vs {d['qb_td']:.1f}")

    for what, bad in off.items():
        if bad:
            blocking.append(f"{len(bad)} teams fail {what} reconciliation: {bad[:3]}")

    # ---- rushing, section from the review --------------------------------
    #
    # The passing side is reconciled in the workbook; the rushing side is not
    # yet, because the baseline was built cross-position on receiving. It
    # matters the moment an evidence-driven update moves carries: a back
    # ruled out has to give his carries to somebody, and without this check
    # the team could quietly gain or lose a hundred of them.
    #
    # Warns until a team rushing budget exists to check against, then blocks.
    if rush_budget:
        bad_att, bad_td = [], []
        for t_, d in sorted(teams.items()):
            want = rush_budget.get(t_)
            if not want:
                continue
            if abs(d["rush_att"] - want["rush_att"]) > max(1.0, want["rush_att"] * 0.002):
                bad_att.append(f"{t_} {d['rush_att']:.0f} vs {want['rush_att']:.0f}")
            # Touchdowns matter as much as carries. If an injury removes
            # eight goal-line scores from one back they have to land
            # somewhere: on a teammate, or in an explicit residual. They
            # cannot simply stop existing.
            if abs(d["rush_td"] - want["rush_td"]) > max(0.5, want["rush_td"] * 0.005):
                bad_td.append(f"{t_} {d['rush_td']:.1f} vs {want['rush_td']:.1f}")
        if bad_att:
            blocking.append(f"{len(bad_att)} teams fail rushing attempt "
                            f"reconciliation: {bad_att[:3]}")
        if bad_td:
            blocking.append(f"{len(bad_td)} teams fail rushing TD "
                            f"reconciliation: {bad_td[:3]}")
    else:
        review.append("no team rushing budget stored, so rushing attempts are "
                      "not reconciled; required before evidence-driven updates "
                      "may publish")

    # ---- fantasy points recompute from raw stats ----------------------
    #
    # A tenth of a point either way is the published column having been
    # rounded; the workbook's QB sheet keeps a Calc and a Locked figure and
    # they differ by 0.20 on most rows. Anything larger means the stored
    # points and the stored stats disagree about something real.
    # The raw stat line is canonical. A stored points column is an artefact
    # of whatever produced the file -- the workbook's Locked QB column was
    # rounded and differs by 0.20 on most rows -- so points are always
    # recomputed and the stored figure is only a cross-check.
    mismatched, rounding = [], 0
    for r in rows:
        if r.get("fpts_ppr") is None:
            continue
        d = abs(score(r, "ppr") - float(r["fpts_ppr"]))
        if d > 1.0:
            mismatched.append(f"{r.get('player')} by {d:.1f}")
        elif d > 0.05:
            rounding += 1
    if mismatched:
        blocking.append(f"{len(mismatched)} rows whose stored points are more "
                        f"than a point from their raw stats: {mismatched[:4]}")
    if rounding:
        review.append(f"{rounding} stored point values differ from the raw "
                      f"line by under a point; the raw line is used")

    return {
        "checked": len(rows), "teams": len(teams),
        "blocking": blocking, "review": review,
        "passed": not blocking,
    }


def compare(conn, season, run_id, prev_run_id) -> list[dict]:
    """What moved between two runs, and by how much. Section 19.2.

    Not a gate -- a record. Somebody has to be able to ask why a player
    dropped forty points overnight and get an answer that is not "the model
    changed".
    """
    if not prev_run_id:
        return []
    prev = {r["player_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM run_projections WHERE run_id=?", (prev_run_id,))}
    out = []
    for r in conn.execute("SELECT * FROM run_projections WHERE run_id=?",
                          (run_id,)):
        p = prev.get(r["player_id"])
        if not p:
            continue
        a, b = score(dict(p)), score(dict(r))
        if abs(b - a) > 0.05:
            flag = "REVIEW_LARGE_MOVE" if abs(b - a) > 10 else ""
            out.append({"player_id": r["player_id"], "player": r["player"],
                        "field": "fpts_ppr", "previous": a, "current": b,
                        "reason_code": flag})
    return out


# --------------------------------------------------------------------------

def rows_from_projections(conn, season) -> list[dict]:
    """Read the staging table.

    Staging is mutable and the site never reads it. A run copies from here
    into run_projections, which is immutable, and only the published_snapshot
    pointer decides what is live.
    """
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM projection_staging WHERE season=?", (season,))]
    except sqlite3.OperationalError:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--model-version", default="offense-v1.0")
    ap.add_argument("--scoring-version", default="scoring-v1.0")
    ap.add_argument("--note", default="")
    ap.add_argument("--source-cutoff-at",
                    help="when the evidence behind this run stops. A run built "
                         "from information through 10:00 and published at "
                         "10:07 keeps both, permanently. Defaults to run start "
                         "for a manual baseline.")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--history", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if args.live:
        s = conn.execute("SELECT * FROM published_snapshot WHERE season=?",
                         (args.season,)).fetchone()
        if not s:
            sys.exit("  nothing published for this season")
        r = conn.execute("SELECT * FROM projection_runs WHERE run_id=?",
                         (s["run_id"],)).fetchone()
        n = conn.execute("SELECT COUNT(*) c FROM run_projections WHERE run_id=?",
                         (s["run_id"],)).fetchone()["c"]
        print(f"\n  live: {s['run_id']}")
        print(f"  published {s['published_at'][:16]}, {n} players")
        print(f"  model {r['model_version']}, scoring {r['scoring_version']}")
        if s["previous_run_id"]:
            print(f"  replaced {s['previous_run_id']}")
        if r["note"]:
            print(f"  {r['note']}")
        return

    if args.history:
        rows = conn.execute(
            "SELECT * FROM projection_runs WHERE season=? ORDER BY started_at",
            (args.season,)).fetchall()
        if not rows:
            sys.exit("  no runs yet")
        live = conn.execute("SELECT run_id FROM published_snapshot WHERE season=?",
                            (args.season,)).fetchone()
        live_id = live["run_id"] if live else None
        print(f"\n  {len(rows)} runs\n")
        for r in rows:
            mark = "  <- live" if r["run_id"] == live_id else ""
            print(f"    {r['started_at'][:16]}  {r['status']:<10} "
                  f"{r['run_id']}{mark}")
            if r["status"] == "failed":
                v = json.loads(r["validation_json"] or "{}")
                for b in v.get("blocking", [])[:2]:
                    print(f"        {b}")
        return

    # ---- a run -------------------------------------------------------
    rows = rows_from_projections(conn, args.season)
    if not rows:
        sys.exit("  no projections loaded. Run import_projections.py first.")

    # map the loaded shape onto the run shape
    prepared = []
    for r in rows:
        prepared.append({
            "player_id": r.get("player_id"), "player": r.get("player"),
            "team": r.get("team"), "position": r.get("position"),
            "pass_att": r.get("pass_att"), "completions": r.get("completions"),
            "pass_yds": r.get("pass_yds"), "pass_td": r.get("pass_td"),
            "ints": r.get("ints"),
            "targets": r.get("targets"), "rec": r.get("rec"),
            "rec_yds": r.get("recyd"), "rec_td": r.get("rec_td"),
            "rush_att": r.get("rush_att"), "rush_yds": r.get("ruyd"),
            "rush_td": r.get("rush_td"), "fumbles": r.get("fumbles"),
            "rank_pos": r.get("rank_pos"),
            "fpts_ppr": r.get("ppr"),
            "is_residual": r.get("is_residual") or 0,
        })

    run_id = new_run_id(args.season, args.model_version)
    started = now()
    ihash = hashlib.sha256(
        json.dumps([sorted(p.items(), key=lambda x: x[0]) for p in prepared],
                   default=str).encode()).hexdigest()[:16]

    budget = {r["team"]: dict(r) for r in conn.execute(
        "SELECT * FROM team_rush_budget WHERE season=?", (args.season,))}
    rush_budget = budget or None
    result = validate(prepared, rush_budget)
    print(f"\n  run {run_id}")
    print(f"  {result['checked']} players across {result['teams']} teams")
    print(f"  input hash {ihash}\n")

    if result["passed"]:
        print("  VALIDATION PASSED, all blocking checks clear")
    else:
        print(f"  VALIDATION FAILED, {len(result['blocking'])} blocking issues\n")
        for b in result["blocking"]:
            print(f"    {b}")
    if result.get("review"):
        print(f"\n  {len(result['review'])} review flags, not blocking:")
        for r_ in result["review"][:5]:
            print(f"    {r_}")

    status = "validated" if result["passed"] else "failed"
    # Plain INSERT. The UUID makes a collision a bug worth surfacing rather
    # than something to overwrite. Updating status afterwards is fine; the
    # row's identity is not.
    conn.execute(
        "INSERT INTO projection_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, args.season, args.model_version, args.scoring_version,
         started, now(), status, ihash,
         args.source_cutoff_at or started,
         json.dumps(result), args.note))

    if result["passed"]:
        for p in prepared:
            # Plain INSERT. A snapshot is immutable, so a duplicate row is
            # a bug to surface rather than something to overwrite quietly.
            conn.execute(
                "INSERT INTO run_projections VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, args.season, p["player_id"], p["player"], p["team"],
                 p["position"], p["pass_att"], p["completions"], p["pass_yds"],
                 p["pass_td"], p["ints"], p["targets"], p["rec"], p["rec_yds"],
                 p["rec_td"], p["rush_att"], p["rush_yds"], p["rush_td"],
                 p["fumbles"], p["rank_pos"], None, None,
                 p.get("is_residual", 0)))
        # Freeze the constraints beside the rows they validated.
        for team, b in budget.items():
            # Immutable, so a duplicate is a bug rather than an update.
            conn.execute("INSERT INTO run_team_budgets VALUES (?,?,?,?,?)",
                         (run_id, team, b["rush_att"], b["rush_td"],
                          b.get("source") or ""))
    conn.commit()

    if not args.publish:
        print(f"\n  Run recorded as '{status}'. Nothing published.")
        if result["passed"]:
            print(f"  Re-run with --publish to make it live.")
        return

    if not result["passed"]:
        live = conn.execute("SELECT * FROM published_snapshot WHERE season=?",
                            (args.season,)).fetchone()
        print(f"\n  REFUSING TO PUBLISH.")
        if live:
            print(f"  {live['run_id']} stays live, unchanged, "
                  f"published {live['published_at'][:16]}.")
        else:
            print(f"  Nothing was live and nothing is now.")
        return 1

    prev = conn.execute("SELECT run_id FROM published_snapshot WHERE season=?",
                        (args.season,)).fetchone()
    prev_id = prev["run_id"] if prev else None

    # Claim whatever the engine staged, so the run can say which event
    # caused it and which model inputs moved. Without this the change log
    # says a player's points moved and cannot say why.
    pend = ROOT / ".engine_pending.json"
    if pend.exists():
        try:
            data = json.loads(pend.read_text())
            conn.execute("INSERT OR REPLACE INTO run_events VALUES (?,?,?,?)",
                         (run_id, data["event_id"], "applied", 1))
            for ch in data.get("changes", []):
                conn.execute(
                    "INSERT OR REPLACE INTO run_input_changes VALUES "
                    "(?,?,?,?,?,?,?)",
                    (run_id, ch["player_id"], ch["player"], ch["field"],
                     ch["previous"], ch["current"], data["event_id"]))
            conn.execute("UPDATE model_events SET status='applied' "
                         "WHERE event_id=?", (data["event_id"],))
            pend.unlink()
            print(f"  linked to event {data['event_id']}, "
                  f"{len(data.get('changes', []))} input changes recorded")
        except Exception as exc:
            print(f"  could not link the pending event: {str(exc)[:60]}")

    changes = compare(conn, args.season, run_id, prev_id)
    for c in changes:
        conn.execute("INSERT INTO projection_changes VALUES (?,?,?,?,?,?,?,?)",
                     (run_id, c["player_id"], c["player"], c["field"],
                      c["previous"], c["current"], c["reason_code"], None))

    # The publish. One row moves, atomically.
    # The one row that is meant to change. UPSERT rather than delete-insert
    # so there is never an instant with nothing published.
    conn.execute("""INSERT INTO published_snapshot VALUES (?,?,?,?)
                    ON CONFLICT(season) DO UPDATE SET
                      run_id=excluded.run_id,
                      published_at=excluded.published_at,
                      previous_run_id=excluded.previous_run_id""",
                 (args.season, run_id, now(), prev_id))
    conn.execute("UPDATE projection_runs SET status='published' WHERE run_id=?",
                 (run_id,))
    conn.commit()

    print(f"\n  PUBLISHED {run_id}")
    if prev_id:
        print(f"  replaced {prev_id}, which is still in the database")
    if changes:
        big = [c for c in changes if c["reason_code"]]
        print(f"  {len(changes)} players moved, {len(big)} by more than 10 points")
        for c in sorted(changes, key=lambda x: -abs(x["current"] - x["previous"]))[:5]:
            print(f"    {c['player'][:22]:<22} {c['previous']:>7.1f} -> "
                  f"{c['current']:>7.1f}  {c['reason_code']}")


if __name__ == "__main__":
    sys.exit(main() or 0)
