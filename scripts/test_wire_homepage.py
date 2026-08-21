#!/usr/bin/env python3
"""The homepage replacement, checked against the rendered page.

    python3 scripts/test_wire_homepage.py

Disabling a renderer stops a thing being drawn. It does not stop it being
shipped, and a hidden report about a player a reviewer rejected is still a
report about him in the bytes a reader downloads. These checks read the
rendered payload, not the code that produced it.

The distinction that matters throughout: a roster row is identity data other
features need -- the photo id, the team code, the ADP -- and a nugget is a
retired report. Anthony Richardson may appear as the former and must never
appear as the latter.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "data" / "wire_homepage_replacement.html"
META = ROOT / "data" / "wire_homepage_replacement.json"
LIVE = ROOT / "site" / "index.html"
DECISIONS = ROOT / "data" / "reviews" / "backfill_decisions.json"

FAILURES = []
EXCLUDED = ("Anthony Richardson", "Ollie Gordon II", "Daniel Jones",
            "Terrace Marshall", "Travis Hunter")


def check(name, ok, detail=""):
    print(f"[{'  ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


if not PAGE.exists():
    print("  replacement preview not built; run wire_homepage_replacement.py")
    sys.exit(1)

html = PAGE.read_text()
meta = json.loads(META.read_text())
section = html.split('id="lbwire"', 1)[1].split('<main id="feed">')[0]


def payload(text):
    i = text.find("const DATA = ")
    if i < 0:
        return None
    j = text.find("\n", i)
    try:
        return json.loads(text[i + len("const DATA = "):j].rstrip(";"))
    except ValueError:
        return None


data = payload(html)
check("the homepage payload still parses", data is not None)

# --- the retired collection is gone from the payload, not merely hidden ---
nuggets = sum(len(s.get("nuggets") or []) for s in (data or {}).get("sports", {}).values())
check("the retired X-report collection is not embedded", nuggets == 0, f"{nuggets} nuggets")
check("no retired report markers remain", "dedupe_key" not in html)
check("the renderer is also disabled", "__LB_WIRE_REPLACEMENT__" in html)

# --- excluded names: never as a report, roster identity is fine ---
for who in EXCLUDED:
    check(f"{who} is absent from the replacement section", who not in section)
    reports = [n for s in (data or {}).get("sports", {}).values()
               for n in (s.get("nuggets") or [])
               if who.split()[-1] in str(n.get("player_name", ""))]
    check(f"{who} has no report in the Wire data payload", not reports)

# --- what other homepage features need must survive ---
players = (data or {}).get("players") or []
check("roster rows are preserved", len(players) > 2000, f"{len(players)} rows")
for field, why in (("espn", "player photos"), ("team", "team marks"),
                   ("pos", "position"), ("adp", "ADP display"),
                   ("rank", "positional rank"), ("name", "search")):
    have = sum(1 for p in players[:400] if field in p)
    check(f"roster rows keep {field} for {why}", have >= 380, f"{have}/400")
for fn in ("loadRoster", "DATA.players"):
    check(f"live homepage code still references {fn}", fn in html)

# --- the five cards ---
approved = [d for d in json.loads(DECISIONS.read_text())["decisions"].values()
            if str(d["action"]).startswith("APPROVE")]
names = ["Chris Blair"] + [d["subject"] for d in approved]
for n in names:
    check(f"{n} appears exactly once", section.count(f'>{n} <span class="wb">') == 1)
check("exactly five cards render",
      len(re.findall(r'<article class="wc"', section)) == 5,
      f"{len(re.findall(chr(60) + 'article class=' + chr(34) + 'wc' + chr(34), section))}")
check("the visible count reads 5 reviewed reports",
      ">5 reviewed reports<" in section)
check("the count equals the cards rendered",
      len(re.findall(r'<article class="wc"', section)) == meta["count_shown"])

for d in approved:
    esc = (d["edited_text"].replace("&", "&amp;").replace("<", "&lt;")
           .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;"))
    check(f"{d['subject']}: approved wording is byte-identical", esc in section)

# --- layout and filters ---
check("two columns at 1000px and up", "repeat(2,minmax(0,1fr))" in html)
check("never three columns in the section", "repeat(3" not in section)
check("the analysis is never clamped",
      "line-clamp" not in section and "text-overflow" not in section)
for f in ("All reports", "Trending up", "Trending down", "Worth noting",
          ">QB<", ">RB<", ">WR<", ">TE<", 'id="wteam"'):
    check(f"filter present: {f}", f in section)
check("filters target the rendered cards",
      'data-dir=' in section and 'data-pos=' in section and 'data-team=' in section)

# --- size: the two payloads must not both ship ---
# Compared against the homepage as it was before the replacement, banked at
# rollback time. Comparing against the live file stopped meaning anything the
# moment the replacement was applied to it.
BEFORE = ROOT / "data" / "rollback" / "index.homepage-before-replacement.html"
if BEFORE.exists():
    before = len(BEFORE.read_text())
    check("the homepage does not grow from carrying both feeds",
          len(html) < before, f"{len(html):,} vs {before:,} bytes before")
    before_data = payload(BEFORE.read_text())
    before_nuggets = sum(len(s.get("nuggets") or [])
                         for s in (before_data or {}).get("sports", {}).values())
    check("the retired collection was there before and is gone now",
          before_nuggets > 100 and nuggets == 0,
          f"{before_nuggets} -> {nuggets}")
    check("removing it saved real bytes",
          before - len(html) > 100_000, f"{before - len(html):,} bytes")

# --- rollback ---
snaps = sorted((ROOT / "data" / "wire_snapshots").glob("wire_publications.*.json")) \
    if (ROOT / "data" / "wire_snapshots").exists() else []
check("a publication snapshot exists for rollback", bool(snaps),
      str(snaps[-1].name) if snaps else "none")
check("the retired feed data still exists for rollback",
      (ROOT / "site" / "data" / "feed.json").exists()
      or (ROOT / "data" / "rollback").exists())
check("the retired renderer is preserved in source, not deleted",
      "All reports" in (ROOT / "site" / "template.html").read_text())
check("the replacement never writes to site/",
      "site/index.html" not in
      (ROOT / "scripts" / "wire_homepage_replacement.py").read_text()
      .split('OUT = ')[1][:200])

print()
if FAILURES:
    print(f"{len(FAILURES)} failed: " + ", ".join(FAILURES[:6]))
    sys.exit(1)
print("all passed")
