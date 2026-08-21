"""Coverage counted from the registry, by distinct team id.

Every number here is derived. A previous report said "32 teams still lack an
independent local source" while also reporting nine independent-local
sources, which cannot both be true: the nine cover nine teams, so twenty-
three lack one. That came from counting sources where the question was about
teams, and it is why nothing in this module is written down as a constant.

An official club site counts toward how many full-text sources a team has,
and never toward independence. Those are different questions and conflating
them is how a team ends up "covered" by two channels the same organisation
controls.
"""

from __future__ import annotations

from . import registry as artreg
from . import si

USABLE = {artreg.AUTO_READY, artreg.MANUAL_URL_ONLY,
          artreg.CUSTOM_ADAPTER_NEEDED, artreg.MANUAL_REVIEW_ONLY}


def teams() -> list[str]:
    return sorted(si.CODE_TO_SLUG)


def by_class(sources=None) -> dict:
    """{source_class: {team: [source_id, ...]}} for usable sources only."""
    out: dict = {}
    for s in (sources if sources is not None else artreg.load()):
        if s.paid or s.status not in USABLE:
            continue
        for t in s.teams:
            out.setdefault(s.source_class or "UNCLASSIFIED", {}) \
               .setdefault(t, []).append(s.source_id)
    return out


def summary(sources=None) -> dict:
    """Every coverage question, answered by distinct team id."""
    cls = by_class(sources)
    onsi = cls.get(artreg.SI_ONSI, {})
    local = cls.get(artreg.INDEPENDENT_LOCAL, {})
    official = cls.get(artreg.OFFICIAL_TEAM_SITE, {})

    def full(t):
        return len(onsi.get(t, [])) + len(local.get(t, [])) + len(official.get(t, []))

    def independent(t):
        # Non-team-owned. An official club site is excluded by definition.
        return len(onsi.get(t, [])) + len(local.get(t, []))

    all_teams = teams()
    return {
        "teams_total": len(all_teams),
        "with_any_full_text": [t for t in all_teams if full(t) >= 1],
        "with_two_full_text": [t for t in all_teams if full(t) >= 2],
        "with_non_team_owned": [t for t in all_teams if independent(t) >= 1],
        "with_independent_local": sorted(local),
        "without_independent_local": [t for t in all_teams if t not in local],
        "onsi_only_non_team_owned": [
            t for t in all_teams
            if onsi.get(t) and not local.get(t)],
        "source_counts": {
            "si_onsi": sum(len(v) for v in onsi.values()),
            "independent_local": sum(len(v) for v in local.values()),
            "official_team": sum(len(v) for v in official.values()),
            "paid_discovery_only": len([
                s for s in (sources if sources is not None else artreg.load())
                if s.paid]),
        },
        "total_full_text_sources": sum(full(t) for t in all_teams),
        "total_independent_sources": sum(independent(t) for t in all_teams),
    }
