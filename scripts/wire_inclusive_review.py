#!/usr/bin/env python3
"""Build an inclusion-first On SI/X human-review catalog.

This is intentionally not the semantic publication funnel. Every complete On
SI article in the publisher-time window stays visible, including opinion,
speculation, rankings, ADP arguments, isolated plays and relayed reporting.
The output makes zero provider calls and writes zero publications.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import registry
from wire.store import WireStore

ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / "data" / "wire_manual_review_inputs.json"
OUT_JSON = ROOT / "data" / "wire_inclusive_review.json"
OUT_HTML = ROOT / "data" / "wire_inclusive_review.html"


def parse_time(value: str):
    try:
        got = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return got if got.tzinfo else got.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def article_rows(store: WireStore, cutoff: datetime, end: datetime,
                 exception_urls: set[str]):
    sources = {s.source_id: s for s in registry.load()
               if s.source_class in (registry.SI_ONSI,
                                     registry.SI_ONSI_ANALYSIS)}
    rows = []
    for row in store.conn.execute(
            "SELECT * FROM wire_source_items ORDER BY published_at DESC"):
        if row["source_id"] not in sources:
            continue
        stamp = parse_time(row["published_at"])
        forced = row["canonical_url"] in exception_urls
        if not forced and (stamp is None or stamp < cutoff or stamp > end):
            continue
        rows.append({**dict(row), "manual_exception": forced,
                     "source_name": sources[row["source_id"]].source_name,
                     "source_ownership": sources[row["source_id"]].source_ownership})
    return rows


def evidence_by_url(store: WireStore):
    grouped = defaultdict(list)
    for row in store.conn.execute(
            "SELECT * FROM wire_evidence WHERE review_status != 'SUPERSEDED' "
            "ORDER BY source_url, player_name, location"):
        grouped[row["source_url"]].append(dict(row))
    return grouped


def exclusion_rows(store: WireStore, cutoff: datetime, end: datetime):
    sources = {s.source_id: s for s in registry.load()
               if s.source_class in (registry.SI_ONSI,
                                     registry.SI_ONSI_ANALYSIS)}
    out = []
    try:
        rows = store.conn.execute(
            "SELECT * FROM wire_exclusions ORDER BY last_seen_at DESC")
    except Exception:
        return out
    for row in rows:
        stamp = parse_time(row["last_seen_at"])
        if row["source_id"] not in sources or stamp is None \
                or stamp < cutoff or stamp > end:
            continue
        # An earlier discovery pass may have refused a URL that a later,
        # broader pass captured.  The exclusion row is audit history, not the
        # current state, so do not show a recovered article twice or continue
        # calling it "not captured."
        if store.seen_url(row["canonical_url"]):
            continue
        src = sources[row["source_id"]]
        out.append({
            "source_id": row["source_id"], "source_name": src.source_name,
            "source_ownership": src.source_ownership,
            "canonical_url": row["canonical_url"],
            "headline": row["headline"], "author": row["author"],
            "published_at": row["last_seen_at"],
            "extraction_status": "DISCOVERED_NOT_CAPTURED",
            "manual_exception": False, "evidence": [],
            "review_note": row["reason"],
        })
    return out


def manual_x(items: list[dict]):
    out = []
    for item in items:
        if item.get("kind") != "x_post":
            continue
        status_id = item["url"].rstrip("/").split("/")[-1]
        out.append({
            "source_id": item["source_id"],
            "source_name": item["source_name"],
            "source_ownership": item["source_ownership"],
            "canonical_url": item["url"],
            "headline": item["text"],
            "author": item["author"],
            "published_at": item["published_at"],
            "extraction_status": "USER_SUPPLIED",
            "manual_exception": True,
            "evidence": [{
                "candidate_id": f"manual:x:{status_id}:{player['name']}",
                "player_name": player["name"],
                "team": player["team"], "position": player["position"],
                "evidence_class": player["evidence_class"],
                "evidence_text": item["text"], "review_status": "PENDING",
                "exclusion_reason": "",
            } for player in item.get("players", [])],
        })
    return out


def render(payload: dict) -> str:
    e = html.escape
    parts = ["<!doctype html><meta charset='utf-8'>",
             "<title>Inclusive Wire review</title>", """<style>
