#!/usr/bin/env python3
"""One minimal authenticated Structured Outputs request. Nothing else.

    python3 scripts/wire_claude_smoke.py

Exit 0 means the key authenticates and the forced-tool schema path returns a
parseable assessment. Any other exit means the Claude evaluation must not
start: a run that begins on a broken key produces a page full of abstentions
that look like model caution rather than an auth failure.

The key is never printed. Presence and shape are reported; the value is not.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import semantic as sem
from wire.providers.claude import ClaudeSemanticProvider, redact

SEGMENT = ("Anthony Richardson led the Colts' second-team offense down the "
           "field for a field goal as time expired.")
PLAYERS = [{"player_id": "SMOKE-1", "player_name": "Anthony Richardson",
            "team": "IND", "position": "QB"}]


def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    shaped = bool(re.fullmatch(r"sk-ant-[A-Za-z0-9_\-]{20,}", key))
    print(f"  key present : {bool(key)}")
    print(f"  key length  : {len(key)}")
    print(f"  key shape   : {'looks like an Anthropic key' if shaped else 'NOT a valid Anthropic key shape'}")
    if not key:
        print("  STOP: ANTHROPIC_API_KEY is not set. Claude evaluation cannot run.")
        return 2
    if not shaped:
        print("  STOP: the value in ANTHROPIC_API_KEY is not shaped like a key.")
        print("        Not sending a request that is certain to 401.")
        return 3

    prov = ClaudeSemanticProvider()
    print(f"  model       : {prov.model}")
    print(f"  schema      : {sem.SCHEMA_VERSION}  prompt {sem.PROMPT_VERSION}")
    a = prov.evaluate(SEGMENT, {"team": "IND", "source_name": "smoke test",
                                "author": "fixture",
                                "source_ownership": "INDEPENDENT"}, PLAYERS)
    if a.decision == sem.ABSTAIN and (a.abstention_reason or "").startswith(
            "provider unavailable"):
        print(f"  STOP: {redact(a.abstention_reason)[:180]}")
        return 4
    print(f"  decision    : {a.decision}")
    print(f"  mechanism   : {a.fantasy_mechanism}")
    print(f"  subject     : {a.claim_subject_player_name}")
    print(f"  quote       : {a.supporting_quote[:70]!r}")
    print(f"  tokens      : {a.tokens_in} in / {a.tokens_out} out")
    print(f"  cost        : ${a.cost_usd:.5f}")
    print(f"  latency     : {a.latency_ms}ms")
    print("  smoke test PASSED; the Claude evaluation may proceed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
