"""The semantic layer: one schema, three providers, one validator.

The deterministic rules engine reached its limit in review. It read "He took
the majority of the second-team reps" as being about Myles Price when the
pronoun points at J.J. McCarthy, and it filed Parker Washington under TARGETS
from a sentence whose whole point was that Washington was absent and Brian
Thomas Jr. was benefiting. Those are not missing patterns. They are questions
about who a sentence is about, and more regular expressions make that worse
rather than better -- each new rule fires on surface shape and the shape is
what was misleading.

So a model is asked instead. What does not change is that nothing it says is
trusted: every response is checked against the passage it was given, the
player registry, and the same authority rules the rest of the pipeline runs
on. A response that cannot be verified becomes ABSTAIN and a human looks at
it. The model gets to read; it does not get to decide.

Three providers implement one interface. The rules engine stays as the
baseline to measure the others against, because "the model seems better" is
not a measurement.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict

SCHEMA_VERSION = "semantic-v1"
PROMPT_VERSION = "wire-fantasy-2026-08-23c"

INTERPRET, NO_FANTASY_IMPACT, ABSTAIN = "INTERPRET", "NO_FANTASY_IMPACT", "ABSTAIN"
DECISIONS = {INTERPRET, NO_FANTASY_IMPACT, ABSTAIN}

RELATIONSHIPS = {"CLAIM_SUBJECT", "QUOTE_SPEAKER", "ABSENT_PLAYER",
                 "BENEFICIARY", "REPLACEMENT", "COMPETITOR", "TEAMMATE",
                 "OTHER"}

CLASSIFICATIONS = {"FIRSTHAND_OBSERVATION", "DIRECT_QUOTATION",
                   "OFFICIAL_DESIGNATION", "RELAYED_REPORTING",
                   "ANALYSIS_OR_OPINION", "UNCERTAIN"}

MECHANISMS = {"FIRST_TEAM_REPS", "SECOND_TEAM_REPS", "THIRD_TEAM_REPS",
              "SNAP_SHARE", "ROUTES", "TARGETS", "CARRIES", "RED_ZONE",
              "DEPTH_CHART", "ROLE_EXPANSION", "ROLE_REDUCTION", "INJURY",
              # An unexplained missed practice is not the same claim as a
              # limited one: "did not participate, no reason given" states
              # less than "limited", and collapsing them would have the wire
              # implying a severity nobody reported.
              "ABSENT_FROM_PRACTICE",
              "LIMITED_PARTICIPATION", "RETURN_TO_PRACTICE", "TRANSACTION",
              "PERFORMANCE", "OTHER", "NO_FANTASY_IMPACT"}

DIRECTIONS = {"POSITIVE", "NEGATIVE", "NEUTRAL", "UNCLEAR"}
STRENGTHS = {"LOW", "MEDIUM", "HIGH"}
HORIZONS = {"IMMEDIATE", "SHORT_TERM", "SEASON_LONG", "UNKNOWN"}
ACTIONS = {"NONE", "REVIEW", "UPDATE_RECOMMENDED"}

# The JSON Schema handed to both model providers. Strict: no extra keys, and
# every enum closed, so a provider cannot invent a mechanism.
RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "claim_subject_player_id",
                 "claim_subject_player_name", "mentioned_players",
                 "quote_speaker", "pronoun_antecedents", "supporting_quote",
                 "evidence_classification", "fantasy_mechanism", "direction",
                 "impact_strength", "impact_horizon", "projection_action",
                 "fantasy_commentary", "why_it_matters", "limitations",
                 "confidence", "abstention_reason"],
    "properties": {
        "decision": {"type": "string", "enum": sorted(DECISIONS)},
        "claim_subject_player_id": {"type": ["string", "null"]},
        "claim_subject_player_name": {"type": ["string", "null"]},
        "mentioned_players": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["player_id", "player_name", "relationship"],
                "properties": {
                    "player_id": {"type": ["string", "null"]},
                    "player_name": {"type": "string"},
                    "relationship": {"type": "string",
                                     "enum": sorted(RELATIONSHIPS)}}}},
        "quote_speaker": {"type": ["string", "null"]},
        "pronoun_antecedents": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["pronoun", "resolved_to", "supporting_text"],
                "properties": {"pronoun": {"type": "string"},
                               "resolved_to": {"type": "string"},
                               "supporting_text": {"type": "string"}}}},
        "supporting_quote": {"type": "string"},
        "evidence_classification": {"type": "string",
                                    "enum": sorted(CLASSIFICATIONS)},
        "fantasy_mechanism": {"type": "string", "enum": sorted(MECHANISMS)},
        "direction": {"type": "string", "enum": sorted(DIRECTIONS)},
        "impact_strength": {"type": "string", "enum": sorted(STRENGTHS)},
        "impact_horizon": {"type": "string", "enum": sorted(HORIZONS)},
        "projection_action": {"type": "string", "enum": sorted(ACTIONS)},
        "fantasy_commentary": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "abstention_reason": {"type": ["string", "null"]},
    },
}


@dataclass
class SemanticAssessment:
    decision: str = ABSTAIN
    claim_subject_player_id: str | None = None
    claim_subject_player_name: str | None = None
    mentioned_players: list = field(default_factory=list)
    quote_speaker: str | None = None
    pronoun_antecedents: list = field(default_factory=list)
    supporting_quote: str = ""
    evidence_classification: str = "UNCERTAIN"
    fantasy_mechanism: str = "NO_FANTASY_IMPACT"
    direction: str = "NEUTRAL"
    impact_strength: str = "LOW"
    impact_horizon: str = "UNKNOWN"
    projection_action: str = "NONE"
    fantasy_commentary: str = ""
    why_it_matters: str = ""
    limitations: list = field(default_factory=list)
    confidence: float = 0.0
    abstention_reason: str | None = None
    # Provenance, filled by the caller rather than the model.
    provider: str = ""
    model: str = ""
    schema_version: str = SCHEMA_VERSION
    prompt_version: str = PROMPT_VERSION
    input_hash: str = ""
    output_hash: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    validation_failures: list = field(default_factory=list)
    attempts: list = field(default_factory=list)
    retry_attempted: bool = False
    retry_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def input_hash(segment: str, players: list) -> str:
    key = segment + "|" + "|".join(sorted(p.get("player_id", "")
                                          for p in players))
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def output_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:20]


class FantasySemanticProvider:
    """One passage in, one assessment out. Implementations must not fetch."""

    name = "base"
    model = ""

    def evaluate(self, evidence_segment: str, article_metadata: dict,
                 matched_players: list) -> SemanticAssessment:
        raise NotImplementedError


# ------------------------------------------------------------- the prompt
SYSTEM = """You read one passage of NFL beat reporting and say what it \
establishes about one specific player, for a fantasy-football wire.

