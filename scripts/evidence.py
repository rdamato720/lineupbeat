#!/usr/bin/env python3
"""Tier A evidence: official sources into shadow candidates. Never publishes.

    python3 scripts/evidence.py --observe \
        --source "Detroit Lions" --tier A \
        --url https://www.detroitlions.com/news/... \
        --external-id lions-2026-08-09-gibbs-ir \
        --title "Lions place RB Jahmyr Gibbs on injured reserve" \
        --text "The Detroit Lions today placed running back Jahmyr Gibbs on
                injured reserve." \
        --team DET --published-at 2026-08-09T14:00:00Z

    python3 scripts/evidence.py --normalize OBSERVATION_ID \
        --event PLAYER_UNAVAILABLE --player "Jahmyr Gibbs" --availability 0.0

    python3 scripts/evidence.py --apply EVIDENCE_EVENT_ID
    python3 scripts/evidence.py --report APPLICATION_ID
    python3 scripts/evidence.py --chain PLAYER

THREE LAYERS, KEPT APART

  observation   what a source actually published. Immutable. Hashed.
  event         what we believe it means, structurally. Interpretation.
  application   what the frozen engine did when handed that event.

The separation is the point. A source does not change a projection; it
creates a record. Somebody decides what that record means. The engine
decides what follows. Each of those is a different kind of mistake and each
is recoverable separately -- a misread headline is a bad event over a good
observation, and can be superseded without anybody re-fetching anything.

WHY THIS CANNOT PUBLISH

There is no publication code here. Not a flag set to false, not a branch
that is never taken: this module contains nothing that writes to
published_snapshot, and it never will. Promotion, when we trust the process
enough to want it, will be a separate command somebody has to run on
purpose.

A boolean called publish=False is one edit away from publish=True. Absence
is not.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

NORMALIZER_VERSION = "normalizer-1.0"

# What this layer understands so far. One event type, deliberately.
#
# An official transaction is the only thing a machine can read without
# interpretation: "placed on injured reserve" means one thing. Beat-reporter
# language does not, and the first version of an evidence pipeline is the
# wrong place to find that out.
SUPPORTED_EVENTS = {"PLAYER_UNAVAILABLE"}

TIERS = {"A", "B", "C"}

SCHEMA = """
-- What a source published. Never edited after insertion.
CREATE TABLE IF NOT EXISTS evidence_observations (
  observation_id TEXT PRIMARY KEY,
  source_id TEXT, source_tier TEXT, source_url TEXT, external_id TEXT,
  team TEXT, published_at TEXT, fetched_at TEXT,
  content_sha256 TEXT, title TEXT, raw_text TEXT, raw_payload_json TEXT,
  -- Official feeds revise posts. Same id and same bytes is a re-serve and
  -- is dropped; same id and different bytes is a correction, which is a new
  -- immutable row pointing at what it replaces. Editing the old one would
  -- destroy the record of what we believed at the time.
  revision INTEGER DEFAULT 1,
  revises_observation_id TEXT,
  created_at TEXT
);
-- Two ways the same item can arrive twice: the source gave it an id, or it
-- did not and the bytes are all we have.
CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_external
  ON evidence_observations(source_id, external_id, content_sha256)
  WHERE external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_content
  ON evidence_observations(source_id, content_sha256);

-- What we believe an observation means.
CREATE TABLE IF NOT EXISTS evidence_events (
  evidence_event_id TEXT PRIMARY KEY,
  observation_id TEXT, season INTEGER, event_type TEXT,
  player_id TEXT, player TEXT, team TEXT,
  payload_json TEXT,
  -- Two different questions, and conflating them is lookahead bias.
  --
  -- observed_at is when we learned it. It decides whether a run may use the
  -- evidence: a candidate whose cutoff is 00:30 must not know something
  -- reported at 01:00, however early the underlying event happened.
  --
  -- effective_at is when the world changed. It is context and provenance,
  -- and it does not grant a past run knowledge of the future.
  observed_at TEXT, effective_start_at TEXT, effective_end_at TEXT,
  -- The interval over which this evidence was BELIEVED, which is what
  -- applicability is decided on.
  knowledge_start_at TEXT, knowledge_end_at TEXT,
  source_tier TEXT, confidence TEXT, normalizer_version TEXT,
  review_status TEXT, supersedes_evidence_event_id TEXT,
  created_at TEXT
);

