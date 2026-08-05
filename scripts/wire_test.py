#!/usr/bin/env python3
"""Pressure-test the beat reporting pipeline before it ships.

    python3 scripts/wire_test.py
    python3 scripts/wire_test.py --strict     # exit 1 on any failure

The projection work has its own scorecard. This checks the other half, which
is the part people will actually read: are the nuggets accurate, attributed,
deduplicated, current, and legally safe to publish.

Ordered by how badly each failure would hurt if it shipped:

  1  ATTRIBUTION   a claim with no source is unpublishable
  2  COPYRIGHT     paraphrase, not reproduction
  3  RESOLUTION    a report on the wrong player is worse than no report
  4  MERGE         one event, one card
  5  FRESHNESS     a wire that is quietly stale is worse than an empty one
  6  SOURCES       a silent source looks like a quiet news day
  7  EXTRACTION    claims that are empty, truncated, or boilerplate
  8  COVERAGE      every team represented

Each check prints offenders by name. A count tells you nothing; a name tells
you what to fix.
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
TEAMS = {"ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB",
         "HOU","IND","JAX","KC","LV","LAC","LAR","MIA","MIN","NE","NO","NYG",
         "NYJ","PHI","PIT","SF","SEA","TB","TEN","WAS"}


def words(s):
    return re.findall(r"[a-z']+", (s or "").lower())


def longest_common_run(a, b):
    """Longest run of consecutive shared words. The copyright question is not
    'do these overlap' but 'did we copy a phrase'."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0
    best = 0
    index = defaultdict(list)
    for j, w in enumerate(wb):
        index[w].append(j)
    prev = defaultdict(int)
    for i, w in enumerate(wa):
        cur = defaultdict(int)
        for j in index.get(w, ()):
            cur[j] = prev[j - 1] + 1
            best = max(best, cur[j])
        prev = cur
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--show", type=int, default=6)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    fails, warns = [], []
    n = args.show

    try:
        rows = conn.execute("SELECT * FROM nuggets").fetchall()
    except sqlite3.OperationalError:
        sys.exit("  no nuggets table. Run the pipeline first.")
    if not rows:
        sys.exit("  no nuggets. Run the pipeline first.")
    print(f"\n  {len(rows)} nuggets under test\n")

    # 1 -------------------------------------------------------------------
    print("  1. ATTRIBUTION")
    bad = []
    for r in rows:
        try:
            attrs = json.loads(r["attributions"] or "[]")
        except json.JSONDecodeError:
            attrs = []
        if not attrs or not any(a.get("url") for a in attrs):
            bad.append(r)
        elif not any(a.get("source_name") for a in attrs):
            bad.append(r)
    print(f"     nuggets with no usable source     {len(bad)}")
    for r in bad[:n]:
        print(f"       {r['player_name'][:22]:<22} {r['claim'][:44]}")
    if bad:
        fails.append(f"{len(bad)} claims cannot be attributed to a reporter")

    # 2 -------------------------------------------------------------------
    print("\n  2. COPYRIGHT — are we paraphrasing or reproducing?")
    # Join nuggets to their source text by URL, which is what attributions
    # carry. The question is not whether words overlap -- they will, both
    # describe the same play -- but whether a PHRASE was lifted.
    try:
        by_url = {r["url"]: (r["title"] + " " + r["body"])
                  for r in conn.execute("SELECT url, title, body FROM items")}
    except sqlite3.OperationalError:
        by_url = {}
    runs, worst = [], []
    if by_url:
        for r in rows:
            try:
                attrs = json.loads(r["attributions"] or "[]")
            except json.JSONDecodeError:
                continue
            src = next((by_url[a["url"]] for a in attrs
                        if a.get("url") in by_url), None)
            if not src:
                continue
            run = longest_common_run(r["claim"], src)
            runs.append(run)
            if run >= 12:
                worst.append((run, r["player_name"], r["claim"]))
        if runs:
            print(f"     claims checked against source      {len(runs)}")
            print(f"     longest shared phrase, median      "
                  f"{statistics.median(runs):.0f} words")
            print(f"     longest anywhere                   {max(runs)} words")
            print(f"     sharing 12+ consecutive words      {len(worst)}")
            for run, who, claim in sorted(worst, reverse=True)[:n]:
                print(f"       {run} words  {who[:20]:<20} {claim[:40]}")
            if worst:
                fails.append(f"{len(worst)} claims share 12 or more consecutive "
                             f"words with their source. That is reproduction "
                             f"rather than paraphrase and it is the thing a "
                             f"writer would object to.")
            elif max(runs) >= 8:
                warns.append(f"longest shared phrase is {max(runs)} words — "
                             f"under the line but worth a look")
        else:
            warns.append("no nugget could be matched to its source text yet; "
                         "run the pipeline again so items accumulate")
    else:
        print("     no stored source text yet")
        warns.append("run the pipeline once more so source text is stored, "
                     "then this check becomes meaningful")

    # 3 -------------------------------------------------------------------
    print("\n  3. RESOLUTION")
    unres = [r for r in rows if not r["player_id"]]
    rate = 100 * len(unres) / len(rows)
    print(f"     unresolved mentions               {len(unres)}  ({rate:.1f}%)")
    for r in unres[:n]:
        print(f"       {r['player_name'][:22]:<22} {r['team']:<4} "
              f"{r['claim'][:38]}")
    if rate > 8:
        fails.append(f"{rate:.1f}% of mentions do not resolve to a player")
    elif rate > 4:
        warns.append(f"{rate:.1f}% unresolved")

    # a resolved player whose team disagrees with the nugget's team
    roster = {}
    rp = ROOT / "rosters" / "nfl.csv"
    if rp.exists():
        import csv
        for x in csv.DictReader(rp.open()):
            roster[x["id"]] = x
    mismatch = [r for r in rows if r["player_id"] in roster
                and r["team"] and roster[r["player_id"]].get("team")
                and r["team"] != roster[r["player_id"]]["team"]]
    print(f"     player on a different team        {len(mismatch)}")
    for r in mismatch[:n]:
        print(f"       {r['player_name'][:22]:<22} nugget {r['team']:<4} "
              f"roster {roster[r['player_id']]['team']}")
    if len(mismatch) > len(rows) * 0.02:
        warns.append(f"{len(mismatch)} nuggets attach a player to a team the "
                     f"roster disagrees with")

    # 4 -------------------------------------------------------------------
    print("\n  4. MERGE")
    keys = Counter(r["dedupe_key"] for r in rows)
    print(f"     distinct stories                  {len(keys)}")
    # the same player and event on the same day appearing more than once
    seen = defaultdict(list)
    for r in rows:
        day = (r["published_at"] or "")[:10]
        if r["player_id"]:
            seen[(r["player_id"], r["event"], day)].append(r)
    split = {k: v for k, v in seen.items() if len(v) > 1}
    print(f"     one event split across cards      {len(split)}")
    for k, v in list(split.items())[:n]:
        print(f"       {v[0]['player_name'][:20]:<20} {k[1]:<18} x{len(v)}")
    if split:
        warns.append(f"{len(split)} player-event-days appear on more than one "
                     f"card — readers see the same news twice")

    # 5 -------------------------------------------------------------------
    print("\n  5. FRESHNESS")
    now = datetime.now(timezone.utc)
    ages = []
    for r in rows:
        try:
            ages.append((now - datetime.fromisoformat(r["published_at"])).total_seconds()/3600)
        except (TypeError, ValueError):
            pass
    if ages:
        ages.sort()
        last24 = sum(1 for a in ages if a <= 24)
        print(f"     newest                            {min(ages):.1f}h ago")
        print(f"     median age                        {statistics.median(ages)/24:.1f} days")
        print(f"     filed in the last 24h             {last24}")
        if min(ages) > 36:
            fails.append(f"newest item is {min(ages):.0f}h old — the pipeline "
                         f"has stopped")
        elif min(ages) > 12:
            warns.append(f"newest item is {min(ages):.0f}h old — fine if you "
                         f"have not run the pipeline today, a problem if it is "
                         f"supposed to be on a schedule")
        elif last24 < 20:
            warns.append(f"only {last24} items in the last day; a wire needs "
                         f"to look alive")

    # 6 -------------------------------------------------------------------
    print("\n  6. SOURCES")
    per_source = Counter()
    for r in rows:
        try:
            for a in json.loads(r["attributions"] or "[]"):
                if a.get("source_id"):
                    per_source[a["source_id"]] += 1
        except json.JSONDecodeError:
            pass
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "sources" / "nfl.yaml").read_text())
        listed = cfg.get("sources", cfg) if isinstance(cfg, dict) else cfg
        configured = [s for s in listed
                      if isinstance(s, dict) and s.get("enabled", True)]
        silent = [s["id"] for s in configured if per_source.get(s["id"], 0) == 0]
        print(f"     configured                        {len(configured)}")
        print(f"     produced nothing                  {len(silent)}")
        for sid in silent[:n]:
            print(f"       {sid}")
        if len(silent) > len(configured) * 0.25:
            fails.append(f"{len(silent)} of {len(configured)} sources produced "
                         f"nothing — a silent source looks like a quiet news day")
        elif silent:
            warns.append(f"{len(silent)} sources produced nothing")
    except Exception as exc:
        print(f"     could not read sources: {str(exc)[:40]}")

    # 7 -------------------------------------------------------------------
    print("\n  7. EXTRACTION")
    lens = [len(r["claim"] or "") for r in rows]
    tiny = [r for r in rows if len(r["claim"] or "") < 25]
    huge = [r for r in rows if len(r["claim"] or "") > 400]
    # Counter over a generator of strings, not over a string -- the first
    # version counted characters and reported that the letter "e" appeared
    # four times.
    # Group by claim AND player. Five players who each "did not participate
    # in practice" is five real events that happen to read alike; the same
    # player saying it five times is a merge failure. The first version
    # counted the former and called it boilerplate.
    dupes = Counter(((r["claim"] or "").lower()[:80], r["player_id"] or r["player_name"])
                    for r in rows)
    repeated = [(c[0], k) for c, k in dupes.items() if k > 2 and c[0].strip()]
    print(f"     claim length median               {statistics.median(lens):.0f}")
    print(f"     under 25 chars                    {len(tiny)}")
    print(f"     over 400 chars                    {len(huge)}")
    print(f"     identical claims (3+ times)       {len(repeated)}")
    for c, k in repeated[:n]:
        print(f"       x{k}  {c[:52]}")
    if tiny:
        warns.append(f"{len(tiny)} claims are too short to say anything")
    if repeated:
        warns.append(f"{len(repeated)} claims repeat verbatim — likely "
                     f"boilerplate the extractor should skip")
    # The rubric is 0-3, documented in models.py. An earlier version of this
    # check assumed 1-5 and flagged every legitimate zero -- the test was
    # wrong, not the data, which is a good argument for checking a range
    # against its definition rather than against memory.
    bad_action = [r for r in rows if r["actionability"] is None
                  or not 0 <= r["actionability"] <= 3]
    print(f"     actionability outside 0-3         {len(bad_action)}")
    if bad_action:
        fails.append(f"{len(bad_action)} nuggets have an out-of-range "
                     f"actionability score")

    # 8 -------------------------------------------------------------------
    print("\n  8. COVERAGE")
    by_team = Counter(r["team"] for r in rows if r["team"] in TEAMS)
    missing = TEAMS - set(by_team)
    thin = [t for t, c in by_team.items() if c < 3]
    print(f"     teams with any coverage           {len(by_team)}/32")
    if missing:
        print(f"     no coverage at all: {', '.join(sorted(missing))}")
        fails.append(f"{len(missing)} teams have no coverage")
    if thin:
        print(f"     fewer than 3 items: {', '.join(sorted(thin))}")
        warns.append(f"{len(thin)} teams are thinly covered")

    # ---------------------------------------------------------------------
    print()
    for w in warns:
        print(f"  WARN   {w}")
    for f in fails:
        print(f"  FAIL   {f}")
    if not fails and not warns:
        print("  Clean.")
    elif not fails:
        print("\n  No blockers. Read the warnings before shipping.")
    else:
        print("\n  Fix the failures before this goes public.")

    if args.strict and fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
