#!/usr/bin/env python3
"""Which sources earn their keep, and what cutting the rest would cost.

    python3 scripts/rank_sources.py --days 14
    python3 scripts/rank_sources.py --days 14 --keep 3
    python3 scripts/rank_sources.py --days 14 --keep 3 --apply

WHY NOT JUST TAKE THREE PER TEAM ALPHABETICALLY

Because sources are not interchangeable. A team can have four writers who
all break roster moves and another can have one who matters and two who post
podcast links. A fixed count chosen without looking keeps the duds and drops
the producers, and the bill barely moves because the duds were cheap anyway.

Three per team is a reasonable target. Which three is a question the data
already answers.

WHAT IS COUNTED

    nuggets       claims that reached the wire from this source
    tier 3        the actionable ones: signings, injuries, starters named
    first         times this source was the first to report something
    items         posts fetched, which is what the model is paid to read

The last column is the one that decides cost. A source with 400 items and
six nuggets is expensive and quiet; a source with 40 items and twelve
nuggets is cheap and useful, and a rule counting only nuggets would rank
them the wrong way round.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--keep", type=int, default=3,
                    help="how many sources to keep per team")
    ap.add_argument("--apply", action="store_true",
                    help="write enabled:false onto the sources being dropped")
    args = ap.parse_args()

    db = ROOT / args.db
    if not db.exists():
        sys.exit(f"  no database at {db}")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    import yaml
    reg_path = ROOT / "sources" / f"{args.sport}.yaml"
    reg = yaml.safe_load(reg_path.read_text())
    sources = {s["id"]: s for s in reg["sources"]}
    live = {k: v for k, v in sources.items() if v.get("enabled", True)}

    # Items fetched per source: what the model was paid to read.
    items = defaultdict(int)
    for r in conn.execute(
            f"""SELECT source_id, COUNT(*) n FROM items
                WHERE fetched_at > datetime('now', '-{args.days} days')
                GROUP BY source_id"""):
        items[r["source_id"]] = r["n"]

    # Nuggets per source, from the first attribution on each.
    nuggets = defaultdict(int)
    tier3 = defaultdict(int)
    first = defaultdict(int)
    for r in conn.execute(
            f"""SELECT attributions, actionability FROM nuggets
                WHERE published_at > datetime('now', '-{args.days} days')"""):
        try:
            attrs = json.loads(r["attributions"] or "[]")
        except (TypeError, ValueError):
            continue
        if not attrs:
            continue
        for i, a in enumerate(attrs):
            sid = a.get("source_id") or a.get("source_name")
            if not sid:
                continue
            nuggets[sid] += 1
            if (r["actionability"] or 0) >= 3:
                tier3[sid] += 1
            if i == 0:
                first[sid] += 1

    # Nuggets are attributed by name; items by id. Bridge them.
    by_name = {}
    for sid, s in sources.items():
        by_name[s.get("name", sid)] = sid

    def stat(sid, table):
        s = sources.get(sid, {})
        return table.get(sid, 0) + table.get(s.get("name", ""), 0)

    rows = []
    for sid, s in live.items():
        teams = s.get("teams") or []
        team = teams[0] if len(teams) == 1 else ("national" if not teams
                                                 else "/".join(teams[:2]))
        n = stat(sid, nuggets)
        rows.append({
            "id": sid, "name": s.get("name", sid), "team": team,
            "items": items.get(sid, 0),
            "nuggets": n, "tier3": stat(sid, tier3),
            "first": stat(sid, first),
            "weight": s.get("weight", 1.0),
        })

    print(f"\n  {len(rows)} live sources, last {args.days} days")
    print(f"  {sum(r['items'] for r in rows):,} items fetched, "
          f"{sum(r['nuggets'] for r in rows):,} nuggets\n")

    # Rank within team: actionable news first, then anything, then cheapness.
    def score(r):
        return (-r["tier3"], -r["nuggets"], -r["first"], r["items"])

    by_team = defaultdict(list)
    for r in rows:
        by_team[r["team"]].append(r)

    keep, drop = [], []
    for team, group in sorted(by_team.items()):
        group.sort(key=score)
        if team == "national":
            keep.extend(group)          # nationals are never a team's quota
            continue
        keep.extend(group[:args.keep])
        drop.extend(group[args.keep:])

    print(f"  KEEPING {len(keep)}, DROPPING {len(drop)}\n")
    kept_items = sum(r["items"] for r in keep)
    drop_items = sum(r["items"] for r in drop)
    kept_nug = sum(r["nuggets"] for r in keep)
    drop_nug = sum(r["nuggets"] for r in drop)
    kept_t3 = sum(r["tier3"] for r in keep)
    drop_t3 = sum(r["tier3"] for r in drop)
    tot_items = kept_items + drop_items or 1
    tot_nug = kept_nug + drop_nug or 1
    tot_t3 = kept_t3 + drop_t3 or 1

    print(f"    {'':<12}{'ITEMS':>10}{'NUGGETS':>10}{'TIER 3':>9}")
    print(f"    {'keep':<12}{kept_items:>10,}{kept_nug:>10,}{kept_t3:>9,}")
    print(f"    {'drop':<12}{drop_items:>10,}{drop_nug:>10,}{drop_t3:>9,}")
    print(f"    {'':<12}{drop_items/tot_items:>9.0%}{drop_nug/tot_nug:>10.0%}"
          f"{drop_t3/tot_t3:>9.0%}  of the total, dropped")

    # The number that matters: items are what the model reads.
    per_item = 0.00108
    print(f"\n  at {per_item*1000:.2f} per thousand extractions and a "
          f"{args.days}-day window,")
    print(f"  dropping those sources is about "
          f"${drop_items * 0.25 * per_item / args.days * 30:,.0f} a month")
    print(f"  (a quarter of items clear the prefilter and reach the model)")

    if drop_t3:
        print(f"\n  BUT they produced {drop_t3} actionable nuggets. "
              f"The biggest losses:\n")
        for r in sorted(drop, key=lambda x: -x["tier3"])[:8]:
            if not r["tier3"]:
                break
            print(f"    {r['tier3']:>3} tier-3  {r['name'][:30]:<30} "
                  f"{r['team']:<6} {r['items']:>5} items")

    print(f"\n  DROPPED, quietest first\n")
    for r in sorted(drop, key=lambda x: (x["nuggets"], -x["items"]))[:20]:
        print(f"    {r['name'][:30]:<30} {r['team']:<6}"
              f"{r['items']:>6} items{r['nuggets']:>5} nuggets"
              f"{r['tier3']:>4} tier3")

    if not args.apply:
        print(f"\n  Nothing changed. Re-run with --apply to disable them.")
        return 0

    dropped = {r["id"] for r in drop}
    text = reg_path.read_text()
    n = 0
    out = []
    current = None
    for line in text.split("\n"):
        if line.startswith("- id: "):
            current = line[6:].strip()
        if (current in dropped and line.strip().startswith("enabled:")):
            out.append(line.split("enabled:")[0] + "enabled: false")
            n += 1
            continue
        out.append(line)
        # a source with no enabled line needs one
        if line.startswith("- id: ") and current in dropped:
            out.append("  enabled: false")
            n += 1
    reg_path.write_text("\n".join(out))

    check = yaml.safe_load(reg_path.read_text())
    still = sum(1 for s in check["sources"] if s.get("enabled", True))
    print(f"\n  disabled {len(dropped)} sources; {still} remain live")
    print(f"  the registry still parses")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
