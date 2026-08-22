"""Claude transport for the independent reviewer."""

from __future__ import annotations

import os
import time

from .claude import ClaudeProviderError, MODEL, COST_IN, COST_OUT, redact
from .. import independent_review as review


class ClaudeIndependentReviewer:
    model = MODEL

    def __init__(self, transport=None):
        self._transport = transport

    def available(self):
        if self._transport is not None:
            return True
        from .claude import ClaudeSemanticProvider
        return ClaudeSemanticProvider().available()

    def evaluate(self, evidence_text: str, identity: dict,
                 assessment: dict) -> dict:
        prompt = review.build_prompt(evidence_text, identity, assessment)
        started = time.time()
        if self._transport is not None:
            payload, usage = self._transport(prompt)
        else:
            try:
                import anthropic
            except ImportError:
                raise ClaudeProviderError("the anthropic package is not installed")
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise ClaudeProviderError("ANTHROPIC_API_KEY is not set")
            try:
                response = anthropic.Anthropic(api_key=key).messages.create(
                    model=self.model, max_tokens=1000, temperature=0,
                    system=review.SYSTEM,
                    tools=[{"name": "record_independent_review",
                            "description": "Record the independent review.",
                            "input_schema": review.RESPONSE_SCHEMA}],
                    tool_choice={"type": "tool",
                                 "name": "record_independent_review"},
                    messages=[{"role": "user", "content": prompt}])
            except Exception as exc:
                raise ClaudeProviderError(redact(exc)) from None
            payload = None
            for block in response.content:
                if getattr(block, "type", "") == "tool_use":
                    payload = dict(block.input)
                    break
            if payload is None:
                raise ClaudeProviderError("no tool_use block in reviewer response")
            usage = {"input_tokens": response.usage.input_tokens,
                     "output_tokens": response.usage.output_tokens}
        tokens_in = usage.get("input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        return {**payload, "model": self.model,
                "tokens_in": tokens_in, "tokens_out": tokens_out,
                "cost_usd": tokens_in * COST_IN + tokens_out * COST_OUT,
                "latency_ms": int((time.time() - started) * 1000)}
