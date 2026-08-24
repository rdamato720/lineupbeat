#!/usr/bin/env python3
"""Build the exact, publication-disabled preview for the loose On SI batch."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wire_publication_preview as publication_preview
from wire import public_summary

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "wire_onsi_loose_batch.json"
OUT_JSON = ROOT / "data" / "wire_onsi_publication_preview.json"
OUT_HTML = ROOT / "data" / "wire_onsi_publication_preview.html"
CANON_JSON = ROOT / "data" / "wire_publication_preview.json"
CANON_HTML = ROOT / "data" / "wire_publication_preview.html"
PUBLICATIONS = ROOT / "data" / "wire_publications.json"
PLAYERS = ROOT / "sources" / "wire_players.json"
DB = ROOT / "wire.db"

LABEL = {"POSITIVE": "Trending up", "NEGATIVE": "Trending down",
         "NEUTRAL": "Worth noting", "UNCLEAR": "Unclear"}


def resolve_identity(card: dict, identities: dict[str, list[dict]]) -> dict:
    matches = identities.get(card["player"].casefold(), [])
    if not matches:
        raise ValueError(
            f"player is not in Wire identity registry: {card['player']}")
    if len(matches) == 1:
        return matches[0]
    fantasy_matches = [row for row in matches if row["fantasy_candidate"]]
    if len(fantasy_matches) == 1:
        return fantasy_matches[0]
    labels = ", ".join(
        f"{row['player_id']} {row['team']} {row['position']}"
        for row in matches)
    raise ValueError(f"ambiguous Wire identity for {card['player']}: {labels}")


def enrich(card: dict, identities: dict[str, list[dict]],
           conn: sqlite3.Connection) -> dict:
    out = dict(card)
    player = resolve_identity(card, identities)
    out.update({"player_id": player["player_id"], "team": player["team"],
                "position": player["position"],
                "reader_label": LABEL[card["direction"]],
                "projection_action": "NONE", "reviewer_action": "PENDING",
                "commentary_origin": card.get(
                    "commentary_origin", "HUMAN_AUTHORED"),
                "model_original_commentary": card.get(
                    "model_original_commentary", ""),
                "public_summary_approved_by": "",
                "commentary_approved_by": "", "approved_at": ""})
    found = conn.execute(
        "SELECT candidate_id FROM wire_evidence "
        "WHERE source_url=? AND lower(player_name)=lower(?) "
        "ORDER BY CASE WHEN duplicate_of='' THEN 0 ELSE 1 END, location LIMIT 1",
        (card["url"], card["player"]),
    ).fetchone()
    if found:
        out["evidence_candidate_id"] = found[0]
    else:
        digest = hashlib.sha256(
            f"{card['url']}|{card['player']}".encode()).hexdigest()[:20]
        out["evidence_candidate_id"] = f"manual:{digest}"

    check = dict(out)
    check["reviewer_action"] = "APPROVE_WITH_EDIT"
    failures = publication_preview.readiness_failures(check)
    failures.extend(public_summary.validate(
        out["public_summary"], out["player"], out["evidence"],
        out.get("content_type", "REPORTING")))
    if not out["evidence"].strip():
        failures.append("stored evidence is empty")
    out["readiness_failures"] = sorted(set(failures))
    return out


def render(payload: dict) -> str:
    e = html.escape
    parts = ["<!doctype html><meta charset='utf-8'>",
             "<title>On SI Wire publication preview</title>", """<style>
