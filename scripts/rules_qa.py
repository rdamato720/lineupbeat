#!/usr/bin/env python3
"""Audit the rules a claim passes through on its way to the page.

    python3 scripts/rules_qa.py
    python3 scripts/rules_qa.py --show 12

A claim crosses six gates between a beat writer's post and a card somebody
reads: the prefilter, extraction, resolution, merge, export and render. Each
one has rules, most of them written at different times for different
reasons, and no single test looks at what they do TOGETHER.

Every wrong card this project has produced got through because one gate's
rule was wrong while every gate's test passed. A torn ACL for a healthy
player. A trade that happened in March filed as news from twenty hours ago.
A practice roundup's video attached to ten unrelated men. A contract
extension buried under practice reports because the rubric asked whether it
changed a lineup TODAY.

So this checks the claims themselves against the rules they were meant to
follow, and reports the ones that look wrong -- by name, so they can be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS, WARNS = [], []


def head(t):
    print(f"\n  {t}")
    print(f"  {'-' * len(t)}")


def ok(label, good, detail="", warn=False):
    print(f"    {'pass' if good else 'FAIL':<5} {label}"
          + (f"   {detail}" if detail else ""))
    if not good:
        (WARNS if warn else FAILS).append(f"{label}: {detail}".strip(": "))


# Words that mean a writer is REFERRING to something, not reporting it. The
# Mike Evans card -- "Traded to the San Francisco 49ers" from an article
# about somebody else -- came from a subordinate clause exactly like these.
REFERENCE_MARKERS = re.compile(
    r"\b(spent \d+ seasons|before joining|after \d+ seasons|last season|"
    r"a year ago|in \d{4}|formerly|previously|who joined|since leaving|"
    r"his time (in|with)|during his \w+ (year|season)s? (in|with))\b", re.I)

# A claim asserting a season-ending event should be traceable to a source
# that says so, not inferred from a mention.
SEVERE = re.compile(r"\b(torn|tore|acl|achilles|season[- ]ending|out for the "
                    r"(season|year)|ruptured|placed on ir)\b", re.I)

HEDGES = re.compile(r"\b(expected|could|may|might|believed|likely|if he|"
                    r"reportedly|appears|seems|plans to|hopes)\b", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--show", type=int, default=8)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "nuggets" not in tables:
        sys.exit("  no nuggets. Run the pipeline first.")
    rows = conn.execute("SELECT * FROM nuggets").fetchall()
    n = args.show
    print(f"\n  {len(rows):,} claims, checked against the rules that made them\n")

    bodies = {}
    if "items" in tables:
        bodies = {r["url"]: (r["title"] or "") + "\n" + (r["body"] or "")
                  for r in conn.execute("SELECT url, title, body FROM items")}

    def source_of(r):
        try:
            attrs = json.loads(r["attributions"] or "[]")
        except json.JSONDecodeError:
            return None, None
        for a in attrs:
            if a.get("url") in bodies:
                return bodies[a["url"]], a
        return None, (attrs[0] if attrs else None)

    # ---------------------------------------------------------------- rule 1
    head("Rule: a passing mention is a reference, not a report")
    bad = []
    for r in rows:
        src, _ = source_of(r)
        if not src:
            continue
        name = (r["player_name"] or "").split()[-1] if r["player_name"] else ""
        if not name or len(name) < 4:
            continue
        # find the sentence in the source that mentions this player
        for sent in re.split(r"(?<=[.!?])\s+", src):
            if re.search(rf"\b{re.escape(name)}\b", sent, re.I):
                # The source sentence having a backward-looking clause is
                # not enough: "opens camp on PUP as he continues rehab from
                # last season's knee injury" reports something current and
                # refers to something past in one breath.
                #
                # Flag only when the CLAIM asserts the past event itself.
                claim_asserts_past = re.search(
                    r"\b(traded|signed|released|waived|claimed|joined|"
                    r"acquired) (to|by|with|from|the)\b",
                    r["claim"] or "", re.I)
                if REFERENCE_MARKERS.search(sent) and claim_asserts_past:
                    bad.append((r, sent.strip()[:110]))
                break
    ok("no claim built on a backward-looking clause", not bad,
       f"{len(bad)} suspect")
    for r, sent in bad[:n]:
        print(f"      {r['player_name'][:20]:<20} {r['event']:<16} "
              f"{(r['claim'] or '')[:44]}")
        print(f"        source: …{sent}…")

    # ---------------------------------------------------------------- rule 2
    head("Rule: match the severity of the source")
    bad = []
    for r in rows:
        if not SEVERE.search(r["claim"] or ""):
            continue
        src, _ = source_of(r)
        if src is None:
            continue
        if not SEVERE.search(src):
            bad.append(r)
    ok("severe claims trace to severe sources", not bad,
       f"{len(bad)} assert an injury the source does not")
    for r in bad[:n]:
        print(f"      {r['player_name'][:20]:<20} {(r['claim'] or '')[:60]}")

    # ---------------------------------------------------------------- rule 3
    head("Rule: keep the source's hedging")
    lost = []
    for r in rows:
        src, _ = source_of(r)
        if not src:
            continue
        name = (r["player_name"] or "").split()[-1] if r["player_name"] else ""
        if not name or len(name) < 4:
            continue
        for sent in re.split(r"(?<=[.!?])\s+", src):
            if re.search(rf"\b{re.escape(name)}\b", sent, re.I):
                if HEDGES.search(sent) and not HEDGES.search(r["claim"] or ""):
                    lost.append((r, sent.strip()[:100]))
                break
    rate = len(lost) / max(len(rows), 1)
    ok("hedging survives extraction", rate < 0.08,
       f"{len(lost)} claims dropped a qualifier the source used "
       f"({rate:.1%})", warn=rate < 0.15)
    for r, sent in lost[:n]:
        print(f"      {r['player_name'][:18]:<18} {(r['claim'] or '')[:42]}")
        print(f"        source said: …{sent}…")

    # ---------------------------------------------------------------- rule 4
    head("Rule: actionability reflects consequence, not immediacy")
    big = ("signed", "traded", "released", "retired", "restructure",
           "ir_placement", "suspension", "starter_named")
    low = [r for r in rows if r["event"] in big and r["actionability"] < 3]
    ok("season-shaping events score 3", not low,
       f"{len(low)} contract or roster moves rated below the top tier")
    for r in low[:n]:
        print(f"      {r['actionability']}  {r['player_name'][:20]:<20} "
              f"{r['event']:<14} {(r['claim'] or '')[:40]}")

    routine = [r for r in rows
               if r["event"] in ("practice_full", "practice_limited",
                                 "context_note", "performance_note")
               and r["actionability"] >= 3]
    ok("routine notes do not score 3", len(routine) < len(rows) * 0.05,
       f"{len(routine)} practice or context notes at the top tier", warn=True)

    # ---------------------------------------------------------------- rule 5
    head("Rule: media belongs to the claim it sits on")
    shared = defaultdict(list)
    for r in rows:
        if not r["media"] or r["media"] == "[]":
            continue
        try:
            u = json.loads(r["attributions"] or "[]")[0].get("url")
        except (json.JSONDecodeError, IndexError, AttributeError):
            continue
        shared[u].append(r)
    spread = {u: v for u, v in shared.items() if len(v) >= 3}
    ok("no clip attached to three or more players", not spread,
       f"{len(spread)} posts spraying one video across a roundup")
    for u, v in list(spread.items())[:4]:
        print(f"      {len(v)} players share one clip: "
              f"{', '.join(x['player_name'][:14] for x in v[:4])}")

    # ---------------------------------------------------------------- rule 6
    head("Rule: one event, one card")
    seen = defaultdict(list)
    for r in rows:
        if r["player_id"]:
            seen[(r["player_id"], r["event"], (r["published_at"] or "")[:10])].append(r)
    split = {k: v for k, v in seen.items() if len(v) > 1}
    ok("no event split across cards", not split,
       f"{len(split)} player-event-days on more than one card", warn=True)
    for k, v in list(split.items())[:4]:
        print(f"      {v[0]['player_name'][:20]:<20} {k[1]:<18} x{len(v)}")

    # ---------------------------------------------------------------- rule 7
    head("Rule: paraphrase, never reproduce")
    if not bodies:
        ok("source text kept", False, "cannot check without items", warn=True)
    else:
        def run_len(a, b):
            """Longest run of CONSECUTIVE words the claim shares with the
            source.

            The first version built a set of every word in the article and
            counted claim words present in it, which is not phrase matching
            at all: a claim written in entirely original phrasing scored its
            full length as long as each word appeared somewhere. It reported
            fifty-four violations, none of them real.

            This compares actual sequences.
            """
            wa = re.findall(r"[a-z']+", (a or "").lower())
            wb = re.findall(r"[a-z']+", (b or "").lower())
            if not wa or not wb:
                return 0
            grams = set()
            for k in range(len(wb)):
                grams.add(tuple(wb[k:k + 1]))
            best = 0
            for i in range(len(wa)):
                for j in range(i + best + 1, len(wa) + 1):
                    seq = tuple(wa[i:j])
                    L = len(seq)
                    if L > len(wb):
                        break
                    found = any(tuple(wb[k:k + L]) == seq
                                for k in range(len(wb) - L + 1))
                    if found:
                        best = max(best, L)
                    else:
                        break
            return best
        runs, worst = [], []
        for r in rows:
            src, _ = source_of(r)
            if not src:
                continue
            L = run_len(r["claim"], src)
            runs.append(L)
            if L >= 15:
                worst.append((L, r))
        if runs:
            med = statistics.median(runs)
            ok("median shared phrase is short", med <= 6,
               f"{med:.0f} words")
            ok("nothing reproduces 15+ consecutive words", not worst,
               f"{len(worst)} over the line")
            for L, r in sorted(worst, key=lambda x: -x[0])[:n]:
                print(f"      {L} words  {r['player_name'][:18]:<18} "
                      f"{(r['claim'] or '')[:44]}")

    # ---------------------------------------------------------------- rule 8
    head("Rule: attribution names whoever wrote the displayed claim")
    bad = []
    for r in rows:
        try:
            attrs = json.loads(r["attributions"] or "[]")
        except json.JSONDecodeError:
            bad.append(r); continue
        if not attrs or not attrs[0].get("url") or not attrs[0].get("source_name"):
            bad.append(r)
    ok("every claim names a source and links to it", not bad,
       f"{len(bad)} unattributed")

    # ---------------------------------------------------------------- rule 9
    head("Rule: horizon separates a day from a season")
    if "horizon" in {c[1] for c in conn.execute("PRAGMA table_info(nuggets)")}:
        h = Counter(r["horizon"] for r in rows)
        print(f"    {dict(h)}")
        weekly_as_season = [r for r in rows if r["horizon"] == "season"
                            and r["event"] in ("practice_limited",
                                               "practice_absent", "practice_full")]
        ok("a practice report is not a season claim", not weekly_as_season,
           f"{len(weekly_as_season)} weekly events marked season-long")
        season_as_day = [r for r in rows if r["horizon"] == "day"
                         and r["event"] in ("signed", "traded", "retired",
                                            "ir_placement", "released")]
        ok("a transaction is not a day claim",
           len(season_as_day) < len(rows) * 0.02,
           f"{len(season_as_day)} roster moves marked day-only", warn=True)
    else:
        ok("horizon column present", False, "re-run the pipeline", warn=True)

    # --------------------------------------------------------------- render
    head("What actually reaches the page")
    feed = ROOT / "site" / "data" / "feed.json"
    if feed.exists():
        d = json.loads(feed.read_text())
        fn = d.get("sports", {}).get("nfl", {}).get("nuggets", [])
        ok("feed populated", len(fn) > 0, f"{len(fn):,} of {len(rows):,} claims")
        if len(fn) < len(rows) * 0.5 and len(rows) > 600:
            ok("export limit is not cutting the wire in half", False,
               f"{len(fn)} exported from {len(rows)}; raise --limit", warn=True)
        noclaim = [x for x in fn if not (x.get("claim") or "").strip()]
        ok("no empty claims on the page", not noclaim, f"{len(noclaim)}")
        unres = [x for x in fn if not x.get("resolved")]
        ok("unmatched mentions are marked, not guessed",
           all(not x.get("player_id") for x in unres),
           f"{len(unres)} unresolved and correctly unlinked")
    else:
        ok("feed built", False, "run export", warn=True)

    print()
    for w in WARNS:
        print(f"  WARN   {w}")
    for f in FAILS:
        print(f"  FAIL   {f}")
    if not FAILS and not WARNS:
        print("  Every rule holds.")
    elif not FAILS:
        print(f"\n  {len(WARNS)} worth reading, nothing blocking.")
    else:
        print(f"\n  {len(FAILS)} rule{'s' if len(FAILS) != 1 else ''} "
              f"broken. Read the named claims -- a rule that fails once is "
              f"usually a rule that is worded wrong.")
    if args.strict and FAILS:
        sys.exit(1)


if __name__ == "__main__":
    main()
