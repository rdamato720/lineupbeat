#!/usr/bin/env python3
"""Prove every firsthand candidate came from an approved authority.

    python3 scripts/wire_authority_audit.py

Authority comes from the verified author registry and from nothing else. It
does not come from a reporter_name in a yaml file, a source_name, a team, or
a series_name -- and that is not a hypothetical: naming Jim Wyatt in the
Titans config granted his articles a firsthand voice at a source where he is
deliberately unapproved, and it took reading the dry run to notice.

This report exists so that failure is caught by a check rather than by
someone happening to look.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import registry as artreg
from wire import si
from wire.store import WireStore

OUT = Path("data/wire_authority_audit.json")


def main():
    store = WireStore()
    sources = {s.source_id: s for s in artreg.load()}
    auth = si.load_authors()

    approved = {}
    for team, e in auth.get("teams", {}).items():
        for name, a in e["authors"].items():
            if a["classification"] == "FIRSTHAND_APPROVED":
                approved[(team, name)] = a

    # A named local reporter is a verified single-author publication; a
    # club writer needs an explicit evidence_access grant AND a series.
    named_local = {}
    for s in sources.values():
        if s.source_class == artreg.OFFICIAL_TEAM_SITE:
            if s.evidence_access == "TEAM_EMPLOYED_FIRSTHAND" and s.reporter_name:
                named_local[(s.teams[0] if s.teams else "", s.reporter_name)] = \
                    f"restricted series {s.qualifying_series!r}"
        elif s.reporter_name and "staff" not in s.reporter_name.lower():
            named_local[(s.teams[0] if s.teams else "", s.reporter_name)] = \
                f"single-author publication {s.source_name}"

    rows = [dict(r) for r in store.evidence()
            if r["evidence_class"] == "FIRSTHAND_OBSERVATION"
            and r["review_status"] in ("PENDING", "APPROVED")]

    grouped = defaultdict(list)
    unauthorised = []
    for r in rows:
        src = sources.get(r["source_id"])
        team = (src.teams[0] if src and src.teams else r["team"])
        who = r["source_author_or_channel"]
        key = (team, r["source_id"], who)
        grouped[key].append(r)
        if (team, who) not in approved and (team, who) not in named_local:
            unauthorised.append(r)

    print(f"  {len(rows)} firsthand candidate(s) across {len(grouped)} "
          f"team/source/author group(s)")
    print(f"  {'TEAM':<5}{'SOURCE':<24}{'AUTHOR':<22}{'N':>4}  BASIS")
    report = []
    for (team, sid, who), items in sorted(grouped.items()):
        a = approved.get((team, who))
        if a:
            basis = f"FIRSTHAND_APPROVED ({a['articles_read']} articles read)"
            status = "APPROVED_AUTHOR"
        elif (team, who) in named_local:
            basis = named_local[(team, who)]
            status = "APPROVED_SERIES_OR_SOLE_AUTHOR"
        else:
            basis = "NO APPROVAL ON RECORD"
            status = "UNAUTHORISED"
        print(f"  {team:<5}{sid[:23]:<24}{(who or '(none)')[:21]:<22}"
              f"{len(items):>4}  {basis[:44]}")
        report.append({"team": team, "source_id": sid, "author": who,
                       "candidates": len(items), "status": status,
                       "basis": basis,
                       "approval_evidence": (a or {}).get("evidence", ""),
                       "sample_urls": (a or {}).get("sample_urls", []),
                       "restricted_series": (
                           sources[sid].qualifying_series
                           if sid in sources else "")})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"firsthand_candidates": len(rows),
         "groups": report,
         "unauthorised_candidates": len(unauthorised),
         "note": "authority comes only from the verified author/series "
                 "registry; configuration fields grant nothing"}, indent=1) + "\n")
    print(f"\n  unauthorised firsthand candidates: {len(unauthorised)}")
    print(f"  wrote {OUT}")
    return 1 if unauthorised else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
