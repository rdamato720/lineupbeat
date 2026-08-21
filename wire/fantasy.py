"""Lineup Beat's reading of a reporter's evidence. Kept separate from it.

Two claims live here, by two different authors, and they can be wrong
independently. The reporter says what happened at practice; we say what it
might mean for a fantasy roster. A reviewer approves them separately because
the observation can be sound and our reading of it wrong, and because our
commentary must never be mistakable for something a reporter said.

WHAT THIS LAYER MAY NOT DO

It may not repeat the publisher's fantasy advice. A source contributes its
real-world reporting and nothing else: "took most of the first-team red-zone
reps" is evidence, "is a sleeper worth a round eight pick" is the source's
conclusion and is not ours to carry. The relevance gate refuses the second
before it can become evidence at all.

It may not invent. Every player, team, number and timeline in a generated
sentence has to be present in the approved evidence or in the player
registry, and validate() rejects the record if it is not. That is why
generation is templated rather than free text: a template can only say what
it was handed.

It may not decide anything on repetition. A syndicated story republished on
four team pages is one report, and independent_source_count counts distinct
articles by distinct reporters -- never copies.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from . import players as pl

POSITIVE, NEGATIVE, NEUTRAL, UNCLEAR = "POSITIVE", "NEGATIVE", "NEUTRAL", "UNCLEAR"
LOW, MEDIUM, HIGH = "LOW", "MEDIUM", "HIGH"
IMMEDIATE, SHORT_TERM, SEASON_LONG, UNKNOWN = (
    "IMMEDIATE", "SHORT_TERM", "SEASON_LONG", "UNKNOWN")
NONE, REVIEW, UPDATE_RECOMMENDED = "NONE", "REVIEW", "UPDATE_RECOMMENDED"
PENDING, APPROVED, REJECTED, SUPERSEDED, INVALIDATED = (
    "PENDING", "APPROVED", "REJECTED", "SUPERSEDED", "INVALIDATED")

IMPACTS = {POSITIVE, NEGATIVE, NEUTRAL, UNCLEAR}
STRENGTHS = {LOW, MEDIUM, HIGH}
HORIZONS = {IMMEDIATE, SHORT_TERM, SEASON_LONG, UNKNOWN}
ACTIONS = {NONE, REVIEW, UPDATE_RECOMMENDED}
STATUSES = {PENDING, APPROVED, REJECTED, SUPERSEDED, INVALIDATED}

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE"}

GENERATOR = "rules-v1"

# ------------------------------------------------------------- role signals
# Ordered: the first match wins, so the more specific and more consequential
# signals are checked before the general ones.
ROLE_SIGNALS: list = [
    ("INJURY", re.compile(
        r"(?i)\b(tore|torn|acl|mcl|achilles|fracture[ds]?|broken|"
        r"out for the (season|year)|placed on (ir|injured reserve)|"
        r"will miss|expected to miss|underwent surgery|carted off)\b")),
    ("RETURN_TO_PRACTICE", re.compile(
        r"(?i)\b(back at practice|returned to practice|activated off|"
        r"cleared to (practice|return)|off the (pup|nfi))\b")),
    ("LIMITED_PARTICIPATION", re.compile(
        r"(?i)\b(did not (practice|participate)|was limited|"
        r"limited participant|held out|non[- ]participant|missed practice)\b")),
    ("FIRST_TEAM_REPS", re.compile(
        r"(?i)\b((?:first|1st|no\.? ?1)[- ]team|with the (?:ones|starters)|"
        r"first[- ]team (?:reps|snaps|offense|defense))\b")),
    ("SECOND_TEAM_REPS", re.compile(
        r"(?i)\b((?:second|2nd|no\.? ?2)[- ]team|with the twos|"
        r"second[- ]team (?:reps|snaps|offense|defense))\b")),
    ("RED_ZONE", re.compile(r"(?i)\b(red[- ]zone|goal[- ]line)\b")),
    ("DEPTH_CHART", re.compile(
        r"(?i)\b(depth chart|starting (job|role|spot)|named (the )?starter|"
        r"moved (up|down) the|first on the depth)\b")),
    ("TARGETS", re.compile(r"(?i)\b(targets?|targeted)\b")),
    ("CARRIES", re.compile(r"(?i)\b(carries|carried the ball|totes)\b")),
    ("ROUTES", re.compile(
        r"(?i)\b(route (?:tree|running)|ran routes|running routes|"
        r"route[- ]runner|in the slot)\b")),
    ("SNAP_SHARE", re.compile(r"(?i)\b(snap (share|count)|snaps)\b")),
    ("PASS_BLOCKING", re.compile(r"(?i)\b(pass (protection|blocking))\b")),
    ("ROLE_EXPANSION", re.compile(
        r"(?i)\b(bigger role|expanded role|more (work|reps|snaps)|"
        r"taking on more)\b")),
    ("ROLE_REDUCTION", re.compile(
        r"(?i)\b(smaller role|fewer (reps|snaps|carries)|lost (reps|ground)|"
        r"dropped (down|behind))\b")),
    ("COACH_QUOTATION", re.compile(
        r"(?i)\b(head coach|coordinator|coach [A-Z])\b")),
    ("PLAYER_QUOTATION", re.compile(r"(?i)\bsaid\b")),
    ("PERFORMANCE", re.compile(
        r"(?i)\b(touchdown|caught|completion|interception|sack|drop|"
        r"beat (his|the) (man|coverage))\b")),
]

POSITIVE_MARKERS = re.compile(
    r"(?i)\b((?:first|1st|no\.? ?1)[- ]team|with the (?:ones|starters)|"
    r"bigger role|expanded role|more (?:work|reps|snaps)|back at practice|"
    r"returned to practice|named (?:the )?starter|touchdown|"
    r"moved up|impressed|stood out)\b")
NEGATIVE_MARKERS = re.compile(
    r"(?i)\b(did not (?:practice|participate)|was limited|held out|"
    r"out for the (?:season|year)|will miss|expected to miss|"
    r"placed on (?:ir|injured reserve)|carted off|smaller role|"
    r"fewer (?:reps|snaps|carries)|lost (?:reps|ground)|dropped (?:down|behind)|"
    r"(?:second|2nd)[- ]team|with the twos)\b")

# A confirmed, material event. Only these may reach HIGH, and only with
# corroboration -- one reporter's practice note never does.
MAJOR_EVENT = re.compile(
    r"(?i)\b(tore|torn (?:acl|mcl|achilles)|achilles|out for the (?:season|year)|"
    r"placed on (?:ir|injured reserve)|will miss (?:multiple|several|the)|"
    r"expected to miss (?:multiple|several)|underwent surgery|"
    r"named (?:the )?(?:starting|starter)|won the (?:starting )?job|"
    r"ruled out for)\b")

# Language that means the source is giving fantasy advice. Never evidence.
SOURCE_FANTASY_ADVICE = re.compile(
    r"(?i)\b(sleeper|breakout candidate|bust|draft him|worth a (?:pick|round)|"
    r"start him|sit him|waiver (?:wire|add)|must[- ]draft|league winner|"
    r"fantasy (?:points|value|relevance|rankings?|projection|advice|"
    r"football|manager|owner|start|sit)|adp|dfs|"
    r"(?:round|rd\.?) \d+ (?:pick|value)|flex play)\b")


@dataclass
class Impact:
    player_id: str
    player_name: str
    team: str
    position: str
    fantasy_impact: str = UNCLEAR
    impact_strength: str = LOW
    impact_horizon: str = UNKNOWN
    role_signal: str = "OTHER"
    lineupbeat_commentary: str = ""
    reasoning: str = ""
    projection_action: str = NONE
    evidence_candidate_ids: list = field(default_factory=list)
    evidence_group_ids: list = field(default_factory=list)
    source_article_ids: list = field(default_factory=list)
    source_count: int = 0
    independent_source_count: int = 0
    generator: str = GENERATOR
    prompt_version: str = ""
    registry_version: str = ""
    review_status: str = PENDING

    @property
    def fantasy_impact_id(self) -> str:
        """Stable for a player and a set of evidence.

        Derived from the evidence it rests on, so regenerating unchanged
        evidence updates one record rather than creating a second.
        """
        key = f"{self.player_id}|" + "|".join(sorted(self.evidence_candidate_ids))
        return hashlib.sha256(key.encode()).hexdigest()[:20]

    def to_record(self) -> dict:
        d = dict(self.__dict__)
        d["fantasy_impact_id"] = self.fantasy_impact_id
        return d


def role_signal(text: str) -> str:
    for name, pat in ROLE_SIGNALS:
        if pat.search(text or ""):
            return name
    return "OTHER"


def direction(text: str) -> str:
    pos = bool(POSITIVE_MARKERS.search(text or ""))
    neg = bool(NEGATIVE_MARKERS.search(text or ""))
    if pos and neg:
        return UNCLEAR
    if pos:
        return POSITIVE
    if neg:
        return NEGATIVE
    return NEUTRAL


def horizon(signal: str, text: str) -> str:
    if signal in ("LIMITED_PARTICIPATION", "RETURN_TO_PRACTICE"):
        return IMMEDIATE
    if signal == "INJURY":
        return (SEASON_LONG
                if re.search(r"(?i)out for the (season|year)|torn|achilles|"
                             r"placed on (ir|injured reserve)", text or "")
                else IMMEDIATE)
    if signal == "DEPTH_CHART":
        return SEASON_LONG if re.search(
            r"(?i)named (the )?starter|won the (starting )?job", text or "") else SHORT_TERM
    if signal in ("FIRST_TEAM_REPS", "SECOND_TEAM_REPS", "ROLE_EXPANSION",
                  "ROLE_REDUCTION", "RED_ZONE", "SNAP_SHARE", "TARGETS",
                  "CARRIES", "ROUTES"):
        return SHORT_TERM
    return UNKNOWN


def independent_sources(rows: list) -> int:
    """Distinct articles by distinct reporters.

    A syndicated story on four team pages is one report. Counting the copies
    would let republication masquerade as corroboration, which is the single
    easiest way for this layer to sound confident about nothing.
    """
    seen = set()
    for r in rows:
        seen.add((r.get("source_url", ""),
                  (r.get("source_author_or_channel") or "").strip().lower()))
    by_reporter = {a for _, a in seen if a}
    return max(len(by_reporter), 1) if seen else 0


def strength(rows: list, signal: str, independent: int) -> str:
    """LOW unless the evidence earns more.

    HIGH needs a confirmed material event AND corroboration from a second
    reporter. One article, one reporter, or the same story repeated, cannot
    reach it however dramatic the wording.
    """
    firsthand = [r for r in rows if r["evidence_class"] == "FIRSTHAND_OBSERVATION"]
    quotes = [r for r in rows if r["evidence_class"] == "DIRECT_QUOTATION"]
    text = " ".join(r["evidence_text"] for r in rows)
    major = bool(MAJOR_EVENT.search(text))
    # Repetition only counts across articles. Two spans of one reporter's
    # account of one practice are one observation described twice, and
    # treating them as corroboration is how a single report acquires
    # confidence it never earned.
    articles = {r["source_url"] for r in rows}
    fh_articles = {r["source_url"] for r in firsthand}

    if major and independent >= 2 and (firsthand or quotes):
        return HIGH
    if len(fh_articles) >= 2:
        return MEDIUM
    if firsthand and quotes and len(articles) >= 2:
        return MEDIUM
    if quotes and signal in ("INJURY", "DEPTH_CHART", "RETURN_TO_PRACTICE"):
        return MEDIUM
    if independent >= 2 and firsthand:
        return MEDIUM
    return LOW


def projection_action(rows: list, strength_val: str, impact: str,
                      independent: int) -> str:
    """Never a projection change. At most a task for a person.

    UPDATE_RECOMMENDED is reserved for material developments that are either
    officially confirmed, directly attributed, or seen firsthand more than
    once -- and never inflated by duplicates.
    """
    if impact in (NEUTRAL, UNCLEAR):
        return NONE
    if strength_val == HIGH and independent >= 2:
        return UPDATE_RECOMMENDED
    if strength_val == MEDIUM:
        return REVIEW
    return NONE


# ------------------------------------------------------------- commentary
# Templates, not prose. A template can only say what it is handed, which is
# what makes the no-invention rule checkable rather than hopeful.
TEMPLATES = {
    "FIRST_TEAM_REPS": ("{name} taking first-team reps could point to a larger "
                        "role, though camp work is not a confirmed regular-season "
                        "job. Worth monitoring."),
    "SECOND_TEAM_REPS": ("{name} working with the second team suggests he is "
                         "behind on the depth chart for now. One practice is not "
                         "a settled role."),
    "RED_ZONE": ("Scoring-area work is the part of {name}'s usage that matters "
                 "most, and this suggests he may be involved there. A single "
                 "practice is not enough to act on."),
    "INJURY": ("An injury of this kind may reduce {name}'s availability, which "
               "would matter more than any usage signal. Flag for review once "
               "the timeline is confirmed."),
    "RETURN_TO_PRACTICE": ("{name} returning to practice is a step toward "
                           "availability, though it does not by itself confirm "
                           "his role on return."),
    "LIMITED_PARTICIPATION": ("Limited or missed participation could affect "
                              "{name}'s availability. Worth monitoring through "
                              "the week."),
    "DEPTH_CHART": ("A depth-chart move could change how much opportunity {name} "
                    "sees. Confirmation from a second report would strengthen it."),
    "TARGETS": ("Target volume is the clearest driver of {name}'s value, and "
                "this suggests involvement. One report is a small sample."),
    "CARRIES": ("Backfield workload drives {name}'s value, and this points to "
                "his share of it. Worth monitoring."),
    "SNAP_SHARE": ("Snap share sets the ceiling on {name}'s opportunity, and "
                   "this suggests where it may sit."),
    "ROLE_EXPANSION": ("A larger role could mean more opportunity for {name}, "
                       "though camp usage does not always survive into the season."),
    "ROLE_REDUCTION": ("A reduced role could mean less opportunity for {name}. "
                       "Worth watching whether it holds."),
    "COACH_QUOTATION": ("A coach describing {name}'s role carries more weight "
                        "than a single practice observation, but it is still a "
                        "statement about intent rather than usage."),
    "PLAYER_QUOTATION": ("This is {name}'s own account of his role, which is "
                         "worth noting and is not the same as observed usage."),
    "PERFORMANCE": ("A good practice showing from {name} is encouraging and is "
                    "not, on its own, a change in role."),
    "OTHER": ("This is a development involving {name} worth monitoring; it does "
              "not yet suggest a change in his fantasy outlook."),
}

HEDGE_BY_STRENGTH = {
    LOW: "Single report, so treat it as a signal to monitor rather than to act on.",
    MEDIUM: "More than one report points the same way, which makes it worth a closer look.",
    HIGH: "This is material and independently corroborated; both players' outlooks should be reviewed.",
}


def commentary(name: str, signal: str, strength_val: str) -> str:
    base = TEMPLATES.get(signal, TEMPLATES["OTHER"]).format(name=name)
    return f"{base} {HEDGE_BY_STRENGTH[strength_val]}"


# ------------------------------------------------------------- validation
NUMBER = re.compile(r"\b\d+\b")


def validate(imp: Impact, rows: list, registry) -> list[str]:
    """Refuse commentary that says more than the evidence does.

    Names, numbers and timelines in the generated sentence must appear in the
    approved evidence or in the player registry. A template makes this cheap
    to enforce and the enforcement is the point: it is what stops a generated
    sentence inventing an injury.
    """
    bad = []
    text = " ".join(r["evidence_text"] for r in rows)
    hay = pl.norm(text)

    if not imp.evidence_candidate_ids:
        bad.append("no supporting evidence candidate ids")
    if imp.position not in FANTASY_POSITIONS:
        bad.append(f"{imp.position} may not receive individual commentary")
    if not imp.player_id:
        bad.append("no exact player id")
    elif registry is not None and imp.player_id not in registry.by_id:
        bad.append(f"player id {imp.player_id} is not in the registry")
    if not imp.team:
        bad.append("no team")

    if pl.norm(imp.player_name).split()[-1] not in hay.split():
        bad.append("the player is not named in the supporting evidence")

    if SOURCE_FANTASY_ADVICE.search(imp.lineupbeat_commentary):
        bad.append("commentary uses fantasy-advice language")

    # Every number we print has to come from the evidence.
    known = set(NUMBER.findall(text))
    for n in NUMBER.findall(imp.lineupbeat_commentary):
        if n not in known:
            bad.append(f"commentary states a number ({n}) absent from the evidence")

    # Injury and timeline language must be grounded.
    if re.search(r"(?i)\b(weeks?|months?|surgery|torn|acl|achilles)\b",
                 imp.lineupbeat_commentary):
        if not re.search(r"(?i)\b(week|month|surgery|torn|acl|achilles|injur)",
                         text):
            bad.append("commentary states an injury or timeline not in evidence")

    if imp.fantasy_impact not in IMPACTS:
        bad.append(f"bad fantasy_impact {imp.fantasy_impact!r}")
    if imp.impact_strength not in STRENGTHS:
        bad.append(f"bad impact_strength {imp.impact_strength!r}")
    if imp.impact_horizon not in HORIZONS:
        bad.append(f"bad impact_horizon {imp.impact_horizon!r}")
    if imp.projection_action not in ACTIONS:
        bad.append(f"bad projection_action {imp.projection_action!r}")
    if imp.review_status != PENDING:
        bad.append("generated commentary must start PENDING")

    if imp.impact_strength == HIGH:
        if imp.independent_source_count < 2:
            bad.append("HIGH requires two independent reporters")
        if not MAJOR_EVENT.search(text):
            bad.append("HIGH requires a confirmed major event in the evidence")
    if imp.projection_action == UPDATE_RECOMMENDED and imp.independent_source_count < 2:
        bad.append("UPDATE_RECOMMENDED requires corroboration")
    return bad


def build(rows: list, registry, registry_version: str = "") -> Impact | None:
    """One player's approved evidence becomes at most one impact record."""
    if not rows:
        return None
    first = rows[0]
    player = registry.by_id.get(first["player_id"]) if registry else None
    if player is None or player.position not in FANTASY_POSITIONS:
        return None

    text = " ".join(r["evidence_text"] for r in rows)
    sig = role_signal(text)
    ind = independent_sources(rows)
    st = strength(rows, sig, ind)
    imp_dir = direction(text)
    act = projection_action(rows, st, imp_dir, ind)

    imp = Impact(
        player_id=player.player_id, player_name=player.full_name,
        team=player.team, position=player.position,
        fantasy_impact=imp_dir, impact_strength=st,
        impact_horizon=horizon(sig, text), role_signal=sig,
        lineupbeat_commentary=commentary(player.full_name, sig, st),
        reasoning=(f"{len(rows)} approved evidence span(s) from {ind} "
                   f"independent reporter(s); role signal {sig}; direction "
                   f"{imp_dir} from the observation language in the evidence"),
        projection_action=act,
        evidence_candidate_ids=sorted(r["candidate_id"] for r in rows),
        evidence_group_ids=sorted({r["evidence_group_id"] for r in rows}),
        source_article_ids=sorted({r["source_url"] for r in rows}),
        source_count=len(rows), independent_source_count=ind,
        registry_version=registry_version, review_status=PENDING)
    return imp