-- What the frozen engine did with it.
-- One candidate can depend on many evidence events, so the link cannot be a
-- column on the application. The trigger is what caused the evaluation; the
-- others were simply still true at the cutoff, and a candidate that omitted
-- them would be a projection of a world that never existed.
CREATE TABLE IF NOT EXISTS evidence_application_events (
  application_id TEXT, evidence_event_id TEXT, engine_event_id TEXT,
  role TEXT,                    -- trigger | active
  PRIMARY KEY (application_id, evidence_event_id)
);

CREATE TABLE IF NOT EXISTS evidence_applications (
  application_id TEXT PRIMARY KEY,
  evidence_event_id TEXT, engine_release_tag TEXT, candidate_run_id TEXT,
  source_cutoff_at TEXT,
  application_status TEXT, validation_status TEXT,
  review_status TEXT, reviewed_at TEXT, reviewed_by TEXT,
  report_text TEXT, created_at TEXT
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def utc(stamp):
    if not stamp:
        return None
    s = stamp.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        sys.exit(f"  not a timestamp: {stamp!r}")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def content_hash(source_id, title, text, payload):
    """What makes two arrivals the same item.

    Source included, because two outlets publishing identical wire copy are
    two observations: one of them may later be corrected and the other not.
    """
    h = hashlib.sha256()
    for part in (source_id, title or "", text or "", payload or ""):
        h.update(part.encode("utf-8"))
        h.update(b"\\x00")
    return h.hexdigest()


def engine_release(conn, tag=None):
    """The frozen release to execute, and where its code actually lives.

    Recording a tag while running the working tree defeats the entire
    release apparatus: after somebody starts engine-1.1, an application
    would say engine-1.0 and execute something else. So this returns the
    artifact directory, and the subprocess runs the preserved copy.

    A tag is required. "Latest tested" is not the same as "the one in
    production", and guessing between them is how a shadow candidate ends
    up produced by code nobody deployed.
    """
    if not tag:
        have = [r["tag"] for r in conn.execute(
            """SELECT tag FROM engine_releases
               WHERE verification_status='tested' ORDER BY frozen_at DESC""")]
        sys.exit(f"  --engine-release is required: a tested release is not "
                 f"necessarily the deployed one.\n"
                 f"  available: {have or 'none frozen yet'}")
    r = conn.execute(
        "SELECT * FROM engine_releases WHERE tag=?", (tag,)).fetchone()
    if not r:
        sys.exit(f"  no release tagged {tag!r}")
    if r["verification_status"] != "tested":
        sys.exit(f"  {tag} was never verified; it cannot produce candidates")
    rd = Path(r["release_dir"]) if r["release_dir"] else None
    if not rd or not (rd / "scripts" / "engine.py").exists():
        sys.exit(f"  {tag} has no preserved engine at {rd}. Evidence must "
                 f"run the frozen code, not the working tree.")
    return tag, rd


def cmd_observe(conn, args):
    """Store what a source published, once."""
    if args.tier not in TIERS:
        sys.exit(f"  source tier must be one of {sorted(TIERS)}")
    text = args.text or ""
    if not text.strip() and not args.payload:
        sys.exit("  an observation needs --text or --payload")

    sha = content_hash(args.source, args.title, text, args.payload)

    # Already have it? Say so and stop. An observation arriving twice is
    # normal -- a feed re-serves, a poller overlaps -- and must not become
    # two records that could be normalized into two events.
    same = conn.execute(
        """SELECT * FROM evidence_observations
           WHERE source_id=? AND content_sha256=?""",
        (args.source, sha)).fetchone()
    if same:
        print(f"  already stored as {same['observation_id']}")
        print(f"  fetched {same['fetched_at'][:19]}, identical, not stored again")
        return 0

    prior = None
    revision = 1
    if args.external_id:
        prior = conn.execute(
            """SELECT * FROM evidence_observations
               WHERE source_id=? AND external_id=?
               ORDER BY revision DESC LIMIT 1""",
            (args.source, args.external_id)).fetchone()
        if prior:
            revision = prior["revision"] + 1

    oid = f"obs-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO evidence_observations VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (oid, args.source, args.tier, args.url, args.external_id,
         (args.team or "").upper() or None,
         utc(args.published_at), now(), sha, args.title, text,
         args.payload, revision,
         prior["observation_id"] if prior else None, now()))
    conn.commit()
    print(f"\n  {oid}")
    print(f"  source   {args.source}  (tier {args.tier})")
    print(f"  team     {(args.team or '-').upper()}")
    print(f"  hash     {sha[:32]}…")
    if prior:
        print(f"  revision {revision}, correcting "
              f"{prior['observation_id']}")
        print(f"           the earlier text is kept: it is what we believed")
    if args.published_at:
        print(f"  published {utc(args.published_at)[:19]}")
    print(f"\n  stored. Nothing has been interpreted: normalize it with")
    print(f"    python3 scripts/evidence.py --normalize {oid} \\")
    print(f"        --event PLAYER_UNAVAILABLE --player NAME --availability 0.0")
    return 0