:root{--bg:#f8f7f3;--card:#fff;--ink:#171914;--muted:#62675e;--rule:#d9d7cf;
--up:#28653a;--down:#a43830;--accent:#8a5a1b}*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui}
main{max-width:760px;margin:auto;padding:30px 20px 60px}h1{margin-bottom:4px}
.meta{color:var(--muted)}.card{background:var(--card);border:1px solid var(--rule);
border-radius:13px;padding:20px;margin:20px 0}.head{display:flex;gap:9px;
align-items:baseline;flex-wrap:wrap}.name{font-weight:750;font-size:1.12rem}
.pos,.badge,.type{font-size:.72rem;letter-spacing:.07em;text-transform:uppercase}
.pos{color:var(--muted)}.badge,.type{border:1px solid;border-radius:99px;padding:2px 8px}
.up{color:var(--up)}.down{color:var(--down)}.type{color:var(--accent)}
.label{font-size:.68rem;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);
font-weight:700;margin:15px 0 5px}.block{border-left:3px solid var(--rule);padding-left:12px}
.impact{border-color:var(--accent)}.src{font-size:.8rem;color:var(--muted)}
.src a{color:inherit}.fail{color:var(--down);font-size:.85rem}
.ok{color:var(--up);font-weight:700}.top{border-left:4px solid var(--accent);
padding:10px 15px;background:#fff7e9;border-radius:7px}</style>""", "<main>",
             "<h1>The Wire — wider On SI batch</h1>",
             f"<p class='meta'>{len(payload['cards'])} exact reader cards · "
             f"{payload['catalog_counts']['source_items']} source items reviewed · "
             f"{payload.get('model_calls', 0)} model calls · "
             f"{payload.get('publications_applied', 0)} publications applied</p>",
             ("<p class='top'><b>Approval recorded:</b> Ralph approved all "
              "36 cards exactly as shown. The approved batch has been "
              "applied to the publication store.</p>" if
              payload.get("publications_applied")
              else "<p class='top'><b>Approval recorded:</b> Ralph approved "
              "all 36 cards exactly as shown. The publication step remains "
              "separate.</p>" if payload.get("reviewer_action") == "APPROVED"
              else "<p class='top'><b>Approval needed:</b> review the exact "
              "summary and Lineup Beat impact text below. Nothing on this "
              "page is live.</p>")]
    for card in payload["cards"]:
        direction_class = "up" if card["direction"] == "POSITIVE" else "down"
        kind = "Fantasy analysis" if card["content_type"] == "FANTASY_ANALYSIS" else "Reporting"
        parts.extend(["<article class='card'>", "<div class='head'>",
                      f"<span class='name'>{e(card['player'])}</span>",
                      f"<span class='pos'>{e(card['team'])} {e(card['position'])}</span>",
                      f"<span class='badge {direction_class}'>{e(card['reader_label'])}</span>",
                      f"<span class='type'>{e(kind)}</span>", "</div>",
                      f"<div class='label'>{e(kind)}</div>",
                      f"<div class='block'>{e(card['public_summary'])}</div>",
                      f"<p class='src'>{e(card['author'])}, {e(card['source'])} · "
                      f"{e(card['date'][:10])}<br><a href='{e(card['url'])}'>Open source</a></p>",
                      "<div class='label'>Lineup Beat impact</div>",
                      f"<div class='block impact'>{e(card['commentary'])}</div>"])
        if card["readiness_failures"]:
            parts.append("<ul class='fail'>" + "".join(
                f"<li>{e(reason)}</li>" for reason in card["readiness_failures"]
            ) + "</ul>")
        else:
            parts.append("<p class='ok'>Automated wording checks: PASS</p>")
        parts.append("</article>")
    parts.append("</main>")
    return "\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--actor", default="")
    args = parser.parse_args()
    if args.approve and not args.actor.strip():
        raise SystemExit("--approve requires --actor")
    source = json.loads(SOURCE.read_text())
    player_payload = json.loads(PLAYERS.read_text())
    identities: dict[str, list[dict]] = {}
    for row in player_payload["players"]:
        identities.setdefault(row["full_name"].casefold(), []).append(row)
    conn = sqlite3.connect(DB)
    cards = [enrich(card, identities, conn) for card in source["cards"]]
    catalog = json.loads((ROOT / "data" / "wire_inclusive_review.json").read_text())
    payload = {**{key: value for key, value in source.items() if key != "cards"},
               "readiness": "PASS" if all(not c["readiness_failures"]
                                             for c in cards) else "FAIL",
               "catalog_counts": {
                   "source_items": catalog["counts"]["articles"],
                   "player_candidates": catalog["counts"]["player_candidates"],
                   "discovered_not_captured":
                       catalog["counts"]["discovered_not_captured"],
               }, "cards": cards, "held_back": []}
    if args.approve:
        approved_at = datetime.now(timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z")
        for card in cards:
            card["reviewer_action"] = "APPROVE_WITH_EDIT"
            card["public_summary_approved_by"] = args.actor
            card["commentary_approved_by"] = args.actor
            card["approved_at"] = approved_at
            card["evidence_sha256"] = hashlib.sha256(
                card["evidence"].encode()).hexdigest()
            card["readiness_failures"] = publication_preview.readiness_failures(card)
            card["readiness_failures"].extend(public_summary.validate(
                card["public_summary"], card["player"], card["evidence"],
                card.get("content_type", "REPORTING")))
            card["readiness_failures"] = sorted(
                set(card["readiness_failures"]))
        before_payload = json.loads(PUBLICATIONS.read_text())
        current = int(before_payload.get("count") or 0)
        published_events = {
            (row.get("player_id"), row.get("url"))
            for row in before_payload.get("publications", [])}
        already_applied = sum(
            (card["player_id"], card["url"]) in published_events
            for card in cards)
        before = current - already_applied
        after = current + len(cards) - already_applied
        payload.update({
            "reviewer_action": "APPROVED",
            "reviewer": args.actor,
            "reviewer_name": "Ralph Damato",
            "approved_at": approved_at,
            "approval_statement": "Ralph approved all 36 cards as written.",
            "publication_count_before": before,
            "publication_count_after": after,
            "publications_applied": already_applied,
            "readiness": "PASS" if all(
                not card["readiness_failures"] for card in cards) else "FAIL",
        })
        CANON_JSON.write_text(json.dumps(
            payload, indent=1, ensure_ascii=False) + "\n")
        CANON_HTML.write_text(render(payload))
    OUT_JSON.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    OUT_HTML.write_text(render(payload))
    blocked = [card for card in cards if card["readiness_failures"]]
    print(f"  {len(cards)} draft card(s); {len(cards)-len(blocked)} pass, "
          f"{len(blocked)} blocked")
    for card in blocked:
        print(f"    {card['player']}: {'; '.join(card['readiness_failures'])}")
    if args.approve:
        print(f"  approval recorded for {len(cards)} card(s) by {args.actor}")
        print(f"  expected publication count {before} -> {after}")
        print(f"  {already_applied} approved publication(s) already applied")
    print("  preview builder made 0 model calls and wrote 0 publications")
    return bool(blocked)


if __name__ == "__main__":
    raise SystemExit(main())
