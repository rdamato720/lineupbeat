#!/usr/bin/env python3
"""The review package renders each card's OWN identity.

    python3 scripts/test_wire_package.py

Written after a stale-variable defect put one player's registry identity on
all sixty cards. The card loop read `ir`, a name left behind by an earlier
loop over the same list, so every card showed whichever candidate happened to
be last. It rendered cleanly and was wrong on every row, which is why these
assert on the built artefact rather than on the builder's intentions.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "data" / "wire_review_package.html"
JSON = ROOT / "data" / "wire_review_package.json"

FAILURES = []


def check(name, ok, detail=""):
    print(f"[{'  ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


if not HTML.exists() or not JSON.exists():
    sys.exit("  build the package first: python3 scripts/wire_review_package.py")

html = HTML.read_text()
pkg = json.loads(JSON.read_text())
items = pkg["items"]

ids = re.findall(r"stable player id</b><span>([^<]*)</span>", html)
names = re.findall(r"registry name</b><span>([^<]*)</span>", html)
teams = re.findall(r"registry team</b><span>([^<]*)</span>", html)
poss = re.findall(r"registry position</b><span>([^<]*)</span>", html)
heads = [re.sub(r"<[^>]+>", "", m).strip()
         for m in re.findall(r"<h3>(.*?)</h3>", html, re.S)]

check("one identity block per card",
      len(ids) == len(items), f"{len(ids)} blocks, {len(items)} items")

# The defect's signature: many cards, one identity.
check("more than one distinct registry identity appears",
      len(set(ids)) > 1, f"{len(set(ids))} distinct id(s)")
worst, n = Counter(ids).most_common(1)[0] if ids else ("", 0)
check("no single identity populates most of the package",
      n < max(2, len(ids) // 2), f"{worst} appears {n}x of {len(ids)}")

# The three the reviewer proposed as cards must be three different people.
THREE = ("Dak Prescott", "Dameon Pierce", "Jonah Coleman")
got = {}
for it in items:
    if it["player"] in THREE:
        got[it["player"]] = it["registry_identity"].get("player_id", "")
present = [p for p in THREE if p in got]
check("the proposed cards are present", len(present) == len(THREE),
      f"found {present}")
if len(present) == len(THREE):
    check("they display three DISTINCT player ids",
          len({got[p] for p in THREE}) == 3,
          ", ".join(f"{p}={got[p]}" for p in THREE))

# Every heading agrees with the identity rendered beneath it.
mismatched = []
for it in items:
    rid = it["registry_identity"]
    if rid.get("player_name") and rid["player_name"] != it["player"]:
        mismatched.append(f"{it['player']} vs {rid['player_name']}")
check("every card heading matches its registry name",
      not mismatched, "; ".join(mismatched[:3]))

for label, seq, key in (("team", teams, "team"), ("position", poss, "position")):
    bad = [f"{it['player']}" for it, v in zip(items, seq)
           if it["registry_identity"].get(key)
           and it["registry_identity"][key] != v]
    check(f"every card heading matches its registry {label}",
          not bad, "; ".join(bad[:3]))

# The builder must refuse rather than render a package like the broken one.
src = (ROOT / "scripts" / "wire_review_package.py").read_text()
check("the builder refuses when a heading and identity disagree",
      "card heading disagrees with registry identity" in src)
check("the builder refuses a package with a single repeated identity",
      "a stale identity is populating them" in src)

# No loop-external name may reach a card. `ir` is the one that did.
card_block = src[src.index("    cards = []"):]
check("the card loop reads identity from the current item",
      'rid = it["registry_identity"]' in card_block)
check("no stale loop variable populates a card",
      "ir.get(\"supplied_identity\")" not in card_block
      and "ir.get('supplied_identity')" not in card_block)

print()
if FAILURES:
    print(f"{len(FAILURES)} failed: " + ", ".join(FAILURES[:5]))
    sys.exit(1)
print("all passed")
