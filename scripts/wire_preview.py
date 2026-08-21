#!/usr/bin/env python3
"""The publication dry run. Writes nothing.

    python3 scripts/wire_preview.py
    python3 scripts/wire_preview.py --team NE --limit 5
    python3 scripts/wire_preview.py --json out.json

Two layers, kept apart on the page as they are in storage:

    What the reporter found   the evidence, its reporter, publication and link
    Lineup Beat impact        our interpretation, generated and reviewed here

Our commentary is never presented as something a reporter said and is never
placed inside quotation marks. Every item keeps a link to the source article.

Teams with nothing publishable are shown as such. A team missing from a
report reads as a team with no news, and those are different things.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import registry as artreg
from wire import si
from wire.store import WireStore

# Firsthand first, then named quotations, then labelled reporting. Analysis
# is shown last and always labelled: it is never dressed as observation.
ORDER = {"FIRSTHAND_OBSERVATION": 0, "DIRECT_QUOTATION": 1,
         "ANALYSIS_OR_OPINION": 2, "UNCERTAIN": 3}

LABEL = {"FIRSTHAND_OBSERVATION": "firsthand observation",
         "DIRECT_QUOTATION": "direct quotation",
         "ANALYSIS_OR_OPINION": "analysis or opinion",
         "UNCERTAIN": "unverified"}


def _aslist(v):
    """Reasons come back as JSON text from sqlite and as a list in tests."""
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except (TypeError, ValueError):
        return []


def team_of(source_id: str, sources: dict, fallback: str) -> str:
    s = sources.get(source_id)
    return s.teams[0] if s and s.teams else fallback


def build(store, limit: int) -> dict:
    sources = {s.source_id: s for s in artreg.load()}
    impacts = {}
    for r in store.impacts():
        if r["review_status"] in ("INVALIDATED", "REJECTED", "SUPERSEDED"):
            continue                      # never previewed
        for cid in json.loads(r["evidence_candidate_ids"]):
            impacts[cid] = r

    by_team = defaultdict(list)
    for r in store.evidence():
        if r["review_status"] in ("SUPERSEDED", "EXCLUDED", "REJECTED"):
            continue
        if r["exclusion_reason"] or r["duplicate_of"]:
            continue
        if not r["player_id"]:
            continue
        by_team[team_of(r["source_id"], sources, r["team"])].append(dict(r))

    out = {}
    for team in sorted(si.CODE_TO_SLUG):
        rows = sorted(by_team.get(team, []),
                      key=lambda r: (ORDER.get(r["evidence_class"], 9),
                                     -r["classification_confidence"],
                                     r["candidate_id"]))
        items = []
        shown_impacts: set = set()
        for r in rows[:limit]:
            imp = impacts.get(r["candidate_id"])
            if imp is not None:
                if imp["fantasy_impact_id"] in shown_impacts:
                    imp = None      # already shown against an earlier span
                else:
                    shown_impacts.add(imp["fantasy_impact_id"])
            items.append({
                "team": team, "player": r["player_name"],
                "position": r["position"],
                "classification": r["evidence_class"],
                "classification_label": LABEL.get(r["evidence_class"], "?"),
                "evidence_excerpt": r["evidence_text"],
                "reporter": r["source_author_or_channel"],
                "source": r["source_id"],
                "article_date": r["published_at"],
                "article_url": r["source_url"],
                "claim_confidence": r["classification_confidence"],
                "identity_confidence": r["resolution_confidence"],
                "registry_version": r["registry_version"],
                "why_qualifies": _aslist(r["classification_reasons"]),
                "evidence_id": r["candidate_id"],
                "fantasy_impact": imp["fantasy_impact"] if imp else None,
                "impact_strength": imp["impact_strength"] if imp else None,
                "impact_horizon": imp["impact_horizon"] if imp else None,
                "role_signal": imp["role_signal"] if imp else None,
                "projection_action": imp["projection_action"] if imp else None,
                "lineupbeat_commentary": imp["lineupbeat_commentary"] if imp else None,
                "impact_reasoning": imp["reasoning"] if imp else None,
                "impact_status": imp["review_status"] if imp else None,
                "supporting_evidence_ids": (
                    json.loads(imp["evidence_candidate_ids"]) if imp else []),
            })
        out[team] = items
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--json")
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    store = WireStore()
    preview = build(store, args.limit)

    if args.json:
        Path(args.json).write_text(json.dumps(preview, indent=1))

    teams = [args.team] if args.team else sorted(preview)
    empty = []
    for team in teams:
        items = preview.get(team, [])
        if not items:
            empty.append(team)
            if not args.full:
                continue
        print(f"\n{'='*72}\n  {team}   {len(items)} publishable item(s)")
        if not items:
            print("    nothing publishable in the window")
        for it in items:
            print(f"\n  {it['player']} ({it['team']} {it['position']})   "
                  f"[{it['classification_label']}]  claim "
                  f"{it['claim_confidence']:.2f} / identity "
                  f"{it['identity_confidence']:.2f}")
            print(f"    WHAT THE REPORTER FOUND  -- {it['reporter']}, "
                  f"{it['source']}, {it['article_date'][:10]}")
            _ex = it["evidence_excerpt"]
            print(f"      {_ex[:420]}"
                  + (f"  [+{len(_ex) - 420} more characters]"
                     if len(_ex) > 420 else ""))
            print(f"      {it['article_url'][:96]}")
            print(f"      qualifies: {'; '.join(it['why_qualifies'])[:110]}")
            if it["lineupbeat_commentary"]:
                print(f"    LINEUP BEAT IMPACT  [{it['fantasy_impact']}/"
                      f"{it['impact_strength']}/{it['impact_horizon']}]  "
                      f"{it['role_signal']}  action {it['projection_action']}"
                      f"  ({it['impact_status']})")
                print(f"      {it['lineupbeat_commentary']}")
                print(f"      because: {it['impact_reasoning'][:110]}")
                print(f"      from evidence: "
                      f"{', '.join(it['supporting_evidence_ids'][:3])}")
            else:
                print("    LINEUP BEAT IMPACT  none generated for this item")

    total = sum(len(v) for v in preview.values())
    print(f"\n{'='*72}")
    print(f"  {total} item(s) across {len(preview)} teams; "
          f"{len(empty)} team(s) with nothing publishable")
    if empty:
        print(f"  empty: {', '.join(empty)}")
    print("  DRY RUN -- wire_publications.json not written")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
