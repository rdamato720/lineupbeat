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
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import claims as claim_rules
from wire import evidence as ev
from wire import currentness
from wire import evidence_integrity as integrity
from wire import players as pl
from wire import registry as artreg
from wire import relevance as rv
from wire import semantic as sem
from wire import semantic_validate as sv
from wire import si
from wire.providers.openai import OpenAISemanticProvider, redact as redact_openai
from wire.store import WireStore

STATE = Path("data/wire_backfill.json")
PLAN = Path("data/wire_backfill_plan.json")
WINDOW_HOURS = 48


EDITORIAL_ONLY = re.compile(
    r"(?i)\b(i['’]?m not sure|people wondered|can be considered|"
    r"bottom half of the league|more dubious|stock (?:up|down)|"
    r"winners? (?:and|&) losers?|player grades?|bold predictions?)\b")

BOX_SCORE_ONLY = re.compile(
    r"(?i)\b(?:added|finished with|recorded|had)\s+\d+\s+"
    r"(?:rushing|receiving|passing)?\s*yards?\s+(?:on|from)\s+"
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:carries|catches|receptions?|attempts?)\b")

NON_DEVELOPMENT_CONTEXT = re.compile(
    r"(?i)\b(pre[- ]game warmups?|caught up with (?:a )?former teammate)\b")

ROUTINE_BACKUP_CONTEXT = re.compile(
    r"(?i)\b(?:a few|limited) snaps? (?:out of|for) .{0,45}"
    r"(?:primarily|mostly) backups?\b")


def _coordinated_usage(text: str, player_name: str) -> bool:
    """True only for an explicit joined subject sharing one usage verb."""
    parts = [re.escape(x) for x in (player_name or "").split()]
    if not parts:
        return False
    player = r"\s+".join(parts)
    other = r"[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,2}"
    return bool(re.search(
        rf"(?i)\b{player}\s+and\s+{other}\s+"
        rf"(?:played|logged|took|received|saw)\s+\d+\s+"
        rf"(?:offensive\s+)?(?:snaps?|targets?|carries|routes?)\b",
        text or ""))


def pre_model_claim_gate(row: dict) -> tuple[bool, str]:
    """Refuse only deterministic player/claim failures before a model call."""
    text = row.get("evidence_text", "") or ""
    name = row.get("player_name", "") or ""
    klass = row.get("evidence_class", "") or ""

    if EDITORIAL_ONLY.search(text):
        return False, "editorial opinion or ranking context, not a development"
    if BOX_SCORE_ONLY.search(text):
        return False, "isolated box-score production with no role change"

    if klass == "OFFICIAL_DESIGNATION":
        return True, "official designation requires semantic interpretation"

    if klass == "DIRECT_QUOTATION":
        quotes = list(re.finditer(r'["“]([^"“”]{2,})["”]', text))
        if not quotes:
            return True, "attributed statement requires semantic interpretation"
        surname = pl.norm(name).split()[-1] if name else ""
        norm_quoted = pl.norm(" ".join(m.group(1) for m in quotes))
        if surname and surname in norm_quoted.split():
            return True, "the supplied player is named in the quoted words"
        raw_player_at = text.lower().find(name.lower()) if name else -1
        if raw_player_at > max(m.end() for m in quotes):
            return False, "the supplied player appears only after the quotation"
        return True, "quotation subject requires semantic interpretation"

    mechanism = claim_rules.fantasy_mechanism(text, name, klass)
    if mechanism["mechanism"] != claim_rules.NO_FANTASY_IMPACT:
        return True, mechanism["detail"]
    if _coordinated_usage(text, name):
        return True, "explicit coordinated usage for both named players"
    if mechanism["detail"].startswith("an isolated play"):
        return False, mechanism["detail"]
    if NON_DEVELOPMENT_CONTEXT.search(text):
        return False, "non-football pregame context, not a development"
    if ROUTINE_BACKUP_CONTEXT.search(text):
        return False, "routine backup participation with no role change"
    name_parts = [re.escape(x) for x in name.split()]
    comparison_name = (r"\s+".join(name_parts) + "|" + name_parts[-1]
                       if name_parts else "")
    if comparison_name and re.search(
            rf"(?i)\b(?:starting )?job\s+to\s+(?:{comparison_name})\b", text):
        return False, "the supplied player is the comparison, not the claim subject"
    return True, "no deterministic failure; semantic interpretation required"


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


