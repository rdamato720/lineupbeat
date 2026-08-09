#!/usr/bin/env python3
"""Lock an engine version, and prove later that nothing moved under it.

    python3 scripts/freeze_release.py --tag engine-1.0 \
        --workbook ~/Downloads/2026_Fantasy_Football_Offense_v1.0_Reconciled.xlsx
    python3 scripts/freeze_release.py --verify
    python3 scripts/freeze_release.py --list

WHY A RELEASE IS A THING AND NOT A FEELING

The engine is correct today because a suite of ten tests says so against one
specific baseline. In four months somebody will ask whether a projection
changed because of evidence or because the code changed underneath it, and
"we tested it in August" is not an answer.

So a release records, together and immutably:

    the exact source of every engine file, by hash
    the workbook the baseline came from, by hash
    the published run that was live when it was frozen
    the model and scoring versions
    the test result, with its assertion count

--verify rehashes everything and reports what differs. A changed engine file
with an unchanged tag is the thing worth catching, and it is invisible
without this.

WHAT IT DOES NOT DO

Stop you changing things. It records what was true, so a later divergence is
a fact rather than an argument.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import shutil
import sqlite3
import tempfile
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The files that decide what a projection is. A change to any of them can
# move a number without any evidence arriving.
ENGINE_FILES = [
    "scripts/engine.py",
    "scripts/derive_inputs.py",
    "scripts/projection_runs.py",
    "scripts/projection_view.py",
    "scripts/import_projections.py",
    "scripts/add_residual.py",
    "scripts/set_rush_budget.py",
    "scripts/test_engine.py",
]

RELEASES = ROOT / "releases"

SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_releases (
  tag TEXT PRIMARY KEY,
  frozen_at TEXT,
  model_version TEXT,
  scoring_version TEXT,
  baseline_run_id TEXT,
  workbook_name TEXT,
  workbook_sha256 TEXT,
  baseline_db_sha256 TEXT,
  file_hashes_json TEXT,
  tests_passed INTEGER,
  test_assertions INTEGER,
  test_output TEXT,
  verification_status TEXT,
  release_dir TEXT,
  -- The manifest is what the artifact checks itself against. Anchoring its
  -- hash externally is what separates "this directory matches its own
  -- manifest" from "this is still the manifest recorded as engine-1.0".
  manifest_sha256 TEXT,
  note TEXT
);
"""


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact_hashes(rd: Path) -> dict:
    """Every preserved file except the manifest, by path relative to it.

    The manifest hashed the eight engine files, the workbook and the
    database. It did not hash the verifier that checks them, the README that
    tells you how, or the rosters and sources the engine reads at run time --
    which is to say the artifact could report itself intact while the thing
    it would replay had changed.

    If a file can affect a replay, it is part of the frozen identity.
    """
    out = {}
    for f in sorted(rd.rglob("*")):
        if f.is_dir() or f.name == "manifest.json":
            continue
        if "__pycache__" in f.parts or f.suffix == ".pyc":
            continue          # generated, never part of the identity
        out[str(f.relative_to(rd))] = sha(f)
    return out


def file_hashes() -> dict:
    out = {}
    for rel in ENGINE_FILES:
        p = ROOT / rel
        out[rel] = sha(p) if p.exists() else None
    return out


