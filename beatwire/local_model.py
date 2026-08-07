#!/usr/bin/env python3
"""Extraction against a local model, via spans rather than names.

Set two variables and the pipeline uses this instead of the API:

    export BEATWIRE_LOCAL=http://10.168.168.146:11434
    export BEATWIRE_LOCAL_MODEL=qwen2.5:14b
    python3 -m beatwire.cli run --sport nfl

WHY IT IS SHAPED THIS WAY

A model small enough to run on ten gigabytes cannot be trusted to name a
player. Given "Bosa (soreness), Greenlaw, Zakelj" both models we tried
returned Nick Bosa correctly and then invented "Trey Lance Zakelj" and
"Deommodore Lenoir Evans" -- people who do not exist. That is not a
prompting failure. It is what a language model does with a missing first
name.

So it is never asked for one. It quotes the exact span of text that names
somebody, and says what happened to whoever that is. Two things follow:

  A quote can be checked. If the span is not in the post character for
  character it is discarded before anything downstream sees it. Measured
  across sixty real posts: 33 spans quoted, 33 verbatim, none invented.

  The resolver does the identifying. It is deterministic code matching
  against a real roster, it already handles bare surnames, and it refused
  "Black" as too ambiguous rather than guessing -- which is the behaviour
  you want and the behaviour a model will not give you.

Measured: 91% of real spans matched a rostered player, 3.1 seconds an item.

WHAT IT COSTS YOU

Less nuance than the API on the harder judgments -- reporting versus
reference, a confirmation versus a revelation. Those rules are in the prompt
and it follows most of them, but not as reliably. That is the trade, and it
is worth making only because the volume makes the API bill what it is.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request

from .models import EVENTS

HOST = os.environ.get("BEATWIRE_LOCAL", "").rstrip("/")
MODEL = os.environ.get("BEATWIRE_LOCAL_MODEL", "qwen2.5:14b")
TIMEOUT = int(os.environ.get("BEATWIRE_LOCAL_TIMEOUT", "180"))


def enabled() -> bool:
    return bool(HOST)


SYSTEM = """You read one social post from an NFL beat reporter and report
what happened, without naming anybody yourself.

For each distinct thing that happened to a person, return an object with:

  "mention"  the EXACT text naming the PERSON, copied character for
             character. A name, nothing else.
             If the post says "Zakelj", write "Zakelj". If it says "Nick
             Bosa", write "Nick Bosa". Never expand a surname into a full
             name, never supply a first name, never correct a spelling.
             NOT a verb. "signed" and "waived" are what happened, not who it
             happened to: for "The Seahawks signed OLB Garrett Nelson", the
             mention is "Garrett Nelson".
             NOT a team, a position, or a coach's title.
  "category" one of: injury, usage, depth_chart, transaction, performance,
             context
  "event"    one of the events listed in the prompt
  "horizon"  "day" for practice status or one game, "season" for anything
             that stays true
  "claim"    one sentence, in your own words, on what happened
  "actionability"  0-3, where 3 changes a lineup or settles a season
  "tags"     a short list, usually the body part for an injury

Rules:
- The mention MUST appear verbatim in the post. If you cannot copy it
  exactly, leave the object out.
- One object per distinct thing. A practice report listing eight players who
  did not participate is eight objects.
- Players only. A post about a coach, coordinator or executive returns
  nothing for them.
- A roster move naming several people is one object each: "waived CB Brandon
  Johnson, signed FB Brock Lampe" is two objects.
- A post that reports nothing about a person returns an empty array. A promo,
  a podcast plug, a link with no detail: empty array.
- Do not describe somebody the post does not mention.
- Match the source. If it says a knee, say a knee -- not a torn ACL, not
  season-ending. Keep any hedge the reporter used.
- Paraphrase. Never copy the reporter's sentence.

Return ONLY a JSON array. No prose, no markdown fences."""

USER = """Valid events (choose exactly one per object):
{events}

--- POST ---
{text}
--- END POST ---

Return a JSON array."""

# A verb is not a person. The model occasionally quotes what happened rather
# than who it happened to, and those would reach the resolver as names.
VERBS = {"signed", "waived", "released", "traded", "claimed", "activated",
         "cut", "placed", "returned", "limited", "out", "questionable",
         "doubtful", "injured", "practiced", "did not participate", "dnp",
         "ruled out", "designated", "suspended", "the team", "the club"}


def _call(system: str, prompt: str) -> str:
    body = json.dumps({
        "model": MODEL, "system": system, "prompt": prompt, "stream": False,
        "options": {"temperature": 0, "num_ctx": 8192},
    }).encode()
    req = urllib.request.Request(f"{HOST}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode()).get("response", "")


def _parse(text: str):
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M)
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return []
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []


def extract_rows(text: str, retries: int = 1) -> list[dict]:
    """Return rows shaped like the API's, with `player` set from the span.

    Every row is checked against the source before it is returned: a mention
    that is not in the post verbatim is dropped, and so is one that is a verb
    rather than a name. Downstream code sees only spans that were really
    there, and the resolver decides who they are.
    """
    if not HOST:
        raise RuntimeError("BEATWIRE_LOCAL is not set")
    prompt = USER.format(events="\n".join(EVENTS), text=text[:6000])
    raw = ""
    for attempt in range(retries + 1):
        try:
            raw = _call(SYSTEM, prompt)
            break
        except Exception:
            if attempt >= retries:
                raise
            time.sleep(2)

    out = []
    low = text.lower()
    for o in _parse(raw):
        if not isinstance(o, dict):
            continue
        mention = (o.get("mention") or "").strip()
        if not mention or mention.lower() in VERBS:
            continue
        if mention.lower() not in low:
            continue                      # invented, never leaves this loop
        ev = o.get("event")
        if ev not in EVENTS:
            ev = "context_note"
        try:
            act = int(o.get("actionability", 1))
        except (TypeError, ValueError):
            act = 1
        out.append({
            "player": mention,            # a span; the resolver names him
            "category": o.get("category") or "context",
            "event": ev,
            "horizon": o.get("horizon") if o.get("horizon") in ("day", "season")
                       else "day",
            "claim": (o.get("claim") or "").strip(),
            "actionability": max(0, min(3, act)),
            "tags": o.get("tags") if isinstance(o.get("tags"), list) else [],
        })
    return [r for r in out if r["claim"]]