def resolve_player(conn, season, name, team=None):
    """Exactly one player, or nothing.

    Ambiguity is not something an evidence layer should resolve by picking
    the first row. Two players matching means the observation needs a human,
    and saying so is more useful than guessing correctly most of the time.
    """
    rows = conn.execute(
        """SELECT player_id, player, team, position FROM model_inputs
           WHERE season=? AND lower(player)=lower(?)""",
        (season, name)).fetchall()
    if not rows:
        rows = conn.execute(
            """SELECT player_id, player, team, position FROM model_inputs
               WHERE season=? AND lower(player) LIKE lower(?)""",
            (season, f"%{name}%")).fetchall()
    if team:
        # Unconditional. The previous version kept the unfiltered result when
        # the team filter matched nothing, so an observation from the Lions
        # naming "Breece Hall" resolved to a Jets running back -- the
        # source's own constraint discarded because honouring it would have
        # returned nothing. Returning nothing was the correct answer.
        rows = [r for r in rows
                if (r["team"] or "").upper() == team.upper()]
        if not rows:
            sys.exit(f"  no player matching {name!r} on {team.upper()}. "
                     f"The source says {team.upper()}; that is a constraint, "
                     f"not a hint.")
    if not rows:
        sys.exit(f"  no player matching {name!r}"
                 + (f" on {team}" if team else ""))
    if len(rows) > 1:
        print(f"  {name!r} matches {len(rows)} players:")
        for r in rows:
            print(f"    {r['player']} ({r['team']} {r['position']})")
        sys.exit("  be more specific, or pass --team")
    return dict(rows[0])


