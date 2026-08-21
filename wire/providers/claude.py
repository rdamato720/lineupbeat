"""Claude, through the native Anthropic Messages API.

The OpenAI-compatible endpoint is deliberately not used: Anthropic does not
guarantee strict schema enforcement through it, and a schema that is only
usually enforced is the kind of thing that fails on the passage that matters.
Structured output here comes from a tool the model is forced to call, which
is the native mechanism.

The key is read from ANTHROPIC_API_KEY and never leaves this module. Every
error is scrubbed before it is raised, because an authentication failure
carries the header that caused it.
"""

from __future__ import annotations

import json
import os
import re
import time

from .. import semantic as sem

MODEL = "claude-sonnet-4-5"
# Published per-million-token prices for the pinned model.
COST_IN, COST_OUT = 3.00 / 1_000_000, 15.00 / 1_000_000

TOOL_NAME = "record_assessment"


def redact(text: str) -> str:
    """Remove anything key-shaped before it can reach a log or a traceback."""
    out = str(text)
    k = os.environ.get("ANTHROPIC_API_KEY", "")
    if k:
        out = out.replace(k, "[REDACTED]")
    out = re.sub(r"sk-ant-[A-Za-z0-9_\-]{8,}", "[REDACTED]", out)
    out = re.sub(r"(?i)(x-api-key|authorization)[\"':=\s]+[^\s\"',}]+",
                 r"\1: [REDACTED]", out)
    return out


class ClaudeProviderError(RuntimeError):
    """A provider failure, already scrubbed."""


class ClaudeSemanticProvider(sem.FantasySemanticProvider):
    name = "claude"
    model = MODEL

    def __init__(self, model: str = MODEL, transport=None):
        self.model = model
        # Injectable so the schema and validation path can be exercised
        # without a key or a network call.
        self._transport = transport

    def available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY")) or self._transport

    def evaluate(self, evidence_segment, article_metadata, matched_players):
        t0 = time.time()
        players = matched_players or []
        a = sem.SemanticAssessment(
            provider=self.name, model=self.model,
            input_hash=sem.input_hash(evidence_segment, players))
        prompt = sem.build_prompt(evidence_segment, article_metadata or {},
                                  players)
        try:
            payload, usage = self._call(prompt)
        except Exception as e:
            a.decision = sem.ABSTAIN
            a.abstention_reason = f"provider unavailable: {redact(e)[:160]}"
            a.latency_ms = int((time.time() - t0) * 1000)
            return a

        for k, v in payload.items():
            if hasattr(a, k):
                setattr(a, k, v)
        a.tokens_in = usage.get("input_tokens", 0)
        a.tokens_out = usage.get("output_tokens", 0)
        a.cost_usd = a.tokens_in * COST_IN + a.tokens_out * COST_OUT
        a.latency_ms = int((time.time() - t0) * 1000)
        a.output_hash = sem.output_hash(payload)
        return a

    def _call(self, prompt: str) -> tuple[dict, dict]:
        if self._transport is not None:
            return self._transport(prompt)
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ClaudeProviderError("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic
        except ImportError:
            raise ClaudeProviderError("the anthropic package is not installed")
        client = anthropic.Anthropic(api_key=key)
        try:
            resp = client.messages.create(
                model=self.model, max_tokens=1600, system=sem.SYSTEM,
                tools=[{"name": TOOL_NAME,
                        "description": "Record the assessment of this passage.",
                        "input_schema": sem.RESPONSE_SCHEMA}],
                tool_choice={"type": "tool", "name": TOOL_NAME},
                messages=[{"role": "user", "content": prompt}])
        except Exception as e:
            raise ClaudeProviderError(redact(f"{type(e).__name__}: {e}")) from None
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                return dict(block.input), {
                    "input_tokens": resp.usage.input_tokens,
                    "output_tokens": resp.usage.output_tokens}
        raise ClaudeProviderError("no tool_use block in the response")
