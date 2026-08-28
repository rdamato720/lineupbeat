"""One tightly bounded editorial decision per Wire V3 story."""

from __future__ import annotations

import json
import os
import re
import time

from .providers.openai import MODEL, PRICES, OpenAIProviderError, redact
from .v3 import MATERIAL, complete_evidence, is_broad_roster_list, is_preseason_lineup


SCHEMA_VERSION = "wire-v3-story-editorial-v1"
PROMPT_VERSION = "wire-v3-story-editor-2026-08-28a"
DECISIONS = {"PROPOSE", "IGNORE", "ABSTAIN"}
EVENT_TYPES = {"AVAILABILITY", "ROLE", "USAGE", "TRANSACTION", "SUSPENSION",
               "FANTASY_ANALYSIS", "OTHER"}
DIRECTIONS = {"POSITIVE", "NEGATIVE", "NEUTRAL", "UNCLEAR"}
CURRENT_UPDATE = re.compile(
    r"(?i)\b(today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|"
    r"sunday|this week|now|returned|activated|placed on|ruled out|diagnosed|"
    r"suffered|left practice|did not practice|limited|full participant|"
    r"will miss|expected to miss|week 1)\b")
INJURY_AREAS = re.compile(
    r"(?i)\b(ankle|knee|hamstring|calf|groin|quad|hip|back|shoulder|elbow|"
    r"wrist|hand|finger|foot|toe|neck|head|concussion|achilles|acl|mcl)\b")
VAGUE_INJURY = re.compile(r"(?i)\b(injur(?:y|ed)|health|banged up|ailment)\b")
CARD = {
    "type": "object", "additionalProperties": False,
    "required": ["player_id", "event_type", "direction", "what_changed",
                 "lineupbeat_impact", "evidence_basis", "limitations", "confidence"],
    "properties": {
        "player_id": {"type": "string"},
        "event_type": {"type": "string", "enum": sorted(EVENT_TYPES)},
        "direction": {"type": "string", "enum": sorted(DIRECTIONS)},
        "what_changed": {"type": "string"},
        "lineupbeat_impact": {"type": "string"},
        "evidence_basis": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
}
RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["decision", "cards", "reason"],
    "properties": {
        "decision": {"type": "string", "enum": sorted(DECISIONS)},
        "cards": {"type": "array", "minItems": 0, "maxItems": 2, "items": CARD},
        "reason": {"type": "string"},
    },
}

SYSTEM = """You are the story editor for Lineup Beat. Review the supplied
real-world story once, even when it names many players or includes many source
reports. You propose copy for a human editor; you never approve or publish.

Use only supplied evidence and exact authoritative player identities. Choose
PROPOSE only for a material fantasy development: an injury or meaningful
availability update; a trade, release or signing with plausible direct fantasy
impact; a suspension; an official depth-chart or starter decision; a clear
regular-season first-team role/workload change; or material fantasy analysis.

Choose IGNORE for isolated practice plays, generic praise, unexplained
single-practice absences, broad inactive or lineup lists, preseason starter
lists without regular-season meaning, ordinary backup-quarterback activity,
fringe transactions without a plausible role, mock-draft filler, or a
speculative beneficiary inferred only from another player's news.

A newly published article repeating an older injury or absence is not a new
development. Availability proposals require a current, directly supported
change such as a new diagnosis, participation update, activation, return,
game-status designation or timetable. Ignore vague references to a player's
"health" or an unspecified injury. Never add an injury area that does not
appear in the supplied evidence.

Return one card by default. A second card is exceptional: it requires a
different supplied player and a separate, explicit, material development with
its own exact evidence excerpt. Never fan a roundup or roster list into player
cards. If the story's only meaningful development concerns one player, return
one card no matter how many other names appear.

Do not strengthen diagnoses, timetables, starter declarations or suspensions.
what_changed must be one short, plain factual sentence naming the player and
stating only the direct development, such as "Alec Pierce was activated from
the Active/PUP list." Do not add throat-clearing or repeat attribution.
lineupbeat_impact should be one short, useful sentence while preserving
uncertainty. Do not use robotic phrases such as modest positive, scoring-use
outlook, short-term momentum, evidence-backed signal, or role certainty.
evidence_basis must be one exact contiguous excerpt from a supplied Evidence
block. For IGNORE or ABSTAIN, cards must be empty.
"""


def build_prompt(story: dict) -> str:
    return (
        "AUTHORITATIVE PLAYER IDENTITIES:\n" +
        json.dumps(story.get("players") or [], sort_keys=True, ensure_ascii=False) +
        f"\n\nREPORT COUNT: {story['report_count']}\n\n" + complete_evidence(story))


