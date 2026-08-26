"""OpenAI draft copy for the human-gated mobile Wire inbox.

This provider may propose text; it cannot approve or publish it.  The exact
evidence and exact proposed wording travel together to the GitHub issue where
the named human makes the editorial decision.
"""

from __future__ import annotations

import json
import os
import time

from . import semantic
from .providers.openai import MODEL, PRICES, OpenAIProviderError, redact


SCHEMA_VERSION = "wire-mobile-draft-v1"
PROMPT_VERSION = "wire-mobile-event-first-2026-08-26a"
DECISIONS = {"CARD", "IGNORE", "ABSTAIN"}
CONTENT_TYPES = {"REPORTING", "FANTASY_ANALYSIS"}

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "content_type", "public_summary",
                 "lineupbeat_impact", "direction", "mechanism",
                 "strength", "horizon", "limitations", "confidence",
                 "reason"],
    "properties": {
        "decision": {"type": "string", "enum": sorted(DECISIONS)},
        "content_type": {"type": "string", "enum": sorted(CONTENT_TYPES)},
        "public_summary": {"type": "string"},
        "lineupbeat_impact": {"type": "string"},
        "direction": {"type": "string", "enum": sorted(semantic.DIRECTIONS)},
        "mechanism": {"type": "string", "enum": sorted(
            semantic.MECHANISMS - {"NO_FANTASY_IMPACT"})},
        "strength": {"type": "string", "enum": sorted(semantic.STRENGTHS)},
        "horizon": {"type": "string", "enum": sorted(semantic.HORIZONS)},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
}

SYSTEM = """You draft one proposed Lineup Beat fantasy-football Wire card for
human review. You do not approve or publish anything.

Use only the supplied evidence. Never import roster knowledge, injuries,
statistics, depth charts, transactions, dates or context from memory. Opinion,
speculation, practice observations, rankings, ADP arguments and fantasy advice
are allowed when the source actually provides them. Label those cards
FANTASY_ANALYSIS and attribute the public summary to the author or publication.

For REPORTING, public_summary is one paraphrased sentence of no more than 180
characters that names the player and states what the source reported. It may
not copy a sentence from the evidence. For FANTASY_ANALYSIS, it is one
attributed sentence of no more than 180 characters explaining the source's
take. Do not use quotation marks.

The source and author are cited directly below the card. Do not begin the
public summary with the outlet name or an outlet possessive such as "Sports
Illustrated's" or "On SI's." When fantasy analysis needs attribution, use the
named author's name alone.

lineupbeat_impact is Lineup Beat's concise fantasy interpretation. It may make
a cautious inference from the supplied evidence, but it must distinguish that
inference from the source's report and state important limits. Do not invent a
guaranteed role, workload, diagnosis or timetable.

First identify the single most meaningful new development in the evidence.
Do not create a card merely because the named player appears in the passage.
Return CARD only when the evidence changes what a fantasy manager should
monitor, expect or do: a new injury or early exit, participation change,
return, transaction, suspension, explicit starter decision, genuine promotion
or demotion, material workload/role change, or an evidence-backed multi-day
performance trend. If your impact would amount to "one play does not establish
a role" or "this changes nothing," return IGNORE instead.

One catch, touchdown, interception, completion, drill result, goal-line period
or red-zone rep is IGNORE for every player, including established starters.
Several plays from one practice remain one isolated session. A PERFORMANCE or
RED_ZONE card requires an explicit pattern across multiple practices or a
separate concrete role/workload change. DEPTH_CHART requires an explicit named
starter, promotion, demotion, or movement ahead of/behind another player;
calling someone QB1 in a practice recap is not a depth-chart change.

An unexpected early exit with medical or return-to-play staff is CARD-worthy
availability news even when the evidence does not establish an injury. State
that the reason and setback status are unknown. Prefer that development over
unrelated highlights appearing in the same evidence.

A useful impact is direct and conversational. Explain the actual fantasy
consequence and the next fact that would change it. Avoid invented jargon such
as "scoring-use outlook" or "short-term starting-QB momentum."

A backup quarterback is not useful unless the evidence shows a real starting
quarterback battle, a promotion to first-team work, a named start, or a starter
absence that can put him on the field. Ordinary QB2/QB3 or developmental-job
competition is IGNORE. For a fringe RB, WR or TE, generic praise, "stood out,"
an isolated preseason play, or roster-watch/deep-league language is IGNORE
unless the evidence establishes material first-team work, workload, a depth
chart move, a transaction into a plausible role, or a starter's absence with
that player as the beneficiary. Use IGNORE for promotion, sponsor copy or no
meaningful fantasy impact. Use ABSTAIN when the player, speaker, claim subject
or evidence is ambiguous."""


def redundant_outlet_lead(summary: str, source_name: str) -> bool:
    """Whether a summary repeats the outlet already cited below the card."""
    folded = (summary or "").strip().lower().replace("’", "'")
    source = (source_name or "").split(" -- ", 1)[0].strip().lower()
    outlets = {source, "sports illustrated", "on si"} - {""}
    return any(folded.startswith(f"{outlet}'s ") for outlet in outlets)


def build_prompt(evidence: str, metadata: dict, identity: dict) -> str:
    provenance = {
        "author": metadata.get("author", ""),
        "source": metadata.get("source_name", ""),
        "ownership": metadata.get("ownership", ""),
        "published_at": metadata.get("published_at", ""),
        "source_url": metadata.get("source_url", ""),
    }
    return (
        "PLAYER IDENTITY (authoritative):\n" +
        json.dumps(identity, sort_keys=True, ensure_ascii=False) +
        "\n\nSOURCE METADATA (provenance only):\n" +
        json.dumps(provenance, sort_keys=True, ensure_ascii=False) +
        "\n\nCOMPLETE EVIDENCE:\n" + evidence
    )


class OpenAIMobileDraftProvider:
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
            raise OpenAIProviderError(
                redact(f"{type(exc).__name__}: {exc}")) from None
        return True

    def draft(self, evidence: str, metadata: dict,
              identity: dict) -> tuple[dict, dict]:
        started = time.time()
        prompt = build_prompt(evidence, metadata, identity)
        payload, usage = self._call(prompt)
        tokens_in = int(usage.get("input_tokens", 0))
        tokens_out = int(usage.get("output_tokens", 0))
        price_in, price_out = PRICES.get(self.model, PRICES[MODEL])
        meta = {
            "provider": self.name,
            "model": self.model,
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": tokens_in * price_in + tokens_out * price_out,
            "latency_ms": int((time.time() - started) * 1000),
        }
        return payload, meta

    def _call(self, prompt: str) -> tuple[dict, dict]:
        if self._transport is not None:
            return self._transport(prompt)
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise OpenAIProviderError("OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
            response = OpenAI(api_key=key).responses.create(
                model=self.model,
                instructions=SYSTEM,
                input=prompt,
                store=False,
                reasoning={"effort": "low"},
                text={"format": {"type": "json_schema",
                                  "name": "wire_mobile_draft",
                                  "strict": True,
                                  "schema": RESPONSE_SCHEMA}},
            )
            payload = json.loads(response.output_text)
        except Exception as exc:
            raise OpenAIProviderError(
                redact(f"{type(exc).__name__}: {exc}")) from None
        usage = getattr(response, "usage", None)
        return payload, {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
        }