You are given the passage, its source metadata, and the players our registry \
matched in it. Judge only what the passage states. Do not use anything you \
know about the NFL, these players, or later events. If the passage does not \
say it, it is not established.

Source metadata is context for provenance and authority only. Article titles, \
publication names, dates and other metadata are not evidence of football \
facts. Do not introduce a game type, practice setting, injury status, role, \
timeline or any other fact into fantasy_commentary, why_it_matters, \
limitations or abstention_reason unless the PASSAGE states it. A missing \
metadata field is not an "unverified" evidentiary status and is never a reason \
to abstain.

The distinctions that matter most, because they are where this goes wrong:

- The claim subject is the player a statement is ABOUT, which is often not \
the player nearest the words. "With no Parker Washington on the field, the \
No. 1 target was Brian Thomas Jr." is an absence claim about Washington and \
a target claim about Thomas. Washington did not draw targets.
- A pronoun belongs to its antecedent. "McCarthy had a completion to Price. \
He took most of the second-team reps" gives the reps to McCarthy, not Price.
- A quote speaker is not automatically the subject. If a quarterback \
describes a receiver's role, the receiver is the claim subject and the \
quarterback gets nothing unless the words are materially about him.
- A player who is absent cannot receive the work that went to whoever \
replaced him. Record the replacement or beneficiary separately.
- Reporting relayed from another outlet is RELAYED_REPORTING, never \
firsthand and never a direct quotation, however it is worded.
- A club or league designation about its own transaction, roster status or \
practice participation is OFFICIAL_DESIGNATION. It is authoritative for that \
designation but is not a reporter's firsthand observation.
- ANALYSIS_OR_OPINION, RELAYED_REPORTING and UNCERTAIN may not support an \
INTERPRET decision. Return NO_FANTASY_IMPACT when the passage is clear but \
non-actionable, or ABSTAIN when its evidentiary status is genuinely unclear.

Return ABSTAIN rather than guessing when the passage is genuinely ambiguous \
about who a claim concerns. Abstaining is a correct answer and is preferred \
to a confident wrong one. NO_FANTASY_IMPACT is for passages that are clear \
but carry no role, usage, availability or opportunity consequence: an \
isolated catch, a penalty, a team-mood quote. PERFORMANCE and OTHER are not \
publishable fantasy mechanisms. If the passage establishes only performance, \
return NO_FANTASY_IMPACT. If performance also establishes a concrete change \
in routes, targets, carries, unit, competition or role, use that concrete \
mechanism instead.

