#!/usr/bin/env python3
"""Merge Average Draft Position into the roster from Fantasy Football Calculator.

    python3 scripts/import_adp.py --dry-run
    python3 scripts/import_adp.py
    python3 scripts/import_adp.py --format ppr --teams 12

Their ADP REST API is free for personal and commercial use, and they ask for
attribution in return -- a link or a mention. The site footer credits them;
if you remove that, stop calling this.

Why ADP and not Sleeper's `search_rank`: rank is a popularity ordering, ADP is
what people actually did in thousands of real drafts. For "is this player worth
a big card" rank is fine. For "this camp report matters because he goes in the
fourth round" you want the real number.

Names are matched through the same resolver the pipeline uses, so accents,
suffixes and punctuation are handled and an ambiguous match is refused rather
than guessed. Expect a handful of misses: their pool is fantasy-relevant
players, ours is every player on a roster.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request

# The window these drafts cover, filled in by fetch() and written beside the
# roster so a page can say how current the numbers are.
META: dict = {}
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatwire.registry import Registry
from beatwire.resolve import Resolver

ROOT = Path(__file__).resolve().parent.parent
API = "https://fantasyfootballcalculator.com/api/v1/adp"
FORMATS = ["standard", "ppr", "half-ppr", "2qb", "dynasty", "rookie"]


def fetch(fmt: str, teams: int, year: int) -> list[dict]:
    """Returns the players, and records the window the drafts came from.

    Fantasy Football Calculator publishes the date range it aggregated --
    a rolling week, roughly five thousand drafts. That is worth showing a
    reader: it says the numbers reflect drafts happening now rather than
    mocks from June, which is the only thing that makes an ADP useful in
    August.
    """
    url = f"{API}/{fmt}?teams={teams}&year={year}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "lineupbeat/1.0 (fantasy news aggregator)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    players = data.get("players") or []
    m = data.get("meta") or {}
    META.update({"start": m.get("start_date"), "end": m.get("end_date"),
                 "drafts": m.get("total_drafts"), "type": m.get("type"),
                 "teams": m.get("teams")})
    print(f"  {url}\n  -> {len(players)} players, "
          f"{data.get('meta', {}).get('total_drafts', '?')} drafts")
    return players


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--format", default="ppr", choices=FORMATS)
    ap.add_argument("--teams", type=int, default=12)
    ap.add_argument("--year", type=int, default=datetime.now().year)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        rows = fetch(args.format, args.teams, args.year)
    except Exception as exc:
        sys.exit(f"  fetch failed: {exc}")
    if not rows:
        sys.exit("  empty response. Their season may not have data yet; try "
                 "--year with the previous season to sanity check the wiring.")

    reg = Registry(args.sport)
    resolver = Resolver(reg.players, reg.profile.position_groups)

    matched, missed = {}, []
    for r in rows:
        name = (r.get("name") or "").strip()
        team = (r.get("team") or "").strip().upper()
        adp = r.get("adp")
        if not name or adp is None:
            continue
        player, conf = resolver.resolve(name, team or None,
                                        (r.get("position") or "").upper() or None)
        if not player:
            # Try without the team hint: their team codes drift after trades,
            # and a stale code is worse than none.
            player, conf = resolver.resolve(name, None,
                                            (r.get("position") or "").upper() or None)
        if player:
            matched[player.id] = {
                "adp": float(adp),
                "adp_hi": r.get("high"),
                "adp_lo": r.get("low"),
                "adp_stdev": r.get("stdev"),
            }
        else:
            missed.append(f"{name} ({team})")

    print(f"\n  matched {len(matched)} of {len(rows)}"
          f"  ({len(missed)} unmatched)")
    if missed:
        print("  unmatched sample:", ", ".join(missed[:8]))

    path = ROOT / "rosters" / f"{args.sport}.csv"
    existing = list(csv.DictReader(path.open()))
    fields = list(existing[0]) if existing else []
    for col in ("adp", "adp_stdev"):
        if col not in fields:
            fields.append(col)

    hits = 0
    for row in existing:
        m = matched.get(row["id"])
        row["adp"] = f"{m['adp']:.1f}" if m else ""
        row["adp_stdev"] = (f"{m['adp_stdev']:.1f}"
                            if m and m.get("adp_stdev") is not None else "")
        hits += 1 if m else 0

    top = sorted((r for r in existing if r["adp"]),
                 key=lambda r: float(r["adp"]))[:8]
    print("\n  top of the board:")
    for r in top:
        print(f"    {r['adp']:>5}  {r['name']:<24} {r['team']:<4} {r['position']}")

    if args.dry_run:
        print("\n  --dry-run, roster not written")
        return

    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(existing)
    print(f"\n  wrote {path} ({hits} players carry an ADP)")
    print("  next: python3 -m beatwire.cli export --sports nfl --limit 4000")
    write_meta()

def write_meta():
    import json as _json
    p = ROOT / "rosters" / "adp_meta.json"
    if META.get("end"):
        p.write_text(_json.dumps(META, indent=1))
        print(f"  drafts from {META['start']} to {META['end']} "
              f"({META.get('drafts', '?')} of them)")


if __name__ == "__main__":
    main()
