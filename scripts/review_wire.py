#!/usr/bin/env python3
"""Editorial review for the Wire. Nothing reaches the site without passing here.

    python3 scripts/review_wire.py                 # work the queue
    python3 scripts/review_wire.py --list          # what is waiting
    python3 scripts/review_wire.py --published     # what has been approved

    a  approve      publish it, or update the card if the event already exists
    e  edit         change the headline, the label or the fantasy line first
    r  reject       with a reason, recorded
    m  merge        same event as an existing card; supersede it
    s  skip         leave it in the queue
    q  quit

Deliberately a terminal tool. The site is static with no backend, so there is
nowhere to click; and a reviewer working a local queue can be the only path to
publication, which is the property that matters most in V1.

The separation is structural rather than procedural: candidates and
publications are different tables, and the site build reads
`data/wire_publications.json`, which only `approve` and `merge` write to.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire.store import WireStore

WRAP = 92


def show(c: dict, n: int, total: int) -> dict:
    p = json.loads(c["payload"])
    print("\n" + "=" * WRAP)
    print(f"  [{n}/{total}]  {p.get('source_name','?')} · "
          f"{p.get('reporter_name','?')} · {'/'.join(p.get('teams') or [])}")
    print(f"  {p.get('reporting_type','')} · trust tier {p.get('trust_tier','?')}"
          f" · {p.get('published_at','')[:16]}")
    print("=" * WRAP)
    print(f"\n  HEADLINE  {p.get('headline') or '(none captured)'}")
    print(f"  URL       {p.get('canonical_url','')}")
    print(f"  BODY      {p.get('body_chars',0):,} chars · sha "
          f"{(p.get('content_sha256') or '')[:12]} · {p.get('original_language','en')}")
    label = p.get("wire_label") or "(none)"
    print(f"  LABEL     {label}")
    print("\n  EXCERPT")
    for line in textwrap.wrap(p.get("excerpt", "")[:520], WRAP - 6):
        print(f"    {line}")
    if p.get("facts"):
        print("\n  EVIDENCE")
        for f in p["facts"]:
            print(f"    · {f.get('claim','')}")
            for line in textwrap.wrap(f'"{f.get("evidence_text","")}"', WRAP - 8):
                print(f"      {line}")
    else:
        # Honest about what has not been built yet, rather than implying a
        # model already read this.
        print("\n  EVIDENCE  none extracted yet (evidence step not built)")
    if p.get("fantasy_relevance"):
        print("\n  FANTASY SPIN (LineupBeat, not the reporter)")
        for line in textwrap.wrap(p["fantasy_relevance"], WRAP - 6):
            print(f"    {line}")
    return p


def ask(prompt: str, default: str = "") -> str:
    try:
        v = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
    return v or default


def edit(p: dict) -> dict:
    """Change what is published. The URL, body and hash are never editable.

    A reviewer may rewrite LineupBeat's words. He may not rewrite what was
    captured from the source, because the audit trail has to keep matching
    the article it came from.
    """
    p = dict(p)
    print("\n  leave blank to keep the current value")
    h = ask(f"    headline [{(p.get('headline') or '')[:50]}]: ")
    if h:
        p["headline"] = h
    lab = ask(f"    label [{p.get('wire_label') or 'none'}]: ")
    if lab:
        p["wire_label"] = lab.upper()
    spin = ask("    fantasy spin (LineupBeat's voice): ")
    if spin:
        p["fantasy_relevance"] = spin
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--published", action="store_true")
    ap.add_argument("--actor", default="reviewer")
    args = ap.parse_args()

    store = WireStore()

    if args.published:
        pubs = store.publications()
        print(f"  {len(pubs)} published item(s)")
        for r in pubs:
            p = json.loads(r["payload"])
            print(f"    v{r['version']}  {p.get('source_name','?'):<18} "
                  f"{(p.get('headline') or p.get('canonical_url'))[:64]}")
        return 0

    queue = store.candidates("EDITORIAL_REVIEW")
    if args.list:
        print(f"  {len(queue)} awaiting review")
        for c in queue:
            p = json.loads(c["payload"])
            print(f"    {c['candidate_id'][:10]}  {p.get('source_name','?'):<18} "
                  f"{(p.get('headline') or p.get('canonical_url'))[:60]}")
        return 0

    if not queue:
        print("  queue is empty")
        return 0

    print(f"  {len(queue)} candidate(s) awaiting review. "
          f"a=approve e=edit r=reject m=merge s=skip q=quit")
    published = rejected = skipped = 0

    for i, c in enumerate(queue, 1):
        p = show(c, i, len(queue))
        while True:
            choice = ask("\n  [a/e/r/m/s/q] > ").lower()[:1]
            if choice == "q":
                print(f"\n  stopped. {published} published, {rejected} rejected, "
                      f"{skipped} skipped, {len(queue) - i + 1} left")
                store.export_publications()
                return 0
            if choice == "s":
                skipped += 1
                break
            if choice == "r":
                why = ask("    reason: ") or "no reason given"
                store.set_state(c["candidate_id"], "REJECTED", args.actor, why)
                rejected += 1
                break
            if choice == "e":
                p = edit(p)
                store.update_payload(c["candidate_id"], p)
                show({"payload": json.dumps(p)}, i, len(queue))
                continue
            if choice == "m":
                other = ask("    merge into which publication_id? ")
                if not other:
                    continue
                # Same real-world event: supersede rather than add a card.
                store.publish(c["candidate_id"], p, other, args.actor)
                published += 1
                break
            if choice == "a":
                if not p.get("wire_label"):
                    print("    a label is required before publishing "
                          "(e=edit to set one)")
                    continue
                store.set_state(c["candidate_id"], "APPROVED", args.actor)
                store.publish(c["candidate_id"], p,
                              c["event_fingerprint"], args.actor)
                published += 1
                break
            print("    a=approve e=edit r=reject m=merge s=skip q=quit")

    n, changed = store.export_publications()
    print(f"\n  done. {published} published, {rejected} rejected, {skipped} skipped")
    print(f"  {n} item(s) in data/wire_publications.json"
          + (" (updated)" if changed else " (unchanged)"))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