class OpenAIV3DraftProvider:
    name = "openai"

    def __init__(self, model: str = MODEL, transport=None):
        self.model, self._transport = model, transport

    def authenticate(self) -> bool:
        if self._transport is not None:
            return True
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise OpenAIProviderError("OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
            OpenAI(api_key=key).models.retrieve(self.model)
        except Exception as exc:
            raise OpenAIProviderError(redact(f"{type(exc).__name__}: {exc}")) from None
        return True

    def draft(self, story: dict) -> tuple[dict, dict]:
        started = time.time()
        payload, usage = self._call(build_prompt(story))
        tokens_in = int(usage.get("input_tokens", 0))
        tokens_out = int(usage.get("output_tokens", 0))
        price_in, price_out = PRICES.get(self.model, PRICES[MODEL])
        return payload, {
            "provider": self.name, "model": self.model,
            "schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost_usd": tokens_in * price_in + tokens_out * price_out,
            "latency_ms": int((time.time() - started) * 1000),
        }

    def _call(self, prompt: str) -> tuple[dict, dict]:
        if self._transport is not None:
            return self._transport(prompt)
        try:
            from openai import OpenAI
            response = OpenAI(api_key=os.environ.get("OPENAI_API_KEY")).responses.create(
                model=self.model, instructions=SYSTEM, input=prompt, store=False,
                reasoning={"effort": "low"},
                text={"format": {"type": "json_schema", "name": "wire_v3_story",
                                  "strict": True, "schema": RESPONSE_SCHEMA}},
            )
            payload = json.loads(response.output_text)
        except Exception as exc:
            raise OpenAIProviderError(redact(f"{type(exc).__name__}: {exc}")) from None
        usage = getattr(response, "usage", None)
        return payload, {"input_tokens": getattr(usage, "input_tokens", 0),
                         "output_tokens": getattr(usage, "output_tokens", 0)}


def load_relevance(path) -> dict[str, str]:
    payload = json.loads(path.read_text())
    return {row["player_id"]: row.get("relevance_tier", "")
            for row in payload.get("players") or []}


def validate(result: dict, story: dict, relevance: dict[str, str] | None = None) -> list[str]:
    failures = []
    if result.get("decision") not in DECISIONS:
        failures.append("decision is outside the closed vocabulary")
    if not isinstance(result.get("reason"), str):
        failures.append("reason is not a string")
    cards = result.get("cards")
    if not isinstance(cards, list):
        return failures + ["cards is not a list"]
    if result.get("decision") != "PROPOSE":
        if cards:
            failures.append("non-proposal decision contains cards")
        return failures
    if not 1 <= len(cards) <= 2:
        failures.append("PROPOSE must contain one or two cards")
        return failures

    identities = {row["player_id"]: row for row in story.get("players") or []}
    all_evidence = "\n".join(row.get("evidence", "") for row in story.get("reports") or [])
    bases, ids = set(), set()
    for number, card in enumerate(cards, 1):
        prefix = f"card {number}: "
        player_id = card.get("player_id")
        identity = identities.get(player_id)
        if not identity:
            failures.append(prefix + "player_id is not a supplied identity")
            continue
        basis = str(card.get("evidence_basis") or "").strip()
        summary = str(card.get("what_changed") or "").strip()
        impact = str(card.get("lineupbeat_impact") or "").strip()
        if not basis or basis not in all_evidence:
            failures.append(prefix + "evidence_basis is not an exact supplied excerpt")
        if not summary or len(summary) > 320:
            failures.append(prefix + "what_changed must be 1-320 characters")
        surname = identity["player"].split()[-1].lower()
        if surname and surname not in summary.lower():
            failures.append(prefix + "what_changed does not name the player")
        if not impact or len(impact) > 700:
            failures.append(prefix + "lineupbeat_impact must be 1-700 characters")
        if card.get("event_type") not in EVENT_TYPES or card.get("direction") not in DIRECTIONS:
            failures.append(prefix + "closed vocabulary violation")
        if not isinstance(card.get("limitations"), list):
            failures.append(prefix + "limitations is not a list")
        if not isinstance(card.get("confidence"), (int, float)) or not 0 <= card.get("confidence", -1) <= 1:
            failures.append(prefix + "confidence is outside 0-1")
        if is_broad_roster_list(story) and not MATERIAL.search(basis):
            failures.append(prefix + "broad roster/lineup list has no player-specific material event")
        if is_preseason_lineup(basis) or (card.get("event_type") in {"ROLE", "USAGE"} and
                                          is_preseason_lineup(all_evidence)):
            failures.append(prefix + "preseason lineup does not establish a regular-season role")
        if card.get("event_type") == "AVAILABILITY":
            supporting = next((row for row in story.get("reports") or []
                               if basis in row.get("evidence", "")), {})
            is_analysis = ("fantasy" in str(supporting.get("source_name") or "").lower() or
                           "analysis" in str(supporting.get("source_class") or "").lower())
            if is_analysis and not CURRENT_UPDATE.search(basis):
                failures.append(prefix + "analysis article does not establish a new availability update")
            if VAGUE_INJURY.search(basis) and not INJURY_AREAS.search(basis):
                failures.append(prefix + "injury reference is too vague for an availability update")
            for area in INJURY_AREAS.findall(summary):
                if not re.search(rf"(?i)\b{re.escape(area)}\b", all_evidence):
                    failures.append(prefix + f"{area.lower()} injury detail is absent from the evidence")
        tier = (relevance or {}).get(player_id, "")
        if identity.get("position") == "QB" and tier != "ROSTERABLE":
            qb_role = re.search(r"(?i)\b(named (?:the )?starter|will start|first[- ]team|"
                                r"starting job|starter .{0,30}(?:out|injur))\b", basis)
            if not qb_role:
                failures.append(prefix + "reserve quarterback has no material starting-role event")
        copy = f"{summary} {impact}".lower()
        if any(phrase in copy for phrase in ("one fewer", "path to snaps", "path to a roster",
                                             "chance to stick", "roster chances")):
            failures.append(prefix + "speculative secondary-beneficiary impact")
        ids.add(player_id)
        bases.add(basis)
    if len(cards) == 2 and (len(ids) != 2 or len(bases) != 2):
        failures.append("second card lacks a distinct player and evidence basis")
    reports = story.get("reports") or []
    if not reports or not all(str(row.get("url") or "").startswith("https://") for row in reports):
        failures.append("story has a missing or non-HTTPS source")
    return failures
