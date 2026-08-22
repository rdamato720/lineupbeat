#!/usr/bin/env python3
"""Build the human review package from a valid independent-review run.

This script publishes nothing.  It deliberately refuses stale or mismatched
identity data instead of producing a plausible-looking review page.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import players

SOURCE = Path("data/wire_independent_review.json")
HTML = Path("data/wire_review_package.html")
JSON = Path("data/wire_review_package.json")


def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def publishable(item):
    a = item.get("assessment") or {}
    return (a.get("decision") == "INTERPRET"
            and a.get("fantasy_mechanism") != "NO_FANTASY_IMPACT"
            and not a.get("validation_failures")
            and not item.get("evidence_integrity", {}).get(
                "blocks_automatic_approval", True))


def action_disagreement(item):
    proposed = publishable(item)
    verdict = item.get("independent_reviewer", {}).get("effective_verdict")
    return ((proposed and verdict in {"REJECT", "HUMAN_REVIEW"})
            or (not proposed and verdict == "AUTO_APPROVE"))


def validate_items(items, registry):
    errors = []
    identities = set()
    for item in items:
        c = item.get("candidate") or {}
        ident = item.get("supplied_identity") or {}
        pid = ident.get("player_id")
        p = registry.by_id.get(pid or "")
        if p is None:
            errors.append(f"{c.get('candidate_id')}: unresolved identity")
            continue
        expected = (p.player_id, players.norm(p.full_name), p.team, p.position)
        shown = (pid, players.norm(ident.get("player_name", "")),
                 ident.get("team"), ident.get("position"))
        heading = (pid, players.norm(c.get("player_name", "")),
                   c.get("team"), c.get("position"))
        if shown != expected:
            errors.append(f"{c.get('candidate_id')}: displayed registry identity mismatch")
        if heading != expected:
            errors.append(f"{c.get('candidate_id')}: heading/registry identity mismatch")
        identities.add(expected)
        integ = item.get("evidence_integrity") or {}
        if not integ.get("hashes_match"):
            errors.append(f"{c.get('candidate_id')}: evidence hashes do not match")
        if integ.get("evidence_text") != c.get("evidence_text"):
            errors.append(f"{c.get('candidate_id')}: displayed evidence differs")
    distinct_players = {i[0] for i in identities}
    candidate_players = {((x.get("candidate") or {}).get("player_id"))
                         for x in items}
    candidate_players.discard(None)
    if len(candidate_players) > 1 and len(distinct_players) <= 1:
        errors.append("multi-player package contains only one registry identity")
    if errors:
        raise ValueError("review package refused:\n  " + "\n  ".join(errors))


def band(item):
    verdict = item.get("independent_reviewer", {}).get("effective_verdict")
    if publishable(item) and verdict == "AUTO_APPROVE":
        return 0, "Card-producing auto-approvals"
    if action_disagreement(item):
        return 1, "Action-level disagreements"
    if publishable(item):
        return 2, "Remaining proposed cards"
    if verdict == "AUTO_APPROVE":
        return 3, "Suppression agreements — no card"
    return 4, "Other assessments"


def row(label, value):
    return f"<div class=kv><b>{esc(label)}</b><span>{esc(value)}</span></div>"


def render_card(item, number):
    c = item["candidate"]
    a = item["assessment"]
    r = item["independent_reviewer"]
    ident = item["supplied_identity"]
    integ = item["evidence_integrity"]
    verdict = r.get("effective_verdict")
    return f"""<article class=card id=i{number}>