def cmd_normalize(conn, args):
    """Turn an observation into something the engine understands."""
    obs = conn.execute(
        "SELECT * FROM evidence_observations WHERE observation_id=?",
        (args.normalize,)).fetchone()
    if not obs:
        sys.exit(f"  no observation {args.normalize}")

    if args.event not in SUPPORTED_EVENTS:
        sys.exit(f"  {args.event} is not supported yet. This layer "
                 f"understands {sorted(SUPPORTED_EVENTS)} and nothing else, "
                 f"on purpose.")
    if obs["source_tier"] != "A":
        sys.exit(f"  {obs['observation_id']} is tier {obs['source_tier']}. "
                 f"Only tier A is normalized automatically: a beat report is "
                 f"evidence about the world, not a statement of it.")
    if args.availability is None:
        sys.exit("  PLAYER_UNAVAILABLE needs --availability")
    if not 0.0 <= args.availability <= 1.0:
        sys.exit(f"  availability must be between 0 and 1, "
                 f"got {args.availability}")

    p = resolve_player(conn, args.season, args.player,
                       args.team or obs["team"])

    # When the world changed, not when we noticed. Falls back to publication,
    # then to when we fetched it -- each a worse approximation, and the
    # ordering matters because a cutoff comparison uses it.
    observed = utc(args.observed_at) or obs["published_at"] or obs["fetched_at"]
    effective = utc(args.effective_at) or observed

    eid = f"ev-{uuid.uuid4().hex[:12]}"
    payload = {"availability": args.availability}

    prior = None
    if args.supersedes:
        prior = conn.execute(
            "SELECT * FROM evidence_events WHERE evidence_event_id=?",
            (args.supersedes,)).fetchone()
        if not prior:
            sys.exit(f"  cannot supersede {args.supersedes}: no such event")
        if prior["player_id"] != p["player_id"] and not args.force:
            sys.exit(f"  {args.supersedes} is about {prior['player']}, not "
                     f"{p['player']}. Pass --force if deliberate.")

    conn.execute(
        "INSERT INTO evidence_events VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, obs["observation_id"], args.season, args.event,
         p["player_id"], p["player"], p["team"], json.dumps(payload),
         observed, effective, None,
         observed, None,          # knowledge starts when we learned it
         obs["source_tier"],
         args.confidence or "high", NORMALIZER_VERSION,
         "pending", args.supersedes, now()))

    if prior:
        # The earlier belief stops being held when the new one is LEARNED,
        # not when the new one's real-world change took effect. A clearance
        # reported at 15:00 and backdated to 09:00 does not mean a run at
        # 12:00 should have known about it.
        conn.execute(
            """UPDATE evidence_events SET effective_end_at=?,
               knowledge_end_at=?, review_status='superseded'
               WHERE evidence_event_id=?""",
            (effective, observed, args.supersedes))
    conn.commit()

    print(f"\n  {eid}")
    print(f"  {args.event}: {p['player']} ({p['team']}) "
          f"availability -> {args.availability}")
    print(f"  from {obs['observation_id']}  [{obs['source_id']}, "
          f"tier {obs['source_tier']}]")
    print(f"  observed  {observed[:19]}")
    print(f"  effective {effective[:19]}")
    if prior:
        print(f"  supersedes {args.supersedes}, whose interval now closes "
              f"at {effective[:19]}")
    print(f"\n  interpreted, not applied. Nothing has run:")
    print(f"    python3 scripts/evidence.py --apply {eid}")
    return 0


def points(row, fmt="ppr"):
    import projection_view as pv
    return pv.score(row, fmt)


def snapshot_players(conn, run_id, team=None):
    q = "SELECT * FROM run_projections WHERE run_id=?"
    a = [run_id]
    if team:
        q += " AND team=?"
        a.append(team)
    return {r["player_id"]: dict(r) for r in conn.execute(q, a)}


def ranks_for(conn, season, run_id, fmt="ppr"):
    """Positional rank per player, for one run and one scoring format."""
    rows = [dict(r) for r in conn.execute(
        """SELECT * FROM run_projections WHERE run_id=?
           AND (is_residual IS NULL OR is_residual=0)
           AND team NOT IN ('FA','FA/UNK','UNK','')""", (run_id,))]
    by_pos = {}
    for r in rows:
        r["_pts"] = points(r, fmt)
        by_pos.setdefault(r["position"], []).append(r)
    out = {}
    for pos, group in by_pos.items():
        group.sort(key=lambda x: (-x["_pts"], str(x["player_id"])))
        for i, r in enumerate(group, 1):
            out[r["player_id"]] = (f"{pos}{i}", r["_pts"])
    return out


