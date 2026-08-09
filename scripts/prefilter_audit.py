#!/usr/bin/env python3
"""What the prefilter throws away, and whether any of it mattered.

    python3 scripts/prefilter_audit.py --n 400
    python3 scripts/prefilter_audit.py --n 400 --show-skipped 40
    python3 scripts/prefilter_audit.py --n 400 --show-passed 40

Seventy-five percent of what the wire fetches never reaches the model, and
that is where the bill is decided. Tightening the filter is the cheapest
saving available and also the easiest way to quietly stop covering something.

So this reads recent items, runs the real prefilter over them, and sorts the
skipped ones by whether they look like news. A skipped item containing a
player's name and an injury word is exactly what should not be skipped, and
it will show at the top.

The judgement stays with a person. This prints what was dropped; deciding
whether the wire can live without it is not a thing to automate.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Words that make an item worth a second look if it was dropped. Not the
# filter itself: a scoring aid for reading the discard pile.
NEWSY = re.compile(
    r"\b(injur|hurt|limited|did not|dnp|out |questionable|doubtful|ruled|"
    r"mri|x-ray|surgery|acl|hamstring|ankle|knee|shoulder|concussion|"
    r"pup|ir\b|reserve|activat|return|cleared|practice|snap|rep|"
    r"start|starter|first team|1s|depth|role|package|carries|targets|"
    r"sign|trade|waive|releas|claim|extension|restructure|cut\b|suspend)",
    re.I)
NOISE = re.compile(
    r"\b(podcast|subscribe|newsletter|giveaway|merch|promo code|"
    r"listen|watch live|full episode|ticket|sponsor|link in bio|"
    r"happy birthday|congrat|rip\b|thank you)", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--show-skipped", type=int, default=25)
    ap.add_argument("--show-passed", type=int, default=0)
    args = ap.parse_args()

    from beatwire.registry import Registry
    from beatwire.resolve import Resolver
    from beatwire.extract import mentions_any_player
    from beatwire.models import RawItem
    import datetime as _dt

    reg = Registry(args.sport)
    resolver = Resolver(reg.players, reg.profile.position_groups)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """SELECT i.item_id, i.title, i.body, i.source_id, i.published_at
           FROM items i WHERE i.source_id LIKE ?
           ORDER BY i.fetched_at DESC LIMIT ?""",
        (f"{args.sport}-%", args.n)).fetchall()
    if not rows:
        sys.exit("  no items stored")

    # Which items produced a nugget, so a skip can be checked against reality
    kept_ids = {r[0] for r in conn.execute(
        """SELECT DISTINCT json_extract(attributions,'$[0].url')
           FROM nuggets""") if r[0]}

    names = sorted({p.name for p in reg.players}, key=len, reverse=True)
    surnames = {n.split()[-1].lower() for n in names if len(n.split()) > 1}

    skipped, passed = [], []
    for r in rows:
        text = ((r["title"] or "") + "\n" + (r["body"] or "")).strip()
        item = RawItem(
            source_id=r["source_id"], sport=args.sport, url="",
            title=r["title"] or "", body=r["body"] or "",
            published_at=_dt.datetime.now(_dt.timezone.utc))
        team = None
        for s in reg.sources:
            if s.id == r["source_id"]:
                team = resolver.source_team_hint(s)
                break
        ok = mentions_any_player(item, resolver, team, skill_only=True)
        (passed if ok else skipped).append((r, text))

    print(f"\n  {len(rows)} recent items: {len(passed)} pass, "
          f"{len(skipped)} skipped ({len(skipped)/len(rows):.0%})\n")

    def score(text):
        hits = len(NEWSY.findall(text))
        noise = len(NOISE.findall(text))
        named = sum(1 for w in re.findall(r"[A-Z][a-z]+", text)
                    if w.lower() in surnames)
        return hits * 2 + named - noise * 3

    ranked = sorted(((score(t), r, t) for r, t in skipped), reverse=True,
                    key=lambda x: x[0])

    worrying = [x for x in ranked if x[0] >= 4]
    print(f"  {len(worrying)} skipped items mention a rostered player and use")
    print(f"  injury, role or transaction language. Those are the ones to read.\n")

    for s, r, text in ranked[:args.show_skipped]:
        flat = " ".join(text.split())
        print(f"    [{s:>3}] {r['source_id'][-22:]:<22} {flat[:96]}")

    if args.show_passed:
        print(f"\n  WHAT PASSED, lowest scoring first: is any of it worth "
              f"paying for?\n")
        pranked = sorted(((score(t), r, t) for r, t in passed),
                         key=lambda x: x[0])
        for s, r, text in pranked[:args.show_passed]:
            flat = " ".join(text.split())
            print(f"    [{s:>3}] {r['source_id'][-22:]:<22} {flat[:96]}")

    print(f"\n  SKIPPED BY SOURCE\n")
    by_src = Counter(r["source_id"] for r, _ in skipped)
    for src, n in by_src.most_common(10):
        tot = sum(1 for r in rows if r["source_id"] == src)
        print(f"    {src[-26:]:<26} {n:>4} of {tot:<4} skipped")

    lowpass = sorted(((score(t), r) for r, t in passed), key=lambda x: x[0])
    cheap = sum(1 for s, _ in lowpass if s <= 0)
    print(f"\n  {cheap} of {len(passed)} items that PASSED score zero or below:")
    print(f"  no rostered name and no news language. At roughly a tenth of a")
    print(f"  cent each that is ${cheap * 0.0011:.2f} an hour, "
          f"${cheap * 0.0011 * 20:.2f} a day, for items the model will")
    print(f"  almost certainly return nothing for.")


if __name__ == "__main__":
    main()
