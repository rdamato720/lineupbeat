#!/usr/bin/env python3
"""The /nfl/wire page, checked against the built HTML.

    python3 scripts/test_wire_page.py

Asserting on the JSON would prove the data is right and say nothing about
what a reader sees. Every check here reads site/nfl/wire/index.html, because
the failure that matters -- a held record rendering, two blocks merging into
one, a dead source link -- happens in the markup or not at all.
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


if not PAGE.exists():
    print("  site/nfl/wire/index.html has not been built; run build_wire.py")
    sys.exit(1)

html = PAGE.read_text()
payload = json.loads(PUBS.read_text())
pubs = payload["publications"]
names = sorted(p["player_name"] for p in pubs)

check("the page was built", len(html) > 5000, f"{len(html):,} bytes")
# Whoever is approved right now, not a hardcoded pair: Anthony Richardson
# was retracted as REJECT_NOT_FANTASY_RELEVANT and a test naming him would
# have to be edited every time a reviewer changes their mind.
check("every published player carries a reviewer approval",
      all(p["reviewer_action"].startswith("APPROVE") for p in pubs), names)
check("Anthony Richardson is not published",
      "Anthony Richardson" not in names, names)
check("Chris Blair is published", "Chris Blair" in names, names)

cards = re.findall(r'<article class="wcard"', html)
check("one card per publication and no more",
      len(cards) == len(pubs), f"{len(cards)} cards, {len(pubs)} publications")

for p in pubs:
    who = p["player_name"]
    # Byte-identical commentary, allowing only HTML escaping.
    esc = (p["lineupbeat_impact"].replace("&", "&amp;").replace("<", "&lt;")
           .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;"))
    check(f"{who}: approved commentary is byte-identical",
          esc in html, p["lineupbeat_impact"][:48])
    check(f"{who}: appears exactly once as a card",
          html.count(f'>{p["player_name"]}</span>') == 1)
    check(f"{who}: direction label matches its structured direction",
          f'>{p["reader_label"]}</span>' in html,
          f'{p["direction"]} -> {p["reader_label"]}')
    check(f"{who}: source link is intact", p["url"] in html)
    check(f"{who}: reporter evidence is present",
          p["reporter_found"][:40].replace("'", "&#x27;") in html)

check("evidence and commentary are separate elements",
      'class="wrep"' in html and 'class="wimp"' in html
      and 'class="wrep"' != 'class="wimp"')
check("the two blocks are never merged into one element",
      not re.search(r'class="wrep"[^>]*>[^<]*class="wimp"', html))

for who in ("Quinn Ewers", "Joe Burrow", "Geno Smith", "Eli Heidenreich",
            "Mark Andrews", "Anthony Richardson"):
    check(f"refused player absent from /nfl/wire/: {who}", who not in html)

check("the disclosure is on the page", "How to read the Wire" in html)
check("the canonical url is /nfl/wire/",
      'rel="canonical" href="https://lineupbeat.com/nfl/wire/"' in html)
check("no projection change is stated when the action is NONE",
      html.count("No projection change") == sum(
          1 for p in pubs if p["projection_action"] == "NONE"))
check("evidence strength and horizon are secondary text",
      "Evidence strength" in html and 'class="wfoot"' in html)
check("the page links to no fantasy data file",
      not re.search(r"projections\.xlsx|nfl_rankings_2026|rosters/nfl\.csv",
                    html))

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
    check("the replacement section is present", 'id="lbwire"' in home)
    _mod = home.split('id="lbwire"')[1].split("<main id=\"feed\">")[0] \
        if 'id="lbwire"' in home else ""
    check("no retracted player is in the replacement section",
          "Anthony Richardson" not in _mod)
    check("the replacement section shows the approved players",
          all(p["player_name"] in _mod for p in pubs))
    check("homepage cards link to /nfl/wire/",
          home.count('href="/nfl/wire/"') >= 1,
          f'{home.count(chr(34) + "/nfl/wire/" + chr(34))} links')
    # The replacement section carries every approved report, not a
    # three-card teaser, so the old cap no longer applies. What matters is
    # that the count shown equals the cards rendered.
    import re as _re
    _cards = len(_re.findall(r'<article class="tile wire"', home))
    check("the replacement renders one card per publication",
          _cards == len(pubs), f"{_cards} cards, {len(pubs)} published")
    check("the homepage offers the full Wire", 'href="/nfl/wire/"' in home)

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "p.html"

    r = build('{"generated_at":"x","count":0,"publications":[]}', out)
    check("a zero-publication build succeeds", r.returncode == 0, r.stderr[-90:])
    empty = out.read_text() if out.exists() else ""
    check("the empty state is shown",
          "No reviewed reports are available yet" in empty)
    check("no filters are shown when there is nothing to filter",
          'id="fteam"' not in empty)

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
check("build_pages protects the wire directory from the stale-page prune",
      bool(_prot) and '"wire"' in _prot.group(1),
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
for _need, _label in [('"nfl" / "wire" / "index.html"', "the exact host path"),
                      ('"The NFL Wire"', "the page heading"),
                      ('wire_publications.json', "the published file")]:
    check(f"the artifact check asserts {_label}", _need in _va)
check("the artifact check names no player",
      "Chris Blair" not in _va and "Anthony Richardson" not in _va)
check("the artifact check resolves every homepage Wire link",
      "resolve(root, href)" in _va)

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
