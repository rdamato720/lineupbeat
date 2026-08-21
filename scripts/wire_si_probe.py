#!/usr/bin/env python3
"""Probe SI team pages. Discovery and evaluation only -- nothing published.

    python3 scripts/wire_si_probe.py --teams BUF MIA NE NYJ --capture
    python3 scripts/wire_si_probe.py --all              # the 32-team table
    python3 scripts/wire_si_probe.py --all --json out.json

Discovery is free of consequence: it reads landing pages, judges each article
against the registered team and the author registry, and reports. --capture
additionally fetches the eligible article bodies so extraction rate is
measured rather than assumed.

Nothing here writes to wire_publications.json and nothing here can.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trafilatura

from wire import si
from wire.capture import MIN_BODY_CHARS, PAYWALL, _get


def probe_team(code: str, pages: int, capture: bool, authors: dict,
               pause: float = 1.0, capture_limit: int = 0) -> dict:
    slug = si.CODE_TO_SLUG[code]
    raw, meta = si.discover_team(slug, pages=pages)
    verdicts = [si.evaluate(r, code, authors, r.get("discovery_url", ""))
                for r in raw]

    # A team page is only that team's page if it says so.
    confirmed = False
    try:
        st, body, _ = _get(si.landing_url(slug), timeout=45)
        confirmed = si.confirms_team(body or "", slug)
    except Exception:
        pass

    eligible = [v for v in verdicts if v.eligible]
    excluded = [v for v in verdicts if not v.eligible]
    authors_seen = sorted({v.author for v in verdicts if v.author})
    firsthand = sorted({v.author for v in verdicts
                        if v.author_class == si.FIRSTHAND_APPROVED})

    row = {
        "team": code, "slug": slug, "url": si.landing_url(slug),
        "reachable": meta["reachable"], "team_confirmed": confirmed,
        "pagination": meta["pagination_works"],
        "pages_fetched": meta["pages_fetched"],
        "discovered": len(raw),
        "eligible": len(eligible),
        "excluded": len(excluded),
        "authors_active": len(authors_seen),
        "authors": authors_seen,
        "firsthand_authors": firsthand,
        "exclusions": {},
        "captured": 0, "capture_attempts": 0, "capture_chars": [],
        "capture_failures": {}, "capture_sampled": False,
        "blocking": "",
    }
    for v in excluded:
        key = v.exclusion_reason.split("(")[0].strip()
        row["exclusions"][key] = row["exclusions"].get(key, 0) + 1

    if capture:
        # A sample, when capped. Reported as a sample: an extraction rate
        # measured on five of twenty articles is an estimate and calling it
        # a rate would be the kind of quiet rounding this pipeline exists to
        # avoid.
        todo = eligible[:capture_limit] if capture_limit else eligible
        row["capture_sampled"] = bool(capture_limit) and len(todo) < len(eligible)
        for v in todo:
            row["capture_attempts"] += 1
            try:
                st, html, _ = _get(v.canonical_url, timeout=45)
            except Exception:
                continue
            if not (isinstance(st, int) and st == 200 and html):
                continue
            txt = trafilatura.extract(html, include_comments=False,
                                      include_tables=False,
                                      favor_precision=True) or ""
            # The paywall pattern is tested against the extracted body, not
            # the raw markup. SI's site chrome carries a "SUBSCRIBE NOW"
            # newsletter link on every page, and matching that in the html
            # marked all 87 pilot articles paywalled while their full bodies
            # extracted cleanly. capture() has always got this right -- it
            # consults the pattern only to explain a body that is already too
            # short -- and this now does the same.
            if len(txt) < MIN_BODY_CHARS:
                row["capture_failures"][
                    "paywalled" if PAYWALL.search(txt) else
                    f"body only {len(txt)} chars"] = row["capture_failures"].get(
                        "paywalled" if PAYWALL.search(txt) else
                        f"body only {len(txt)} chars", 0) + 1
            else:
                row["captured"] += 1
                row["capture_chars"].append(len(txt))
            time.sleep(pause)

    if not row["reachable"]:
        row["blocking"] = "landing page not reachable"
    elif not confirmed:
        row["blocking"] = "page does not identify itself as this team"
    elif row["discovered"] == 0:
        row["blocking"] = "no NewsArticle blocks on the landing page"
    elif not firsthand:
        row["blocking"] = "no firsthand-approved author researched yet"
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--teams", nargs="*", default=["BUF", "MIA", "NE", "NYJ"])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--capture", action="store_true")
    ap.add_argument("--capture-limit", type=int, default=0,
                    help="cap article fetches per team; result is a sample")
    ap.add_argument("--json")
    ap.add_argument("--pause", type=float, default=1.0)
    args = ap.parse_args()

    authors = si.load_authors()
    codes = sorted(si.CODE_TO_SLUG) if args.all else args.teams
    rows = []
    for code in codes:
        if code not in si.CODE_TO_SLUG:
            print(f"  {code}: not an NFL team code")
            continue
        row = probe_team(code, args.pages, args.capture, authors, args.pause,
                         args.capture_limit)
        rows.append(row)
        rate = (f"{100*row['captured']/row['capture_attempts']:.0f}%"
                if row["capture_attempts"] else "-")
        print(f"  {row['team']:<4}{'ok' if row['reachable'] else 'DOWN':<5}"
              f"{'pg' if row['pagination'] else '--':<4}"
              f"{row['discovered']:>4} found{row['eligible']:>4} elig"
              f"{row['authors_active']:>4} auth"
              f"{len(row['firsthand_authors']):>3} fh"
              f"  cap {rate:<5}{row['blocking'][:34]}")
        time.sleep(args.pause)

    tot = {k: sum(r[k] for r in rows) for k in
           ("discovered", "eligible", "excluded", "captured", "capture_attempts")}
    print(f"\n  {len(rows)} team(s): {tot['discovered']} discovered, "
          f"{tot['eligible']} eligible, {tot['excluded']} excluded")
    if tot["capture_attempts"]:
        print(f"  full text: {tot['captured']}/{tot['capture_attempts']} "
              f"({100*tot['captured']/tot['capture_attempts']:.0f}%)")
    merged: dict = {}
    for r in rows:
        for k, v in r["exclusions"].items():
            merged[k] = merged.get(k, 0) + v
    if merged:
        print("  exclusions:")
        for k, v in sorted(merged.items(), key=lambda x: -x[1]):
            print(f"    {v:>4}  {k}")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1))
        print(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
