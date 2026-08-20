#!/usr/bin/env python3
"""Wire regressions, including the isolation guarantee. No network, no keys.

    python3 scripts/test_wire.py

The isolation half is the important part. The Wire is an editorial news
product and must never read a projection, a ranking, an ADP, a draft value, a
strength-of-schedule figure or a durability rating -- nor recommend that any
of them change. That is a claim about the code, so it is checked against the
code rather than remembered.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wire import registry
from wire.store import WireStore

FAILURES = []


def check(name, ok, detail=""):
    print(f"[{'  ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


# ---------------------------------------------------------------- isolation

FORBIDDEN_IMPORTS = re.compile(
    r"^\s*(?:from|import)\s+(beatwire|scripts)\b", re.M)
FORBIDDEN_NAMES = re.compile(
    r"beatwire\.db|projections\.xlsx|nfl_rankings|draft_value|adp_curve|"
    r"schedule_strength|durability|coaching\.csv|rosters/nfl\.csv", re.I)

wire_files = sorted((ROOT / "wire").glob("*.py"))
check("the wire package exists", len(wire_files) >= 3,
      f"{len(wire_files)} modules")

def code_only(src: str) -> str:
    """Strip docstrings and comments before scanning.

    These modules explain the isolation rule in prose, naming the very things
    they must not touch. Scanning raw text flags the explanation and passes
    the violation, which is the wrong way round.
    """
    tree = ast.parse(src)
    doc_spans = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            doc_spans.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in doc_spans:
            continue
        out.append(line.split("#", 1)[0])
    return "\n".join(out)


for f in wire_files:
    src = code_only(f.read_text())
    hits = FORBIDDEN_IMPORTS.findall(src)
    check(f"{f.name} imports nothing from the fantasy side", not hits, str(hits))
    named = [m for m in FORBIDDEN_NAMES.findall(src)]
    check(f"{f.name} names no fantasy data file", not named, str(named[:3]))

ingest = (ROOT / "scripts" / "wire_ingest.py").read_text()
review = (ROOT / "scripts" / "review_wire.py").read_text()
for name, src in (("wire_ingest.py", code_only(ingest)),
                  ("review_wire.py", code_only(review))):
    check(f"{name} imports nothing from beatwire",
          not re.search(r"^\s*(?:from|import)\s+beatwire\b", src, re.M))
    check(f"{name} names no fantasy data file",
          not FORBIDDEN_NAMES.search(src))

# The site build must not be able to read candidates.
build = (ROOT / "scripts" / "build_pages.py").read_text()
# `wire.db` is a substring of `beatwire.db`, so this needs a boundary or it
# reports the fantasy database as a Wire leak.
check("the site build never reads wire_candidates",
      "wire_candidates" not in build
      and not re.search(r"(?<!beat)wire\.db", build)
      and "wire_publications" not in build)

# ---------------------------------------------------------------- registry

sources = registry.load()
check("registry loads", len(sources) >= 10, f"{len(sources)} sources")
check("registry passes its own rules", not registry.problems(sources),
      str(registry.problems(sources)[:2]))

by_id = {s.source_id: s for s in sources}
blocked = [s for s in sources if s.status == registry.BLOCKED]
check("blocked sources are never pollable",
      all(not s.pollable for s in blocked), f"{len(blocked)} blocked")
check("blocked sources keep a recorded reason",
      all(s.blocked_reason or s.status == registry.BLOCKED for s in blocked))

# Manual submission is a route around missing discovery, never around a
# publisher's refusal. This is the rule the spec is most explicit about.
mass = by_id.get("masslive_mark_daniels")
check("a 403 publisher rejects manual submission too",
      mass is not None and not mass.manual_ok)
ath = by_id.get("the_athletic_dan_wiederer")
check("a paywalled publisher rejects manual submission too",
      ath is not None and not ath.manual_ok, ath.blocked_reason if ath else "")
bh = by_id.get("boston_herald_andrew_callahan")
check("a discovery-only gap still allows manual submission",
      bh is not None and bh.manual_ok and not bh.pollable)

# A site-wide feed without a filter would ingest a publisher's other desks.
loose = registry.Source(
    source_id="x", source_name="x", reporter_name="x", teams=["ARI"],
    domains=["example.com"], status=registry.AUTO_READY,
    reporting_type="LOCAL_BEAT", adapter=registry.FULL_TEXT_FEED,
    feed_url="https://example.com/feed", active=True, feed_scope="site")
check("an unfiltered site feed is rejected by the validator",
      any("no filter" in p for p in registry.problems([loose])))

check("url ownership matches on host, not substring",
      by_id["pewter_report"].owns("https://www.pewterreport.com/x/")
      and not by_id["pewter_report"].owns("https://evil.com/pewterreport.com/x"))

# ------------------------------------------------------------------- store

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "t.db"
    out = Path(tmp) / "pub.json"
    st = WireStore(db)

    st.add_candidate("c1", "i1", "pewter_report",
                     {"headline": "one", "canonical_url": "u1"}, "fp1")
    check("a new candidate lands in review, not published",
          len(st.candidates("EDITORIAL_REVIEW")) == 1
          and len(st.publications()) == 0)

    n, changed = st.export_publications(out)
    check("an unreviewed candidate reaches the published file never",
          n == 0 and json.loads(out.read_text())["publications"] == [])

    st.publish("c1", {"headline": "one", "canonical_url": "u1"}, "fp1")
    check("approving publishes exactly one item", len(st.publications()) == 1)

    st.add_candidate("c2", "i2", "pewter_report",
                     {"headline": "one, updated", "canonical_url": "u1b"}, "fp1")
    st.publish("c2", {"headline": "one, updated", "canonical_url": "u1b"}, "fp1")
    pubs = st.publications()
    check("the same event updates the card rather than adding a second",
          len(pubs) == 1 and pubs[0]["version"] == 2, f"v{pubs[0]['version']}")

    st.add_candidate("c3", "i3", "pewter_report",
                     {"headline": "other", "canonical_url": "u2"}, "fp2")
    st.publish("c3", {"headline": "other", "canonical_url": "u2"}, "fp2")
    check("a different event does get its own card", len(st.publications()) == 2)

    hist = st.history("fp1")
    check("every action is recorded in the audit trail", len(hist) >= 4,
          f"{len(hist)} entries for one event")

    n, changed = st.export_publications(out)
    check("the published file carries the approved items", n == 2 and changed)
    n2, changed2 = st.export_publications(out)
    check("an unchanged file is not rewritten", n2 == 2 and not changed2)

    try:
        st.set_state("c3", "NOT_A_STATE")
        check("an unknown state is refused", False)
    except ValueError:
        check("an unknown state is refused", True)

print()
if FAILURES:
    print(f"{len(FAILURES)} failed: " + ", ".join(FAILURES[:6]))
    sys.exit(1)
print("all passed")