<header><b>{number}. {esc(c['player_name'])}</b> · {esc(c['team'])} {esc(c['position'])}
<span class=verdict>{esc(verdict)}</span></header>
<h3>Complete verified evidence</h3><blockquote>{esc(c['evidence_text'])}</blockquote>
<div class=grid>
{row('player id', ident['player_id'])}{row('registry name', ident['player_name'])}
{row('registry team', ident['team'])}{row('registry position', ident['position'])}
{row('reporter', item.get('author'))}{row('publication', item.get('source_name'))}
{row('ownership', item.get('ownership'))}{row('published', item.get('published_at'))}
{row('stored evidence sha256', integ['evidence_sha256'])}
{row('generator evidence sha256', integ['generator_input_evidence_sha256'])}
{row('reviewer evidence sha256', integ['reviewer_input_evidence_sha256'])}
{row('human evidence sha256', integ['human_display_evidence_sha256'])}
{row('generator request sha256', integ['generator_request_sha256'])}
{row('reviewer request sha256', integ['reviewer_request_sha256'])}
</div>
<p><a href="{esc(item.get('source_url'))}" target=_blank rel="nofollow noopener">Open source</a></p>
<h3>Generator</h3><div class=grid>
{row('decision', a.get('decision'))}{row('mechanism', a.get('fantasy_mechanism'))}
{row('direction', a.get('direction'))}{row('strength', a.get('impact_strength'))}
{row('horizon', a.get('impact_horizon'))}{row('projection action', a.get('projection_action'))}
</div><p class=impact>{esc(a.get('fantasy_commentary'))}</p>
<h3>Independent review</h3><div class=grid>
{row('model verdict', r.get('model_verdict'))}{row('effective verdict', verdict)}
{row('subject correct', r.get('subject_is_correct'))}
{row('different subject', r.get('passage_names_a_different_subject'))}
{row('mechanism supported', r.get('mechanism_is_supported'))}
{row('direction supported', r.get('direction_is_supported'))}
{row('performance without role', r.get('performance_only_no_role_information'))}
{row('enforcement', '; '.join(r.get('enforcement_reasons') or []) or 'none')}
</div><p>{esc(r.get('disagreement_summary'))}</p>
<h3>Public evidence sentence</h3><p class=missing>REQUIRES HUMAN APPROVAL — deliberately blank</p>
<div class=controls><label><input type=radio name=d{number} value=APPROVE> Approve</label>
<label><input type=radio name=d{number} value=APPROVE_WITH_EDIT> Approve with edit</label>
<label><input type=radio name=d{number} value=REJECT> Reject</label>
<input type=text placeholder="reason or approved wording"></div></article>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--html", type=Path, default=HTML)
    ap.add_argument("--json", type=Path, default=JSON)
    args = ap.parse_args()
    payload = json.loads(args.source.read_text())
    if payload.get("run_status") != "VALID":
        raise ValueError("only a VALID reviewer run may build a package")
    items = list(payload.get("items") or [])
    registry = players.load()
    validate_items(items, registry)
    items.sort(key=lambda x: (band(x)[0],
                              (x.get("candidate") or {}).get("candidate_id", "")))
    metrics = {
        "items": len(items),
        "card_producing_auto_approvals": sum(
            publishable(x) and x["independent_reviewer"].get(
                "effective_verdict") == "AUTO_APPROVE" for x in items),
        "suppression_agreements": sum(
            not publishable(x) and x["independent_reviewer"].get(
                "effective_verdict") == "AUTO_APPROVE" for x in items),
        "action_disagreements": sum(action_disagreement(x) for x in items),
        "verdicts": dict(Counter(x["independent_reviewer"].get(
            "effective_verdict") for x in items)),
        "reviewer_cost_usd": payload.get("cost_usd", 0),
        "publications_applied": 0,
    }
    package = {**payload, "metrics": metrics, "items": items}
    args.json.write_text(json.dumps(package, indent=1, ensure_ascii=False) + "\n")
    cards, previous = [], None
    for index, item in enumerate(items, 1):
        title = band(item)[1]
        if title != previous:
            cards.append(f"<h2>{esc(title)}</h2>")
            previous = title
        cards.append(render_card(item, index))
    css = """body{background:#080b0d;color:#eee;font:15px system-ui;max-width:1000px;margin:auto;padding:24px}h1,h2,h3{font-family:system-ui}h2{margin-top:36px}.card{border:1px solid #293036;border-left:4px solid #c6f53c;padding:20px;margin:18px 0;background:#0d1114}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.kv{background:#12181c;padding:8px}.kv b{display:block;color:#9aa3a8;font-size:11px;text-transform:uppercase}.verdict{float:right;color:#c6f53c}blockquote{border-left:2px solid #566;padding-left:14px}.impact{font-size:17px}.missing{color:#ffcb6b}.controls{display:flex;gap:14px;flex-wrap:wrap}.controls input[type=text]{min-width:320px}@media(max-width:700px){.grid{grid-template-columns:1fr}}"""
    doc = ("<!doctype html><meta charset=utf-8><title>Wire review package</title>"
           f"<style>{css}</style><h1>Wire review package</h1>"
           f"<pre>{esc(json.dumps(metrics, indent=2))}</pre>" + "".join(cards))
    args.html.write_text(doc)
    for path in (args.html, args.json):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{path} {path.stat().st_size} bytes sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
