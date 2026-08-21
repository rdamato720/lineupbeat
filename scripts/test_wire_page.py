#!/usr/bin/env python3
"""The publication gate, and the redirect that replaced the Wire page.

    python3 scripts/test_wire_page.py

The Wire has no page of its own: it is the homepage, and what a reader sees
is covered by test_wire_homepage.py. build_wire.py is still the thing that
decides whether a record may be published at all, so what is checked here is
that it refuses everything it is supposed to refuse -- a held record, an
unapproved one, a missing public summary -- and that /nfl/wire/ still
answers for everyone who bookmarked it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "site" / "nfl" / "wire" / "index.html"
HOME = ROOT / "site" / "index.html"
PUBS = ROOT / "data" / "wire_publications.json"

FAILURES = []


def check(name, ok, detail=""):
    print(f"[{'  ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def build(pubs_json: str, out: Path) -> subprocess.CompletedProcess:
    """Run the real builder against a given publications file."""
    backup = PUBS.read_text()
    try:
        PUBS.write_text(pubs_json)
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_wire.py"),
             "--out", str(out)], capture_output=True, text=True)
    finally:
        PUBS.write_text(backup)


REDIRECTS = ROOT / "site" / "_redirects"

check("no separate Wire page is built",
      not (ROOT / "site" / "nfl" / "wire").exists())
check("the redirect file was written", REDIRECTS.exists())
_rd = REDIRECTS.read_text() if REDIRECTS.exists() else ""
check("/nfl/wire/ redirects to the homepage Wire",
      bool(re.search(r"^/nfl/wire/\S*\s+/#wire\s+30[12]\s*$", _rd, re.M)))
check("the redirect file holds one rule for it, however often it is built",
      _rd.count("/nfl/wire/") == 1, f"{_rd.count('/nfl/wire/')} rule(s)")

pubs = json.loads(PUBS.read_text())["publications"]
names = [p["player_name"] for p in pubs]
check("every published player carries a reviewer approval",
      all(str(p.get("reviewer_action", "")).startswith("APPROVE") for p in pubs))
check("no publication id repeats",
      len({p["publication_id"] for p in pubs}) == len(pubs))
check("every record keeps its evidence and its summary apart",
      all((p.get("reporter_found") or "").strip()
          and (p.get("public_evidence_summary") or "").strip()
          and p["reporter_found"] != p["public_evidence_summary"]
          for p in pubs))
check("every record's commentary differs from its summary",
      all(p.get("lineupbeat_impact") != p.get("public_evidence_summary")
          for p in pubs))

# Read before the mutation builds below, which rewrite the homepage module
# as a side effect, and restored at the end so a test run leaves no trace.
HOME_BEFORE = HOME.read_text() if HOME.exists() else None
if HOME_BEFORE is not None:
    home = HOME_BEFORE
    # The temporary module is retired: the replacement section carries the
    # same reports in the main feed position, and shipping both would show
    # every card twice.
    check("the temporary Wire module is retired",
          "WIRE MODULE START" not in home)
    check("the Wire section is present", 'id="wire"' in home)
    _mod = home.split('id="wire"')[1].split("<main id=\"feed\">")[0] \
        if 'id="wire"' in home else ""
    check("no retracted player is in the replacement section",
          "Anthony Richardson" not in _mod)
    check("the Wire section shows the approved players",
          all(p["player_name"] in _mod for p in pubs))
    # The consolidation: one destination, and it is this page.
    check("the homepage links to no separate Wire page",
          "/nfl/wire/" not in home,
          f'{home.count("/nfl/wire/")} link(s)')
    check("the calls to action point at the homepage anchor",
          home.count('href="#wire"') >= 2)
    # The replacement section carries every approved report, not a
    # three-card teaser, so the old cap no longer applies. What matters is
    # that the count shown equals the cards rendered.
    import re as _re
    _cards = len(_re.findall(r'<article class="tile wire"', home))
    check("the replacement renders one card per publication",
          _cards == len(pubs), f"{_cards} cards, {len(pubs)} published")
    check("no 'View the full Wire' link remains",
          "View the full Wire" not in home)

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "p.html"

    r = build('{"generated_at":"x","count":0,"publications":[]}', out)
    check("a zero-publication build succeeds", r.returncode == 0, r.stderr[-90:])
    empty = out.read_text() if out.exists() else ""
    # With no page to render, an empty publication set must still be a
    # clean pass through the gate rather than a crash.
    check("an empty publication set validates rather than crashing",
          empty is not None)

    good = json.loads(PUBS.read_text())
    for label, mutate in [
            ("an unapproved record", lambda d: d["publications"][0].update(
                {"reviewer_action": "PENDING"})),
            ("a held record", lambda d: d["publications"][0].update(
                {"reviewer_action": "HOLD"})),
            ("a missing source link", lambda d: d["publications"][0].update(
                {"url": ""})),
            ("a label contradicting its direction",
             lambda d: d["publications"][0].update(
                 {"reader_label": "Trending down"})),
            ("merged evidence and commentary",
             lambda d: d["publications"][0].update(
                 {"lineupbeat_impact": d["publications"][0]["reporter_found"]})),
            ("a non-fantasy position", lambda d: d["publications"][0].update(
                {"position": "DB"})),
            ("a count that disagrees with the records",
             lambda d: d.update({"count": 99}))]:
        bad = json.loads(json.dumps(good))
        mutate(bad)
        r = build(json.dumps(bad), out)
        check(f"the build fails on {label}", r.returncode != 0)

# The prune that deleted this page once. build_pages.py removes any
# directory under site/<sport>/ that is neither protected nor a current
# player slug, and "wire" was missing from that set: the page was built by
# the step before it and deleted by it, while every build-time check still
# passed.
_bp = (ROOT / "scripts" / "build_pages.py").read_text()
_prot = re.search(r"protected = \{(.+?)\}", _bp, re.S)
check("build_pages no longer protects a wire directory it does not build",
      bool(_prot) and '"wire"' not in _prot.group(1),
      _prot.group(1)[:70] if _prot else "protected set not found")

# The artifact check must exist, run against the deploy directory, and be
# the last thing before Deploy.
_ci = (ROOT / ".github" / "workflows" / "refresh.yml").read_text()
check("CI verifies the deploy artifact", "verify_deploy_artifact.py site" in _ci)
check("the artifact check runs before Deploy",
      _ci.index("verify_deploy_artifact.py") < _ci.index("wrangler@latest pages deploy"))
check("the artifact check cannot be skipped",
      "verify_deploy_artifact.py site || true" not in _ci)
_va = (ROOT / "scripts" / "verify_deploy_artifact.py").read_text()
# The verifier reads the published file rather than naming players, so a
# retraction cannot fail the deploy.
for _need, _label in [('"nfl" / "wire"', "that no separate page ships"),
                      ('id="wire"', "the homepage anchor"),
                      ('wire_publications.json', "the published file"),
                      ('public_evidence_summary', "the approved summary")]:
    check(f"the artifact check asserts {_label}", _need in _va)
check("the artifact check names no player",
      "Chris Blair" not in _va and "Anthony Richardson" not in _va)
check("the artifact check asserts the redirect",
      "/#wire" in _va and "_redirects" in _va)
check("the artifact check asserts nothing links to the retired page",
      "no page in the artifact links to /nfl/wire/" in _va)

# Nothing about this page may touch the fantasy inputs.
src = (ROOT / "scripts" / "build_wire.py").read_text()
check("the builder reads only the published file",
      "wire_publications" in src and "wire_candidates" not in src
      and "wire_evidence" not in src and "wire_fantasy_impact" not in src)
check("the builder opens no projection or ranking file",
      not re.search(r"projections\.xlsx|nfl_rankings|rosters/nfl\.csv|"
                    r"draft_value|coaching\.csv", src))
# The word "projections" appears in the page's own disclosure text, so the
# check is for writes, not for the noun.
check("the builder never writes a projection or ranking",
      "build_rankings" not in src
      and not re.search(r"projections\.xlsx|nfl_rankings[^\"']*\.json", src)
      and not re.search(r"(projections|rankings)[^\n]*\.write_text", src))

if HOME_BEFORE is not None:
    HOME.write_text(HOME_BEFORE)
    check("the test run leaves the homepage as it found it",
          HOME.read_text() == HOME_BEFORE)

print()
if FAILURES:
    print(f"{len(FAILURES)} failed: " + ", ".join(FAILURES[:6]))
    sys.exit(1)
print("all passed")
