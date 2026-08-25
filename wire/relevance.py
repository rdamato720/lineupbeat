"""Is a report about this player worth interpreting at all?

Position eligibility answered the wrong question. Anthony Richardson is a
quarterback in the registry, so every identity and authority check passed,
and a routine second-team practice rep became a published card. Nothing had
asked whether a report about him could matter to a 2026 redraft roster.

Two ways in, and only two:

    the player is in the relevance registry, which is built upstream from the
    projection board and contains tiers and reasons, never numbers

    the evidence itself establishes a material opportunity -- named the
    starter, promoted to first-team work, replacing someone absent, real
    red-zone or goal-line work, repeated targets or carries, a signing into a
    plausible role, a depth-chart move, or an absence with an identified
    beneficiary

Routine reserve work is neither, and that is the whole point. A backup
quarterback taking the second-team snaps he takes every day is not news
because he is a quarterback.

Relevance opens the door to review. It never authorises publication.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "wire_fantasy_relevance.json"

ROSTERABLE, WATCHLIST, CONTINGENT, NOT_RELEVANT = (
    "ROSTERABLE", "WATCHLIST", "CONTINGENT", "NOT_RELEVANT")

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE"}

# Evidence that creates relevance for a player the registry does not carry.
OPPORTUNITY = [
    ("named the starter", re.compile(
        r"(?i)\b(named (?:[A-Z][\w.'-]+\s+){0,3}(?:the )?(?:starting|starter)|"
        r"will start|won the starting (?:job|competition)|"
        r"announced as (?:the )?starter|gets the (?:start|nod)|"
        r"(?:is|as) the (?:new )?starter)\b")),
    ("promoted to first-team work", re.compile(
        r"(?i)\b((?:moved|promoted|elevated|bumped) (?:up )?(?:in)?to the "
        r"(?:first|1st|no\.? ?1)[-\s]team|took over (?:with )?the (?:ones|1s)|"
        r"first[-\s]team (?:reps?|snaps?|offense|defense)|"
        r"with the (?:ones|1s|starters))\b")),
    ("replacing an absent starter", re.compile(
        r"(?i)\b(in place of|replac(?:ed|ing)|filled in for|stepped in for|"
        r"with .{0,28} (?:out|sidelined|absent|unavailable)|"
        r"while .{0,28} (?:is |was )?(?:out|sidelined|absent))\b")),
    ("red-zone or goal-line work", re.compile(
        r"(?i)\b(red[-\s]?zone|goal[-\s]?line)\b")),
    ("a material workload or role", re.compile(
        r"(?i)\b(?:(?:led|most|majority|featured|primary|every[- ]down|"
        r"first[- ]team|with the (?:ones|1s)).{0,45}"
        r"(?:targets?|routes?|carries|touches|backfield|snap share|workload)|"
        r"(?:targets?|routes?|carries|touches|backfield|snap share|workload)"
        r".{0,45}(?:led|most|majority|increased|first[- ]team|"
        r"with the (?:ones|1s)))\b")),
    ("a signing or trade into a role", re.compile(
        r"(?i)\b(signed (?:with|by|a)|traded (?:to|for)|claimed off waivers|"
        r"agreed to terms)\b")),
    ("a depth-chart change", re.compile(
        r"(?i)\b(depth chart|moved (?:up|ahead of)|passed .{0,24} on the|"
        r"climbed|demoted|dropped behind)\b")),
    ("an absence with an identified beneficiary", re.compile(
        r"(?i)\b(with .{0,28} (?:out|absent|sidelined).{0,60}"
        r"(?:took|received|got|saw|worked)|benefit(?:s|ed)? from)\b")),
]

# A backup quarterback needs one of these specifically. Second-team reps are
# his ordinary Tuesday.
QB_PROMOTION = re.compile(
    r"(?i)\b(named (?:[A-Z][\w.'-]+\s+){0,3}(?:the )?(?:starting|starter)|"
    r"will start|won the starting (?:job|competition)|"
    r"(?:competing|competition|battle) (?:for|to be) (?:the )?"
    r"(?:starter|starting quarterback (?:job|role))|"
    r"(?:is|as) the (?:new )?starter|took (?:over )?(?:the )?first[-\s]team|"
    r"with the (?:ones|1s|starters)|first[-\s]team (?:reps?|snaps?)|"
    r"in place of|replac(?:ed|ing)|elevated to (?:the )?starter|"
    r"moved ahead of .{0,35}(?:for the starting job|to start)|"
    r"starter (?:is |was )?(?:out|injured|sidelined))\b")

ROUTINE = re.compile(
    r"(?i)\b(second[-\s]team|third[-\s]team|with the (?:twos|2s|threes|3s)|"
    r"expected to play|preseason (?:snaps|action|playing time)|"
    r"scout team|reserve)\b")

def load(path: Path | None = None) -> dict:
    p = Path(path or REGISTRY)
    if not p.exists():
        return {"players": {}, "boundaries": {}, "count": 0}
    payload = json.loads(p.read_text())
    return {"players": {r["player_id"]: r for r in payload.get("players", [])},
            "boundaries": payload.get("boundaries", {}),
            "count": payload.get("count", 0)}


def evidence_opportunity(text: str) -> str:
    """Which material opportunity this passage establishes, or ""."""
    for label, pat in OPPORTUNITY:
        if pat.search(text or ""):
            return label
    return ""


def assess(player_id: str, position: str, text: str,
           registry: dict | None = None) -> dict:
    """May this report enter Claude interpretation and review?

    Returns the decision and the reason for it. The reason is stored, because
    "suppressed" without "why" cannot be audited and is indistinguishable
    from a bug.
    """
    registry = registry if registry is not None else load()
    pos = (position or "").upper()

    if pos not in FANTASY_POSITIONS:
        return {"eligible": False, "tier": NOT_RELEVANT,
                "reason": f"{pos or 'unknown position'} is team context, not a "
                          f"fantasy player"}

    entry = registry["players"].get(player_id)
    tier = entry["relevance_tier"] if entry else None
    opportunity = evidence_opportunity(text)

    # A backup quarterback is the specific case that went wrong. Being a
    # quarterback is not the qualification; moving in the competition is.
    if pos == "QB" and tier is None:
        if QB_PROMOTION.search(text or ""):
            return {"eligible": True, "tier": CONTINGENT,
                    "reason": "quarterback outside the registry whose evidence "
                              "shows movement in the starting competition"}
        return {"eligible": False, "tier": NOT_RELEVANT,
                "reason": "backup quarterback with no promotion, starter "
                          "absence or first-team opportunity in the evidence"}

    if tier in (ROSTERABLE, WATCHLIST):
        if pos == "QB" and tier == WATCHLIST and not QB_PROMOTION.search(text or ""):
            return {"eligible": False, "tier": tier,
                    "reason": "watchlist quarterback with no true starting-job "
                              "battle, named start, starter absence or first-team "
                              "promotion"}
        # Even a rostered quarterback's routine second-team rep is not news.
        if pos == "QB" and ROUTINE.search(text or "") and not QB_PROMOTION.search(text or ""):
            return {"eligible": False, "tier": tier,
                    "reason": "routine reserve work by a quarterback: no "
                              "promotion or starter change in the evidence"}
        # A fringe/watchlist player needs the report itself to establish a
        # material role. Generic praise, an isolated preseason play and an
        # availability note do not become useful merely because the player
        # sits just beyond the core draft boundary.
        if tier == WATCHLIST and not opportunity:
            return {"eligible": False, "tier": tier,
                    "reason": "watchlist report with no actionable fantasy "
                              "role, workload or opportunity"}
        return {"eligible": True, "tier": tier,
                "reason": entry["relevance_reason"]}

    if opportunity:
        return {"eligible": True, "tier": CONTINGENT,
                "reason": f"outside the relevance registry, but the evidence "
                          f"establishes {opportunity}"}

    return {"eligible": False, "tier": NOT_RELEVANT,
            "reason": "outside the relevance registry and the evidence shows "
                      "only routine reserve work"}
