#!/usr/bin/env python3
"""The review package: one HTML page and one JSON, for a person to decide from.

    python3 scripts/wire_review_package.py

Four opinions per item, kept separate on purpose:

  generator   what the semantic pass decided
  validator   what the deterministic post-check said about that decision
  reviewer    what an independently-prompted second pass said (dark launch)
  human       blank, because that is the only one that can publish anything

It writes two files and nothing else. It cannot publish, cannot touch
wire_publications.json, and cannot alter a stored decision.

The public evidence sentence is deliberately NOT generated here. A card shows
one sentence in our words instead of the reporter's passage, and that field
carries a recorded human approval -- writing it automatically into a review
page is how it would arrive on a card without one.
"""

from __future__ import annotations

import html
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import evidence_integrity as eint
from wire.store import WireStore

BACKFILL = Path("data/wire_backfill.json")
REVIEW = Path("data/wire_independent_review.json")
INVALID_REVIEW = Path("data/wire_independent_review.INVALID.json")
REFUSAL_AUDIT = Path("data/wire_integrity_rule_audit.json")
BRIDGE = Path("data/wire_funnel_bridge.json")
PUBS = Path("data/wire_publications.json")
OUT_HTML = Path("data/wire_review_package.html")
OUT_JSON = Path("data/wire_review_package.json")


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def main() -> int:
    bf = json.loads(BACKFILL.read_text())
    rev = json.loads(REVIEW.read_text()) if REVIEW.exists() else {"items": []}
    bridge = json.loads(BRIDGE.read_text()) if BRIDGE.exists() else {}
    pubs = json.loads(PUBS.read_text())["publications"] if PUBS.exists() else []
    invalid = (json.loads(INVALID_REVIEW.read_text())
               if INVALID_REVIEW.exists() else None)
    rule_audit = (json.loads(REFUSAL_AUDIT.read_text())
                  if REFUSAL_AUDIT.exists() else None)
    by_cid = {i["candidate_id"]: i for i in rev.get("items", [])}

    results = bf.get("results", [])
    cl = bf.get("claude", {})

    # The stored row is the authority. Every hash below is computed against
    # it, including the one for what this page is about to display, so a
    # future truncation anywhere in the chain shows up as a mismatch rather
    # than as a model that appears to invent facts.
    store = WireStore()
    stored_text = {r["candidate_id"]: r["evidence_text"]
                   for r in store.evidence()}
    stored_url = {r["candidate_id"]: r["source_url"] for r in store.evidence()}
    # The source body makes the completeness check boundary-aware. Without it
    # this page reported two passages incomplete that the corpus-wide check,
    # which does pass it, accepts -- the same rule reaching two verdicts
    # because one caller withheld the evidence it needed.
    bodies = {r["canonical_url"]: r["raw_text"] for r in store.conn.execute(
        "SELECT canonical_url, raw_text FROM wire_source_items")}
    win = bf.get("window_applied") or bf.get("window", {})

    items = []
    for r in results:
        c = r.get("candidate") or {}
        a = r.get("assessment") or {}
        cid = c.get("candidate_id", "")
        ir = by_cid.get(cid, {})
        rv = ir.get("review", {})
        decision = a.get("decision", "")
        validator = ("ABSTAIN" if a.get("abstention_reason") else "PASS")

        canonical = stored_text.get(cid, c.get("evidence_text", ""))
        shown = canonical                      # this page displays the stored row
        rev_seen = (ir.get("evidence_integrity") or {}).get("evidence_sha256")
        gen_seen = (r.get("evidence_integrity") or {}).get(
            "generator_input_evidence_sha256")
        integrity = eint.check(
            canonical,
            generator_input=canonical if gen_seen == eint.sha256(canonical) else None,
            reviewer_input=canonical if rev_seen == eint.sha256(canonical) else None,
            human_display=shown,
            start=(r.get("evidence_integrity") or {}).get("segment_start"),
            end=(r.get("evidence_integrity") or {}).get("segment_end"),
            source_body=bodies.get(stored_url.get(cid, "")))
        items.append({
            "candidate_id": cid,
            "player": c.get("player_name", ""),
            "team": c.get("team", ""),
            "position": c.get("position", ""),
            "evidence_text": canonical,
            "evidence_integrity": integrity,
            # Attached to the item. The card loop used to read `ir`, a
            # variable left behind by THIS loop, so every one of 60 cards
            # showed the identity of whichever candidate happened to be last
            # -- A.J. Brown. A loop-external name that still resolves is the
            # worst kind of bug: it renders, it looks plausible, and it is
            # wrong on every row.
            "registry_identity": dict(ir.get("supplied_identity") or {}),
            "public_evidence_summary": "",
            "public_evidence_summary_status":
                "REQUIRES HUMAN APPROVAL — deliberately not auto-generated",
            "reporter": r.get("author", ""),
            "publication": r.get("source_name", ""),
            "url": r.get("source_url", ""),
            "published_at": r.get("published_at", ""),
            "evidence_class": c.get("evidence_class", ""),
            "ownership": r.get("ownership", ""),
            "authority": r.get("relevance_tier", ""),
            "relevance_reason": r.get("relevance_reason", ""),
            "underlying_report_id": r.get("underlying_report_id", "") or "",
            "generator": {
                "decision": decision,
                "mechanism": a.get("fantasy_mechanism", ""),
                "direction": a.get("direction", ""),
                "strength": a.get("impact_strength", ""),
                "horizon": a.get("impact_horizon", ""),
                "projection_action": a.get("projection_action", ""),
                "commentary": a.get("fantasy_commentary", ""),
                "limitations": a.get("limitations", ""),
                "confidence": a.get("confidence", ""),
                "abstention_reason": a.get("abstention_reason", ""),
                "model": a.get("model", cl.get("model", "")),
                "prompt_version": a.get("prompt_version", cl.get("prompt_version", "")),
                "schema_version": cl.get("schema_version", ""),
                "latency_ms": a.get("latency_ms", ""),
                "cost_usd": a.get("cost_usd", ""),
            },
            "validator": {"verdict": validator,
                          "reason": a.get("abstention_reason", "")},
            "independent_reviewer": rv or {"verdict": "NOT RUN"},
            "human": {"decision": None, "note": ""},
        })

    # Would this item become a card if the generator had its way?
    #
    # The distinction the reviewer's verdict does not carry on its own: it
    # judges whether the INTERPRETATION is sound, not whether a card should
    # exist. Seven of the ten AUTO_APPROVE verdicts are on NO_FANTASY_IMPACT
    # items -- the reviewer agreeing there is nothing here. Reading those as
    # ten cards ready to publish would be badly wrong, so publishability is
    # computed separately and shown separately.
    def publishable(it):
        return (it["generator"]["decision"] == "INTERPRET"
                and it["generator"]["mechanism"] not in ("NO_FANTASY_IMPACT", "")
                and it["validator"]["verdict"] == "PASS")

    # A disagreement is one that would lead to a DIFFERENT ACTION.
    #
    # Keyed on the verdict alone this flagged 49 of 60, because a REJECT on an
    # item the generator already called NO_FANTASY_IMPACT is two systems
    # agreeing that nothing should be published. That is not a disagreement,
    # it is a consensus, and burying eleven real conflicts inside forty-nine
    # flags is how a reviewer gets ignored.
    def disagrees(it):
        v = (it["independent_reviewer"] or {}).get("verdict", "")
        if v in ("", "NOT RUN", "ABSTAIN"):
            return False
        would_publish = publishable(it)
        # The generator would put this on the page and the reviewer says no.
        if would_publish and v == "REJECT":
            return True
        # The reviewer would pass something the validator refused.
        if v == "AUTO_APPROVE" and it["validator"]["verdict"] != "PASS":
            return True
        # A publishable item whose subject or mechanism the reviewer doubts.
        r = it["independent_reviewer"]
        if would_publish and (r.get("subject_is_correct") is False
                              or r.get("mechanism_is_supported") is False
                              or r.get("direction_is_supported") is False
                              or r.get("inference_not_in_evidence") is True
                              or r.get("commentary_overstates") is True):
            return True
        return False

    def conflict_kind(it):
        """Claim-subject conflict, or a roster objection to be ignored.

        Roster objections are no longer solicited and no longer count. The
        distinction is kept explicit on every card because the two used to
        arrive as one flag: "does not play for Minnesota" is worthless, and
        "filed under Swift but describing Johnson" is decisive.
        """
        r = it["independent_reviewer"] or {}
        if r.get("subject_is_correct") is False or r.get(
                "passage_names_a_different_subject") is True:
            return "CLAIM SUBJECT — the passage may be about another player"
        if r.get("identity_conflicts_with_supplied_registry") is True:
            return ("stale roster objection — ignored, identity is validated "
                    "in code")
        return "none"

    for it in items:
        it["identity_conflict_kind"] = conflict_kind(it)
        it["would_publish"] = (publishable(it)
                               and not it["evidence_integrity"]
                               ["blocks_automatic_approval"])
        it["disagreement"] = disagrees(it)

    tot = {
        "model_calls": cl.get("calls", len(results)),
        "interpretations": cl.get("interpretations", 0),
        "abstentions": cl.get("abstentions", 0),
        "no_fantasy_impact": sum(
            1 for i in items if i["generator"]["mechanism"] == "NO_FANTASY_IMPACT"),
        "validation_failures": sum(
            1 for i in items if i["validator"]["verdict"] != "PASS"),
        "pending_human_reviews": len(items),
        "publications_applied": 0,
        "publications_live": len(pubs),
        "generator_cost_usd": cl.get("cost_usd", 0),
        "reviewer_cost_usd": rev.get("cost_usd", 0),
        "invalid_run_cost_usd": (invalid or {}).get("cost_usd", 0),
        "generator_median_latency_ms": cl.get("median_latency_ms", ""),
        "generator_p95_latency_ms": cl.get("p95_latency_ms", ""),
        "by_position": dict(Counter(i["position"] for i in items)),
        "by_team": dict(Counter(i["team"] for i in items)),
        "by_evidence_class": dict(Counter(i["evidence_class"] for i in items)),
        "by_mechanism": dict(Counter(i["generator"]["mechanism"] for i in items)),
        "reviewer_verdicts": dict(Counter(
            (i["independent_reviewer"] or {}).get("verdict", "NOT RUN")
            for i in items)),
        "disagreements": sum(1 for i in items if i["disagreement"]),
        "suppression_agreements": sum(
            1 for i in items if not i["would_publish"]
            and (i["independent_reviewer"] or {}).get("verdict") == "AUTO_APPROVE"),
        "claim_subject_conflicts": sum(
            1 for i in items
            if i["identity_conflict_kind"].startswith("CLAIM SUBJECT")),
        "roster_objections_ignored": sum(
            1 for i in items
            if i["identity_conflict_kind"].startswith("stale roster")),
        "evidence_hashes_all_match": sum(
            1 for i in items if i["evidence_integrity"]["hashes_match"]),
        "evidence_incomplete": sum(
            1 for i in items if not i["evidence_integrity"]["evidence_complete"]),
        "blocked_by_evidence_integrity": sum(
            1 for i in items
            if i["evidence_integrity"]["blocks_automatic_approval"]),
        "would_publish": sum(1 for i in items if i["would_publish"]),
        "reviewer_auto_approve_on_publishable": sum(
            1 for i in items if i["would_publish"]
            and (i["independent_reviewer"] or {}).get("verdict") == "AUTO_APPROVE"),
        "reviewer_auto_approve_on_no_impact": sum(
            1 for i in items if not i["would_publish"]
            and (i["independent_reviewer"] or {}).get("verdict") == "AUTO_APPROVE"),
    }

    # The reviewer's order: the three that would publish on a machine's say-so
    # first, then everything the two systems disagree about, then the rest of
    # the proposed cards. Suppression agreements go last and are counted
    # separately -- an AUTO_APPROVE on a NO_FANTASY_IMPACT item is two systems
    # agreeing to publish nothing, and it carries no publication risk.
    def band(it):
        v = (it["independent_reviewer"] or {}).get("verdict")
        if it["would_publish"] and v == "AUTO_APPROVE":
            return (0, "Card-producing auto-approvals")
        if it["disagreement"]:
            return (1, "Action-level disagreements")
        if it["would_publish"]:
            return (2, "Remaining proposed cards")
        if v == "AUTO_APPROVE":
            return (3, "Suppression agreements (no card proposed)")
        return (4, "Other assessments")

    for it in items:
        it["band"], it["band_name"] = band(it)
    items.sort(key=lambda x: (x["band"], x["player"]))

    payload = {
        "generated_at": datetime.now(timezone.utc).replace(
            microsecond=0).isoformat(),
        "note": ("REVIEW ONLY. Nothing in this package has been published. "
                 "The live card set is unchanged."),
        "window_applied": win,
        "window_label_as_written_by_the_run":
            bf.get("window_label_as_written_by_the_run"),
        "window_metadata_note": bf.get("window_metadata_note", ""),
        "funnel": {"deterministic": bf.get("deterministic", {}),
                   "window_articles": bf.get("window_articles", {}),
                   "bridge": bridge},
        "totals": tot,
        "integrity_rule_audit": rule_audit,
        "human_verdict_on_card_producing_auto_approvals": {
            "reviewed": 3, "approved": 0, "rejected": 3,
            "precision": "0 of 3",
            "detail": [
              {"player": "Dak Prescott", "decision": "STALE_EVENT",
               "reason": "UNRELIABLE_EVENT_TIME",
               "note": ("June minicamp content on a continuously updated page "
                        "stamped 20 August")},
              {"player": "Dameon Pierce", "decision": "NOT_FANTASY_RELEVANT",
               "reason": "WATCHLIST_INJURY_WITHOUT_ACTIONABLE_ROLE",
               "note": ("a valid designation, but an injury alone cannot "
                        "create fantasy relevance for a watchlist player")},
              {"player": "Jonah Coleman", "decision": "NO_FANTASY_IMPACT",
               "reason": "INSUFFICIENT_ACTIONABLE_ROLE",
               "note": ("third running back behind two others is not an "
                        "opportunity, and is not Trending up")},
            ],
            "conclusion": ("Card-producing auto-approval precision on verified "
                           "evidence is 0 of 3. Automatic publication stays "
                           "disabled."),
        },
        "open_safeguard_required": {
            "issue": "dynamic live-update pages need span-level event dates",
            "found_via": "Dak Prescott",
            "detail": ("A continuously updated page carries one page-level "
                       "timestamp, and the window filter reads it as the date "
                       "of every passage on the page. June minicamp content "
                       "therefore entered a 48-hour window stamped 20 August. "
                       "A page's latest timestamp cannot make every older "
                       "update on it recent."),
            "status": "NOT YET IMPLEMENTED — no span-level date extraction",
        },
        "identity_flag_caveat": {
            "status": "NOT RELIABLE — do not act on this flag alone",
            "finding": ("The registry-authority instruction is in the prompt "
                "and the identity block is supplied first, and the reviewer "
                "still overrode it from pretrained knowledge. It rejected a "
                "Kyler Murray item on the grounds that 'Kyler Murray does not "
                "play for Minnesota'; the 2026 registry (version "
                "911ca3da496f792d, fetched 2026-08-20) has him on MIN. That "
                "is precisely the stale-roster failure the instruction was "
                "written to prevent."),
            "mixed_with_genuine_catches": ("Other flags are real subject "
                "errors -- one correctly says a passage attributed to "
                "D'Andre Swift is about Roschon Johnson, and the registry "
                "agrees both are CHI RBs. So the flag cannot be read as "
                "either reliably right or reliably wrong."),
            "supplied_identity_self_consistent": "58 of 60 (2 carry no id)",
            "recommendation": ("Treat every identity flag as HUMAN_REVIEW. Do "
                "not let it drive a rejection until the reviewer stops "
                "contradicting the supplied roster."),
        },
        "audit_history": {
            "note": ("Kept for accountability. Excluded from every metric "
                     "above."),
            "invalid_reviewer_run": ({
                "validity": invalid.get("validity"),
                "reason": invalid.get("invalidation_reason"),
                "verdicts": invalid.get("verdicts"),
                "cost_usd": invalid.get("cost_usd"),
                "reviewed": invalid.get("reviewed"),
                "excluded_from_metrics": invalid.get("excluded_from_metrics"),
            } if invalid else None),
        },
        "items": items,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=1) + "\n")

    # ---- the page --------------------------------------------------------
    def row(k, v):
        return f"<div class=kv><b>{esc(k)}</b><span>{esc(v)}</span></div>"

    # A card whose heading and registry identity name different players is
    # not a formatting problem, it is a card about nobody. Refuse to build.
    disagree = [f"{i['player']!r} vs registry {i['registry_identity'].get('player_name')!r}"
                for i in items
                if i["registry_identity"].get("player_name")
                and i["registry_identity"]["player_name"] != i["player"]]
    if disagree:
        raise SystemExit("card heading disagrees with registry identity: "
                         + "; ".join(disagree[:5]))
    distinct = {i["registry_identity"].get("player_id") for i in items
                if i["registry_identity"].get("player_id")}
    if len(items) > 1 and len(distinct) < 2:
        raise SystemExit(
            f"only {len(distinct)} distinct registry identity across "
            f"{len(items)} cards -- a stale identity is populating them")

    cards = []
    for i, it in enumerate(items, 1):
        g, va, rvw = it["generator"], it["validator"], it["independent_reviewer"]
        rid = it["registry_identity"]
        verdict = (rvw or {}).get("verdict", "NOT RUN")
        if i == 1 or items[i - 2]["band_name"] != it["band_name"]:
            cards.append(
                f'<h2 style="margin-top:30px">{esc(it["band_name"])}</h2>')
        cards.append(f"""
<article class="c{' dis' if it['disagreement'] else ''}" id="i{i}"
  data-verdict="{esc(verdict)}" data-pos="{esc(it['position'])}">
  <header>
    <span class=n>{i}</span>
    <h3>{esc(it['player'])} <em>{esc(it['team'])} {esc(it['position'])}</em></h3>
    <span class="tag v-{esc(verdict)}">{esc(verdict)}</span>
    {'<span class="tag pub">WOULD BECOME A CARD</span>' if it['would_publish'] else '<span class="tag nopub">no card proposed</span>'}
    {'<span class="tag dis">GENERATOR / REVIEWER DISAGREE</span>' if it['disagreement'] else ''}
  </header>

  <h4>Complete stored evidence <small>({it['evidence_integrity']['evidence_chars']} characters,
      {'complete' if it['evidence_integrity']['evidence_complete'] else 'INCOMPLETE'})</small></h4>
  <blockquote>{esc(it['evidence_text'])}</blockquote>
  <h4>Evidence integrity</h4>
  <div class=grid>
    {row('stored sha256', it['evidence_integrity']['evidence_sha256'][:24])}
    {row('generator input', (it['evidence_integrity']['generator_input_evidence_sha256'] or 'NOT RECORDED')[:24])}
    {row('reviewer input', (it['evidence_integrity']['reviewer_input_evidence_sha256'] or 'NOT RECORDED')[:24])}
    {row('human display', (it['evidence_integrity']['human_display_evidence_sha256'] or 'NOT RECORDED')[:24])}
    {row('characters', it['evidence_integrity']['evidence_chars'])}
    {row('segment start / end', f"{it['evidence_integrity']['segment_start']} / {it['evidence_integrity']['segment_end']}")}
    {row('all hashes match', it['evidence_integrity']['hashes_match'])}
    {row('status', it['evidence_integrity']['status'])}
  </div>
  {'<p class=missing>' + esc('; '.join(it['evidence_integrity']['incompleteness_reasons'])) + '</p>' if it['evidence_integrity']['incompleteness_reasons'] else ''}
  {'<p class=missing>hash mismatch: ' + esc(', '.join(it['evidence_integrity']['hash_mismatches'] + it['evidence_integrity']['hashes_not_recorded'])) + ' — blocks automatic approval</p>' if it['evidence_integrity']['blocks_automatic_approval'] else ''}

  <h4>Public evidence sentence</h4>
  <p class=missing>{esc(it['public_evidence_summary_status'])}</p>

  <div class=grid>
    {row('Reporter', it['reporter'])}
    {row('Publication', it['publication'])}
    {row('Published', it['published_at'])}
    {row('Evidence class', it['evidence_class'])}
    {row('Ownership', it['ownership'])}
    {row('Authority / tier', it['authority'])}
    {row('Underlying report', it['underlying_report_id'] or 'none — original')}
    <div class=kv><b>Link</b><span><a href="{esc(it['url'])}"
       target=_blank rel="nofollow noopener">source</a></span></div>
  </div>

  <h4>Player registry identity <small>(from wire_players, validated in code)</small></h4>
  <div class=grid>
    {row('stable player id', rid.get('player_id'))}
    {row('registry name', rid.get('player_name'))}
    {row('registry team', rid.get('team'))}
    {row('registry position', rid.get('position'))}
    {row('roster snapshot', rid.get('registry_version'))}
    {row('registry check', rid.get('registry_check'))}
  </div>

  <h4>Proposed public evidence sentence</h4>
  <p class=missing>{esc(it['public_evidence_summary_status'])}</p>

  <h4>Generator</h4>
  <div class=grid>
    {row('Decision', g['decision'])}
    {row('Mechanism', g['mechanism'])}
    {row('Direction', g['direction'])}
    {row('Strength', g['strength'])}
    {row('Horizon', g['horizon'])}
    {row('Projection action', g['projection_action'])}
    {row('Confidence', g['confidence'])}
    {row('Model', g['model'])}
    {row('Prompt', g['prompt_version'])}
    {row('Schema', g['schema_version'])}
    {row('Latency ms', g['latency_ms'])}
  </div>
  <h4>Proposed Lineup Beat impact</h4>
  <p class=imp>{esc(g['commentary']) or '<em>none</em>'}</p>
  <h4>Limitations</h4>
  <p class=lim>{esc(g['limitations']) or '<em>none stated</em>'}</p>

  <h4>Deterministic validator</h4>
  <p class="ver v-{esc(va['verdict'])}">{esc(va['verdict'])}
     {esc(va['reason'])}</p>

  <h4>Independent reviewer <small>(dark launch, publishes nothing)</small></h4>
  <div class=grid>
    {row('Verdict', verdict)}
    {row('Subject correct', rvw.get('subject_is_correct'))}
    {row('Mechanism supported', rvw.get('mechanism_is_supported'))}
    {row('Direction supported', rvw.get('direction_is_supported'))}
    {row('Commentary overstates', rvw.get('commentary_overstates'))}
    {row('Repeats the evidence', rvw.get('commentary_repeats_evidence'))}
    {row('Inference not in evidence', rvw.get('inference_not_in_evidence'))}
    {row('Performance only, no role info', rvw.get('performance_only_no_role_information'))}
    {row('Passage names another subject', rvw.get('passage_names_a_different_subject'))}
    {row('Conflict kind', it['identity_conflict_kind'])}
    {row('Reviewer model', rvw.get('model'))}
    {row('Reviewer prompt', rvw.get('prompt_version'))}
  </div>
  <p class=rev>{esc(rvw.get('disagreement_summary'))}</p>

  <h4>Your decision</h4>
  <div class=controls>
    <label><input type=radio name="d{i}" value=APPROVE> Approve</label>
    <label><input type=radio name="d{i}" value=APPROVE_WITH_EDIT> Approve with edit</label>
    <label><input type=radio name="d{i}" value=REJECT> Reject</label>
    <input class=note type=text placeholder="reason or edited wording">
  </div>
</article>""")

    def tbl(d):
        return "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>"
                       for k, v in sorted(d.items(), key=lambda kv: -kv[1]))

    b = bridge.get("steps", [])
    bridge_rows = "".join(
        f"<tr><td>{esc(s['step'])}</td><td class=r>{esc(s['dropped'])}</td>"
        f"<td class=r>{esc(s['left'])}</td></tr>" for s in b)

    det = bf.get("deterministic", {})
    rejected = {k: v for k, v in det.items() if not k.startswith("note::")}
    funnel_rows = "".join(
        f"<tr><td>{esc(k)}</td><td class=r>{esc(v)}</td></tr>"
        for k, v in sorted(rejected.items(), key=lambda kv: -kv[1]))
    total_cand = sum(rejected.values()) + tot["model_calls"]

    page = f"""<!doctype html><meta charset=utf-8>
<title>Wire review package — {len(items)} unique claims</title>
<style>
:root{{color-scheme:dark}}
body{{background:#0d0f10;color:#e6eae7;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:28px}}
.wrap{{max-width:1000px;margin:0 auto}}
h1{{font-size:1.5rem;margin:0 0 4px}}
.sub{{color:#8f978f;margin:0 0 22px}}
.banner{{background:#1c2a1c;border:1px solid #3a5a3a;border-radius:8px;padding:12px 16px;margin:0 0 22px}}
table{{border-collapse:collapse;width:100%;margin:0 0 18px;font-size:.86rem}}
td,th{{border-bottom:1px solid #232726;padding:5px 8px;text-align:left}}
td.r{{text-align:right;font-variant-numeric:tabular-nums}}
h2{{font-size:1rem;text-transform:uppercase;letter-spacing:.08em;color:#8f978f;margin:26px 0 8px}}
article.c{{background:#131617;border:1px solid #232726;border-left:3px solid #2f6b8f;border-radius:9px;padding:16px 18px;margin:0 0 16px}}
article.c.dis{{border-left-color:#e0a24a;background:#171412}}
header{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}}
header h3{{margin:0;font-size:1.1rem}} header em{{font-style:normal;color:#8f978f;font-size:.85rem}}
.n{{color:#5d655e;font-variant-numeric:tabular-nums}}
.tag{{font-size:.62rem;letter-spacing:.08em;text-transform:uppercase;font-weight:700;padding:2px 8px;border-radius:99px;border:1px solid #35403a}}
.v-AUTO_APPROVE{{color:#7fbf8a;border-color:#7fbf8a}}
.v-HUMAN_REVIEW{{color:#d8c26a;border-color:#d8c26a}}
.v-REJECT{{color:#e08a7f;border-color:#e08a7f}}
.v-ABSTAIN,.v-NOT.RUN{{color:#8f978f}}
.tag.dis{{color:#e0a24a;border-color:#e0a24a}}
.tag.pub{{color:#a9d64b;border-color:#a9d64b}}
.tag.nopub{{color:#6a726c;border-color:#2c332e}}
h4{{font-size:.62rem;letter-spacing:.1em;text-transform:uppercase;color:#8f978f;margin:14px 0 4px}}
blockquote{{margin:0;padding:9px 12px;background:#0e1112;border-left:2px solid #35403a;border-radius:0 6px 6px 0;font-size:.9rem;color:#c2c9c4}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:2px 14px}}
.kv{{display:flex;gap:7px;font-size:.8rem;padding:1px 0}}
.kv b{{color:#8f978f;font-weight:600;min-width:118px}}
.imp{{background:#161b14;border-left:3px solid #a9d64b;border-radius:0 6px 6px 0;padding:9px 12px;margin:0}}
.lim,.rev{{color:#a8b0aa;font-size:.87rem;margin:0}}
.missing{{color:#d8a06a;font-size:.85rem;margin:0}}
.ver{{margin:0;font-size:.85rem}}
.controls{{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-top:6px}}
.controls label{{font-size:.83rem;display:flex;gap:5px;align-items:center}}
.note{{flex:1;min-width:220px;background:#0e1112;border:1px solid #232726;border-radius:6px;color:#e6eae7;padding:6px 9px;font:inherit;font-size:.83rem}}
a{{color:#8fb8d8}}
</style>
<div class=wrap>
<h1>Wire review package</h1>
<p class=sub>{len(items)} unique claims,
{tot['would_publish']} of which would become a card,
{tot['disagreements']} with an action-level generator/reviewer disagreement. Generated {esc(payload['generated_at'])}.</p>

<div class=banner style="background:#2a2113;border-color:#6a5320">
<b>Read the verdicts carefully.</b> The reviewer judges whether an
INTERPRETATION is sound, not whether a card should exist.
{tot['reviewer_auto_approve_on_no_impact']} of its
{tot['reviewer_verdicts'].get('AUTO_APPROVE', 0)} AUTO_APPROVE verdicts are on
items the generator already called NO_FANTASY_IMPACT — the two agreeing there
is nothing to publish. Only
<b>{tot['reviewer_auto_approve_on_publishable']}</b> auto-approvals sit on an
item that would actually become a card. The
<b>{tot['would_publish']}</b> items tagged WOULD BECOME A CARD are the ones
carrying publication risk.</div>

<div class=banner><b>Nothing here is published.</b> The live set is unchanged
at {tot['publications_live']} cards. The independent reviewer is a dark
launch: advisory only, and it publishes nothing. The public evidence sentence
is not auto-generated — it carries a recorded human approval.</div>

<h2>Window applied</h2>
<table>
<tr><td>from</td><td>{esc(win.get('from'))}</td></tr>
<tr><td>to</td><td>{esc(win.get('to'))}</td></tr>
<tr><td>hours</td><td>{esc(win.get('hours'))}</td></tr>
</table>
<p class=sub style="font-size:.83rem">{esc(payload['window_metadata_note'])}</p>

<h2>Funnel — {total_cand} candidates in the window</h2>
<table><tr><th>rejected before the model, by reason</th><th class=r>n</th></tr>
{funnel_rows}
<tr><td><b>reaching the model</b></td><td class=r><b>{tot['model_calls']}</b></td></tr>
<tr><td><b>total</b></td><td class=r><b>{total_cand}</b></td></tr></table>

<h2>How eligible evidence rows became {tot['model_calls']} unique calls</h2>
<table><tr><th>step</th><th class=r>dropped</th><th class=r>left</th></tr>
{bridge_rows}</table>

<h2>Totals</h2>
<table>
<tr><td>interpretations</td><td class=r>{tot['interpretations']}</td></tr>
<tr><td>abstentions</td><td class=r>{tot['abstentions']}</td></tr>
<tr><td>no-fantasy-impact outcomes</td><td class=r>{tot['no_fantasy_impact']}</td></tr>
<tr><td>validation failures</td><td class=r>{tot['validation_failures']}</td></tr>
<tr><td>pending human reviews</td><td class=r>{tot['pending_human_reviews']}</td></tr>
<tr><td>publications applied</td><td class=r>{tot['publications_applied']}</td></tr>
<tr><td>evidence: all four hashes match</td><td class=r>{tot['evidence_hashes_all_match']} / {len(items)}</td></tr>
<tr><td>evidence incomplete</td><td class=r>{tot['evidence_incomplete']}</td></tr>
<tr><td>blocked by evidence integrity</td><td class=r>{tot['blocked_by_evidence_integrity']}</td></tr>
<tr><td>generator cost (valid rerun)</td><td class=r>${tot['generator_cost_usd']:.4f}</td></tr>
<tr><td>reviewer cost (valid rerun)</td><td class=r>${tot['reviewer_cost_usd']:.4f}</td></tr>
<tr><td>invalid run cost (excluded)</td><td class=r>${tot['invalid_run_cost_usd']:.4f}</td></tr>
<tr><td>generator latency median / p95</td><td class=r>{tot['generator_median_latency_ms']} / {tot['generator_p95_latency_ms']} ms</td></tr>
</table>

<h2>Reviewer verdicts <small>(valid rerun only)</small></h2>
<table>{tbl(tot['reviewer_verdicts'])}</table>

<h2>Identity flag — not reliable</h2>
<div class=banner style="background:#2a2113;border-color:#6a5320">
<b>Do not act on identity_conflicts_with_supplied_registry alone.</b>
The reviewer overrode the supplied 2026 registry from pretrained knowledge:
it rejected a Kyler Murray item because "Kyler Murray does not play for
Minnesota", and the registry has him on MIN. Other flags are genuine subject
catches. Treat every one as human review.</div>

<h2>Evidence integrity rule — measured before it was applied</h2>
{('<table><tr><th>rule state</th><th class=r>rows refused</th>'
  '<th class=r>of corpus</th></tr>'
  + ''.join(f"<tr><td>{esc(x['state'])}</td><td class=r>{esc(x['refused'])}</td>"
            f"<td class=r>{esc(x['pct'])}</td></tr>"
            for x in rule_audit['stages'])
  + '</table><p class=sub style="font-size:.84rem">'
  + esc(rule_audit['conclusion']) + '</p>') if rule_audit else
 '<p class=sub>not recorded</p>'}

<h2>Audit history — invalid reviewer run, excluded from all metrics above</h2>
{('<div class=banner style="background:#2a1616;border-color:#6a2a2a">'
  f"<b>{esc(invalid.get('validity'))}</b><br>"
  f"{esc(invalid.get('invalidation_reason'))}<br><br>"
  f"verdicts {esc(invalid.get('verdicts'))} &middot; "
  f"cost ${invalid.get('cost_usd', 0):.4f} &middot; "
  f"{esc(invalid.get('reviewed'))} items reviewed</div>")
 if invalid else '<p class=sub>none</p>'}
<h2>By position</h2><table>{tbl(tot['by_position'])}</table>
<h2>By evidence class</h2><table>{tbl(tot['by_evidence_class'])}</table>
<h2>By mechanism</h2><table>{tbl(tot['by_mechanism'])}</table>
<h2>By team</h2><table>{tbl(tot['by_team'])}</table>

<h2>Publication risk</h2>
<table>
<tr><td>card-producing auto-approvals</td><td class=r>{tot['reviewer_auto_approve_on_publishable']}</td></tr>
<tr><td>action-level disagreements</td><td class=r>{tot['disagreements']}</td></tr>
<tr><td>proposed cards in total</td><td class=r>{tot['would_publish']}</td></tr>
<tr><td>claim-subject conflicts (real)</td><td class=r>{tot['claim_subject_conflicts']}</td></tr>
<tr><td>stale roster objections (ignored)</td><td class=r>{tot['roster_objections_ignored']}</td></tr>
</table>
<p class=sub style="font-size:.84rem">Suppression agreements —
{tot['suppression_agreements']} items where the reviewer endorsed a
NO_FANTASY_IMPACT call — are reported separately and are NOT part of the
publication-risk count.</p>

<h2>The {len(items)} claims, in review order</h2>
{''.join(cards)}
</div>"""
    OUT_HTML.write_text(page)
    print(f"  {len(items)} item(s); {tot['disagreements']} disagreement(s)")
    print(f"  reviewer verdicts: {tot['reviewer_verdicts']}")
    print(f"  wrote {OUT_HTML} and {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
