#!/usr/bin/env python3
"""Draft an inclusion-first On SI/X mobile review batch.

This script spends only inside explicit call and dollar ceilings.  It records
an attempted candidate before asking the provider, but it never records human
approval and never invokes the publication route.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wire_publication_preview as publication_preview
from beatwire.registry import Registry as BeatRegistry
from wire import players, public_summary, semantic
from wire.mobile_approval import MAX_CARDS
from wire.mobile_draft import OpenAIMobileDraftProvider
from wire.providers.openai import MODEL


ROOT = Path(__file__).resolve().parent.parent
INCLUSIVE = ROOT / "data" / "wire_inclusive_review.json"
PUBLICATIONS = ROOT / "data" / "wire_publications.json"
BEAT_DB = ROOT / "wire-mobile-x.db"
OUT = ROOT / "data" / "wire_mobile_batch.json"
SEEN = ROOT / "data" / "wire_mobile_seen.json"
SCHEMA = "wire-mobile-batch-v1"
SEEN_SCHEMA = "wire-mobile-seen-v1"
LABEL = {"POSITIVE": "Trending up", "NEGATIVE": "Trending down",
         "NEUTRAL": "Worth noting", "UNCLEAR": "Unclear"}


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


def onsi_candidates(registry, cutoff: datetime) -> list[dict]:
    if not INCLUSIVE.exists():
        return []
    payload = json.loads(INCLUSIVE.read_text())
    grouped: dict[tuple[str, str], dict] = {}
    for article in payload.get("articles") or []:
        if "si.com/" not in str(article.get("canonical_url") or "").lower():
            continue
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
            row = grouped.setdefault(key, {
                "candidate_id": "mobile:onsi:" + digest(url, player.player_id),
                "player": player.full_name, "player_id": player.player_id,
                "team": player.team, "position": player.position,
                "source_name": str(article.get("source_name") or "On SI"),
                "ownership": str(article.get("source_ownership") or "INDEPENDENT"),
                "author": str(article.get("author") or "On SI"),
                "source_url": url, "published_at": stamp.isoformat(),
                "evidence_parts": [], "origin": "ONSI",
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
                "ownership": "INDEPENDENT", "author": source.name or source.handle,
                "source_url": str(raw["url"]), "published_at": stamp.isoformat(),
                "evidence": evidence[:6000], "origin": "X",
            })
    return out


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
    check = {**card, "reviewer_action": "APPROVE_WITH_EDIT"}
    failures = publication_preview.readiness_failures(check)
    failures.extend(public_summary.validate(
        card["public_summary"], card["player"], card["evidence"],
        card["content_type"], bool(card.get("summary_subject_context"))))
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
    candidates = onsi_candidates(registry, cutoff) + x_candidates(registry, cutoff)
    candidates.sort(key=lambda row: row["published_at"], reverse=True)
    seen = load_seen()
    save_seen(seen)
    seen_ids = {str(row.get("candidate_id") or "") for row in seen["attempts"]}
    published_ids, published_pairs = published_keys()
    candidates = [row for row in candidates
                  if row["candidate_id"] not in seen_ids
                  and row["candidate_id"] not in published_ids
                  and (row["player_id"], row["source_url"]) not in published_pairs]

    cards, outcomes = [], []
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
            card = card_from(candidate, result)
            errors.extend(card["readiness_failures"])
            if not errors:
                cards.append(card)
            else:
                status = "VALIDATION_FAILED"
        attempt.update({"status": status, "completed_at": now_utc().replace(
            microsecond=0).isoformat(), "provider": meta["provider"],
            "model": meta["model"], "cost_usd": round(meta["cost_usd"], 6)})
        save_seen(seen)
        outcomes.append({
            "candidate_id": candidate["candidate_id"], "player": candidate["player"],
            "origin": candidate["origin"], "decision": status,
            "reason": result.get("reason", ""), "validation_failures": errors,
            "cost_usd": round(meta["cost_usd"], 6),
        })

    payload = {
        "schema_version": SCHEMA,
        "generated_at": generated.replace(microsecond=0).isoformat(),
        "window": {"from": cutoff.isoformat(), "to": generated.isoformat(),
                   "hours": args.hours},
        "published": False, "model": args.model,
        "model_calls": calls, "cost_usd": round(cost, 6),
        "limits": {"max_calls": args.max_calls, "cap_usd": args.cap,
                   "max_cards": args.max_cards},
        "candidate_count": len(candidates), "cards": cards,
        "outcomes": outcomes,
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"  mobile draft: {len(candidates)} new candidates, {calls} calls, "
          f"${cost:.4f}, {len(cards)} review cards, 0 publications")
    if cost > args.cap:
        print("  observed cap crossed by the final in-flight response; no later call sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
