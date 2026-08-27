#!/usr/bin/env python3
"""Draft an inclusion-first article/X mobile review batch.

This script spends only inside explicit call and dollar ceilings.  It records
an attempted candidate before asking the provider, but it never records human
approval and never invokes the publication route.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wire_publication_preview as publication_preview
from beatwire.registry import Registry as BeatRegistry
from wire import mobile_dedupe, players, public_summary, relevance, semantic
from wire import registry as article_registry
from wire.mobile_approval import MAX_CARDS
from wire.mobile_draft import OpenAIMobileDraftProvider, redundant_outlet_lead
from wire.public_labels import DIRECTION_LABELS
from wire.providers.openai import MODEL


ROOT = Path(__file__).resolve().parent.parent
INCLUSIVE = ROOT / "data" / "wire_inclusive_review.json"
PUBLICATIONS = ROOT / "data" / "wire_publications.json"
BEAT_DB = ROOT / "wire-mobile-x.db"
OUT = ROOT / "data" / "wire_mobile_batch.json"
SEEN = ROOT / "data" / "wire_mobile_seen.json"
SCHEMA = "wire-mobile-batch-v1"
SEEN_SCHEMA = "wire-mobile-seen-v1"
LABEL = DIRECTION_LABELS
ARTICLE_RESERVED_CALLS = 8

MULTI_PRACTICE_PATTERN = re.compile(
    r"(?i)\b(across|over|during)\s+(?:the\s+)?(?:last\s+|past\s+|prior\s+)?"
    r"(?:\d+|two|three|four|five|six|seven|eight|nine|ten|multiple|several)\s+"
    r"(?:practices?|days?|sessions?)\b|"
    r"\b(?:second|third|fourth|fifth)\s+(?:straight|consecutive)\s+"
    r"(?:practice|day|session)\b|\bthroughout\s+(?:camp|the\s+week)\b")
DEPTH_CHART_CHANGE = re.compile(
    r"(?i)\b(named (?:the )?(?:starter|starting)|will start|won the starting|"
    r"depth chart|promoted|demoted|moved ahead of|dropped behind|"
    r"took over (?:the )?first[- ]team|elevated to (?:the )?starter)\b")
CONCRETE_ROLE_CHANGE = re.compile(
    r"(?i)\b(role (?:expanded|increased|changed)|workload (?:increased|rose)|"
    r"took over|replaced|in place of|first[- ]team (?:reps|snaps)|"
    r"led .{0,30}(?:targets|routes|carries|touches)|"
    r"majority of .{0,30}(?:targets|routes|carries|touches))\b")
ISOLATED_LIMITATION = re.compile(
    r"(?i)\b(isolated|one (?:play|rep|practice|session)|single "
    r"(?:play|rep|practice|session)|does not establish (?:a |the )?"
    r"(?:regular )?(?:role|workload|target volume|trend))\b")
EDITORIAL_JARGON = ("scoring-use outlook", "short-term starting-qb momentum")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def load_seen() -> dict:
    if not SEEN.exists():
        return {"schema_version": SEEN_SCHEMA, "count": 0, "attempts": []}
    payload = json.loads(SEEN.read_text())
    if payload.get("schema_version") != SEEN_SCHEMA:
        raise ValueError("mobile seen ledger schema is unsupported")
    attempts = payload.get("attempts")
    if not isinstance(attempts, list) or payload.get("count") != len(attempts):
        raise ValueError("mobile seen ledger count is invalid")
    return payload


def save_seen(payload: dict) -> None:
    payload["count"] = len(payload["attempts"])
    tmp = SEEN.with_suffix(SEEN.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(SEEN)


def published_keys() -> tuple[set[str], set[tuple[str, str]]]:
    payload = json.loads(PUBLICATIONS.read_text())
    ids, pairs = set(), set()
    for row in payload.get("publications") or []:
        candidate_id = str(row.get("evidence_candidate_id") or "")
        if candidate_id:
            ids.add(candidate_id)
        player_id, url = str(row.get("player_id") or ""), str(row.get("url") or "")
        if player_id and url:
            pairs.add((player_id, url))
    return ids, pairs


def published_cards() -> list[dict]:
    return list(json.loads(PUBLICATIONS.read_text()).get("publications") or [])


def article_candidates(registry, cutoff: datetime) -> list[dict]:
    if not INCLUSIVE.exists():
        return []
    payload = json.loads(INCLUSIVE.read_text())
    grouped: dict[tuple[str, str], dict] = {}
    for article in payload.get("articles") or []:
        stamp = parse_time(article.get("published_at"))
        if stamp is None or stamp < cutoff:
            continue
        for evidence in article.get("evidence") or []:
            matches, _ = registry.resolve(
                str(evidence.get("player_name") or ""),
                str(evidence.get("team") or ""),
                str(evidence.get("position") or ""),
                str(evidence.get("player_id") or ""),
            )
            if len(matches) != 1 or not matches[0].fantasy_candidate:
                continue
            player = matches[0]
            url = str(article.get("canonical_url") or "")
            key = (url, player.player_id)
            # Preserve historical SI ids so the source expansion does not
            # replay SI evidence that the monitor already reviewed.
            candidate_prefix = (
                "mobile:onsi:" if "si.com/" in url.lower()
                else "mobile:article:"
            )
            row = grouped.setdefault(key, {
                "candidate_id": candidate_prefix + digest(url, player.player_id),
                "player": player.full_name, "player_id": player.player_id,
                "team": player.team, "position": player.position,
                "source_name": str(article.get("source_name") or "Article source"),
                "source_id": str(article.get("source_id") or ""),
                "source_class": str(article.get("source_class") or ""),
                "ownership": str(article.get("source_ownership") or "INDEPENDENT"),
                "author": str(article.get("author") or "Article source"),
                "source_url": url, "published_at": stamp.isoformat(),
                "evidence_parts": [], "origin": "ARTICLE",
            })
            text = str(evidence.get("evidence_text") or "").strip()
            if text and text not in row["evidence_parts"]:
                row["evidence_parts"].append(text)
    out = []
    for row in grouped.values():
        # Every selected segment travels intact into the human-review issue.
        # Do not replace full evidence with a display truncation here.
        row["evidence"] = "\n\n".join(row.pop("evidence_parts"))
        if row["evidence"]:
            out.append(row)
    return out


def player_aliases(registry) -> list[tuple[str, object]]:
    """Exact full-name aliases only; no surname or fuzzy X matching."""
    aliases = []
    for player in registry.players:
        if not player.fantasy_candidate:
            continue
        names = {players.norm(player.full_name), *player.aliases}
        for alias in names:
            words = alias.split()
            if len(words) < 2 or len(words[0]) == 1:
                continue
            aliases.append((alias, player))
    return aliases


def x_candidates(registry, cutoff: datetime) -> list[dict]:
    if not BEAT_DB.exists():
        return []
    sources = {source.id: source for source in BeatRegistry(
        "nfl", load_players=False).sources
               if source.kind == "twitterapi"}
    aliases = player_aliases(registry)
    conn = sqlite3.connect(BEAT_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT item_id,source_id,url,title,body,published_at,fetched_at "
        "FROM items ORDER BY published_at DESC, fetched_at DESC"
    ).fetchall()
    out = []
    for raw in rows:
        source = sources.get(raw["source_id"])
        stamp = parse_time(raw["published_at"] or raw["fetched_at"])
        if source is None or stamp is None or stamp < cutoff:
            continue
        evidence = str(raw["body"] or raw["title"] or "").strip()
        if not evidence:
            continue
        normalized = f" {players.norm(evidence)} "
        matches = {}
        for alias, player in aliases:
            if f" {alias} " in normalized:
                matches[player.player_id] = player
        # The source's single team is a corroborating identity constraint.
        if len(source.teams) == 1:
            matches = {pid: player for pid, player in matches.items()
                       if player.team == source.teams[0]}
        for player in matches.values():
            item_id = str(raw["item_id"])
            out.append({
                "candidate_id": "mobile:x:" + digest(item_id, player.player_id),
                "player": player.full_name, "player_id": player.player_id,
                "team": player.team, "position": player.position,
                "source_name": source.outlet or "X",
                "source_id": source.id, "source_class": "X",
                "ownership": "INDEPENDENT", "author": source.name or source.handle,
                "source_url": str(raw["url"]), "published_at": stamp.isoformat(),
                "evidence": evidence[:6000], "origin": "X",
            })
    return out


def relevance_filter(rows: list[dict], registry: dict) -> tuple[list[dict], list[dict]]:
    """Apply the deterministic draftability gate before any provider call."""
    kept, suppressed = [], []
    for row in rows:
        verdict = relevance.assess(
            row["player_id"], row["position"], row["evidence"], registry)
        if verdict["eligible"]:
            kept.append(row)
        else:
            suppressed.append({"candidate_id": row["candidate_id"],
                               "player": row["player"],
                               "reason": verdict["reason"]})
    return kept, suppressed


def prioritize_candidates(rows: list[dict], max_calls: int) -> list[dict]:
    """Reserve early provider calls for articles without discarding recency.

    Capture combines article candidates with a much faster X stream. A single
    newest-first queue let X consume the entire call ceiling before an article
    published minutes earlier could be interpreted. Put up to eight newest
    article candidates first, then return to a normal newest-first queue.
    """
    newest = sorted(rows, key=lambda row: row["published_at"], reverse=True)
    reserved = min(ARTICLE_RESERVED_CALLS, max_calls)
    articles = [row for row in newest if row.get("origin") == "ARTICLE"]
    priority_ids = {row["candidate_id"] for row in articles[:reserved]}
    return articles[:reserved] + [
        row for row in newest if row["candidate_id"] not in priority_ids
    ]


def validate_response(result: dict) -> list[str]:
    errors = []
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        errors.append("confidence is outside 0-1")
    for field in ("decision", "content_type", "public_summary",
                  "lineupbeat_impact", "direction", "mechanism", "strength",
                  "horizon", "reason"):
        if not isinstance(result.get(field), str):
            errors.append(f"{field} is not a string")
    if not isinstance(result.get("limitations"), list):
        errors.append("limitations is not a list")
    allowed = {
        "decision": {"CARD", "IGNORE", "ABSTAIN"},
        "content_type": {"REPORTING", "FANTASY_ANALYSIS"},
        "direction": semantic.DIRECTIONS,
        "mechanism": semantic.MECHANISMS - {"NO_FANTASY_IMPACT"},
        "strength": semantic.STRENGTHS,
        "horizon": semantic.HORIZONS,
    }
    for field, values in allowed.items():
        if isinstance(result.get(field), str) and result[field] not in values:
            errors.append(f"{field} is outside the closed vocabulary")
    return errors


def event_quality_failures(candidate: dict, result: dict) -> list[str]:
    """Reject structurally valid cards that do not contain a real event.

    The semantic provider proposes copy, but deterministic policy decides
    whether that proposal may reach the human inbox. Pure practice performance
    needs a multi-session pattern. A depth-chart label needs an explicit
    change, not a reporter casually calling the current starter QB1.
    """
    if result.get("decision") != "CARD":
        return []
    evidence = str(candidate.get("evidence") or "")
    summary = str(result.get("public_summary") or "")
    impact = str(result.get("lineupbeat_impact") or "")
    combined = "\n".join((evidence, summary, impact))
    mechanism = str(result.get("mechanism") or "")
    failures = []

    if mechanism == "DEPTH_CHART" and not DEPTH_CHART_CHANGE.search(combined):
        failures.append("depth-chart card has no explicit starter or pecking-order change")

    if mechanism in {"PERFORMANCE", "RED_ZONE"}:
        material = (MULTI_PRACTICE_PATTERN.search(combined) or
                    CONCRETE_ROLE_CHANGE.search(combined))
        if not material:
            failures.append(
                "isolated practice performance has no multi-practice trend or role change")
        if ISOLATED_LIMITATION.search(impact):
            failures.append("impact admits the practice result is isolated or non-actionable")

    folded = impact.lower().replace("‑", "-")
    for phrase in EDITORIAL_JARGON:
        if phrase in folded:
            failures.append(f"impact uses empty editorial jargon: {phrase}")
    return failures


def card_from(candidate: dict, result: dict) -> dict:
    card = {
        "player": candidate["player"], "player_id": candidate["player_id"],
        "team": candidate["team"], "position": candidate["position"],
        "content_type": result["content_type"],
        "direction": result["direction"], "mechanism": result["mechanism"],
        "strength": result["strength"], "horizon": result["horizon"],
        "projection_action": "NONE", "reader_label": LABEL[result["direction"]],
        "public_summary": result["public_summary"].strip(),
        "evidence": candidate["evidence"],
        "commentary": result["lineupbeat_impact"].strip(),
        "source": candidate["source_name"], "author": candidate["author"],
        "date": candidate["published_at"], "url": candidate["source_url"],
        "ownership": candidate["ownership"],
        "evidence_candidate_id": candidate["candidate_id"],
        "reviewer_action": "PENDING", "public_summary_approved_by": "",
        "commentary_approved_by": "", "approved_at": "",
        "commentary_origin": "MODEL_DRAFT",
        "model_original_commentary": result["lineupbeat_impact"].strip(),
        "draft_limitations": result["limitations"],
        "draft_confidence": result["confidence"],
    }
    corroborating = []
    for report in candidate.get("corroborating_candidates") or []:
        corroborating.append({
            "author": report.get("author", ""),
            "source": report.get("source_name", ""),
            "url": report.get("source_url", ""),
            "date": report.get("published_at", ""),
            "evidence_candidate_id": report.get("candidate_id", ""),
        })
    if corroborating:
        card["corroborating_sources"] = corroborating
    check = {**card, "reviewer_action": "APPROVE_WITH_EDIT"}
    failures = publication_preview.readiness_failures(check)
    failures.extend(public_summary.validate(
        card["public_summary"], card["player"], card["evidence"],
        card["content_type"], bool(card.get("summary_subject_context"))))
    if redundant_outlet_lead(card["public_summary"], card["source"]):
        failures.append("public summary redundantly starts with the cited outlet")
    card["readiness_failures"] = sorted(set(failures))
    return card


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=2.0)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--cap", type=float, required=True)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--max-cards", type=int, default=MAX_CARDS)
    args = parser.parse_args()
    if args.hours <= 0 or args.max_calls <= 0 or args.cap <= 0:
        raise SystemExit("--hours, --max-calls and --cap must be positive")
    if not 1 <= args.max_cards <= MAX_CARDS:
        raise SystemExit(f"--max-cards must be 1-{MAX_CARDS}")

    provider = OpenAIMobileDraftProvider(model=args.model)
    provider.authenticate()  # Zero Responses calls if credentials fail.
    generated = now_utc()
    cutoff = generated - timedelta(hours=args.hours)
    registry = players.load()
    candidates = article_candidates(registry, cutoff) + x_candidates(registry, cutoff)
    candidates.sort(key=lambda row: row["published_at"], reverse=True)
    seen = load_seen()
    save_seen(seen)
    seen_ids = {str(row.get("candidate_id") or "") for row in seen["attempts"]}
    published_ids, published_pairs = published_keys()
    candidates = [row for row in candidates
                  if row["candidate_id"] not in seen_ids
                  and row["candidate_id"] not in published_ids
                  and (row["player_id"], row["source_url"]) not in published_pairs]
    candidates, relevance_suppressed = relevance_filter(
        candidates, relevance.load())
    draftable_candidates = list(candidates)
    candidates, precall_duplicates = mobile_dedupe.collapse_precall(candidates)
    candidates = prioritize_candidates(candidates, args.max_calls)

    cards, outcomes = [], []
    publications = published_cards()
    attempts_by_id, outcomes_by_id = {}, {}
    calls, cost = 0, 0.0
    for candidate in candidates:
        if calls >= args.max_calls or cost >= args.cap or len(cards) >= args.max_cards:
            break
        attempted_at = now_utc().replace(microsecond=0).isoformat()
        attempt = {
            "candidate_id": candidate["candidate_id"],
            "player_id": candidate["player_id"], "source_url": candidate["source_url"],
            "attempted_at": attempted_at, "status": "ATTEMPTED",
        }
        seen["attempts"].append(attempt)
        attempts_by_id[candidate["candidate_id"]] = attempt
        save_seen(seen)
        identity = {key: candidate[key] for key in
                    ("player", "player_id", "team", "position")}
        metadata = {"author": candidate["author"],
                    "source_name": candidate["source_name"],
                    "ownership": candidate["ownership"],
                    "published_at": candidate["published_at"],
                    "source_url": candidate["source_url"]}
        result, meta = provider.draft(candidate["evidence"], metadata, identity)
        calls += 1
        cost += float(meta["cost_usd"])
        errors = validate_response(result)
        status = result.get("decision", "INVALID") if not errors else "INVALID"
        if status == "CARD":
            errors.extend(event_quality_failures(candidate, result))
            card = card_from(candidate, result)
            errors.extend(card["readiness_failures"])
            if not errors:
                published_match = mobile_dedupe.find_duplicate(card, publications)
                pending_match = mobile_dedupe.find_duplicate(card, cards)
                if published_match:
                    _, _, prior, detail = published_match
                    status = "DUPLICATE_EVENT"
                    attempt.update({"duplicate_of": prior.get(
                        "evidence_candidate_id", prior.get("publication_id", "")),
                                    "dedupe_detail": detail})
                elif pending_match:
                    _, index, prior, detail = pending_match
                    prior_id = prior["evidence_candidate_id"]
                    if mobile_dedupe.quality(card) > mobile_dedupe.quality(prior):
                        refs = list(prior.get("corroborating_sources") or [])
                        refs.append(mobile_dedupe.source_ref(prior))
                        card["corroborating_sources"] = refs
                        cards[index] = card
                        old_attempt = attempts_by_id[prior_id]
                        old_attempt.update({"status": "SUPERSEDED_EVENT",
                                            "duplicate_of": card[
                                                "evidence_candidate_id"],
                                            "dedupe_detail": detail})
                        if prior_id in outcomes_by_id:
                            outcomes_by_id[prior_id].update({
                                "decision": "SUPERSEDED_EVENT",
                                "duplicate_of": card["evidence_candidate_id"],
                                "dedupe_detail": detail})
                    else:
                        prior.setdefault("corroborating_sources", []).append(
                            mobile_dedupe.source_ref(card))
                        status = "DUPLICATE_EVENT"
                        attempt.update({"duplicate_of": prior_id,
                                        "dedupe_detail": detail})
                else:
                    cards.append(card)
            else:
                status = "VALIDATION_FAILED"
        attempt.update({"status": status, "completed_at": now_utc().replace(
            microsecond=0).isoformat(), "provider": meta["provider"],
            "model": meta["model"], "cost_usd": round(meta["cost_usd"], 6)})
        save_seen(seen)
        outcome = {
            "candidate_id": candidate["candidate_id"], "player": candidate["player"],
            "player_id": candidate["player_id"], "team": candidate["team"],
            "origin": candidate["origin"], "source_id": candidate.get("source_id", ""),
            "source_name": candidate["source_name"],
            "source_url": candidate["source_url"], "decision": status,
            "reason": result.get("reason", ""), "validation_failures": errors,
            "cost_usd": round(meta["cost_usd"], 6),
        }
        if status == "VALIDATION_FAILED":
            outcome["held_for_review"] = {
                "evidence": candidate["evidence"],
                "public_summary": result.get("public_summary", ""),
                "lineupbeat_impact": result.get("lineupbeat_impact", ""),
                "direction": result.get("direction", ""),
                "mechanism": result.get("mechanism", ""),
            }
        if attempt.get("duplicate_of"):
            outcome.update({"duplicate_of": attempt["duplicate_of"],
                            "dedupe_detail": attempt["dedupe_detail"]})
        outcomes.append(outcome)
        outcomes_by_id[candidate["candidate_id"]] = outcome
        save_seen(seen)

    outcome_counts = Counter(row["decision"] for row in outcomes)
    source_counts = Counter(row.get("source_id") or row["source_name"]
                            for row in draftable_candidates)
    team_counts = Counter(row["team"] for row in draftable_candidates)
    all_teams = sorted({player.team for player in registry.players if player.team})
    eligible_article_sources = sorted(
        source.source_id for source in article_registry.load()
        if source.active and source.adapter and not source.paid)
    article_sources_with_candidates = sorted({
        row.get("source_id") for row in draftable_candidates
        if row.get("origin") == "ARTICLE" and row.get("source_id")})
    payload = {
        "schema_version": SCHEMA,
        "generated_at": generated.replace(microsecond=0).isoformat(),
        "window": {"from": cutoff.isoformat(), "to": generated.isoformat(),
                   "hours": args.hours},
        "published": False, "model": args.model,
        "model_calls": calls, "cost_usd": round(cost, 6),
        "limits": {"max_calls": args.max_calls, "cap_usd": args.cap,
                   "max_cards": args.max_cards},
        "raw_candidate_count": len(draftable_candidates),
        "candidate_count": len(candidates), "cards": cards,
        "precall_duplicate_count": len(precall_duplicates),
        "precall_duplicates": precall_duplicates,
        "unreviewed_count": max(0, len(candidates) - calls),
        "relevance_suppressed": len(relevance_suppressed),
        "validation_failed": outcome_counts.get("VALIDATION_FAILED", 0),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "coverage": {
            "source_candidate_counts": dict(sorted(source_counts.items())),
            "team_candidate_counts": dict(sorted(team_counts.items())),
            "teams_without_candidates": sorted(set(all_teams) - set(team_counts)),
            "eligible_article_sources": eligible_article_sources,
            "article_sources_with_candidates": article_sources_with_candidates,
            "article_sources_without_candidates": sorted(
                set(eligible_article_sources) - set(article_sources_with_candidates)),
        },
        "event_duplicates": sum(row["decision"] in {
            "DUPLICATE_EVENT", "SUPERSEDED_EVENT"} for row in outcomes),
        "outcomes": outcomes,
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"  mobile draft: {len(candidates)} new candidates, {calls} calls, "
          f"${cost:.4f}, {len(cards)} review cards, 0 publications")
    if relevance_suppressed:
        print(f"  {len(relevance_suppressed)} candidate(s) suppressed by "
              "the draftability gate before provider spend")
    if len(candidates) > calls:
        print(f"  {len(candidates) - calls} eligible candidate(s) remain "
              "unreviewed after the call/dollar ceiling")
    if cost > args.cap:
        print("  observed cap crossed by the final in-flight response; no later call sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
