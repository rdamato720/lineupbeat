"""OpenAI, through the Responses API with Structured Outputs.

Same contract as the Claude provider and the same refusal to trust the
answer: strict schema on the way out, deterministic validation afterwards.

The key is read from OPENAI_API_KEY and never leaves this module.
"""

from __future__ import annotations

import json
import os
import re
import time

from .. import semantic as sem

MODEL = "gpt-4.1"
COST_IN, COST_OUT = 2.00 / 1_000_000, 8.00 / 1_000_000


def redact(text: str) -> str:
    out = str(text)
    k = os.environ.get("OPENAI_API_KEY", "")
    if k:
        out = out.replace(k, "[REDACTED]")
    out = re.sub(r"sk-[A-Za-z0-9_\-]{12,}", "[REDACTED]", out)
    out = re.sub(r"(?i)(authorization|api[-_]?key)[\"':=\s]+[^\s\"',}]+",
                 r"\1: [REDACTED]", out)
    return out


class OpenAIProviderError(RuntimeError):
    """A provider failure, already scrubbed."""


class OpenAISemanticProvider(sem.FantasySemanticProvider):
    name = "openai"
    model = MODEL

    def __init__(self, model: str = MODEL, transport=None):
        self.model = model
        self._transport = transport

    KEY_SHAPE = re.compile(r"sk-[A-Za-z0-9_\-]{20,}")

    def available(self) -> bool:
        """Usable, not merely present. Disabled for this launch regardless."""
        if self._transport is not None:
            return True
        return bool(self.KEY_SHAPE.fullmatch(
            os.environ.get("OPENAI_API_KEY", "") or ""))

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
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise OpenAIProviderError("OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
        except ImportError:
            raise OpenAIProviderError("the openai package is not installed")
        client = OpenAI(api_key=key)
        try:
            resp = client.responses.create(
                model=self.model,
                instructions=sem.SYSTEM,
                input=prompt,
                text={"format": {"type": "json_schema",
                                 "name": "wire_semantic_assessment",
                                 "strict": True,
                                 "schema": sem.RESPONSE_SCHEMA}})
        except Exception as e:
            raise OpenAIProviderError(redact(f"{type(e).__name__}: {e}")) from None
        try:
            payload = json.loads(resp.output_text)
        except Exception:
            raise OpenAIProviderError("response was not valid JSON") from None
        u = getattr(resp, "usage", None)
        return payload, {"input_tokens": getattr(u, "input_tokens", 0),
                         "output_tokens": getattr(u, "output_tokens", 0)}
