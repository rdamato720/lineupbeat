#!/usr/bin/env python3
"""Every readiness gate, evaluated from stored state.

    python3 scripts/wire_readiness.py

ACCELERATED_DISCOVERY_READY replaces the 72-hour prelaunch requirement: at
least three successful runs spread across at least four elapsed hours, with
every team checked in every run. The 72-hour measurement continues after
launch as monitoring.

Elapsed time is read from the recorded observations and never adjusted. A
gate that can be satisfied by editing a timestamp is not a gate.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import coverage
from wire import registry as artreg
from wire import si
from wire.store import WireStore

WINDOW = Path("data/wire_discovery_window.json")
MIN_RUNS = 3
MIN_HOURS = 4.0
PREFERRED_HOURS = 12.0


def extraction_accounting(rows) -> dict:
    """Separate intentional editorial refusals from extraction attempts."""
    good = failed = refused = 0
    for row in rows:
        count = int(row["c"])
        status = row["extraction_status"]
        note = row["note"] or ""
        if status == "COMPLETE":
            good += count
        elif note.startswith("official team site:"):
            refused += count
        else:
            failed += count
    attempted = good + failed
    return {"complete": good, "failed": failed, "refused": refused,
            "rate": round(100 * good / max(1, attempted), 1)}


def reviewed_publications_valid(payload: dict) -> bool:
    rows = payload.get("publications") or []
    return (payload.get("count") == len(rows)
            and all(str(r.get("reviewer_action", "")).startswith("APPROVE")
                    and r.get("public_evidence_summary_approved_by")
                    for r in rows))


def discovery_gate(store) -> tuple[bool, list[str], dict]:
    fails, facts = [], {}
    obs = (json.loads(WINDOW.read_text())["observations"]
           if WINDOW.exists() else [])
    facts["runs"] = len(obs)
    if obs:
        first = datetime.fromisoformat(obs[0]["observed_at"])
        hours = (datetime.now(timezone.utc) - first).total_seconds() / 3600
    else:
        hours = 0.0
    facts["elapsed_hours"] = round(hours, 2)
    facts["preferred_met"] = hours >= PREFERRED_HOURS

    if len(obs) < MIN_RUNS:
        fails.append(f"only {len(obs)} discovery run(s); {MIN_RUNS} required")
    if hours < MIN_HOURS:
        fails.append(f"only {hours:.1f}h elapsed; {MIN_HOURS}h is the hard minimum")

    teams = sorted(si.CODE_TO_SLUG)
    for o in obs:
        missing = [t for t in teams if t not in o["teams"]]
        if missing:
            fails.append(f"run at {o['observed_at']} missed {len(missing)} team(s)")
    facts["teams_checked_every_run"] = bool(obs) and not any(
        [t for t in teams if t not in o["teams"]] for o in obs)

    rows = [dict(r) for r in store.evidence()
            if r["review_status"] in ("PENDING", "APPROVED")]
    srcs = {s.source_id: s for s in artreg.load()}

    def team_of(r):
        s = srcs.get(r["source_id"])
        return s.teams[0] if s and s.teams else r["team"]

    producing = {team_of(r) for r in rows if not r["duplicate_of"]}
    facts["teams_producing"] = len(producing & set(teams))
    if facts["teams_producing"] < 32:
        fails.append(f"{facts['teams_producing']}/32 teams produce candidates")

    wrong = [r for r in rows if r["team"] and team_of(r)
             and r["team"] != team_of(r)]
    facts["wrong_team_candidates"] = len(wrong)
    if wrong:
        fails.append(f"{len(wrong)} wrong-team candidate(s)")

    paid_ids = {s.source_id for s in srcs.values() if s.paid}
    paid_rows = [r for r in rows if r["source_id"] in paid_ids]
    facts["paid_candidates"] = len(paid_rows)
    if paid_rows:
        fails.append(f"{len(paid_rows)} candidate(s) from a paid source")

    items = store.conn.execute(
        "SELECT extraction_status, note, COUNT(*) c FROM wire_source_items "
        "GROUP BY extraction_status, note").fetchall()
    extraction = extraction_accounting(items)
    facts["extraction_rate"] = extraction["rate"]
    facts["intentional_refusals"] = extraction["refused"]
    facts["extraction_failures"] = extraction["failed"]
    if extraction["complete"] + extraction["failed"] and \
            extraction["rate"] < 90:
        fails.append(f"extraction {facts['extraction_rate']}% below 90%")

    facts["duplicates_linked"] = sum(1 for r in rows if r["duplicate_of"])
    facts["underlying_reports_linked"] = len(
        {r["underlying_report_id"] for r in rows if r["underlying_report_id"]})
    return (not fails), fails, facts


def main():
    store = WireStore()
    cov = coverage.summary()
    auth = si.load_authors()
    fh_teams = sorted(t for t, e in auth.get("teams", {}).items()
                      if any(a["classification"] == "FIRSTHAND_APPROVED"
                             for a in e["authors"].values()))

    ok_disc, disc_fails, facts = discovery_gate(store)
    impacts = store.impacts()
    review_built = Path("data/wire_fantasy_review.json").exists()
    audit = Path("data/wire_authority_audit.json")
    unauth = (json.loads(audit.read_text())["unauthorised_candidates"]
              if audit.exists() else None)
    snaps = sorted(Path("data/wire_snapshots").glob("wire_publications.*.json")) \
        if Path("data/wire_snapshots").exists() else []
    pubs = json.loads(Path("data/wire_publications.json").read_text()) \
        if Path("data/wire_publications.json").exists() else {"count": 0}

    print("  ACCELERATED_DISCOVERY_READY")
    print(f"    runs {facts['runs']}  elapsed {facts['elapsed_hours']}h "
          f"(minimum {MIN_HOURS}h, preferred {PREFERRED_HOURS}h"
          f"{' -- met' if facts['preferred_met'] else ' -- not met'})")
    print(f"    every team checked each run : {facts['teams_checked_every_run']}")
    print(f"    teams producing candidates  : {facts['teams_producing']}/32")
    print(f"    wrong-team candidates       : {facts['wrong_team_candidates']}")
    print(f"    paid-source candidates      : {facts['paid_candidates']}")
    print(f"    full-text extraction        : {facts['extraction_rate']}%")
    print(f"    intentional content refusals: {facts['intentional_refusals']}")
    print(f"    extraction failures         : {facts['extraction_failures']}")
    print(f"    duplicates linked           : {facts['duplicates_linked']}")
    print(f"    underlying reports linked   : {facts['underlying_reports_linked']}")
    print(f"    -> {'PASS' if ok_disc else 'FAIL'}")
    for f in disc_fails:
        print(f"       blocker: {f}")

    mins = [
        ("ACCELERATED_DISCOVERY_READY passes", ok_disc,
         "; ".join(disc_fails)),
        ("32/32 teams produce eligible candidates",
         facts["teams_producing"] == 32,
         f"{facts['teams_producing']}/32"),
        ("15+ teams with a verified firsthand authority",
         len(fh_teams) >= 15, f"{len(fh_teams)} teams"),
        ("every team has a non-team-owned source",
         len(cov["with_non_team_owned"]) == 32,
         f"{len(cov['with_non_team_owned'])}/32"),
        ("extraction at least 90%", facts["extraction_rate"] >= 90,
         f"{facts['extraction_rate']}%"),
        ("wrong-team contamination zero",
         facts["wrong_team_candidates"] == 0, ""),
        ("paid content contributes zero evidence",
         facts["paid_candidates"] == 0, ""),
        ("authority restrictions hold", unauth == 0,
         f"{unauth} unauthorised" if unauth else ""),
        ("fantasy dry run built", review_built, ""),
        ("rollback snapshot banked", bool(snaps),
         str(snaps[-1]) if snaps else "none"),
        ("reviewed publication set is valid", reviewed_publications_valid(pubs),
         f"{pubs.get('count', 0)} reviewed publication(s)"),
    ]
    print("\n  MINIMUM_SWITCH_READY")
    for name, ok, note in mins:
        print(f"    [{'PASS' if ok else 'FAIL'}] {name:<46}{note}")
    print("    [HOLD] FANTASY_SPIN_REVIEW_READY               "
          "awaiting your review of the preview")

    full_ok = len(cov["with_two_full_text"]) >= 24
    print("\n  FULL_COVERAGE_READY")
    print(f"    [{'PASS' if full_ok else 'FAIL'}] 24+ teams with two full-text "
          f"sources           {len(cov['with_two_full_text'])}/32")
    print("    [HOLD] every minimum requirement                "
          "gated on the fantasy review")

    print(f"\n  coverage: {cov['source_counts']}")
    print(f"    teams with an independent local source : "
          f"{len(cov['with_independent_local'])}")
    print(f"    teams without one                      : "
          f"{len(cov['without_independent_local'])}")
    print(f"    teams relying on On SI alone           : "
          f"{len(cov['onsi_only_non_team_owned'])}")
    print(f"    firsthand teams ({len(fh_teams)}): {', '.join(fh_teams)}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
