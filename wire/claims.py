"""What a passage actually says about one named player.

Everything here answers the same question: is this claim about *this* player,
or is he merely nearby? The reviewed batch failed that question repeatedly.
Geno Smith praising Omar Cooper became Geno's account of his own role.
Daniel Jones inherited second-team reps from a paragraph about Anthony
Richardson. Anthony Hankerson, who had been waived, was described as
returning to practice. Each of those is proximity read as causality.

Three rules follow, and they are deliberately strict:

    A quotation belongs to its speaker, and is only about the speaker when
    the words themselves are about him.

    A unit claim belongs to the grammatical subject of the unit phrase. The
    nearest preceding name wins, and if it is not our player we make no
    claim -- we do not fall back to "someone in this paragraph".

    An availability signal needs the player to be the subject of the
    availability language, and is blocked outright by language that means
    something else happened to him: waived, signed, released.

When no rule fires, the answer is NO_FANTASY_IMPACT rather than a hedged
sentence. Not every mention of a player deserves commentary.
"""

from __future__ import annotations

import re

from . import players as pl

NO_FANTASY_IMPACT = "NO_FANTASY_IMPACT"

FIRST_TEAM, SECOND_TEAM, THIRD_TEAM = "FIRST_TEAM", "SECOND_TEAM", "THIRD_TEAM"

UNIT_PATTERNS = [
    (FIRST_TEAM, re.compile(
        r"(?i)\b(?:first|1st|no\.?\s?1)[-\s]team\b|\bwith the (?:ones|1s|starters)\b"
        r"|\bfirst[-\s]team (?:offense|defense|line|reps?|snaps?)\b")),
    (SECOND_TEAM, re.compile(
        r"(?i)\b(?:second|2nd|no\.?\s?2)[-\s]team\b|\bwith the (?:twos|2s)\b"
        r"|\bsecond[-\s]team (?:offense|defense|line|reps?|snaps?)\b")),
    (THIRD_TEAM, re.compile(
        r"(?i)\b(?:third|3rd|no\.?\s?3)[-\s]team\b|\bwith the (?:threes|3s)\b"
        r"|\bthird[-\s]team (?:offense|defense|line|reps?|snaps?)\b")),
]

# A capitalised personal name, used to find who a clause is about.
NAME = re.compile(r"\b[A-Z][a-z']+(?:\s+[A-Z][a-z'\-]+)?(?:\s+(?:Jr|Sr|II|III)\.?)?\b")

# Words that look like names but are not people.
NOT_A_NAME = {
    "the", "on", "in", "at", "with", "and", "but", "he", "she", "they", "it",
    "that", "this", "there", "here", "when", "while", "after", "before",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "week", "day", "practice", "camp", "training", "preseason",
    "offense", "defense", "team", "first", "second", "third", "no",
}

RETURN_LANG = re.compile(
    r"(?i)\b(returned to (?:the )?practice|back (?:at|on|in) (?:the )?(?:practice|field)|"
    r"back at practice|practiced (?:again|for the first time)|participated again|"
    r"was (?:a )?full participant|cleared to (?:practice|return)|"
    r"activated (?:off|from)|came off (?:the )?(?:pup|nfi)|"
    r"returned (?:to action|to the field))\b")

ABSENCE_LANG = re.compile(
    r"(?i)\b(did not (?:practice|participate|take part)|was (?:limited|held out)|"
    r"missed (?:his |the |another |a )?(?:\w+\s+)?(?:practice|session|day|week)|"
    r"has (?:not|yet to) (?:practiced|participated)|"
    r"remains? (?:out|sidelined|on the (?:pup|nfi))|non[-\s]participant|"
    r"sidelined|in a walking boot|left (?:practice|the field)|"
    r"reaggravated|re-?injured|carted off|ruled out|"
    r"absent from (?:practice|the field)|out of practice)\b")

