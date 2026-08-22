"""OpenAI Responses API transport for independent semantic review."""

from __future__ import annotations

import json
import os
import time

from .openai import OpenAIProviderError, PRICES, redact
from .. import independent_review as review

MODEL = "gpt-5.6-sol"


class OpenAIIndependentReviewer:
    """A separate pass over the full evidence and generator assessment."""

    model = MODEL

    def __init__(self, model: str = MODEL, transport=None):
        self.model = model
        self._transport = transport

    def available(self):
        if self._transport is not None:
            return True
        from .openai import OpenAISemanticProvider
        return OpenAISemanticProvider(model=self.model).available()

    def evaluate(self, evidence_text: str, identity: dict,
                 assessment: dict) -> dict:
        prompt = review.build_prompt(evidence_text, identity, assessment)
        started = time.time()
        if self._transport is not None:
            payload, usage = self._transport(prompt)
        else:
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise OpenAIProviderError("OPENAI_API_KEY is not set")
            try:
                from openai import OpenAI
            except ImportError:
                raise OpenAIProviderError("the openai package is not installed")
            try:
                response = OpenAI(api_key=key).responses.create(
                    model=self.model,
                    instructions=review.SYSTEM,
                    input=prompt,
                    store=False,
                    reasoning={"effort": "low"},
                    text={"format": {"type": "json_schema",
                                     "name": "wire_independent_review",
                                     "strict": True,
                                     "schema": review.RESPONSE_SCHEMA}})
            except Exception as exc:
                raise OpenAIProviderError(
                    redact(f"{type(exc).__name__}: {exc}")) from None
            try:
                payload = json.loads(response.output_text)
            except Exception:
                raise OpenAIProviderError("response was not valid JSON") from None
            u = getattr(response, "usage", None)
            usage = {"input_tokens": getattr(u, "input_tokens", 0),
                     "output_tokens": getattr(u, "output_tokens", 0)}
        errors = review.validate_response(payload)
        if errors:
            raise OpenAIProviderError("invalid reviewer response: " +
                                      "; ".join(errors))
        tokens_in = usage.get("input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        cost_in, cost_out = PRICES.get(self.model, PRICES[MODEL])
        return {**payload, "provider": "openai", "model": self.model,
                "tokens_in": tokens_in, "tokens_out": tokens_out,
                "cost_usd": tokens_in * cost_in + tokens_out * cost_out,
                "latency_ms": int((time.time() - started) * 1000)}