def in_window(store, cutoff, end) -> tuple[list, Counter]:
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
        if ts > end:
            counts["after_window"] += 1
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


def deterministic_filter(rows, rel_registry, sources,
                         player_registry=None) -> tuple[list, Counter, list]:
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
        if player_registry is not None:
            reg_player = player_registry.by_id.get(r["player_id"])
            if (reg_player is None or pl.norm(reg_player.full_name) !=
                    pl.norm(r["player_name"]) or reg_player.team != r["team"] or
                    reg_player.position != r["position"]):
                counts["registry_identity_mismatch"] += 1
                suppressed.append({**_slim(r),
                                   "reason": "candidate identity disagrees "
                                             "with wire_players"})
                continue
        current = currentness.automatic_currentness(
            r["source_url"], r.get("event_timestamp", ""))
        if not current["eligible"]:
            counts["unreliable_event_time"] += 1
            suppressed.append({**_slim(r), "reason": current["reason"]})
            continue
        if r["duplicate_of"]:
            counts["duplicate_claim"] += 1
            suppressed.append({**_slim(r), "reason": "duplicate of an earlier claim"})
            continue
        if r["evidence_class"] == "RELAYED_REPORTING":
            counts["relayed_reporting"] += 1
            suppressed.append({**_slim(r), "reason": "relayed reporting"})
            continue
        # What may reach the model. An official designation is not firsthand
        # and is not a quotation, and it is better evidence of availability
        # than either: the club is the authority on its own practice report.
        if r["evidence_class"] not in ("FIRSTHAND_OBSERVATION",
                                       "DIRECT_QUOTATION",
                                       "OFFICIAL_DESIGNATION"):
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
            # A tally of survivors, not a rejection: this row continues to
            # the model. Named so it cannot be added into a rejection total
            # again -- doing so is how the earlier accounting reported 2,637
            # outcomes for a corpus of 2,636.
            counts["note::contingent_relevance_survivors"] += 1

        claim_ok, claim_reason = pre_model_claim_gate(r)
        if not claim_ok:
            counts["no_player_specific_development"] += 1
            suppressed.append({**_slim(r), "reason": claim_reason,
                               "tier": verdict["tier"]})
            continue

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


def _result_candidate(r):
    """The model/reviewer record always carries complete evidence.

    ``_slim`` is only for the suppressed list.  Reusing it for model results
    caused the independent reviewer and human page to receive 220 characters
    while the generator had read the full passage.
    """
    return {"candidate_id": r["candidate_id"],
            "player_id": r["player_id"], "player_name": r["player_name"],
            "team": r["team"], "position": r["position"],
            "source_id": r["source_id"],
            "source_title": r["source_title"],
            "evidence_class": r["evidence_class"],
            "evidence_text": r["evidence_text"]}


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