# Things that are emphatically not a return to practice, and which the
# reviewed batch turned into one.
NOT_RETURN = re.compile(
    r"(?i)\b(waived|released|cut|signed|claimed off waivers|traded|"
    r"placed on (?:ir|injured reserve)|activated to the roster|"
    r"agreed to terms|added to the (?:roster|practice squad))\b")

USAGE_LANG = re.compile(
    r"(?i)\b(targets?|targeted|carries|carried|touches|red[-\s]zone|"
    r"goal[-\s]line|routes?|snap share|snaps|workload|"
    r"two[-\s]minute drill|third[-\s]down|passing[-\s]down)\b")

PERFORMANCE_LANG = re.compile(
    r"(?i)\b(touchdown|caught|reception|completion|interception|sack|"
    r"drop(?:ped)?|fumble|penalty|holding|beat \w+ for)\b")

# Material information a quotation must carry to be worth interpreting.
# Deliberately narrow. A loose "out" matched "the energy out here has been
# unbelievable" and turned a team-mood quote into fantasy commentary, so the
# availability words must carry their own context.
MATERIAL_QUOTE = re.compile(
    r"(?i)\b(role|reps?|snaps?|starter|starting|depth chart|package|rotation|"
    r"targets?|carries|routes?|workload|"
    r"(?:ruled|sat|held) out|out for (?:the|a|\d)|miss(?:ed|ing)? "
    r"(?:the|a|\d|time|practice|games?|weeks?)|"
    r"back (?:on|at|to) (?:the )?(?:field|practice)|"
    r"return(?:ing|ed)? (?:to|from)|cleared to|limited to|"
    r"(?:feel|feels|feeling|100 ?%) healthy|my (?:health|knee|hamstring|ankle)|"
    r"surgery|rehab|first[-\s]team|second[-\s]team|third[-\s]team|"
    r"red[-\s]zone|goal[-\s]line|touches)\b")


def _names_before(text: str, pos: int) -> list[str]:
    """Personal names appearing before `pos`, nearest last."""
    out = []
    for m in NAME.finditer(text[:pos]):
        tok = m.group(0)
        if tok.split()[0].lower() in NOT_A_NAME:
            continue
        out.append(tok)
    return out


def is_subject_of(text: str, player_name: str, pos: int) -> bool:
    """Is this player the nearest named subject before `pos`?

    Nearest wins, and only nearest. Daniel Jones was named a starter two
    sentences before a clause about Anthony Richardson running the second
    team; anything looser than this hands Jones the second-team reps.
    """
    last = pl.norm(player_name).split()[-1] if player_name else ""
    if not last:
        return False
    names = _names_before(text, pos)
    if not names:
        return False
    nearest = pl.norm(names[-1]).split()
    return last in nearest


def unit_claim(text: str, player_name: str) -> str:
    """Which unit this player is stated to work with, or "".

    Returns nothing rather than guessing. A pass caught from a quarterback
    who was taking second-team reps says nothing about the receiver's unit,
    and the reviewed batch published exactly that inference.
    """
    for unit, pat in UNIT_PATTERNS:
        for m in pat.finditer(text or ""):
            if is_subject_of(text, player_name, m.start()):
                return unit
    return ""


def availability(text: str, player_name: str) -> tuple[str, str]:
    """(signal, direction) for health and participation, or ("", "").

    Direction is explicit because the reviewed batch produced its opposite:
    a waived player and a player missing practice were both described as
    returning.
    """
    t = text or ""
    last = pl.norm(player_name).split()[-1] if player_name else ""
    if not last:
        return "", ""

    for m in NOT_RETURN.finditer(t):
        if is_subject_of(t, player_name, m.start()):
            # A transaction is not a practice return. It may still matter,
            # but it is a different kind of fact and is handled elsewhere.
            return "", ""

    for m in ABSENCE_LANG.finditer(t):
        if is_subject_of(t, player_name, m.start()):
            return "LIMITED_PARTICIPATION", "NEGATIVE"

    for m in RETURN_LANG.finditer(t):
        if is_subject_of(t, player_name, m.start()):
            return "RETURN_TO_PRACTICE", "POSITIVE"
    return "", ""