def cmd_apply(conn, args):
    """Hand the event to the frozen engine and record what it did.

    This function creates a candidate. It does not, and cannot, publish one:
    there is no call in this module that writes published_snapshot.
    """
    ev = conn.execute(
        "SELECT * FROM evidence_events WHERE evidence_event_id=?",
        (args.apply,)).fetchone()
    if not ev:
        sys.exit(f"  no evidence event {args.apply}")

    tag, release_dir = engine_release(conn, args.engine_release)
    live_before = conn.execute(
        "SELECT run_id FROM published_snapshot WHERE season=?",
        (ev["season"],)).fetchone()
    if not live_before:
        sys.exit("  nothing published to compare a candidate against")
    before_run = live_before["run_id"]

    cutoff = utc(args.source_cutoff_at) or now()

    # Every event that was believed at the cutoff, not only the trigger.
    #
    # Applying one event to a fresh copy of the baseline produced a
    # projection of a world that never existed: Breece Hall out, then
    # Garrett Wilson out, and the second candidate had Hall back at 258.8
    # because his event was not included. --apply means "this triggered a
    # re-evaluation", not "this is the only thing that is true".
    applicable = [dict(r) for r in conn.execute(
        """SELECT * FROM evidence_events
           WHERE season=?
             AND COALESCE(knowledge_start_at, observed_at) <= ?
             AND (knowledge_end_at IS NULL OR knowledge_end_at > ?)
           ORDER BY COALESCE(knowledge_start_at, observed_at),
                    created_at, evidence_event_id""",
        (ev["season"], cutoff, cutoff))]
    if not any(x["evidence_event_id"] == ev["evidence_event_id"]
               for x in applicable):
        print(f"\n  {ev['evidence_event_id']} was not known at "
              f"{cutoff[:19]} and is not in this candidate.")

    # The engine runs in a sandbox, from the frozen release.
    #
    # This module has no publication code, which sounded like enough until
    # the first application: engine.py --run publishes, so calling it moved
    # the live pointer. Absence of a publish statement is worthless if you
    # invoke something that has one.
    workspace = Path(tempfile.mkdtemp(prefix="shadow-"))
    atexit.register(shutil.rmtree, workspace, True)
    sandbox = workspace / "shadow.db"
    snap = sqlite3.connect(sandbox)
    with snap:
        sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True).backup(snap)
    snap.close()

    frozen_engine = str(release_dir / "scripts" / "engine.py")
    injected = {}
    for x in applicable:
        pay = json.loads(x["payload_json"])
        r = subprocess.run(
            [sys.executable, frozen_engine,
             "--db", str(sandbox),
             "--add-event", x["event_type"],
             "--player", x["player"],
             "--availability", str(pay["availability"]),
             "--reason", f"evidence {x['evidence_event_id']}",
             "--source", f"tier {x['source_tier']}",
             # Knowledge time, not effective time. The engine decides
             # applicability by cutoff, and the cutoff is an information
             # boundary.
             "--observed-at", x["knowledge_start_at"] or x["observed_at"],
             "--force"],
            capture_output=True, text=True, cwd=release_dir)
        if r.returncode:
            sys.exit(f"  engine refused {x['evidence_event_id']}:\n"
                     + r.stdout + r.stderr)
        tok = r.stdout.split()
        injected[x["evidence_event_id"]] = tok[0] if tok else None

    run = subprocess.run(
        [sys.executable, frozen_engine,
         "--db", str(sandbox), "--run", "--source-cutoff-at", cutoff],
        capture_output=True, text=True, cwd=release_dir)
    out = run.stdout + run.stderr
    candidate = None
    for line in out.splitlines():
        if line.strip().startswith("candidate "):
            candidate = line.split()[-1]
    validation = "passed" if run.returncode == 0 else "failed"

    # Copy the candidate back for inspection. Never published_snapshot, and
    # never with a status that says published: the sandbox engine publishes
    # internally, and copying that verbatim created a second meaning of the
    # word in the live database.
    if candidate:
        sconn = sqlite3.connect(sandbox)
        sconn.row_factory = sqlite3.Row
        for table in ("projection_runs", "run_projections", "run_model_inputs",
                      "run_team_environment", "run_events",
                      "run_input_changes",
                      "run_reconciliation_adjustments",
                      "run_player_reconciliation", "model_events"):
            try:
                if table == "model_events":
                    rows = [dict(x) for x in sconn.execute(
                        f"SELECT * FROM {table}")]
                else:
                    rows = [dict(x) for x in sconn.execute(
                        f"SELECT * FROM {table} WHERE run_id=?", (candidate,))]
            except sqlite3.OperationalError:
                continue
            if not rows:
                continue
            have = {x[1] for x in conn.execute(f"PRAGMA table_info({table})")}
            if not have:
                # The production database predates these tables -- it was
                # frozen before the engine created them -- so take the
                # sandbox's own schema rather than inventing one.
                ddl = sconn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' "
                    "AND name=?", (table,)).fetchone()
                if not ddl or not ddl["sql"]:
                    continue
                conn.executescript(ddl["sql"])
                have = {x[1] for x in conn.execute(
                    f"PRAGMA table_info({table})")}
                if not have:
                    continue
            cols = [c for c in rows[0] if c in have]
            ph = ",".join("?" * len(cols))
            for row in rows:
                if table == "projection_runs" and "status" in row:
                    row["status"] = "shadow_validated"
                try:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {table} "
                        f"({','.join(cols)}) VALUES ({ph})",
                        [row[c] for c in cols])
                except sqlite3.OperationalError as exc:
                    sys.exit(f"  could not copy the candidate back: "
                             f"{table}: {exc}")
        sconn.close()
        conn.commit()

        conn.commit()


    aid = f"app-{uuid.uuid4().hex[:12]}"
    others = [x for x in applicable
              if x["evidence_event_id"] != ev["evidence_event_id"]]
    report = build_report(conn, ev, tag, candidate, cutoff, before_run,
                          validation, out, others)
    conn.execute(
        "INSERT INTO evidence_applications VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, ev["evidence_event_id"], tag, candidate, cutoff,
         "applied" if validation == "passed" else "rejected",
         validation, "unreviewed", None, None, report, now()))
    # Which events this candidate actually depended on, and the engine
    # event each became. One application, many evidence events.
    for x in applicable:
        conn.execute(
            "INSERT OR REPLACE INTO evidence_application_events "
            "VALUES (?,?,?,?)",
            (aid, x["evidence_event_id"], injected.get(x["evidence_event_id"]),
             "trigger" if x["evidence_event_id"] == ev["evidence_event_id"]
             else "active"))
    conn.execute("UPDATE evidence_events SET review_status='applied' "
                 "WHERE evidence_event_id=?", (ev["evidence_event_id"],))
    conn.commit()

    print(report)

    # The assertion this whole design exists to make.
    live_after = conn.execute(
        "SELECT run_id FROM published_snapshot WHERE season=?",
        (ev["season"],)).fetchone()
    moved = live_after["run_id"] != before_run
    print(f"\n  live pointer before  {before_run}")
    print(f"  live pointer after   {live_after['run_id']}")
    if moved:
        print(f"\n  THE LIVE POINTER MOVED. That should be impossible from "
              f"this module; something published outside it.")
        return 1
    print(f"  unchanged, as it must be: this module has no publication code.")
    print(f"\n  application {aid}")
    return 0