supporting_quote must be copied EXACTLY from the passage, character for \
character. It is checked automatically and a mismatch discards your answer.

AVAILABILITY has two different mechanisms and they are not interchangeable. LIMITED_PARTICIPATION is for a player not practising, held out, limited, or absent, when the passage does not say why. INJURY is only for a stated injury: a named injury, a diagnosis, a re-aggravation, a player leaving the field hurt. "Those not participating included Sam LaPorta" is LIMITED_PARTICIPATION -- calling it INJURY asserts a cause the passage never gives. Where the cause is unknown, say so in limitations rather than choosing a mechanism that implies it.

STRENGTH is graded against corroboration, not drama. One practice observation from one reporter is LOW, however consequential it sounds -- a starting quarterback taking second-team reps is still LOW on one report. MEDIUM needs the same usage across several practices, a clear coach or player statement about role or health, or a second independent reporter. HIGH is reserved for an official act the club or league confirms -- injured reserve, a transaction, a formally announced starter, a season-ending injury -- or a role change corroborated by two independent reporters. If you are looking at one article by one reporter and no official act, the answer is LOW or MEDIUM, never HIGH.

PROJECTION ACTION follows strength. LOW evidence may use NONE or REVIEW, \
never UPDATE_RECOMMENDED. Do not turn a supported football interpretation \
into ABSTAIN merely because the evidence is LOW; lower the action instead.

HORIZON follows the mechanism: participation and injury news is IMMEDIATE, camp usage and competition is SHORT_TERM, a confirmed starting role or a season-ending injury is SEASON_LONG, and UNKNOWN when the passage gives no timeframe.

Interpret only quarterbacks, running backs, wide receivers and tight ends. For any other position, including offensive linemen, defensive players and kickers, return NO_FANTASY_IMPACT.

fantasy_commentary is two to four sentences naming the actual mechanism -- \
availability, snap share, routes, targets, carries, red-zone work, \
depth-chart position. Never write "worth monitoring", "may affect his \
value", or "a defined role" when no role is defined. State what was seen and \
what it does not establish. Do not mention rankings, ADP or projections."""


QUOTE_RETRY_SYSTEM = """You previously assessed a passage and named a supporting quotation that does not appear in it verbatim.

Your only task now is to copy ONE exact contiguous substring of the passage that supports the assessment you already made. Copy it character for character, including punctuation and capitalisation. Do not paraphrase, trim, join two separate parts, correct anything, or add ellipses. Do not \
complete or tidy punctuation: if the passage ends without a closing \
quotation mark, neither does your answer.

Do not reconsider the football interpretation. The claim subject, mechanism, direction, strength and horizon are already decided and are not yours to revisit here. Return only the quotation."""

QUOTE_RETRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["supporting_quote"],
    "properties": {"supporting_quote": {"type": "string"}},
}


def build_quote_retry_prompt(segment: str, prior) -> str:
    return f"""PASSAGE
\"\"\"{segment}\"\"\"

THE ASSESSMENT YOU ALREADY MADE, WHICH IS NOT UNDER REVIEW
  claim subject : {prior.claim_subject_player_name}
  mechanism     : {prior.fantasy_mechanism}
  direction     : {prior.direction}

THE QUOTATION YOU RETURNED, WHICH IS NOT IN THE PASSAGE
  {prior.supporting_quote!r}

Return one exact contiguous substring of the passage above that supports \
that assessment."""


def build_prompt(segment: str, meta: dict, players: list) -> str:
    roster = "\n".join(
        f"  - {p['player_name']} (id {p['player_id']}, {p['team']} "
        f"{p['position']})" for p in players) or "  (none matched)"
    return f"""PASSAGE
\"\"\"{segment}\"\"\"

SOURCE
  article: {meta.get('article_title', '')}
  published: {meta.get('published_at', '')}
  team beat: {meta.get('team', '')}
  publication: {meta.get('source_name', '')}
  author: {meta.get('author', '')}
  ownership: {meta.get('source_ownership', 'INDEPENDENT')}
  evidence access: {meta.get('evidence_access') or 'not supplied'}
  duplicate of another article: {meta.get('duplicate_of') or 'no'}
  underlying report id: {meta.get('underlying_report_id') or 'none'}

REGISTRY PLAYERS MATCHED IN THIS PASSAGE
{roster}

Return JSON matching the schema. claim_subject_player_id must be one of the \
ids above, or null."""
