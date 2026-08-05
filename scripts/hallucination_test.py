#!/usr/bin/env python3
"""Find claims about players who do not appear in the source they came from.

    python3 scripts/hallucination_test.py
    python3 scripts/hallucination_test.py --strict
    python3 scripts/hallucination_test.py --event injury_reported

Built after the wire asserted that Tucker Kraft was out with a torn ACL. The
article was about two other tight ends being waived; Kraft was not mentioned
in it at all, and four other nuggets from the same week correctly had him
activated off PUP and practising in pads.

That is the one failure class that makes a wire unpublishable. Attribution,
paraphrasing, deduplication and coverage all assume the claims are true. A
pipeline that can invent a season-ending injury for a healthy player fails
underneath all of them.

The check is simple and that is the point: if we say something about a player,
his name should appear in the text we read. Surname alone is enough -- writers
use surnames after first reference -- so this only fires when the player is
genuinely absent.

It cannot catch everything. A claim can name the right player and still
mischaracterise what was said. But it catches the specific and dangerous case
of a name conjured from surrounding context, which is what happened here.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Getty and Icon captions precede most SB Nation articles and mention players
# who have nothing to do with the story. Text inside one does not count as a
# mention -- it is the caption of a photograph, not reporting.
CAPTION_END = re.compile(r"\|\s*(Getty Images|Icon Sportswire[^|]*)\s*", re.I)


def strip_caption(body: str) -> tuple[str, str]:
    """Split a body into (caption, article). Captions routinely name a player
    the article never discusses, which is exactly how a wrong attribution
    gets made to look supported."""
    m = CAPTION_END.search(body or "")
    if not m:
        return "", body or ""
    return body[:m.end()], body[m.end():]


def mentions(name: str, text: str) -> bool:
    if not name or not text:
        return False
    low = text.lower()
    if name.lower() in low:
        return True
    # surname, which is how a writer refers to someone after first mention
    parts = [p for p in re.split(r"\s+", name) if len(p) > 2]
    if not parts:
        return False
    surname = re.sub(r"[^a-z]", "", parts[-1].lower())
    if len(surname) < 4:
        return False
    return re.search(rf"\b{re.escape(surname)}\b", low) is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--event", help="only this event type")
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    try:
        bodies = {r["url"]: (r["title"] or "") + "\n" + (r["body"] or "")
                  for r in conn.execute("SELECT url, title, body FROM items")}
    except sqlite3.OperationalError:
        sys.exit("  no items table — run the pipeline so source text is stored")
    if not bodies:
        sys.exit("  no source text stored yet — run the pipeline first")

    q = "SELECT * FROM nuggets"
    params = ()
    if args.event:
        q += " WHERE event = ?"
        params = (args.event,)
    rows = conn.execute(q, params).fetchall()

    checked, missing, caption_only = 0, [], []
    for r in rows:
        try:
            attrs = json.loads(r["attributions"] or "[]")
        except json.JSONDecodeError:
            continue
        body = next((bodies[a["url"]] for a in attrs if a.get("url") in bodies), None)
        if body is None:
            continue
        checked += 1
        caption, article = strip_caption(body)
        name = r["mention"] or r["player_name"]
        if mentions(name, article):
            continue
        if caption and mentions(name, caption):
            caption_only.append(r)      # named only in a photo caption
        else:
            missing.append(r)

    print(f"\n  {checked} claims checked against the text they came from\n")

    print(f"  NAMED NOWHERE IN THE SOURCE          {len(missing)}")
    for r in missing[:args.show]:
        print(f"    {r['player_name'][:22]:<22} {r['event']:<18} "
              f"{(r['claim'] or '')[:44]}")
    if len(missing) > args.show:
        print(f"    … and {len(missing) - args.show} more")

    print(f"\n  NAMED ONLY IN A PHOTO CAPTION        {len(caption_only)}")
    for r in caption_only[:6]:
        print(f"    {r['player_name'][:22]:<22} {r['event']:<18} "
              f"{(r['claim'] or '')[:44]}")

    if missing:
        print(f"\n  by event:")
        for ev, n in Counter(r["event"] for r in missing).most_common(8):
            print(f"    {ev:<22} {n}")
        print(f"\n  by source:")
        srcs = Counter()
        for r in missing:
            try:
                srcs[json.loads(r["attributions"])[0].get("source_name", "?")] += 1
            except (json.JSONDecodeError, IndexError, KeyError):
                pass
        for s, n in srcs.most_common(8):
            print(f"    {s[:30]:<30} {n}")

    rate = 100 * len(missing) / max(1, checked)
    print(f"\n  rate: {rate:.1f}% of checked claims name a player the source "
          f"never mentions")

    fails = []
    if rate > 2:
        fails.append(f"{rate:.1f}% of claims are about players absent from "
                     f"their source. This is invention, not extraction, and "
                     f"no amount of good formatting survives it.")
    elif missing:
        print("\n  Low, but read every one above. An invented injury is worse")
        print("  than a hundred dull-but-correct items.")
    if caption_only:
        print("\n  Caption-only matches are nearly as bad: a photo of one")
        print("  player above an article about another is not a mention.")

    high = [r for r in missing
            if r["event"] in ("injury_reported", "ir_placement", "surgery",
                              "carted_off", "ruled_out", "retired")]
    if high:
        print(f"\n  {len(high)} of them assert an INJURY or a season-ending "
              f"event.")
        for r in high[:6]:
            print(f"    {r['player_name'][:22]:<22} {(r['claim'] or '')[:50]}")
        fails.append(f"{len(high)} invented claims concern injuries — the "
                     f"category a reader acts on and a player's employer "
                     f"would object to")

    print()
    for f in fails:
        print(f"  FAIL   {f}")
    if not fails and not missing:
        print("  Every claim names a player its source actually mentions.")
    if args.strict and fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