def build_report(conn, ev, tag, candidate, cutoff, before_run, validation,
                 out, others=()):
    """What a person needs to decide whether this candidate is right."""
    L = []
    obs = conn.execute(
        "SELECT * FROM evidence_observations WHERE observation_id=?",
        (ev["observation_id"],)).fetchone()
    payload = json.loads(ev["payload_json"])

    L.append("")
    L.append(f"  {ev['player']}  ({ev['team']})")
    L.append(f"  {ev['event_type']}")
    L.append("")
    L.append(f"  EVIDENCE")
    L.append(f"    source      {obs['source_id']}  (tier {obs['source_tier']})")
    if obs["source_url"]:
        L.append(f"    url         {obs['source_url'][:70]}")
    if obs["title"]:
        L.append(f"    headline    {obs['title'][:70]}")
    L.append(f"    observed    {ev['observed_at'][:19]} UTC")
    L.append(f"    effective   {ev['effective_start_at'][:19]} UTC")
    L.append(f"    observation {ev['observation_id']}")
    L.append(f"    event       {ev['evidence_event_id']}  (trigger)")
    if others:
        L.append(f"    also active at this cutoff:")
        for x in others:
            L.append(f"      {x['evidence_event_id']}  {x['player'][:22]:<22}"
                     f"{x['team']:<4} {x['event_type']}")

    L.append("")
    L.append(f"  MODEL INPUT")
    L.append(f"    availability -> {payload['availability']:.2f}")

    if not candidate:
        L.append("")
        L.append(f"  NO CANDIDATE PRODUCED")
        L.append(f"    {out.strip().splitlines()[-1] if out.strip() else ''}")
        return "\n".join(L)

    before = snapshot_players(conn, before_run, ev["team"])
    after = snapshot_players(conn, candidate, ev["team"])
    rb = ranks_for(conn, ev["season"], before_run)
    ra = ranks_for(conn, ev["season"], candidate)

    moves = []
    for pid, a in after.items():
        b = before.get(pid)
        if not b:
            continue
        for fmt in ("ppr",):
            d = points(a, fmt) - points(b, fmt)
            if abs(d) > 0.05:
                moves.append((abs(d), a["player"], points(b, "ppr"),
                              points(a, "ppr"),
                              points(b, "half"), points(a, "half"),
                              points(b, "standard"), points(a, "standard"),
                              rb.get(pid, ("-", 0))[0],
                              ra.get(pid, ("-", 0))[0]))
    moves.sort(reverse=True)

    L.append("")
    L.append(f"  PROJECTION IMPACT, {ev['team']}")
    L.append(f"    {'PLAYER':<24}{'PPR':>16}{'HALF':>16}{'STD':>16}")
    for m in moves[:8]:
        L.append(f"    {m[1][:24]:<24}"
                 f"{m[2]:>7.1f} ->{m[3]:>6.1f}"
                 f"{m[4]:>8.1f} ->{m[5]:>6.1f}"
                 f"{m[6]:>8.1f} ->{m[7]:>6.1f}")
    if len(moves) > 8:
        L.append(f"    … and {len(moves) - 8} more")

    rank_moves = [m for m in moves if m[8] != m[9]]
    if rank_moves:
        L.append("")
        L.append(f"  RANK IMPACT")
        for m in rank_moves[:8]:
            L.append(f"    {m[1][:24]:<24}{m[8]:>8} -> {m[9]}")

    # Every constraint, from the run's own frozen environment.
    L.append("")
    L.append(f"  RECONCILIATION")
    e = conn.execute(
        "SELECT * FROM run_team_environment WHERE run_id=? AND team=?",
        (candidate, ev["team"])).fetchone()
    if e:
        CATCH = ("RB", "FB", "WR", "TE")
        def s(field, pos=None):
            q = (f"SELECT COALESCE(SUM({field}),0) v FROM run_projections "
                 f"WHERE run_id=? AND team=?")
            a = [candidate, ev["team"]]
            if pos:
                q += " AND position IN (" + ",".join("?" * len(pos)) + ")"
                a += list(pos)
            return conn.execute(q, a).fetchone()["v"]
        checks = (
            ("QB attempts", s("pass_att", ("QB",)), e["pass_att"]),
            ("QB completions", s("completions", ("QB",)), e["completions"]),
            ("QB pass yards", s("pass_yds", ("QB",)), e["pass_yds"]),
            ("QB pass TDs", s("pass_td", ("QB",)), e["pass_td"]),
            ("targets", s("targets", CATCH),
             e["pass_att"] * (1 - e["non_target_rate"])),
            ("receptions", s("rec", CATCH), e["completions"]),
            ("receiving yards", s("rec_yds", CATCH), e["pass_yds"]),
            ("receiving TDs", s("rec_td", CATCH), e["pass_td"]),
            ("rush attempts", s("rush_att"), e["rush_att"]),
            ("rush TDs", s("rush_td"), e["rush_td"]),
        )
        ok = 0
        for label, got, want in checks:
            good = abs(got - want) <= max(0.6, abs(want) * 0.004)
            ok += good
            L.append(f"    {'pass' if good else 'FAIL':<5} {label:<18}"
                     f"{got:>10.1f} vs {want:>10.1f}")
        L.append(f"    {ok}/10 constraints")

    L.append("")
    L.append(f"  ENGINE")
    L.append(f"    release        {tag}")
    L.append(f"    baseline run   {before_run}")
    L.append(f"    candidate run  {candidate}")
    L.append(f"    source cutoff  {cutoff[:19]} UTC")
    L.append(f"    validation     {validation}")

    L.append("")
    L.append(f"  PUBLICATION")
    L.append(f"    SHADOW ONLY. The live projections are unchanged and this")
    L.append(f"    module contains no code that could change them.")
    return "\n".join(L)


