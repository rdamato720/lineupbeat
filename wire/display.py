"""Fantasy display data, joined to an approved publication and nowhere else.

ADP, positional rank and projected points belong on a card and nowhere near
a decision. So this module is deliberately downstream of everything: it is
called after a reviewer has approved a publication, it joins on the stable
player_id and on nothing else, and it returns display strings.

Three rules it exists to enforce:

    the join is by player_id. Never by name -- two Josh Allens play different
    positions for different teams, and a name match would put one man's ADP
    on the other man's card.

    a miss omits the field. It never guesses, never falls back to a name, and
    never blocks the report: a reviewed observation is worth publishing
    without an ADP beside it.

    nothing here is read during interpretation. The model is never shown a
    number, the relevance gate reads tiers rather than ranks, and no value
    below can travel back upstream.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_CACHE: dict = {}
DISPLAY = ROOT / "data" / "wire_display_fantasy.json"


def _load() -> dict:
    """The prebuilt display file, and nothing else.

    An earlier version read rosters/nfl.csv and the projection board from
    inside the wire package, which is exactly what the Wire's isolation rule
    forbids: that file carries ADP, and the reason the Wire has its own
    player registry is that no fantasy number should be reachable from
    evidence interpretation. The join is built upstream by
    scripts/build_wire_display.py; this reads the flat result.
    """
    if _CACHE:
        return _CACHE
    if DISPLAY.exists():
        _CACHE.update(json.loads(DISPLAY.read_text()).get("players", {}))
    return _CACHE


def for_player(player_id: str) -> dict:
    """Display fields for an approved publication, or {} if we have none.

    An empty result is a normal outcome. The card renders without the field.
    """
    if not player_id:
        return {}
    return dict(_load().get(player_id, {}))


def decorate(publication: dict) -> dict:
    """Add display-only fields to an approved publication.

    Returns a copy. The publication itself is not modified, so nothing here
    can leak back into the record a decision was made from.
    """
    out = dict(publication)
    extra = for_player(publication.get("player_id", ""))
    if extra.get("position_rank"):
        out["display_position_rank"] = extra["position_rank"]
    if extra.get("adp") is not None:
        out["display_adp"] = extra["adp"]
    if extra.get("projected_points") is not None:
        out["display_projected_points"] = extra["projected_points"]
    # Image identity. Not a fantasy value: the site keys headshots on its
    # own player id, and without it the card can only draw initials.
    if extra.get("player_ref"):
        out["display_player_ref"] = extra["player_ref"]
    if extra.get("espn"):
        out["display_espn"] = extra["espn"]
    out["display_join"] = "player_id" if extra else "no match"
    return out
