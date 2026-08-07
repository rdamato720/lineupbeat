#!/usr/bin/env python3
"""Ask a local model what happened, never who, and let code do the matching.

    python3 scripts/local_spans.py --host http://10.168.168.146:11434 --n 60

WHY THIS SHAPE

Both local models failed the same way and it was not a prompting problem.
Given a practice report reading "Bosa (soreness), Greenlaw, Zakelj", they
returned Nick Bosa correctly and then invented "Trey Lance Zakelj" and
"Deommodore Lenoir Evans" -- people who do not exist. A model small enough
to run on ten gigabytes fills in a missing first name because that is what
a language model does.

So do not ask it for the name.

Ask it to quote the exact span of text that names somebody, and to say what
happened to whoever that is. Copying a substring is a much easier task than
recalling a roster, and a quote can be CHECKED: if the span is not in the
post, the extraction is thrown away before anything downstream sees it.

Then the resolver does the identifying. It is deterministic code matching
against a real roster, it already handles bare surnames, and it would have
rejected "Zakelj" outright rather than inventing a first name for him.

The division is the point: the model reads, the code identifies. Neither is
asked to do the thing it is bad at.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SPAN_SYSTEM = """You read one social post from an NFL beat reporter and
report what happened, without naming anybody yourself.

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
  "event"    one of the events listed in the prompt
  "claim"    one sentence, in your own words, on what happened
  "actionability"  0-3, where 3 changes a lineup or settles a season

Rules:
- The mention MUST appear verbatim in the post. If you cannot copy it
  exactly, leave the object out.
- One object per distinct thing that happened. A practice report listing
  eight players who did not participate is eight objects.
- A post that reports nothing about a person returns an empty array. A
  promo, a podcast plug, a link with no detail: empty array.
- Do not describe somebody the post does not mention.
- Players only. A post about a coach, a coordinator or an executive returns
  nothing for them.
- A roster move naming several people is one object each, and the mention is
  each player's name: "waived CB Brandon Johnson, signed FB Brock Lampe" is
  two objects, "Brandon Johnson" and "Brock Lampe".
- Paraphrase. Never copy the reporter's sentence.

Return ONLY a JSON array. No prose, no markdown fences."""

USER = """Valid events (choose exactly one per object):
{events}

--- POST ---
{text}
--- END POST ---

Return a JSON array of objects with keys: mention, event, claim,
actionability."""


def ask(host, model, system, prompt, timeout=180):
    body = json.dumps({"model": model, "system": system, "prompt": prompt,
                       "stream": False,
                       "options": {"temperature": 0, "num_ctx": 8192}}).encode()
    req = urllib.request.Request(f"{host.rstrip('/')}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("response", ""), time.time() - t0


def parse(text):
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M)
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, list) else None
    except json.JSONDecodeError:
        return None


# Words that describe an event rather than a person.
VERBS = {"signed", "waived", "released", "traded", "claimed", "activated",
         "cut", "placed", "returned", "limited", "out", "questionable",
         "doubtful", "injured", "practiced", "did not participate", "dnp"}


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    return " ".join(re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n).split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--model", default="qwen2.5:14b")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--show", type=int, default=6)
    args = ap.parse_args()

    from beatwire.models import EVENTS
    from beatwire.registry import Registry
    from beatwire.resolve import Resolver
    reg = Registry("nfl")
    resolver = Resolver(reg.players)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT i.url, i.body, i.source_id,
               (SELECT json_group_array(json_object(
                    'player', n.player_name, 'event', n.event))
                FROM nuggets n
                WHERE json_extract(n.attributions,'$[0].url') = i.url) AS got
        FROM items i WHERE i.source_id LIKE '%-tapi-%' AND length(i.body) > 80
        ORDER BY i.fetched_at DESC LIMIT ?""", (args.n,)).fetchall()
    if not rows:
        sys.exit("  no posts stored")

    print(f"\n  {args.model}, quoting spans instead of naming players")
    print(f"  {len(rows)} posts\n")

    n_spans = n_verbatim = n_resolved = 0
    agree = compared = 0
    fabricated, unmatched = [], []
    times, shown = [], 0

    for r in rows:
        body = r["body"] or ""
        prod = {key(x["player"]) for x in json.loads(r["got"] or "[]")
                if x.get("player")}
        try:
            raw, secs = ask(args.host, args.model, SPAN_SYSTEM,
                            USER.format(events="\n".join(EVENTS),
                                        text=body[:3000]))
            times.append(secs)
        except Exception as exc:
            print(f"    failed: {str(exc)[:60]}")
            continue
        out = parse(raw)
        if out is None:
            continue

        got = set()
        for o in out:
            mention = (o.get("mention") or "").strip()
            if not mention:
                continue
            n_spans += 1
            # The check that makes this work: a quote that is not in the post
            # is discarded before anything downstream sees it.
            # A verb is not a person. The model occasionally quoted
            # "signed" or "waived" -- the thing that happened rather than
            # who it happened to -- and those reached the resolver as names.
            if mention.lower() in VERBS:
                continue
            if mention.lower() not in body.lower():
                fabricated.append((mention, body[:60]))
                continue
            n_verbatim += 1
            player, conf = resolver.resolve(mention, None)
            if player:
                n_resolved += 1
                got.add(key(player.name))
            else:
                unmatched.append((mention, body[:60]))

        if prod or got:
            compared += 1
            if prod & got:
                agree += 1
            if shown < args.show and (prod != got):
                shown += 1
                print(f"    {body[:76]}")
                print(f"      production: {sorted(prod) or 'nothing'}")
                print(f"      spans     : {sorted(got) or 'nothing'}")
                print()

    print(f"\n  SPANS\n")
    print(f"    {n_spans:>5}  quoted")
    print(f"    {n_verbatim:>5}  {n_verbatim/max(n_spans,1):>5.0%} actually in the post")
    print(f"    {len(fabricated):>5}  {len(fabricated)/max(n_spans,1):>5.0%} invented, discarded before use")
    print(f"    {n_resolved:>5}  {n_resolved/max(n_verbatim,1):>5.0%} of real spans matched a rostered player")
    print(f"\n  AGAINST PRODUCTION\n")
    if compared:
        print(f"    {agree:>5}  {agree/compared:>5.0%} of {compared} posts found "
              f"at least one player in common")
    if fabricated:
        print(f"\n  invented spans, caught by the verbatim check:")
        for m, src in fabricated[:6]:
            print(f"      {m[:26]:<26} {src[:46]}")
    if unmatched:
        print(f"\n  real spans the resolver could not place ({len(unmatched)}):")
        for m, src in unmatched[:6]:
            print(f"      {m[:26]:<26} {src[:46]}")
    if times:
        avg = sum(times)/len(times)
        print(f"\n  {avg:.1f}s an item")
    print(f"\n  The number to read is the invented rate. Under the old shape")
    print(f"  a fabricated name reached the page. Here it cannot: a quote")
    print(f"  that is not in the post is thrown away, and the resolver -- not")
    print(f"  the model -- decides who anybody is.")


if __name__ == "__main__":
    main()