def cmd_chain(conn, args):
    """The whole provenance chain for one player, source to candidate."""
    rows = conn.execute(
        """SELECT e.*, o.source_id, o.source_url, o.title, o.content_sha256,
                  o.source_tier AS tier, o.published_at AS obs_published
           FROM evidence_events e
           JOIN evidence_observations o ON o.observation_id = e.observation_id
           WHERE lower(e.player) LIKE lower(?)
           ORDER BY e.effective_start_at""",
        (f"%{args.chain}%",)).fetchall()
    if not rows:
        sys.exit(f"  no evidence for {args.chain!r}")
    for e in rows:
        live = "active" if not e["effective_end_at"] else "closed"
        print(f"\n  {e['player']} ({e['team']})  {e['event_type']}  [{live}]")
        print(f"    observation {e['observation_id']}")
        print(f"      {e['source_id']}  tier {e['tier']}")
        if e["title"]:
            print(f"      {e['title'][:66]}")
        print(f"      sha {e['content_sha256'][:24]}…")
        print(f"    event       {e['evidence_event_id']}")
        print(f"      payload   {e['payload_json']}")
        print(f"      effective {e['effective_start_at'][:19]}"
              + (f" to {e['effective_end_at'][:19]}"
                 if e["effective_end_at"] else " onwards"))
        print(f"      normalizer {e['normalizer_version']}")
        for a in conn.execute(
                """SELECT * FROM evidence_applications
                   WHERE evidence_event_id=? ORDER BY created_at""",
                (e["evidence_event_id"],)):
            print(f"    application {a['application_id']}")
            print(f"      engine    {a['engine_release_tag']}")
            print(f"      candidate {a['candidate_run_id']}")
            print(f"      cutoff    {a['source_cutoff_at'][:19]}")
            print(f"      status    {a['application_status']}, "
                  f"validation {a['validation_status']}, "
                  f"review {a['review_status']}")
    return 0