def select_survivors(rows: list[dict], include=None, exclude=None):
    """Select exact candidate ids without changing deterministic order.

    An included id that is not in the current survivor set is an error at the
    call site, not something to ignore: the 48-hour window may have moved
    since the plan was reviewed, and silently substituting another row would
    spend money on evidence nobody selected.
    """
    wanted = set(include or [])
    refused = set(exclude or [])
    available = {r["candidate_id"] for r in rows}
    missing = sorted(wanted - available)
    overlap = sorted(wanted & refused)
    selected = [r for r in rows if not wanted or r["candidate_id"] in wanted]
    selected = [r for r in selected if r["candidate_id"] not in refused]
    return selected, missing, overlap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--interpret", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--plan", action="store_true",
                    help="write the deterministic model-call plan; no API calls")
    ap.add_argument("--cap", type=float, default=15.0)
    ap.add_argument("--max-calls", type=int, default=15,
                    help="hard model-call limit, independent of dollar cap")
    ap.add_argument("--limit-per-source", type=int, default=12)
    ap.add_argument("--candidate-id", action="append", default=[],
                    help="interpret/plan only these exact candidate ids; repeatable")
    ap.add_argument("--exclude-candidate-id", action="append", default=[],
                    help="skip these candidate ids; repeatable")
    ap.add_argument("--hours", type=int, default=WINDOW_HOURS)
    args = ap.parse_args()

    store = WireStore()
    end = now_utc()
    cutoff = end - timedelta(hours=args.hours)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if args.interpret:
        # This file describes the current run.  A legacy Claude summary from
        # the tracked handoff must not look like part of an OpenAI-only run.
        for stale in ("claude", "openai", "results",
                      "stopped_at_call_cap", "stopped_at_cap",
                      "stopped_at_provider_failure"):
            state.pop(stale, None)
    window = {"from": cutoff.replace(microsecond=0).isoformat(),
              "to": end.replace(microsecond=0).isoformat(),
              "hours": args.hours}
    state.setdefault("first_window", dict(state.get("window") or window))
    state["window"] = window
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
    articles, wcounts = in_window(store, cutoff, end)
    urls = {a["canonical_url"] for a in articles}
    state["window_articles"] = {k: v for k, v in wcounts.items()}
    print(f"  articles: {wcounts['inside_window']} inside the window, "
          f"{wcounts['outside_window']} older, "
          f"{wcounts['no_reliable_publication_time']} with no reliable time")

    rows = candidates_for(store, urls)
    print(f"  evidence candidates in window: {len(rows)}")
    sources = {s.source_id: s for s in artreg.load()}
    rel = rv.load()
    player_registry = pl.load()
    survivors, counts, suppressed = deterministic_filter(
        rows, rel, sources, player_registry)
    state["deterministic"] = dict(counts)
    state["suppressed"] = suppressed
    rejected = {k: v for k, v in counts.items() if not k.startswith("note::")}
    notes = {k[6:]: v for k, v in counts.items() if k.startswith("note::")}
    print(f"  after deterministic filters: {len(survivors)} reach the model")
    print(f"  {sum(rejected.values())} rejected + {len(survivors)} sent = "
          f"{sum(rejected.values()) + len(survivors)} "
          f"({'reconciles' if sum(rejected.values()) + len(survivors) == len(rows) else 'DOES NOT RECONCILE against ' + str(len(rows))})")
    for k, v in counts.most_common():
        print(f"      {v:>5}  {k}")

    selected, missing, overlap = select_survivors(
        survivors, args.candidate_id, args.exclude_candidate_id)
    if overlap:
        print("  EXACT SELECTION INVALID; ids are both included and excluded:")
        for candidate_id in overlap:
            print(f"    {candidate_id}")
        return 5
    if missing:
        print("  EXACT SELECTION INVALID; requested ids are not current survivors:")
        for candidate_id in missing:
            print(f"    {candidate_id}")
        print("  0 API calls")
        return 5
    if args.candidate_id or args.exclude_candidate_id:
        print(f"  exact selection: {len(selected)} of {len(survivors)} "
              "current survivor(s)")
    survivors = selected
    if args.interpret and not survivors:
        print("  exact selection is empty; 0 API calls")
        return 5

    if args.plan:
        planned = []
        for r in survivors:
            planned.append({"candidate": _result_candidate(r),
                            "source_url": r["source_url"],
                            "published_at": r["published_at"],
                            "author": r["source_author_or_channel"],
                            "source_id": r["source_id"],
                            "relevance_tier": r.get("relevance_tier"),
                            "relevance_reason": r.get("relevance_reason")})
        payload = {"generated_at": now_utc().isoformat(), "window": window,
                   "count": len(planned), "model_calls_made": 0,
                   "candidates": planned}
        PLAN.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
        print(f"  wrote {PLAN}: {len(planned)} planned call(s), 0 API calls")
        return 0

    if not args.interpret:
        STATE.write_text(json.dumps(state, indent=1) + "\n")
        return 0

    prov = OpenAISemanticProvider()
    if not prov.available():
        print("  OpenAI unavailable; evidence retained, nothing interpreted")
        return 4

    spend = 0.0
    results, lat = [], []
    calls = fails = abstains = interprets = retries = 0
    provider_errors = []
    for i, r in enumerate(survivors, 1):
        if calls >= args.max_calls:
            print(f"\n  CALL CAP {args.max_calls} reached; stopping cleanly "
                  f"with {len(survivors) - i + 1} row(s) un-interpreted")
            state["stopped_at_call_cap"] = {"after_calls": calls,
                                             "remaining": len(survivors) - i + 1}
            break
        if spend >= args.cap:
            print(f"\n  COST CAP ${args.cap:.2f} reached after {calls} calls; "
                  f"stopping cleanly with {len(survivors) - i + 1} row(s) "
                  f"un-interpreted")
            state["stopped_at_cap"] = {"after_calls": calls,
                                       "remaining": len(survivors) - i + 1}
            break
        reg_player = player_registry.by_id.get(r["player_id"])
        if (reg_player is None or pl.norm(reg_player.full_name) !=
                pl.norm(r["player_name"]) or reg_player.team != r["team"] or
                reg_player.position != r["position"]):
            fails += 1
            continue
        players = [{"player_id": reg_player.player_id,
                    "player_name": reg_player.full_name,
                    "team": reg_player.team,
                    "position": reg_player.position}]
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
                                       players, player_registry)
        except Exception as e:
            # An attempted request that fails is neither an abstention nor an
            # interpretation. Stop immediately so an account or network
            # failure cannot consume the rest of the batch.
            calls += 1
            fails += 1
            error = redact_openai(f"{type(e).__name__}: {e}")[:400]
            provider_errors.append(error)
            state["stopped_at_provider_failure"] = {
                "after_calls": calls,
                "remaining": len(survivors) - i,
                "error": error,
            }
            print(f"\n  PROVIDER FAILURE after {calls} attempted call(s); "
                  f"stopping with {len(survivors) - i} row(s) un-interpreted")
            break
        calls += 1
        if getattr(a, "retry_attempted", False):
            retries += 1
        spend += a.cost_usd
        lat.append(a.latency_ms)
        if a.decision == "INTERPRET":
            interprets += 1
        elif a.decision == sem.ABSTAIN:
            abstains += 1
        evidence_sha = integrity.sha256_text(r["evidence_text"])
        request_payload = {"evidence_text": r["evidence_text"],
                           "metadata": meta, "players": players}
        results.append({"candidate": _result_candidate(r),
                        "relevance_tier": r.get("relevance_tier"),
                        "relevance_reason": r.get("relevance_reason"),
                        "source_url": r["source_url"],
                        "published_at": r["published_at"],
                        "author": r["source_author_or_channel"],
                        "source_name": meta["source_name"],
                        "ownership": meta["source_ownership"],
                        "underlying_report_id": r["underlying_report_id"],
                        "supplied_identity": {
                            "player_id": reg_player.player_id,
                            "player_name": reg_player.full_name,
                            "team": reg_player.team,
                            "position": reg_player.position,
                            "registry_version": player_registry.version,
                            "registry_check": "VERIFIED"},
                        "generator_input_evidence_sha256": evidence_sha,
                        "generator_request_sha256":
                            integrity.request_sha256(request_payload),
                        "assessment": a.to_dict()})
        if i % 10 == 0:
            print(f"    {i}/{len(survivors)}  ${spend:.2f}  "
                  f"{interprets} interpret, {abstains} abstain")

    import statistics
    state["openai"] = {
        "model": prov.model, "prompt_version": sem.PROMPT_VERSION,
        "schema_version": sem.SCHEMA_VERSION,
        "calls": calls, "interpretations": interprets, "abstentions": abstains,
        "provider_failures": fails, "provider_errors": provider_errors,
        "quote_retries": retries,
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
    c = state["openai"]
    print(f"\n  OpenAI: {c['calls']} calls, {c['interpretations']} interpret, "
          f"{c['abstentions']} abstain, {c['provider_failures']} provider "
          f"failures, {c['validator_failures']} validator failures, "
          f"{c['quote_retries']} quote retries")
    print(f"  tokens {c['tokens_in']} in / {c['tokens_out']} out, "
          f"${c['cost_usd']} of ${c['cap_usd']} cap")
    print(f"  latency median {c['median_latency_ms']}ms p95 {c['p95_latency_ms']}ms")
    print(f"  wrote {STATE}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
