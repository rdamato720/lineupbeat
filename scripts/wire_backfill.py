#!/usr/bin/env python3
"""The 48-hour website backfill. Deterministic filters first, model last.

    python3 scripts/wire_backfill.py --discover        # stages 1-4
    python3 scripts/wire_backfill.py --interpret --cap 15
    python3 scripts/wire_backfill.py --report

Claude is the most expensive stage and the only one that can be wrong in an
interesting way, so everything that can be decided without it is decided
first: the publication window, the canonical team path, extraction,
currentness, position, fantasy relevance, identity, authority, relay, and
duplicate and underlying-report linking. What reaches the model is what none
of those could settle.

Two rewrites of one original report are one report. Sending both would cost
twice and produce a second card that looks like corroboration.

The window is measured on the publisher's own timestamp, never on when we
happened to fetch it. An article with no reliable publication time is
excluded rather than assumed recent.

Nothing here publishes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import evidence as ev
from wire import players as pl
from wire import registry as artreg
from wire import relevance as rv
from wire import semantic as sem
from wire import semantic_validate as sv
from wire import si
from wire.providers.claude import ClaudeSemanticProvider
from wire.store import WireStore

STATE = Path("data/wire_backfill.json")
WINDOW_HOURS = 48


def now_utc():
    return datetime.now(timezone.utc)


def parse_time(value: str):
    """The publisher's timestamp, or None. None means excluded, not recent."""
    if not value:
        return None
    v = str(value).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d"):
        try:
            dt = datetime.fromisoformat(v) if fmt is None \
                else datetime.strptime(v, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def discover(store, cutoff, limit_per_source: int) -> dict:
    """Stages 1-4: discovery, canonical/team validation, extraction, storage."""
    sources = [s for s in artreg.load()
               if s.active and s.adapter and not s.paid]
    stats = Counter()
    stats["sources_checked"] = len(sources)
    per_source = {}
    for src in sources:
        r = subprocess.run(
            [sys.executable, "scripts/wire_ingest.py", "--review",
             "--only", src.source_id, "--limit", str(limit_per_source)],
            capture_output=True, text=True, timeout=1800)
        out = r.stdout
        import re as _re
        m = _re.search(r"new candidates (\d+), already seen (\d+), "
                       r"not-a-candidate (\d+)", out)
        if m:
            new, seen, bad = (int(x) for x in m.groups())
            stats["articles_new"] += new
            stats["articles_seen"] += seen
            stats["not_a_candidate"] += bad
            per_source[src.source_id] = {"new": new, "seen": seen,
                                         "not_candidate": bad}
        for key, label in (("refused before capture", "refused_pre_capture"),
                           ("refused on content type", "refused_content"),
                           ("extraction failed", "extraction_failed"),
                           ("other  ", "other")):
            mm = _re.search(rf"{key}\s+(\d+)", out)
            if mm:
                stats[label] += int(mm.group(1))
        for line in out.split("\n"):
            mm = _re.match(r"\s+(\d+)\s+(\w+)::?\s*(.+)", line.strip()
                           if False else line)
            if mm:
                stats[f"detail::{mm.group(2)}::{mm.group(3).strip()[:56]}"] += int(mm.group(1))
        for line in []:
            if "excluded:" in line:
                reason = line.split("excluded:")[1].strip()
                key = reason.split("(")[0].strip()
                if key.startswith("canonical url is a"):
                    key = "wrong team"
                elif "not in the registry" in reason:
                    key = "unknown author"
                stats[f"refused::{key}"] += 1
    stats["per_source"] = per_source
    return dict(stats)


def in_window(store, cutoff) -> tuple[list, Counter]:
    """Stored articles whose publisher timestamp is inside the window."""
    counts = Counter()
    keep = []
    for r in store.conn.execute(
            "SELECT source_item_id, canonical_url, published_at, source_id, "
            "headline FROM wire_source_items").fetchall():
        ts = parse_time(r["published_at"])
        if ts is None:
            counts["no_reliable_publication_time"] += 1
            continue
        if ts < cutoff:
            counts["outside_window"] += 1
            continue
        counts["inside_window"] += 1
        keep.append(dict(r))
    return keep, counts


def candidates_for(store, urls: set) -> list:
    """Evidence candidates belonging to in-window articles."""
    out = []
    for r in store.evidence():
        if r["review_status"] != "PENDING":
            continue
        if r["source_url"] not in urls:
            continue
        out.append(dict(r))
    return out


def deterministic_filter(rows, rel_registry, sources) -> tuple[list, Counter, list]:
    """Everything decidable without the model. Reasons recorded, not implied."""
    counts = Counter()
    suppressed = []
    survivors = []
    seen_reports = {}
    seen_claims = {}

    for r in rows:
        src = sources.get(r["source_id"])
        # Paid sources never reach here, but assert it rather than assume.
        if src is not None and src.paid:
            counts["paid_source"] += 1
            suppressed.append({**_slim(r), "reason": "paid source"})
            continue
        if r["exclusion_reason"]:
            counts["non_fantasy_position_or_context"] += 1
            suppressed.append({**_slim(r), "reason": r["exclusion_reason"]})
            continue
        if not r["player_id"]:
            counts["identity_not_exact"] += 1
            suppressed.append({**_slim(r), "reason": "no exact player identity"})
            continue
        if r["duplicate_of"]:
            counts["duplicate_claim"] += 1
            suppressed.append({**_slim(r), "reason": "duplicate of an earlier claim"})
            continue
        if r["evidence_class"] == "RELAYED_REPORTING":
            counts["relayed_reporting"] += 1
            suppressed.append({**_slim(r), "reason": "relayed reporting"})
            continue
        if r["evidence_class"] not in ("FIRSTHAND_OBSERVATION",
                                       "DIRECT_QUOTATION"):
            counts["not_original_evidence"] += 1
            suppressed.append({**_slim(r),
                               "reason": f"{r['evidence_class']} is not "
                                         f"original evidence"})
            continue

        verdict = rv.assess(r["player_id"], r["position"], r["evidence_text"],
                            rel_registry)
        if not verdict["eligible"]:
            counts["fantasy_relevance_gate"] += 1
            suppressed.append({**_slim(r), "reason": verdict["reason"],
                               "tier": verdict["tier"]})
            continue
        if verdict["tier"] == rv.CONTINGENT:
            counts["evidence_created_relevance"] += 1

        # One underlying report, one call. Two sites rewriting one original
        # is not two reports and must not be paid for twice.
        urid = r["underlying_report_id"]
        if urid:
            if urid in seen_reports:
                counts["same_underlying_report"] += 1
                suppressed.append({**_slim(r),
                                   "reason": "another article already carries "
                                             "this underlying report"})
                continue
            seen_reports[urid] = r["candidate_id"]
        ckey = (r["player_id"], ev.norm_claim(r["evidence_text"])[:180])
        if ckey in seen_claims:
            counts["identical_claim"] += 1
            suppressed.append({**_slim(r), "reason": "identical claim already "
                                                     "queued"})
            continue
        seen_claims[ckey] = r["candidate_id"]

        r["relevance_tier"] = verdict["tier"]
        r["relevance_reason"] = verdict["reason"]
        survivors.append(r)
    return survivors, counts, suppressed


def _slim(r):
    return {"candidate_id": r["candidate_id"], "player_name": r["player_name"],
            "team": r["team"], "position": r["position"],
            "source_id": r["source_id"],
            "evidence_class": r["evidence_class"],
            "evidence_text": r["evidence_text"][:220]}


# Review order: what a fantasy manager needs first.
PRIORITY = [
    ("injury, absence, participation, return", ("INJURY",
     "LIMITED_PARTICIPATION", "RETURN_TO_PRACTICE", "TRANSACTION")),
    ("starter and depth-chart developments", ("DEPTH_CHART",)),
    ("first- and second-team reps", ("FIRST_TEAM_REPS", "SECOND_TEAM_REPS",
                                     "THIRD_TEAM_REPS")),
    ("red-zone and goal-line work", ("RED_ZONE",)),
    ("targets, routes, carries, backfield", ("TARGETS", "ROUTES", "CARRIES",
                                             "SNAP_SHARE")),
    ("material quotations", ("COACH_OR_PLAYER_QUOTATION",)),
    ("other fantasy-relevant observation", ()),
]


def priority_of(mechanism: str) -> int:
    for i, (_, mechs) in enumerate(PRIORITY):
        if mechanism in mechs:
            return i
    return len(PRIORITY) - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--interpret", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--cap", type=float, default=15.0)
    ap.add_argument("--limit-per-source", type=int, default=12)
    ap.add_argument("--hours", type=int, default=WINDOW_HOURS)
    args = ap.parse_args()

    store = WireStore()
    end = now_utc()
    cutoff = end - timedelta(hours=args.hours)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    state.setdefault("window", {"from": cutoff.replace(microsecond=0).isoformat(),
                                "to": end.replace(microsecond=0).isoformat(),
                                "hours": args.hours})
    print(f"  window {state['window']['from']} .. {state['window']['to']} "
          f"({args.hours}h, publisher time)")

    if args.discover:
        t0 = time.time()
        d = discover(store, cutoff, args.limit_per_source)
        d["seconds"] = round(time.time() - t0, 1)
        state["discovery"] = d
        STATE.write_text(json.dumps(state, indent=1) + "\n")
        # Mutually exclusive, and every outcome accounted for. The old line
        # printed one "unusable" number that mixed pre-capture refusals,
        # content refusals and extraction failures, and it was then reported
        # as an extraction-failure count, which it never was.
        nc = d.get("not_a_candidate", 0)
        print(f"  sources checked {d['sources_checked']}")
        print(f"    {'newly captured':<30}{d.get('articles_new', 0):>6}")
        print(f"    {'already stored':<30}{d.get('articles_seen', 0):>6}")
        print(f"    {'not a candidate':<30}{nc:>6}")
        for label, key in (("refused before capture", "refused_pre_capture"),
                           ("refused on content type", "refused_content"),
                           ("extraction failed", "extraction_failed"),
                           ("other", "other")):
            print(f"       {label:<28}{d.get(key, 0):>6}")
        parts = sum(d.get(k, 0) for k in ("refused_pre_capture",
                                          "refused_content",
                                          "extraction_failed", "other"))
        print(f"       {'sub-total':<28}{parts:>6}"
              f"{'  reconciles' if parts == nc else '  DOES NOT RECONCILE'}")
        for k, v in sorted(((k, v) for k, v in d.items()
                            if str(k).startswith("detail::")),
                           key=lambda x: -x[1])[:12]:
            print(f"      {v:>5}  {k.split('::', 1)[1].replace('::', ': ')}")
        return 0

    # Stage 5 onward, from what is stored.
    articles, wcounts = in_window(store, cutoff)
    urls = {a["canonical_url"] for a in articles}
    state["window_articles"] = {k: v for k, v in wcounts.items()}
    print(f"  articles: {wcounts['inside_window']} inside the window, "
          f"{wcounts['outside_window']} older, "
          f"{wcounts['no_reliable_publication_time']} with no reliable time")

    rows = candidates_for(store, urls)
    print(f"  evidence candidates in window: {len(rows)}")
    sources = {s.source_id: s for s in artreg.load()}
    rel = rv.load()
    survivors, counts, suppressed = deterministic_filter(rows, rel, sources)
    state["deterministic"] = dict(counts)
    state["suppressed"] = suppressed
    print(f"  after deterministic filters: {len(survivors)} reach the model")
    for k, v in counts.most_common():
        print(f"      {v:>5}  {k}")

    if not args.interpret:
        STATE.write_text(json.dumps(state, indent=1) + "\n")
        return 0

    prov = ClaudeSemanticProvider()
    if not prov.available():
        print("  Claude unavailable; evidence retained, nothing interpreted")
        return 4

    spend = 0.0
    results, lat = [], []
    calls = fails = abstains = interprets = retries = 0
    for i, r in enumerate(survivors, 1):
        if spend >= args.cap:
            print(f"\n  COST CAP ${args.cap:.2f} reached after {calls} calls; "
                  f"stopping cleanly with {len(survivors) - i + 1} row(s) "
                  f"un-interpreted")
            state["stopped_at_cap"] = {"after_calls": calls,
                                       "remaining": len(survivors) - i + 1}
            break
        players = [{"player_id": r["player_id"], "player_name": r["player_name"],
                    "team": r["team"], "position": r["position"]}]
        meta = {"team": r["team"], "article_title": r["source_title"],
                "published_at": r["published_at"],
                "source_name": (sources[r["source_id"]].source_name
                                if r["source_id"] in sources else r["source_id"]),
                "author": r["source_author_or_channel"],
                "source_ownership": r["source_ownership"] or "INDEPENDENT",
                "duplicate_of": r["duplicate_of"],
                "underlying_report_id": r["underlying_report_id"]}
        try:
            a = sv.evaluate_with_retry(prov, r["evidence_text"], meta,
                                       players, pl.load())
        except Exception as e:
            fails += 1
            continue
        calls += 1
        if getattr(a, "retry_attempted", False):
            retries += 1
        spend += a.cost_usd
        lat.append(a.latency_ms)
        if a.decision == "INTERPRET":
            interprets += 1
        elif a.decision == sem.ABSTAIN:
            abstains += 1
        results.append({"candidate": _slim(r),
                        "relevance_tier": r.get("relevance_tier"),
                        "relevance_reason": r.get("relevance_reason"),
                        "source_url": r["source_url"],
                        "published_at": r["published_at"],
                        "author": r["source_author_or_channel"],
                        "source_name": meta["source_name"],
                        "ownership": meta["source_ownership"],
                        "underlying_report_id": r["underlying_report_id"],
                        "assessment": a.to_dict()})
        if i % 10 == 0:
            print(f"    {i}/{len(survivors)}  ${spend:.2f}  "
                  f"{interprets} interpret, {abstains} abstain")

    import statistics
    state["claude"] = {
        "model": prov.model, "prompt_version": sem.PROMPT_VERSION,
        "schema_version": sem.SCHEMA_VERSION,
        "calls": calls, "interpretations": interprets, "abstentions": abstains,
        "provider_failures": fails, "quote_retries": retries,
        "validator_failures": sum(1 for x in results
                                  if x["assessment"]["validation_failures"]),
        "tokens_in": sum(x["assessment"]["tokens_in"] for x in results),
        "tokens_out": sum(x["assessment"]["tokens_out"] for x in results),
        "cost_usd": round(spend, 4), "cap_usd": args.cap,
        "median_latency_ms": int(statistics.median(lat)) if lat else 0,
        "p95_latency_ms": int(sorted(lat)[max(0, int(len(lat) * .95) - 1)]) if lat else 0,
    }
    state["results"] = results
    STATE.write_text(json.dumps(state, indent=1, default=str) + "\n")
    c = state["claude"]
    print(f"\n  Claude: {c['calls']} calls, {c['interpretations']} interpret, "
          f"{c['abstentions']} abstain, {c['validator_failures']} validator "
          f"failures, {c['quote_retries']} quote retries")
    print(f"  tokens {c['tokens_in']} in / {c['tokens_out']} out, "
          f"${c['cost_usd']} of ${c['cap_usd']} cap")
    print(f"  latency median {c['median_latency_ms']}ms p95 {c['p95_latency_ms']}ms")
    print(f"  wrote {STATE}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