body{font:15px/1.5 system-ui;background:#f7f6f2;color:#171915;margin:0}
main{max-width:920px;margin:auto;padding:30px}.meta{color:#62675f}
article{background:#fff;border:1px solid #d9d8d1;border-radius:12px;
padding:18px;margin:18px 0}h2{font-size:1.05rem;margin:0 0 6px}
.tag{font-size:.72rem;font-weight:700;text-transform:uppercase;
letter-spacing:.06em;border:1px solid #bbb;border-radius:99px;padding:2px 7px}
.player{border-left:3px solid #8a5a1b;padding-left:12px;margin:13px 0}
.text{white-space:pre-wrap}.why{color:#9b332d}.ok{color:#2f6b3a}
a{color:#405d85}</style>""", "<main>",
             "<h1>Inclusive Wire review</h1>",
             f"<p class='meta'>{payload['counts']['articles']} source items · "
             f"{payload['counts']['player_candidates']} player candidates · "
             "0 model calls · 0 publications</p>"]
    for row in payload["articles"]:
        parts.append("<article>")
        parts.append(f"<h2>{e(row.get('headline') or '(untitled)')}</h2>")
        parts.append(f"<p class='meta'>{e(row.get('source_name',''))} · "
                     f"{e(row.get('author',''))} · {e(row.get('published_at',''))} "
                     f"<span class='tag'>{e(row.get('source_ownership',''))}</span>"
                     + (" <span class='tag'>manual exception</span>"
                        if row.get("manual_exception") else "") + "</p>")
        parts.append(f"<p><a href='{e(row['canonical_url'])}'>Open source</a></p>")
        candidates = row.get("evidence", [])
        if not candidates:
            note = row.get("review_note") or (
                "No resolved QB/RB/WR/TE passage; kept visible for completeness.")
            parts.append(f"<p class='why'>{e(note)}</p>")
        for cand in candidates:
            parts.append("<div class='player'>")
            parts.append(f"<b>{e(cand.get('player_name',''))}</b> "
                         f"{e(cand.get('team',''))} {e(cand.get('position',''))} "
                         f"<span class='tag'>{e(cand.get('evidence_class',''))}</span>")
            if cand.get("exclusion_reason"):
                parts.append(f"<p class='why'>{e(cand['exclusion_reason'])}</p>")
            parts.append(f"<p class='text'>{e(cand.get('evidence_text',''))}</p>")
            parts.append("</div>")
        parts.append("</article>")
    parts.append("</main>")
    return "\n".join(parts) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--end", default="",
                    help="freeze the UTC window end (ISO-8601); defaults to now")
    args = ap.parse_args()
    end = parse_time(args.end) if args.end else datetime.now(timezone.utc)
    if end is None:
        raise SystemExit("--end must be an ISO-8601 timestamp")
    cutoff = end - timedelta(hours=args.hours)
    manual = json.loads(MANUAL.read_text()) if MANUAL.exists() else {"items": []}
    exception_urls = {x["url"] for x in manual.get("items", [])
                      if x.get("kind") == "article_exception"}
    store = WireStore()
    evidence = evidence_by_url(store)
    articles = article_rows(store, cutoff, end, exception_urls)
    for row in articles:
        row["evidence"] = evidence.get(row["canonical_url"], [])
        row.pop("raw_text", None)
    articles.extend(exclusion_rows(store, cutoff, end))
    articles.extend(manual_x(manual.get("items", [])))
    articles.sort(key=lambda r: str(r.get("published_at", "")), reverse=True)
    payload = {
        "schema_version": "wire-inclusive-review-v1",
        "generated_at": end.isoformat(),
        "window": {"from": cutoff.isoformat(), "to": end.isoformat(),
                   "hours": args.hours},
        "published": False, "model_calls_made": 0,
        "counts": {
            "articles": len(articles),
            "player_candidates": sum(len(r.get("evidence", []))
                                     for r in articles),
            "manual_exceptions": sum(bool(r.get("manual_exception"))
                                     for r in articles),
            "discovered_not_captured": sum(
                r.get("extraction_status") == "DISCOVERED_NOT_CAPTURED"
                for r in articles),
        },
        "articles": articles,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    OUT_HTML.write_text(render(payload))
    print(f"  {payload['counts']['articles']} source item(s), "
          f"{payload['counts']['player_candidates']} player candidate(s)")
    print("  0 model calls, 0 publications")
    print(f"  wrote {OUT_JSON} and {OUT_HTML}")


if __name__ == "__main__":
    main()
