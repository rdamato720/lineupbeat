#!/usr/bin/env python3
"""The homepage replacement, checked against the rendered page.

    python3 scripts/test_wire_homepage.py

Disabling a renderer stops a thing being drawn. It does not stop it being
shipped, and a hidden report about a player a reviewer rejected is still a
report about him in the bytes a reader downloads. These checks read the
rendered payload, not the code that produced it.

The distinction that matters throughout is which system a record belongs to.
feed.json powers Recent News, Moving Now, My Roster and search;
wire_publications.json powers the Wire section and nothing else reads it. The
Wire replaces one renderer -- All reports -- and must leave the feed beneath
it alone. It once emptied that collection on the way past, which is what
Recent News and Moving Now render from, and both shipped blank.

So a player a Wire reviewer rejected may still appear in Recent News: that is
a legitimate X-wire report about him, not a hidden Wire card. What he may
never be is a card in the replacement section.
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
section = html.split('id="wire"', 1)[1].split('<main id="feed">')[0]


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

# --- the feed beneath the replacement survives it ---
nuggets = [n for s_ in (data or {}).get("sports", {}).values()
           for n in (s_.get("nuggets") or [])]
check("the feed records are still in the payload", bool(nuggets),
      f"{len(nuggets)} report(s)")
check("the renderer is disabled without emptying the feed",
      "__LB_WIRE_REPLACEMENT__" in html and bool(nuggets))

# The two sections that read the same collection. Each needs its mount point
# and something to put in it; both shipped blank when the collection went.
resolved = [n for n in nuggets if n.get("resolved")]
check("Recent News has its mount point", 'id="livelist"' in html)
check("Recent News has items to render", bool(resolved),
      f"{len(resolved)} resolved report(s)")
check("Moving Now has its mount point", 'id="trending"' in html)
check("Moving Now has more than one player to rank",
      len({n.get("player_id") for n in resolved if n.get("player_id")}) > 1)

# --- excluded names: never as a Wire card; a feed report about them is fine ---
for who in EXCLUDED:
    check(f"{who} is absent from the replacement section", who not in section)

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

# --- the cards ---
#
# The publication file is the authority, not the review queue. Deriving the
# expected set from backfill decisions missed Alec Pierce, who was approved
# and published directly, and compared Mack Hollins against superseded
# wording -- the queue records what a reviewer decided at the time, the
# publication records what is published now.
PUBS_F = ROOT / "data" / "wire_publications.json"
published = (json.loads(PUBS_F.read_text())["publications"]
             if PUBS_F.exists() else [])
names = [p["player_name"] for p in published]
CARD = r'<article class="tile wire'
cards = re.findall(CARD, section)
rendered = [re.sub(r"<[^>]+>", "", m).strip()
            for m in re.findall(r"<h4>(.*?)</h4>", section, re.S)]
for n in names:
    check(f"{n} appears exactly once", rendered.count(n) == 1,
          f"{rendered.count(n)} card(s)")
# No fixed card count. The section renders every approved report, and a
# number written into a test is a cap nobody meant to impose.
check("one card renders per approved publication",
      len(cards) == len(names), f"{len(cards)} cards, {len(names)} published")
check("the count equals the cards rendered", len(cards) == meta["count_shown"])

# --- the design, which regressed to placeholders once ---
per_card = re.findall(r'<article class="tile wire".*?</article>', section, re.S)
check("every card carries a real player photo",
      all('class="shot"' in c for c in per_card))
check("every card carries a real team logo",
      all("teamlogos/nfl/500" in c for c in per_card))
check("no card falls back to initials by default",
      'class="wpic"' not in section and 'class="wlogo"' not in section)
check("every card carries its team colour",
      len(re.findall(r"--c1:#", section)) == len(per_card))
gi = html.find("#wire .tiles{")
rule = html[gi:gi + 120] if gi >= 0 else ""
check("the Wire renders one card per row",
      "display:block" in rule and "repeat(" not in rule, rule[:56])

# --- the public sentence, and the evidence it may not replace ------------
records = published
for r in records:
    who = r["player_name"]
    summ = (r.get("public_evidence_summary") or "").strip()
    check(f"{who} carries an approved public summary",
          bool(summ) and bool(r.get("public_evidence_summary_approved_by")))
    check(f"{who}'s summary is one sentence within 180 characters",
          bool(summ) and len(summ) <= 180
          and summ.count(". ") == 0, f"{len(summ)} chars")
    check(f"{who}'s stored evidence is retained",
          bool((r.get("reporter_found") or "").strip()))
    check(f"{who}'s passage is not published on the card",
          (r.get("reporter_found") or "x" * 9)[:80] not in section)
check("the card asks 'What changed'",
      "What changed" in section and "What the reporter found" not in section)

# --- one destination -----------------------------------------------------
check("the homepage does not link to a separate Wire page",
      "/nfl/wire/" not in html)
check("the Wire section is the #wire anchor", 'id="wire"' in html)
check("no 'View the full Wire' link remains", "View the full Wire" not in html)

# --- retired sections ----------------------------------------------------
check("League News is gone from the markup",
      'class="league"' not in html and "<h2>League news</h2>" not in html)
check("the video section is gone from the markup",
      "<h2>Video from the beat</h2>" not in html and 'class="vgrid"' not in html)

for d in published:
    esc = (d["lineupbeat_impact"].replace("&", "&amp;").replace("<", "&lt;")
           .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;"))
    check(f"{d['player_name']}: published wording is byte-identical",
          esc in section)

# --- layout and filters ---
# One card per row at every width. Two columns put the reporting, the
# attribution and the analysis into a half-width measure.
check("one card per row at every width",
      "#wire .tiles{display:block" in html
      and "minmax(0,1fr)" not in html.split("#wire .tiles{")[1][:120])
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
    before_data = payload(BEFORE.read_text())
    before_nuggets = sum(len(s_.get("nuggets") or [])
                         for s_ in (before_data or {}).get("sports", {}).values())
    # The page carries both systems on purpose, so it is expected to grow.
    # What must not happen is the feed shrinking: the earlier version saved
    # half a megabyte by deleting the records Recent News renders.
    check("no feed record was dropped on the way through",
          len(nuggets) >= before_nuggets,
          f"{before_nuggets} before, {len(nuggets)} now")

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
