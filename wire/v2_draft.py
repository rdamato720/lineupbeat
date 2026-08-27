"""One ChatGPT editorial decision per Wire V2 event."""

from __future__ import annotations

import json
import os
import time

from .providers.openai import MODEL, PRICES, OpenAIProviderError, redact
from .v2 import complete_evidence


SCHEMA_VERSION = "wire-v2-editorial-v1"
PROMPT_VERSION = "wire-v2-event-editor-2026-08-27a"
DECISIONS = {"PROPOSE", "IGNORE", "ABSTAIN"}
EVENT_TYPES = {
    "AVAILABILITY", "ROLE", "USAGE", "TRANSACTION", "SUSPENSION",
    "FANTASY_ANALYSIS", "OTHER",
}
DIRECTIONS = {"POSITIVE", "NEGATIVE", "NEUTRAL", "UNCLEAR"}

RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["decision", "event_type", "what_changed", "lineupbeat_impact",
                 "direction", "evidence_basis", "limitations", "confidence",
                 "reason"],
    "properties": {
        "decision": {"type": "string", "enum": sorted(DECISIONS)},
        "event_type": {"type": "string", "enum": sorted(EVENT_TYPES)},
        "what_changed": {"type": "string"},
        "lineupbeat_impact": {"type": "string"},
        "direction": {"type": "string", "enum": sorted(DIRECTIONS)},
        "evidence_basis": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
}

SYSTEM = """You are the event editor for Lineup Beat, a fantasy-football news
service. Review one player event assembled from one or more source reports.
You propose copy for a human editor; you never approve or publish anything.

Use only the supplied sources. Do not import facts, ranks, statistics, depth
charts, injuries or timetables from memory. Multiple sources may describe the
same event; treat them as corroboration, not separate developments.

Choose PROPOSE when the current event gives a fantasy manager something useful
to know or monitor: availability, injury, return, transaction, suspension,
starter decision, meaningful role/workload movement, repeated usage, or a
specific fantasy/ADP argument. Opinion and speculation are welcome when clearly
attributed and labeled FANTASY_ANALYSIS. Choose IGNORE for mere name mentions,
mock-draft filler, generic praise, isolated highlights, ordinary backup work,
promotion, sponsor copy, or an article that contains no new development for
this player. Choose ABSTAIN only when identity or meaning is genuinely unclear.

For PROPOSE, what_changed should be one or two plain-English sentences naming
the player and accurately summarizing the event. lineupbeat_impact should sound
like useful fantasy analysis: direct, conversational and specific about what
the news changes or what managers should watch next. It may draw a cautious
inference, but must distinguish inference from reporting and preserve important
uncertainty. Avoid robotic phrases such as scoring-use outlook, short-term
momentum, modest positive, evidence-backed signal, or role certainty.

evidence_basis must be one exact, contiguous excerpt copied from a supplied
Evidence block. Never rewrite it. For IGNORE or ABSTAIN it may be empty.
"""


def build_prompt(event: dict) -> str:
    identity = {key: event[key] for key in ("player", "player_id", "team", "position")}
    return (
        "PLAYER IDENTITY (authoritative):\n" +
        json.dumps(identity, sort_keys=True, ensure_ascii=False) +
        f"\n\nSOURCE COUNT: {event['source_count']}\n\n" +
        complete_evidence(event)
    )


class OpenAIV2DraftProvider:
    name = "openai"

    def __init__(self, model: str = MODEL, transport=None):
        self.model = model
        self._transport = transport

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

    def draft(self, event: dict) -> tuple[dict, dict]:
        started = time.time()
        payload, usage = self._call(build_prompt(event))
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
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise OpenAIProviderError("OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
            response = OpenAI(api_key=key).responses.create(
                model=self.model, instructions=SYSTEM, input=prompt,
                store=False, reasoning={"effort": "low"},
                text={"format": {"type": "json_schema", "name": "wire_v2_event",
                                  "strict": True, "schema": RESPONSE_SCHEMA}},
            )
            payload = json.loads(response.output_text)
        except Exception as exc:
            raise OpenAIProviderError(redact(f"{type(exc).__name__}: {exc}")) from None
        usage = getattr(response, "usage", None)
        return payload, {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
        }


def validate(result: dict, event: dict) -> list[str]:
    """Minimal safeguards: schema, identity, provenance and exact grounding."""
    failures = []
    for field in ("decision", "event_type", "what_changed", "lineupbeat_impact",
                  "direction", "evidence_basis", "reason"):
        if not isinstance(result.get(field), str):
            failures.append(f"{field} is not a string")
    if result.get("decision") not in DECISIONS:
        failures.append("decision is outside the closed vocabulary")
    if result.get("event_type") not in EVENT_TYPES:
        failures.append("event_type is outside the closed vocabulary")
    if result.get("direction") not in DIRECTIONS:
        failures.append("direction is outside the closed vocabulary")
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        failures.append("confidence is outside 0-1")
    if not isinstance(result.get("limitations"), list):
        failures.append("limitations is not a list")
    if result.get("decision") != "PROPOSE":
        return failures

    summary = str(result.get("what_changed") or "").strip()
    impact = str(result.get("lineupbeat_impact") or "").strip()
    basis = str(result.get("evidence_basis") or "").strip()
    all_evidence = "\n".join(str(row.get("evidence") or "")
                             for row in event.get("sources") or [])
    last_name = str(event.get("player") or "").split()[-1].lower()
    if not summary or len(summary) > 320:
        failures.append("what_changed must be 1-320 characters")
    if last_name and last_name not in summary.lower():
        failures.append("what_changed does not name the player")
    if not impact or len(impact) > 700:
        failures.append("lineupbeat_impact must be 1-700 characters")
    if not basis or basis not in all_evidence:
        failures.append("evidence_basis is not an exact supplied excerpt")
    sources = event.get("sources") or []
    if not sources or not all(str(row.get("url") or "").startswith("https://")
                              for row in sources):
        failures.append("event has a missing or non-HTTPS source")
    return failures
