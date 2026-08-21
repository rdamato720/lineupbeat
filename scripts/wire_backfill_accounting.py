#!/usr/bin/env python3
"""Mutually exclusive accounting for the backfill, and the extraction failures.

    python3 scripts/wire_backfill_accounting.py

The earlier report mixed two denominators: 619 was discovery outcomes from
one run, while 485/323/11 were the whole stored corpus split by publication
time. Neither was wrong; presenting them as one series was. Every number
here states what it counts.

Extraction quality is measured on eligible, inside-window, unique articles.
A mailbag we deliberately refused is not an extraction failure, and putting
it in the denominator would flatter the rate; a genuine failure on a real
beat report is exactly what this is for, so those are listed by source.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from wire import registry as artreg
from wire.store import WireStore
from wire_backfill import parse_time

OUT = Path("data/wire_backfill_accounting.json")
WINDOW_HOURS = 48


def main():
    store = WireStore()
    sources = {s.source_id: s for s in artreg.load()}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    state = json.loads(Path("data/wire_backfill.json").read_text())
    disc = state.get("discovery", {})

    rows = [dict(r) for r in store.conn.execute(
        "SELECT * FROM wire_source_items").fetchall()]
    for r in rows:
        t = parse_time(r["published_at"])
        r["_when"] = ("missing_time" if t is None
                      else "inside" if t >= cutoff else "outside")
        src = sources.get(r["source_id"])
        r["_class"] = src.source_class if src else "UNKNOWN"
        r["_adapter"] = src.adapter if src else "UNKNOWN"
        r["_team"] = (src.teams[0] if src and src.teams else "?")

    # --- 1. discovery outcomes, one run -------------------------------
    d_new = disc.get("articles_new", 0)
    d_seen = disc.get("articles_seen", 0)
    d_bad = disc.get("extraction_failures", 0)
    refused = {k.split("::")[1]: v for k, v in disc.items()
               if str(k).startswith("refused::")}
    print("  1. DISCOVERY OUTCOMES — one backfill run, denominator = URLs the")
    print("     adapters offered after their own team/author/content filters")
    print(f"     {'newly captured':<34}{d_new:>6}")
    print(f"     {'already stored (canonical URL seen)':<34}{d_seen:>6}")
    print(f"     {'captured but unusable':<34}{d_bad:>6}")
    print(f"     {'total discovery outcomes':<34}{d_new + d_seen + d_bad:>6}")
    print(f"     refused before capture (not counted above): "
          f"{sum(refused.values())}")
    for k, v in sorted(refused.items(), key=lambda x: -x[1])[:6]:
        print(f"        {v:>5}  {k}")

    # --- 2. the stored corpus, mutually exclusive ---------------------
    when = Counter(r["_when"] for r in rows)
    print(f"\n  2. UNIQUE CANONICAL ARTICLES STORED — denominator = every")
    print(f"     article ever captured, cumulative, one row per canonical URL")
    print(f"     {'inside the 48h window':<34}{when['inside']:>6}")
    print(f"     {'outside the window':<34}{when['outside']:>6}")
    print(f"     {'no reliable publication time':<34}{when['missing_time']:>6}")
    print(f"     {'total':<34}{len(rows):>6}")

    # --- 3. extraction, on the eligible in-window population ----------
    # A deliberate content refusal is not an extraction failure. The
    # official-site adapter marks marketing, broadcast notes and mailbags
    # INCOMPLETE with a note, and counting those as failures put policy
    # decisions in the denominator: all 98 returned HTTP 200 with a readable
    # body. Only a genuine failure -- a paywall, a body too short to use, a
    # fetch that did not return -- belongs here.
    def refused_by_policy(r):
        note = (r["note"] or "").lower()
        return note.startswith("official team site:") or "excluded" in note

    inside = [r for r in rows if r["_when"] == "inside"]
    policy = [r for r in inside if r["extraction_status"] != "COMPLETE"
              and refused_by_policy(r)]
    eligible = [r for r in inside if r not in policy]
    ok = [r for r in eligible if r["extraction_status"] == "COMPLETE"]
    bad = [r for r in eligible if r["extraction_status"] != "COMPLETE"]
    print(f"\n  3. EXTRACTION — denominator = unique, inside-window articles")
    print(f"     that were eligible: {len(inside)} in window, minus "
          f"{len(policy)} refused by content policy = {len(eligible)}")
    print(f"     {'full text extracted':<34}{len(ok):>6}")
    print(f"     {'failed':<34}{len(bad):>6}")
    print(f"     success rate on eligible in-window articles: "
          f"{100 * len(ok) / max(1, len(eligible)):.1f}%")

    allbad = [r for r in rows if r["extraction_status"] != "COMPLETE"
              and not refused_by_policy(r)]
    allpolicy = [r for r in rows if r["extraction_status"] != "COMPLETE"
                 and refused_by_policy(r)]
    print(f"     content refusals, not failures, corpus-wide: {len(allpolicy)}")
    from collections import Counter as _C
    for k, v in _C((r["note"] or "").split(":")[-1].split("(")[0].strip()[:44]
                   for r in allpolicy).most_common(5):
        print(f"        {v:>5}  {k}")
    print(f"\n     genuine extraction failures corpus-wide: {len(allbad)}")
    print(f"     {'by window':<22}{dict(Counter(r['_when'] for r in allbad))}")
    print(f"     {'by source class':<22}{dict(Counter(r['_class'] for r in allbad))}")
    print(f"     {'by adapter':<22}{dict(Counter(r['_adapter'] for r in allbad))}")
    reasons = Counter()
    for r in allbad:
        note = (r["note"] or "unrecorded").split(";")[0].strip()
        note = note.split("(")[0].strip()[:52]
        reasons[note] += 1
    print("     by reason:")
    for k, v in reasons.most_common(8):
        print(f"        {v:>5}  {k}")
    print(f"     by HTTP status: "
          f"{dict(Counter(str(r['http_status']) for r in allbad).most_common(6))}")
    print(f"     a previously stored full body was available for: "
          f"{sum(1 for r in allbad if (r['raw_text'] or '').strip())} of "
          f"{len(allbad)}")

    print(f"\n     ten sources with the most eligible (in-window) failures:")
    per = Counter(r["source_id"] for r in bad)
    for sid, n in per.most_common(10):
        tot = sum(1 for r in eligible if r["source_id"] == sid)
        src = sources.get(sid)
        print(f"        {sid[:30]:<32}{n:>3} of {tot:<4} "
              f"{(src.source_class if src else '?'):<20}"
              f"{100 * n / max(1, tot):.0f}% fail")
    if not per:
        print("        none")

    payload = {
        "window_hours": WINDOW_HOURS,
        "discovery_outcomes": {"newly_captured": d_new,
                               "already_stored": d_seen,
                               "captured_unusable": d_bad,
                               "total": d_new + d_seen + d_bad,
                               "refused_before_capture": refused},
        "stored_articles": {"inside_window": when["inside"],
                            "outside_window": when["outside"],
                            "missing_publication_time": when["missing_time"],
                            "total": len(rows)},
        "extraction": {
            "in_window": len(inside),
            "refused_by_content_policy": len(policy),
            "eligible_in_window": len(eligible),
            "succeeded": len(ok), "failed": len(bad),
            "success_rate_pct": round(100 * len(ok) / max(1, len(eligible)), 1),
            "corpus_genuine_failures": len(allbad),
            "corpus_content_refusals": len(allpolicy),
            "by_window": dict(Counter(r["_when"] for r in allbad)),
            "by_source_class": dict(Counter(r["_class"] for r in allbad)),
            "by_adapter": dict(Counter(r["_adapter"] for r in allbad)),
            "by_team": dict(Counter(r["_team"] for r in allbad).most_common(12)),
            "by_reason": dict(reasons.most_common(10)),
            "by_http_status": dict(Counter(str(r["http_status"])
                                           for r in allbad).most_common(8)),
            "partial_body_available": sum(
                1 for r in allbad if (r["raw_text"] or "").strip()),
            "worst_sources_in_window": [
                {"source_id": sid, "failed": n,
                 "eligible": sum(1 for r in eligible if r["source_id"] == sid)}
                for sid, n in per.most_common(10)],
        },
    }
    OUT.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"\n  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