def cmd_report(conn, args):
    a = conn.execute(
        "SELECT * FROM evidence_applications WHERE application_id=?",
        (args.report,)).fetchone()
    if not a:
        sys.exit(f"  no application {args.report}")
    print(a["report_text"])
    return 0


def cmd_list(conn, args):
    obs = conn.execute(
        "SELECT COUNT(*) n FROM evidence_observations").fetchone()["n"]
    evs = conn.execute(
        "SELECT COUNT(*) n FROM evidence_events").fetchone()["n"]
    apps = conn.execute(
        "SELECT COUNT(*) n FROM evidence_applications").fetchone()["n"]
    print(f"\n  {obs} observations, {evs} events, {apps} applications\n")
    for r in conn.execute(
            """SELECT e.evidence_event_id, e.player, e.team, e.event_type,
                      e.effective_start_at, e.effective_end_at,
                      e.review_status,
                      (SELECT COUNT(*) FROM evidence_applications a
                       WHERE a.evidence_event_id = e.evidence_event_id) apps
               FROM evidence_events e ORDER BY e.created_at DESC LIMIT 20"""):
        state = "active" if not r["effective_end_at"] else "closed"
        print(f"    {r['evidence_event_id']}  {r['player'][:22]:<22}"
              f"{r['team']:<4} {state:<7} {r['apps']} applications")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2026)

    ap.add_argument("--observe", action="store_true")
    ap.add_argument("--source")
    ap.add_argument("--tier")
    ap.add_argument("--url")
    ap.add_argument("--external-id")
    ap.add_argument("--title")
    ap.add_argument("--text")
    ap.add_argument("--payload")
    ap.add_argument("--team")
    ap.add_argument("--published-at")

    ap.add_argument("--normalize", metavar="OBSERVATION_ID")
    ap.add_argument("--event")
    ap.add_argument("--player")
    ap.add_argument("--availability", type=float)
    ap.add_argument("--observed-at")
    ap.add_argument("--effective-at")
    ap.add_argument("--confidence")
    ap.add_argument("--supersedes")
    ap.add_argument("--force", action="store_true")

    ap.add_argument("--apply", metavar="EVIDENCE_EVENT_ID")
    ap.add_argument("--source-cutoff-at")
    ap.add_argument("--engine-release",
                    help="which frozen release to run, e.g. engine-1.0. "
                         "Required: a tested release is not necessarily the "
                         "deployed one.")

    ap.add_argument("--report", metavar="APPLICATION_ID")
    ap.add_argument("--chain", metavar="PLAYER")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    db = Path(args.db).expanduser()
    args.db_path = (ROOT / db).resolve() if not db.is_absolute() else db.resolve()
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if args.observe:
        return cmd_observe(conn, args)
    if args.normalize:
        return cmd_normalize(conn, args)
    if args.apply:
        return cmd_apply(conn, args)
    if args.report:
        return cmd_report(conn, args)
    if args.chain:
        return cmd_chain(conn, args)
    if args.list:
        return cmd_list(conn, args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
