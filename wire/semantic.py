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
PROMPT_VERSION = "wire-fantasy-2026-08-21"

INTERPRET, NO_FANTASY_IMPACT, ABSTAIN = "INTERPRET", "NO_FANTASY_IMPACT", "ABSTAIN"
DECISIONS = {INTERPRET, NO_FANTASY_IMPACT, ABSTAIN}

RELATIONSHIPS = {"CLAIM_SUBJECT", "QUOTE_SPEAKER", "ABSENT_PLAYER",
                 "BENEFICIARY", "REPLACEMENT", "COMPETITOR", "TEAMMATE",
                 "OTHER"}

CLASSIFICATIONS = {"FIRSTHAND_OBSERVATION", "DIRECT_QUOTATION",
                   "RELAYED_REPORTING", "ANALYSIS_OR_OPINION", "UNCERTAIN"}

MECHANISMS = {"FIRST_TEAM_REPS", "SECOND_TEAM_REPS", "THIRD_TEAM_REPS",
              "SNAP_SHARE", "ROUTES", "TARGETS", "CARRIES", "RED_ZONE",
              "DEPTH_CHART", "ROLE_EXPANSION", "ROLE_REDUCTION", "INJURY",
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

Return ABSTAIN rather than guessing when the passage is genuinely ambiguous \
about who a claim concerns. Abstaining is a correct answer and is preferred \
to a confident wrong one. NO_FANTASY_IMPACT is for passages that are clear \
but carry no role, usage, availability or opportunity consequence: an \
isolated catch, a penalty, a team-mood quote.

supporting_quote must be copied EXACTLY from the passage, character for \
character. It is checked automatically and a mismatch discards your answer.

fantasy_commentary is two to four sentences naming the actual mechanism -- \
availability, snap share, routes, targets, carries, red-zone work, \
depth-chart position. Never write "worth monitoring", "may affect his \
value", or "a defined role" when no role is defined. State what was seen and \
what it does not establish. Do not mention rankings, ADP or projections."""


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
  evidence access: {meta.get('evidence_access', 'unverified')}
  duplicate of another article: {meta.get('duplicate_of') or 'no'}
  underlying report id: {meta.get('underlying_report_id') or 'none'}

REGISTRY PLAYERS MATCHED IN THIS PASSAGE
{roster}

Return JSON matching the schema. claim_subject_player_id must be one of the \
ids above, or null."""