def verify_artifact(path: Path) -> int:
    """Check a release directory using nothing but itself.

    The registry-backed check needs the production database, which a copied
    artifact does not have -- and cannot have, since the baseline is
    snapshotted before the release row is written. So the artifact's own
    integrity check has to be answerable from the directory alone, or the
    command printed in its README does not work on the machine somebody
    copied it to.

    Reads manifest.json and rehashes everything it names. Opens no registry.
    """
    path = path.expanduser().resolve()
    mf = path / "manifest.json"
    if not mf.exists():
        print(f"  no manifest.json in {path}")
        print(f"  that is not a frozen release directory")
        return 1
    m = json.loads(mf.read_text())
    print(f"\n  {m.get('tag')}, frozen {str(m.get('frozen_at'))[:16]}")
    print(f"  {path}\n")

    problems = []

    # Every preserved file, not a chosen few. A roster or a source file can
    # change what a replay produces, and the verifier itself can change what
    # a check reports.
    # The shape a release promises, checked before its contents.
    #
    # The hash map proves the files that are here are unaltered. It cannot
    # notice a file that was never included: a release frozen without a
    # workbook has no workbook entry, so every hash matches and the artifact
    # reports itself intact while being unreproducible.
    REQUIRED = ("baseline.db", "workbook.xlsx", "README.txt",
                "scripts/test_engine.py", "scripts/freeze_release.py")
    for rel in REQUIRED:
        if not (path / rel).exists():
            print(f"    MISSING  {rel} (every release must contain it)")
            problems.append(f"required:{rel}")

    art = m.get("artifact_files") or {}
    if not art:
        print("    MISSING  manifest has no artifact hash map")
        problems.append("artifact_files")
    for rel in REQUIRED:
        if rel not in art:
            print(f"    MISSING  {rel} is not in the manifest hash map")
            problems.append(f"unhashed:{rel}")
    for rel, want in sorted(art.items()):
        f = path / rel
        if not f.exists():
            print(f"    MISSING  {rel}")
            problems.append(rel)
        elif sha(f) != want:
            print(f"    CHANGED  {rel}")
            problems.append(rel)

    # And nothing extra in the directories the engine reads at run time.
    present = {str(f.relative_to(path)) for f in path.rglob("*")
               if f.is_file() and f.name != "manifest.json"
               and "__pycache__" not in f.parts and f.suffix != ".pyc"}
    extra = sorted(present - set(art))
    for rel in extra:
        print(f"    EXTRA    {rel}")
        problems.append(f"extra:{rel}")

    if art and not problems:
        print(f"    ok       {len(art)} files, all hashes match")

    # A release only counts if its suite passed, and the manifest is the
    # only record of that inside the artifact.
    tests = m.get("tests") or {}
    if not tests.get("passed"):
        print(f"    FAIL     the manifest does not record a passing suite")
        problems.append("tests.passed")
    elif not tests.get("assertions"):
        print(f"    FAIL     the manifest records no assertion count")
        problems.append("tests.assertions")
    else:
        print(f"    ok       {tests['assertions']} assertions recorded passing")

    for want_file in ("README.txt",):
        if not (path / want_file).exists():
            print(f"    MISSING  {want_file}")
            problems.append(want_file)

    if problems:
        print(f"\n  {len(problems)} problems with this artifact")
        return 1
    print(f"\n  {m.get('tag')} is intact, verified from the directory alone")
    print(f"\n  replay it:")
    print(f"    cp baseline.db /tmp/replay.db")
    print(f"    python3 scripts/test_engine.py --db /tmp/replay.db")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--tag")
    ap.add_argument("--workbook")
    ap.add_argument("--note", default="")
    ap.add_argument("--verify", action="store_true",
                    help="is the preserved artifact intact?")
    ap.add_argument("--verify-artifact", metavar="PATH",
                    help="check a release directory using only itself. Works "
                         "on a copy, on another machine, with no registry.")
    ap.add_argument("--compare-working-tree", action="store_true",
                    help="does the current checkout match this release?")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    # Before opening anything: this mode is meant to work where there is no
    # database to open.
    if args.verify_artifact:
        return verify_artifact(Path(args.verify_artifact))

    # Resolved once, here, and used everywhere below.
    #
    # The script variously opened `args.db` and `ROOT / args.db`. Those are
    # the same file when run from the project root with the default, and
    # different files the moment somebody passes an absolute path or runs
    # from elsewhere -- so the snapshot and the registry could be two
    # different databases without anything saying so.
    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (ROOT / db_path).resolve()
    else:
        db_path = db_path.resolve()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if args.list:
        rows = conn.execute(
            "SELECT * FROM engine_releases ORDER BY frozen_at").fetchall()
        if not rows:
            sys.exit("  no releases recorded")
        print(f"\n  {len(rows)} releases\n")
        for r in rows:
            state = ("verified" if r["verification_status"] == "tested"
                     else r["verification_status"] or "unknown")
            print(f"    {r['tag']:<16} {r['frozen_at'][:16]}  {state} "
                  f"({r['test_assertions']} assertions)")
            print(f"      model {r['model_version']}, "
                  f"scoring {r['scoring_version']}")
            print(f"      baseline {r['baseline_run_id']}")
            if r["note"]:
                print(f"      {r['note']}")
        return

    if args.verify or args.compare_working_tree:
        # A named release, or the newest. Somebody investigating an August
        # projection needs the August release, not whatever was cut last.
        if args.tag:
            r = conn.execute("SELECT * FROM engine_releases WHERE tag=?",
                             (args.tag,)).fetchone()
            if not r:
                have = [x["tag"] for x in conn.execute(
                    "SELECT tag FROM engine_releases ORDER BY frozen_at")]
                sys.exit(f"  no release tagged {args.tag!r}. "
                         f"Known: {have or 'none'}")
        else:
            r = conn.execute("SELECT * FROM engine_releases "
                             "ORDER BY frozen_at DESC LIMIT 1").fetchone()
            if not r:
                sys.exit("  no releases recorded")

        print(f"\n  {r['tag']}, frozen {r['frozen_at'][:16]}\n")
        if r["verification_status"] != "tested":
            print(f"    recorded as {r['verification_status']!r}: the suite "
                  f"never ran against it, so it is not deployable")
            return 1

        problems = []
        stored = json.loads(r["file_hashes_json"])
        rd = Path(r["release_dir"]) if r["release_dir"] else None

        if args.compare_working_tree:
            # A different question from "is the artifact intact", and one
            # that will correctly fail the moment engine-1.1 exists. Asking
            # it about an old release during an investigation is not useful;
            # asking it about the current one before a deploy is.
            print(f"  does the working tree match {r['tag']}?\n")
            current = file_hashes()
            for rel, want in stored.items():
                got = current.get(rel)
                state = ("ok" if got == want else
                         "MISSING" if got is None else "CHANGED")
                print(f"    {state:<8} {rel}")
                if state != "ok":
                    problems.append(rel)
            if problems:
                print(f"\n  the checkout differs from {r['tag']} in "
                      f"{len(problems)} files.")
                print(f"  Expected while developing a newer version; a "
                      f"problem only if you are about to deploy {r['tag']}.")
                return 1
            print(f"\n  the working tree is exactly {r['tag']}")
            return 0

        # Artifact verification. Says nothing about the working tree, so it
        # keeps passing for engine-1.0 long after engine-1.1 is the checkout.
        print(f"  is the preserved artifact intact?\n")
        if not rd or not rd.exists():
            print(f"    MISSING  release directory {rd}")
            return 1

        mf = rd / "manifest.json"
        if not mf.exists():
            print(f"    MISSING  manifest.json")
            problems.append("manifest")
        else:
            # The externally anchored question. Standalone verification asks
            # whether the directory matches its own manifest; this asks
            # whether it is still the manifest recorded as this release.
            if r["manifest_sha256"]:
                if sha(mf) != r["manifest_sha256"]:
                    print(f"    CHANGED  manifest.json differs from the one "
                          f"recorded at freeze")
                    problems.append("manifest:sha")
                else:
                    print(f"    ok       manifest matches the recorded hash")
            m = json.loads(mf.read_text())
            # Every field, not the interesting ones. A release record and a
            # manifest that disagree about anything -- the note, the
            # timestamp, the assertion count -- means one of them was edited
            # after the freeze, and which field it was does not matter.
            for field, col in (("tag", "tag"),
                               ("frozen_at", "frozen_at"),
                               ("baseline_run_id", "baseline_run_id"),
                               ("workbook_name", "workbook_name"),
                               ("workbook_sha256", "workbook_sha256"),
                               ("baseline_db_sha256", "baseline_db_sha256"),
                               ("model_version", "model_version"),
                               ("scoring_version", "scoring_version"),
                               ("note", "note")):
                if (m.get(field) or None) != (r[col] or None):
                    print(f"    MISMATCH manifest {field}: "
                          f"{m.get(field)!r} vs recorded {r[col]!r}")
                    problems.append(f"manifest:{field}")
            tests = m.get("tests") or {}
            if not tests.get("passed") or not r["tests_passed"]:
                print(f"    MISMATCH manifest tests.passed vs the record")
                problems.append("manifest:tests.passed")
            if tests.get("assertions") != r["test_assertions"]:
                print(f"    MISMATCH manifest assertions "
                      f"{tests.get('assertions')} vs recorded "
                      f"{r['test_assertions']}")
                problems.append("manifest:tests.assertions")
            if m.get("engine_files") != stored:
                print(f"    MISMATCH manifest engine hashes")
                problems.append("manifest:engine_files")
            if not any(x.startswith("manifest") for x in problems):
                print(f"    ok       manifest agrees with the release record")

        # Every preserved file, from the manifest's own map.
        #
        # This checked the eight engine files, the workbook and the database,
        # which is a subset: the rosters and sources the engine reads at run
        # time, the README, and the verifier itself were all unguarded here
        # even though the standalone check covers them. The two modes should
        # differ in what anchors them, not in what they look at.
        art = (json.loads(mf.read_text()).get("artifact_files")
               if mf.exists() else {}) or {}
        if not art:
            print(f"    MISSING  manifest has no artifact hash map")
            problems.append("artifact_files")
        for rel, want in sorted(art.items()):
            f = rd / rel
            if not f.exists():
                print(f"    MISSING  {rel}")
                problems.append(rel)
            elif sha(f) != want:
                print(f"    CHANGED  {rel}")
                problems.append(rel)
        present = {str(f.relative_to(rd)) for f in rd.rglob("*")
                   if f.is_file() and f.name != "manifest.json"
                   and "__pycache__" not in f.parts and f.suffix != ".pyc"}
        for rel in sorted(present - set(art)):
            print(f"    EXTRA    {rel}")
            problems.append(f"extra:{rel}")
        if art and not any(x in art or x.startswith("extra:")
                           for x in problems):
            print(f"    ok       {len(art)} artifact files")

        # The engine-file map is kept separately because it is what
        # --compare-working-tree needs, and it is cross-checked here.
        for rel, want in stored.items():
            if art.get(rel) != want:
                print(f"    MISMATCH engine_files disagrees with "
                      f"artifact_files for {rel}")
                problems.append(f"map:{rel}")

        if problems:
            print(f"\n  {len(problems)} problems with the {r['tag']} artifact.")
            return 1
        print(f"\n  {r['tag']} is intact ({r['test_assertions']} assertions "
              f"when frozen)")
        print(f"\n  replay it from a copy, so the artifact stays pristine:")
        print(f"    cd {rd}")
        print(f"    cp baseline.db /tmp/replay.db")
        print(f"    PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_engine.py "
              f"--db /tmp/replay.db")
        return 0

    if not args.tag:
        sys.exit("  pass --tag, or --verify / --list")

    # First, before anything expensive. A frozen release is immutable,
    # so a second freeze under the same name is somebody about to lose
    # history, and they should be told now rather than after a minute
    # of tests.
    if conn.execute("SELECT frozen_at FROM engine_releases WHERE tag=?",
                    (args.tag,)).fetchone():
        sys.exit(f"  release {args.tag} already exists; cut a new version")
    if (RELEASES / args.tag).exists():
        sys.exit(f"  {RELEASES / args.tag} exists but is not a recorded "
                 f"release; remove it or choose another tag")

    # A release without its workbook is not the thing the contract promises.
    #
    # Not required=True on the option, because --verify and --list have no
    # use for it. Required here, on the freeze path, where a release with a
    # null workbook hash would pass every integrity check afterwards while
    # being unreproducible: the numbers everything derives from would be
    # missing and nothing would say so.
    if not args.workbook:
        sys.exit("  --workbook is required when freezing a release: the "
                 "artifact has to contain the numbers it derives from")

    # One pristine snapshot, taken now, used for everything after this line.
    #
    # The freeze used to take two: one to test against, and another minutes
    # later for the artifact. If a normal run published in between, the
    # release recorded "baseline run A, 71 assertions" while baseline.db
    # actually held run B -- and every hash check afterwards would pass,
    # because the wrong database had been faithfully hashed.
    #
    # S is the baseline and the artifact. T is a disposable copy of S for
    # the tests. Production is read once more at the very end, only to
    # confirm it has not moved underneath us.
    workspace = Path(tempfile.mkdtemp(prefix="release-freeze-"))
    # Registered rather than called at each exit point: there are several
    # sys.exit paths between here and the end, and one of them will be
    # forgotten.
    atexit.register(shutil.rmtree, workspace, True)
    S = workspace / "baseline-snapshot.db"
    snap = sqlite3.connect(S)
    with snap:
        sqlite3.connect(f"file:{db_path}?mode=ro", uri=True).backup(snap)
    snap.close()

    sconn = sqlite3.connect(S)
    sconn.row_factory = sqlite3.Row
    live = sconn.execute("SELECT * FROM published_snapshot WHERE season=?",
                         (args.season,)).fetchone()
    if not live:
        shutil.rmtree(workspace, ignore_errors=True)
        sys.exit("  nothing published; freeze a release against a live "
                 "baseline")
    run = sconn.execute("SELECT * FROM projection_runs WHERE run_id=?",
                        (live["run_id"],)).fetchone()
    live, run = dict(live), (dict(run) if run else None)
    sconn.close()
    print(f"  snapshot taken; baseline run {live['run_id']}")

    # No flag to skip this.
    #
    # A boolean that starts True and is only set False when tests run is
    # exactly how an untested release gets recorded as verified. A release
    # nobody tested is not a release.
    # Against a throwaway copy, never the live database.
    #
    # The suite creates events, publishes runs and restores from a snapshot
    # at the end. That is fine when it finishes; when a process is killed
    # halfway the restore never happens and synthetic events are left in the
    # real rankings. A regression test should not be able to reach
    # production at all.
    # A copy of S, so the tests and the artifact describe the same database.
    print("  running the regression suite against a copy of the snapshot\n")
    T = workspace / "verify.db"
    shutil.copy2(S, T)
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "test_engine.py"),
         "--db", str(T)],
        capture_output=True, text=True, cwd=ROOT)
    output = r.stdout + r.stderr
    assertions = output.count("    pass ")
    passed = r.returncode == 0 and "all tests pass" in output
    print(f"  {assertions} assertions, {'all pass' if passed else 'FAILURES'}")
    if not passed:
        for line in output.splitlines():
            if "FAIL" in line:
                print(f"    {line.strip()}")
        shutil.rmtree(workspace, ignore_errors=True)
        sys.exit("\n  refusing to freeze a release whose tests fail")

    hashes = file_hashes()
    missing = [k for k, v in hashes.items() if v is None]
    if missing:
        sys.exit(f"  engine files missing: {missing}")

    # A release is a directory, not a row.
    #
    # Recording a filename and a hash tells you what existed. Keeping the
    # files lets you replay it, and in four months the question is whether
    # an August projection is reproducible -- which has to be answerable
    # with a command rather than an argument.
    # The project layout, not a flat directory.
    #
    # Copying eight files into engine/ preserves their bytes and nothing
    # else: the scripts resolve each other and the database relative to a
    # project root, so nobody could enter the release four months later and
    # run anything. Preserving the shape means the artifact is replayable
    # rather than merely intact.
    rd = RELEASES / args.tag
    (rd / "scripts").mkdir(parents=True)
    for rel in ENGINE_FILES:
        shutil.copy2(ROOT / rel, rd / rel)
    # And the verifier itself. The README tells somebody to run
    # --verify-artifact from inside this directory, so the tool has to be in
    # it: an integrity check that requires the machine it was created on is
    # not an integrity check for a copy.
    shutil.copy2(ROOT / "scripts" / "freeze_release.py",
                 rd / "scripts" / "freeze_release.py")

    # The database the suite actually ran against.
    # S itself: the exact database the suite ran against, not a second read
    # of production taken minutes later.
    shutil.copy2(S, rd / "baseline.db")
    db_hash = sha(rd / "baseline.db")

    # Whatever else the engine reads at run time.
    for extra in ("rosters", "sources"):
        src = ROOT / extra
        if src.exists():
            shutil.copytree(src, rd / extra, dirs_exist_ok=True)

    wb_name = wb_hash = None
    if args.workbook:
        wb = Path(args.workbook).expanduser()
        if not wb.exists():
            shutil.rmtree(rd)
            sys.exit(f"  no workbook at {wb}")
        wb_name, wb_hash = wb.name, sha(wb)
        shutil.copy2(wb, rd / "workbook.xlsx")

    frozen_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "tag": args.tag, "frozen_at": frozen_at,
        "model_version": run["model_version"] if run else None,
        "scoring_version": run["scoring_version"] if run else None,
        "baseline_run_id": live["run_id"],
        "workbook_name": wb_name, "workbook_sha256": wb_hash,
        "baseline_db_sha256": db_hash, "engine_files": hashes,
        "tests": {"passed": True, "assertions": assertions},
        "note": args.note,
    }
    (rd / "README.txt").write_text(f"""\
{args.tag}, frozen {frozen_at[:19]} UTC

This directory is the engine as it stood, not a description of it. To replay
the regression suite exactly as it ran at the freeze:

    cd {rd.name}
    cp baseline.db /tmp/replay.db
    PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_engine.py --db /tmp/replay.db

Both parts matter. Copy the database, because the suite migrates the schema
and would edit the artifact. And suppress bytecode, because importing these
modules writes __pycache__ into scripts/ -- files that were not here at the
freeze, which the integrity check will then report as unexpected. Replaying
a release should not be able to break it. The suite migrates the schema and publishes runs, so pointing it
straight at baseline.db would edit the artifact and it would stop matching
its own manifest -- which is exactly what the integrity check is for.

That should report {assertions} assertions and "all tests pass". If it does
not, either the Python here differs from the Python there, or something in
this directory has been edited.

  baseline run     {live['run_id']}
  model version    {run['model_version'] if run else '-'}
  scoring version  {run['scoring_version'] if run else '-'}
  workbook         {wb_name or '-'}

To check this artifact has not been altered, from anywhere, with no
database and no project checkout:

    python3 scripts/freeze_release.py --verify-artifact .

From the original project, where the release registry lives, the stronger
check compares this directory against the recorded release:

    python3 scripts/freeze_release.py --verify --tag {args.tag}
""")

    # Last, once every other file exists: the manifest hashes the artifact,
    # so it cannot hash itself and nothing may be written after it.
    manifest["artifact_files"] = artifact_hashes(rd)
    (rd / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True))
    manifest_sha = sha(rd / "manifest.json")

    # The final check and the record, in one write transaction.
    #
    # Checking the pointer and then inserting leaves a window: a publisher
    # can move it between the SELECT and the COMMIT, and the release
    # completes against a baseline that stopped being live halfway through.
    # BEGIN IMMEDIATE takes SQLite's write reservation, so no other writer
    # can change the pointer between the comparison and the insert.
    #
    # Everything expensive -- the snapshot, 71 assertions, hashing, building
    # the artifact -- has already happened. The lock covers only this.
    try:
        conn.execute("BEGIN IMMEDIATE")
        now_live = conn.execute(
            "SELECT run_id FROM published_snapshot WHERE season=?",
            (args.season,)).fetchone()
        if not now_live or now_live["run_id"] != live["run_id"]:
            conn.execute("ROLLBACK")
            shutil.rmtree(rd, ignore_errors=True)
            sys.exit(f"  live snapshot changed during freeze; rerun release "
                     f"freeze\n    started against {live['run_id']}"
                     f"\n    live now       "
                     f"{now_live['run_id'] if now_live else 'nothing'}")
        conn.execute("INSERT INTO engine_releases VALUES "
                     "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (args.tag, frozen_at,
                      run["model_version"] if run else None,
                      run["scoring_version"] if run else None,
                      live["run_id"], wb_name, wb_hash, db_hash,
                      json.dumps(hashes, indent=1),
                      1, assertions, output[-8000:], "tested",
                      str(rd), manifest_sha, args.note))
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        shutil.rmtree(rd, ignore_errors=True)
        sys.exit(f"  could not record the release: {exc}")

    print(f"\n  froze {args.tag}\n")
    print(f"    baseline run    {live['run_id']}")
    print(f"    model version   {run['model_version'] if run else '—'}")
    print(f"    scoring version {run['scoring_version'] if run else '—'}")
    if wb_name:
        print(f"    workbook        {wb_name}")
        print(f"                    {wb_hash[:32]}…")
    print(f"    engine files    {len(hashes)}, hashed")
    print(f"    tests           {assertions} assertions passing")
    print(f"\n  Check it before any deployment:")
    print(f"    python3 scripts/freeze_release.py --verify")


if __name__ == "__main__":
    sys.exit(main() or 0)