def quote_subject(text: str, speaker: str) -> str:
    """Who a quotation is about: the speaker, another player, or nobody.

    A quarterback praising a rookie receiver is evidence about the receiver.
    A quarterback describing the team's energy is evidence about nothing we
    can use.
    """
    if not MATERIAL_QUOTE.search(text or ""):
        return ""
    return speaker or ""


def quote_is_about(text: str, player_name: str, speaker: str) -> bool:
    """Is this quotation usable evidence about this particular player?

    Two conditions, both required: the words carry material role, usage or
    availability information, and this player is the one they are about --
    either because he is the speaker talking about himself, or because he is
    named inside the quoted words.
    """
    if not MATERIAL_QUOTE.search(text or ""):
        return False
    last = pl.norm(player_name).split()[-1] if player_name else ""
    if not last:
        return False
    quoted = " ".join(re.findall(r"[\"“]([^\"“”]{8,})[\"”]", text or ""))
    named_inside = last in pl.norm(quoted).split()
    speaks = bool(speaker) and last in pl.norm(speaker).split()
    if named_inside:
        return True
    # The speaker talking about himself: no other player named in the quote.
    if speaks:
        return not any(
            tok.split()[0].lower() not in NOT_A_NAME
            for tok in NAME.findall(quoted or ""))
    return False


def fantasy_mechanism(text: str, player_name: str, klass: str,
                      speaker: str = "") -> dict:
    """The one thing this passage establishes about this player.

    Returns a mechanism and a direction, or NO_FANTASY_IMPACT. This is the
    gate that stops a penalty, a team-cohesion quote or a single highlight
    becoming fantasy commentary.
    """
    t = text or ""
    unit = unit_claim(t, player_name)
    signal, direction = availability(t, player_name)

    if signal:
        return {"mechanism": signal, "direction": direction,
                "unit": "", "detail": "availability"}
    if unit == FIRST_TEAM:
        return {"mechanism": "FIRST_TEAM_REPS", "direction": "POSITIVE",
                "unit": unit, "detail": "depth-chart position"}
    if unit == SECOND_TEAM:
        return {"mechanism": "SECOND_TEAM_REPS", "direction": "NEUTRAL",
                "unit": unit, "detail": "depth-chart position"}
    if unit == THIRD_TEAM:
        return {"mechanism": "THIRD_TEAM_REPS", "direction": "NEGATIVE",
                "unit": unit, "detail": "depth-chart position"}

    if klass == "DIRECT_QUOTATION":
        if not quote_is_about(t, player_name, speaker):
            return {"mechanism": NO_FANTASY_IMPACT, "direction": "NEUTRAL",
                    "unit": "", "detail": "the quotation is not materially "
                                          "about this player"}
        return {"mechanism": "COACH_OR_PLAYER_QUOTATION",
                "direction": "NEUTRAL", "unit": "",
                "detail": "stated role or health"}

    for m in USAGE_LANG.finditer(t):
        if is_subject_of(t, player_name, m.start()):
            word = m.group(0).lower()
            mech = ("RED_ZONE" if "zone" in word or "goal" in word
                    else "TARGETS" if "target" in word
                    else "CARRIES" if "carr" in word or "touch" in word
                    else "ROUTES" if "route" in word or "drill" in word
                    else "SNAP_SHARE")
            return {"mechanism": mech, "direction": "POSITIVE",
                    "unit": "", "detail": "usage"}

    for m in PERFORMANCE_LANG.finditer(t):
        if is_subject_of(t, player_name, m.start()):
            # A play is a play. It is not an opportunity change, and the
            # reviewed batch was right to be criticised for implying it was.
            return {"mechanism": NO_FANTASY_IMPACT, "direction": "NEUTRAL",
                    "unit": "", "detail": "an isolated play, not a change "
                                          "in role or opportunity"}

    return {"mechanism": NO_FANTASY_IMPACT, "direction": "NEUTRAL",
            "unit": "", "detail": "no role, usage or availability claim "
                                  "about this player"}
