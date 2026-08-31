#!/usr/bin/env python3
"""Run one bounded, private Tank01 news observation and build its audit.

This script cannot write the Wire evidence store or publication store.  Its
only durable state is the isolated path supplied with --state, normally an
Actions cache.  Raw responses and reports are private workflow artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wire import tank01  # noqa: E402
from wire.players import norm  # noqa: E402


STATE_SCHEMA = "tank01-dark-state-v1"
REPORT_SCHEMA = "tank01-dark-report-v1"
DEFAULT_STATE = ROOT / "wire-tank01-state.json"
DEFAULT_RAW = ROOT / "data" / "wire_tank01_dark_raw.json"
DEFAULT_JSON = ROOT / "data" / "wire_tank01_dark_report.json"
DEFAULT_MD = ROOT / "data" / "wire_tank01_dark_report.md"
PLAYERS = ROOT / "sources" / "wire_players.json"
PUBLICATIONS = ROOT / "data" / "wire_publications.json"
MOBILE_BATCH = ROOT / "data" / "wire_mobile_batch.json"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(stamp: datetime) -> str:
    return stamp.replace(microsecond=0).isoformat()


def parse_time(value: object):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def empty_state(started_at: str) -> dict:
    return {
        "schema_version": STATE_SCHEMA,
        "started_at": started_at,
        "requests_attempted": 0,
        "attempts": [],
        "observations": [],
        "stories": {},
    }


def load_state(path: Path, started_at: str) -> dict:
    if not path.exists():
        return empty_state(started_at)
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != STATE_SCHEMA:
        raise ValueError("Tank01 dark-launch state schema is unsupported")
    if payload.get("requests_attempted") != len(payload.get("attempts") or []):
        raise ValueError("Tank01 request ledger does not reconcile")
    if not isinstance(payload.get("stories"), dict):
        raise ValueError("Tank01 story state is invalid")
    return payload


def save_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    temp.replace(path)


def player_alias_index(path: Path = PLAYERS) -> dict[str, list[dict]]:
    payload = json.loads(path.read_text())
    out: dict[str, list[dict]] = {}
    for player in payload.get("players") or []:
        if not player.get("fantasy_candidate"):
            continue
        names = {norm(player.get("full_name", "")), *player.get("aliases", [])}
        for alias in names:
            key = norm(alias)
            if len(key.split()) >= 2:
                out.setdefault(key, []).append(player)
    return out


def match_players(story: dict, aliases: dict[str, list[dict]]) -> list[dict]:
    text = f" {norm(story.get('headline', '') + ' ' + story.get('summary', ''))} "
    matches = {}
    for alias, players in aliases.items():
        if len(alias.split()) >= 2 and len(players) == 1 and f" {alias} " in text:
            player = players[0]
            matches[player["player_id"]] = {
                "player_id": player["player_id"],
                "full_name": player["full_name"],
                "team": player["team"],
                "position": player["position"],
            }
    return sorted(matches.values(), key=lambda row: row["full_name"])


def comparison_rows(publications_path: Path = PUBLICATIONS,
                    batch_path: Path = MOBILE_BATCH) -> tuple[list[dict], list[dict]]:
    publications = []
    batch = []
    if publications_path.exists():
        publications = json.loads(publications_path.read_text()).get("publications") or []
    if batch_path.exists():
        payload = json.loads(batch_path.read_text())
        generated = payload.get("generated_at")
        for row in payload.get("outcomes") or []:
            batch.append({**row, "generated_at": generated})
    return publications, batch


def close_in_time(left: object, right: object, hours: int = 72) -> bool:
    a, b = parse_time(left), parse_time(right)
    return bool(a and b and abs((a - b).total_seconds()) <= hours * 3600)


def coverage(story: dict, publications: list[dict], batch: list[dict]) -> dict:
    published = parse_time(story.get("published_at"))
    first_seen = parse_time(story.get("first_seen_at"))
    if published and first_seen and (first_seen - published).total_seconds() > 72 * 3600:
        return {"status": "STALE_AT_FIRST_SEEN", "matches": []}
    names = {player["full_name"] for player in story.get("fantasy_players") or []}
    if not names:
        return {"status": "NO_FANTASY_PLAYER", "matches": []}
    story_time = story.get("published_at") or story.get("first_seen_at")
    story_url = story.get("url") or ""
    publication_matches = []
    for row in publications:
        same_url = bool(story_url and story_url == row.get("url"))
        same_player = row.get("player_name") in names and close_in_time(
            story_time, row.get("published_at"))
        if same_url or same_player:
            publication_matches.append(str(row.get("publication_id") or row.get("player_name")))
    if publication_matches:
        return {"status": "MATCHED_PUBLICATION", "matches": publication_matches}
    batch_matches = []
    for row in batch:
        if row.get("player") in names and close_in_time(story_time, row.get("generated_at")):
            batch_matches.append(str(row.get("candidate_id") or row.get("player")))
    if batch_matches:
        return {"status": "MATCHED_CURRENT_BATCH", "matches": batch_matches}
    return {"status": "POTENTIAL_MISS", "matches": []}


def elapsed_hours(state: dict, at: datetime) -> float:
    started = parse_time(state.get("started_at"))
    return max(0.0, (at - started).total_seconds() / 3600) if started else 0.0


def build_report(state: dict, generated: datetime, days: int,
                 max_requests: int) -> dict:
    publications, batch = comparison_rows()
    stories = []
    for stored in state.get("stories", {}).values():
        row = dict(stored)
        row["coverage"] = coverage(row, publications, batch)
        stories.append(row)
    stories.sort(key=lambda row: row.get("first_seen_at", ""), reverse=True)
    counts = {}
    latencies = []
    for row in stories:
        status = row["coverage"]["status"]
        counts[status] = counts.get(status, 0) + 1
        published, first = parse_time(row.get("published_at")), parse_time(row.get("first_seen_at"))
        if published and first and first >= published:
            latencies.append((first - published).total_seconds() / 60)
    latencies.sort()
    hours = elapsed_hours(state, generated)
    complete = hours >= days * 24 or state["requests_attempted"] >= max_requests
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at": iso(generated),
        "dark_launch": True,
        "published": False,
        "model_calls": 0,
        "window": {"started_at": state["started_at"], "elapsed_hours": round(hours, 2),
                   "target_days": days, "complete": complete},
        "limits": {"max_requests": max_requests, "requests_attempted": state["requests_attempted"]},
        "observations": len(state.get("observations") or []),
        "stories_observed": len(stories),
        "coverage_counts": dict(sorted(counts.items())),
        "latency_minutes": {
            "sample_size": len(latencies),
            "median": round(latencies[len(latencies) // 2], 1) if latencies else None,
            "maximum": round(max(latencies), 1) if latencies else None,
        },
        "schema_fingerprints": sorted({
            row.get("schema_sha256", "") for row in state.get("observations") or []
            if row.get("schema_sha256")
        }),
        "potential_misses": [row for row in stories
                             if row["coverage"]["status"] == "POTENTIAL_MISS"][:100],
        "stories": stories[:500],
    }


def markdown(report: dict) -> str:
    window = report["window"]
    lines = [
        "# Tank01 NFL news dark launch",
        "",
        "Private coverage audit only. Nothing in this report can publish or change fantasy data.",
        "",
        f"- Window: {window['elapsed_hours']}h / {window['target_days'] * 24}h",
        f"- Requests attempted: {report['limits']['requests_attempted']} / {report['limits']['max_requests']}",
        f"- Successful observations: {report['observations']}",
        f"- Unique stories observed: {report['stories_observed']}",
        f"- Model calls: {report['model_calls']}",
        f"- Publications: 0",
        "",
        "## Coverage comparison",
        "",
    ]
    for key, value in report.get("coverage_counts", {}).items():
        lines.append(f"- {key}: {value}")
    latency = report["latency_minutes"]
    lines.extend([
        "",
        f"Provider latency sample: {latency['sample_size']} stories; median "
        f"{latency['median']} minutes; maximum {latency['maximum']} minutes.",
        "",
        "## Potential misses",
        "",
    ])
    misses = report.get("potential_misses") or []
    if not misses:
        lines.append("No unmatched fantasy-player stories in the observations so far.")
    for index, row in enumerate(misses[:30], 1):
        names = ", ".join(player["full_name"] for player in row.get("fantasy_players") or [])
        headline = row.get("headline") or "Untitled Tank01 item"
        source = row.get("source") or "source not supplied"
        url = row.get("url") or ""
        suffix = f" — {url}" if url.startswith("https://") else ""
        lines.append(f"{index}. **{names}** — {headline} ({source}){suffix}")
    lines.extend([
        "",
        "A potential miss is an exact player-name match without a nearby current mobile-batch "
        "or publication match. It is an investigation lead, not proof that Lineup Beat missed "
        "the underlying event.",
        "",
    ])
    return "\n".join(lines)


def run(args, transport=None, at: datetime | None = None) -> dict:
    generated = at or now_utc()
    state = load_state(args.state, iso(generated))
    if elapsed_hours(state, generated) < args.days * 24 and \
            state["requests_attempted"] < args.max_requests:
        attempt = {"attempted_at": iso(generated), "outcome": "PENDING"}
        state["attempts"].append(attempt)
        state["requests_attempted"] = len(state["attempts"])
        # Bank before transport so a failed request cannot be retried for free.
        save_json(args.state, state)
        try:
            payload = tank01.scrub_payload(
                tank01.fetch_news(transport=transport),
                os.environ.get(tank01.KEY_ENV, ""),
            )
            raw = {
                "schema_version": "tank01-dark-raw-v1",
                "fetched_at": iso(generated),
                "response_sha256": hashlib.sha256(json.dumps(
                    payload, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":")).encode()).hexdigest(),
                "response": payload,
            }
            save_json(args.raw, raw)
            items = tank01.extract_items(payload)
            fingerprint = tank01.schema_fingerprint(payload, items)
            aliases = player_alias_index()
            normalized = []
            for item in items:
                story = tank01.normalize(item)
                story["fantasy_players"] = match_players(story, aliases)
                normalized.append(story)
                prior = state["stories"].get(story["story_id"])
                if prior:
                    prior["last_seen_at"] = iso(generated)
                    prior["observations"] = int(prior.get("observations", 1)) + 1
                else:
                    state["stories"][story["story_id"]] = {
                        **story, "first_seen_at": iso(generated),
                        "last_seen_at": iso(generated), "observations": 1,
                    }
            state["observations"].append({
                "observed_at": iso(generated),
                "item_count": len(items),
                "new_story_count": sum(1 for row in normalized
                                       if state["stories"][row["story_id"]]["observations"] == 1),
                "schema_sha256": fingerprint["sha256"],
                "schema": fingerprint,
            })
            attempt["outcome"] = "SUCCESS"
            attempt["item_count"] = len(items)
        except Exception as exc:
            attempt["outcome"] = "FAILED"
            attempt["error"] = tank01.redact(exc)
            save_json(args.state, state)
            report = build_report(state, generated, args.days, args.max_requests)
            save_json(args.report_json, report)
            args.report_md.write_text(markdown(report))
            raise
        save_json(args.state, state)

    report = build_report(state, generated, args.days, args.max_requests)
    save_json(args.report_json, report)
    args.report_md.parent.mkdir(parents=True, exist_ok=True)
    args.report_md.write_text(markdown(report))
    return report


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    ap.add_argument("--report-json", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--report-md", type=Path, default=DEFAULT_MD)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max-requests", type=int, default=180)
    ap.add_argument("--check-key", action="store_true")
    return ap


def main() -> int:
    args = parser().parse_args()
    if not 1 <= args.days <= 14 or not 1 <= args.max_requests <= 336:
        raise SystemExit("days must be 1-14 and max-requests must be 1-336")
    if args.check_key:
        tank01.fetch_news(transport=lambda _url, _headers, _timeout: {"statusCode": 200, "body": []})
        print("Tank01 secret is present; 0 external API requests")
        return 0
    report = run(args)
    print(
        f"  Tank01 dark launch: {report['observations']} observations, "
        f"{report['stories_observed']} stories, "
        f"{report['coverage_counts'].get('POTENTIAL_MISS', 0)} potential misses, "
        "0 model calls, 0 publications"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
