#!/usr/bin/env python3
"""Integrity checks for the provider-neutral league history prototype."""

from __future__ import annotations

import copy
import hashlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from league_history.demo import demo_history
from league_history.engine import summarize_history, validate_history


def close(actual: float, expected: float, tolerance: float = 0.051) -> None:
    assert abs(actual - expected) <= tolerance, (actual, expected)


def main() -> int:
    canonical = demo_history()
    validate_history(canonical)
    summary = summarize_history(canonical)

    assert summary["counts"] == {"seasons": 2, "franchises": 10, "games": 96}
    assert len({row["id"] for row in canonical["managers"]}) == 10
    assert len({row["id"] for row in canonical["franchises"]}) == 10
    assert [row["year"] for row in canonical["seasons"]] == [2024, 2025]
    assert canonical["seasons"][0]["complete"] is True
    assert canonical["seasons"][1]["complete"] is True

    rows = {row["manager"]: row for row in summary["franchises"]}
    alex = rows["Alex Morgan"]
    assert (alex["wins"], alex["losses"], alex["titles"], alex["seasons"]) == (12, 9, 1, 2)
    close(alex["pointsFor"], 2616.02, .001)
    close(alex["expectedWins"], 9.889, .001)
    close(alex["luck"], 2.111, .001)
    close(alex["allPlayPct"], .4709, .0001)
    close(alex["elo"], 1545.3)
    close(alex["peakElo"], 1545.3)

    records = summary["records"]
    assert records["highestWeek"]["score"] == 170.42
    assert records["lowestWeek"]["score"] == 86.1
    close(records["biggestBlowout"]["margin"], 67.96, .001)
    close(records["closestGame"]["margin"], 8.04, .001)
    close(records["highestScoringGame"]["total"], 326.6, .001)

    broken = copy.deepcopy(canonical)
    broken["franchises"][0]["managerId"] = "missing-manager"
    try:
        validate_history(broken)
    except ValueError as exc:
        assert "unknown manager" in str(exc)
    else:
        raise AssertionError("invalid franchise identity passed validation")

    command = [sys.executable, str(ROOT / "scripts/build_league_history.py")]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    outputs = [
        ROOT / "site/league-history/index.html",
        ROOT / "site/data/league-history-demo.json",
        ROOT / "site/assets/league-history-dashboard.js",
    ]
    first = [hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    second = [hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs]
    assert first == second

    page = outputs[0].read_text()
    dashboard = outputs[2].read_text()
    for required in ("League history", "Trophy case", "All-time leaders", "Managers",
                     "League records", "noindex,nofollow", "ESPN history import",
                     "Save manager matches", "LB_LEAGUE_HISTORY_SAVE_REVIEW_REQUEST",
                     "Are these the same person?", "Yes, same person",
                     "No, different people", "One quick step", "data-demo=",
                     "Your league history is ready",
                     "Every historical team and season is included automatically.",
                     "/assets/league-history-dashboard.js",
                     "document.body.classList.add('has-import')",
                     "canonical[find(row.identityId)]",
                     "headerSeasons.textContent=p.counts.seasons",
                     ".has-import .tabs,.has-import .panel,.has-import .lh-footer",
                     "--history-bg:#080c0b", ".tab[aria-selected=true]::after",
                     "<th>Record</th><th>Win%</th><th>PPG</th><th>Titles</th>",
                     'id="trophy-ledger"', 'id="head-to-head"',
                     'id="manager-detail"', 'id="record-book"', 'id="top-weeks"',
                     "trophy-stats", "trophy-stat.is-title strong",
                     'class="lh-atmosphere"', 'id="ambient-seasons"',
                     'id="ambient-games"', "background-size:72px 72px",
                     'id="publish-panel"', 'id="publish-league"',
                     'id="published-url"', 'name="league-visibility"',
                     'id="unpublish-league"', 'id="recovery-key-value"',
                     'id="restore-publication-access"',
                     'id="retry-shared-league"',
                     "Already published? Restore commissioner access",
                     ".shared-history.history-ready .import-card",
                     ".shared-history-error #retry-shared-league",
                     ".visibility-options{grid-template-columns:1fr}",
                     "96", "Prototype boundary", "fictional"):
        assert required in page, required
    assert "identity.seasons.join(', ')+' · '+identity.teamNames.join(' / ')" not in page
    assert "Merge into " not in page
    assert "MANAGER NAME" not in page
    assert "Review team history" not in page
    assert "ownership change" not in page.lower()
    assert "password" not in page.lower().replace('type="password"', '')
    assert "cookie" not in page.lower()
    for private_name in ("Adrian Chadzynski", "Ralph Damato", "Bobby Digital"):
        assert private_name not in page
    for required in ("summarize(payload, review)", "renderDashboard",
                     "renderTrophies", "renderAllTime", "renderManagers",
                     "renderSeasons", "renderRecords",
                     "const labels = ['#', 'Manager', 'Record', 'Win%', 'PPG', 'Titles']",
                     "element('details', 'team-history career-aliases')",
                     "renderHeadToHead", "renderManagerDetail", "renderTopWeeks",
                     "Most championships", "Best regular season",
                     "headToHead: series", "titleYears", "seasonStats",
                     "Private ESPN history · processed only in this browser.",
                     "publishLeague", "loadSharedLeague", "/api/leagues/",
                     "recoverPublicationAccess", "unpublishLeague",
                     "storePublication", "retryShared",
                     "checkPublication", "comparisonText",
                     "method: 'PATCH'", "method: 'DELETE'",
                     "Shared page is up to date.", "New ESPN data:",
                     "Update the shared page when ready.",
                     "Save the key under Recovery key.",
                     "This browser could not save access",
                     "Shared league history · view only.",
                     "Shared by your commissioner.",
                     "ESPN credentials and private league access are not included."):
        assert required in dashboard, required
    assert "innerHTML" not in dashboard
    subprocess.run(["node", "--check", str(outputs[2])], cwd=ROOT, check=True)
    subprocess.run(["node", str(ROOT / "scripts/test_league_history_dashboard.js")],
                   cwd=ROOT, check=True)
    print("league history calculations, identity, records, privacy and deterministic page: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
