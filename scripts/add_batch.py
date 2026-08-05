#!/usr/bin/env python3
"""Add a curated batch of X handles as twitterapi sources.

    python3 scripts/add_batch.py --dry-run
    python3 scripts/add_batch.py

Idempotent: anything already in sources/nfl.yaml is skipped, so this is safe
to rerun.

National insiders are added with no team. That is deliberate but has a real
cost: without a team hint the resolver cannot use team scoping, so bare
surnames from those accounts will mostly go unresolved. Full names still
resolve fine, and national insiders overwhelmingly write full names
("The Jets are placing Breece Hall on IR"), so this is the right trade. Watch
the unresolved count after the first run and revisit if it jumps.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

NATIONAL = ["RapSheet", "AdamSchefter", "TomPelissero"]

BY_TEAM = {
    "BUF": ["JoeBuscaglia", "MattParrino", "salmaiorana", "JaySkurski"],
    "CAR": ["josephperson", "mike_e_kaye"],
    "CHI": ["kfishbain", "BradBiggs", "CourtneyRCronin"],
    "CIN": ["pauldehnerjr", "ByJayMorrison", "Ben_Baby"],
    "CLE": ["AkronJackson", "MaryKayCabot", "DanielOyefusi"],
    "DAL": ["toddarcher", "jonmachota", "SlaterNFL"],
    "DEN": ["ParkerJGabriel", "NickKosmider"],
    "DET": ["davebirkett", "Justin_Rogers"],
    "HOU": ["AaronWilson_NFL", "jonmalexander"],
    "IND": ["mchappell51", "JoelAErickson"],
    "JAX": ["_John_Shipley", "Demetrius82", "ESPNdirocco"],
    "KC":  ["ByNateTaylor", "mattderrick", "jessenewell", "pgsween"],
    "LV":  ["HondoCarpenter"],
    "LAC": ["danielrpopper", "krisrhim1"],
    "LAR": ["NateAtkins_", "LATimesklein"],
    "MIA": ["DavidFurones_", "OmarKelly", "schadjoe"],
    "MIN": ["alec_lewis", "BenGoessling"],
    "NE":  ["ezlazar", "MikeReiss", "DougKyed"],
    "NO":  ["MikeTriplett", "nick_underhill"],
    "NYJ": ["ZackBlatt", "BrianCoz"],
    "PHI": ["Jeff_McLane", "EliotShorrParks"],
    "SF":  ["Eric_Branch", "mattbarrows"],
    "SEA": ["gbellseattle", "bcondotta", "MikeDugar"],
    "TB":  ["NFLSTROUD", "gregauman"],
    "TEN": ["jwyattsports", "terrymc13"],
    "WAS": ["john_keim", "BenStandig"],
}


def slug(handle: str) -> str:
    return re.sub(r"[^a-z0-9]", "", handle.lower())[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-national", action="store_true")
    args = ap.parse_args()

    path = ROOT / "sources" / f"{args.sport}.yaml"
    doc = yaml.safe_load(path.read_text())
    have = {(s.get("handle") or "").lower() for s in doc["sources"]}

    wanted: list[tuple[str, str | None]] = []
    if not args.skip_national:
        wanted += [(h, None) for h in NATIONAL]
    for team, handles in BY_TEAM.items():
        wanted += [(h, team) for h in handles]

    # A handle listed under two teams is a beat change or a mistake; keep the
    # first and report it rather than creating two sources for one person.
    seen: dict[str, str | None] = {}
    conflicts = []
    for handle, team in wanted:
        key = handle.lower()
        if key in seen and seen[key] != team:
            conflicts.append((handle, seen[key], team))
            continue
        seen.setdefault(key, team)

    added, skipped = [], []
    for handle, team in wanted:
        key = handle.lower()
        if key in have:
            skipped.append(handle)
            continue
        if seen.get(key) != team:
            continue
        have.add(key)
        entry = {
            "id": f"{args.sport}-{(team or 'natl').lower()}-tapi-{slug(handle)}",
            "kind": "twitterapi",
            "handle": handle,
            "name": handle,
            "outlet": "X",
            "teams": [team] if team else [],
        }
        doc["sources"].append(entry)
        added.append((handle, team or "NATIONAL"))

    per = Counter(t for _, t in added)
    print(f"  adding {len(added)}, skipping {len(skipped)} already present")
    if conflicts:
        print("  listed under two teams (kept the first):")
        for h, a, b in conflicts:
            print(f"    {h}: {a} then {b}")
    if per:
        print(f"  teams touched: {len(per)}")
    natl = sum(1 for _, t in added if t == "NATIONAL")
    if natl:
        print(f"  {natl} national accounts added with no team hint")

    daily = len(added) * 20 * 0.00015
    print(f"  estimated: ${daily:.2f}/day, ${daily * 30:.2f}/month for these")

    if args.dry_run:
        print("\n  --dry-run, nothing written")
        return

    header = "\n".join(l for l in path.read_text().splitlines() if l.startswith("#"))
    path.write_text(
        header + "\n\n"
        + yaml.dump(doc, sort_keys=False, allow_unicode=True, width=100)
    )
    total = sum(1 for s in doc["sources"] if s.get("kind") == "twitterapi")
    print(f"\n  wrote {path} ({total} twitterapi sources total)")


if __name__ == "__main__":
    main()
