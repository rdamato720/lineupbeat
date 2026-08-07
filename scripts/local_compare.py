#!/usr/bin/env python3
"""Run a local model over posts we have already extracted, and compare.

    python3 scripts/local_compare.py --host http://10.168.168.146:11434 --n 60
    python3 scripts/local_compare.py --host ... --model llama3.3 --n 60
    python3 scripts/local_compare.py --host ... --n 60 --show-all

The earlier test used six article-shaped cases, and the two it failed were
both article problems: a feature mentioning somebody else's transfer, and a
formal announcement following an earlier report. Neither shape occurs on X,
which is now the only thing the wire reads.

So this is the test that counts. Real posts, already extracted by the model
in production, run again locally, and the two sets put side by side.

WHAT IT COMPARES

Not wording. Two models will phrase the same fact differently and neither is
wrong. What matters is whether they agree on the things a reader acts on:

    did both find a player, and the same one
    did both call it the same kind of event
    did both rate it as mattering the same amount
    did one invent something the other did not

A local model that agrees on those and differs on phrasing is a model you can
move to. One that finds players nobody mentioned, or rates a practice note as
season-shaping, is not -- however fast it is.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def ask(host, model, system, prompt, timeout=180):
    body = json.dumps({"model": model, "system": system, "prompt": prompt,
                       "stream": False,
                       "options": {"temperature": 0, "num_ctx": 8192}}).encode()
    req = urllib.request.Request(f"{host.rstrip('/')}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return d.get("response", ""), time.time() - t0


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


def key(n):
    n = re.sub(r"[.'`]", "", (n or "").lower())
    return " ".join(re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n).split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--model", default="qwen2.5:14b")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--show-all", action="store_true")
    args = ap.parse_args()

    from beatwire.extract import SYSTEM, USER_TMPL
    from beatwire.models import CATEGORIES, EVENTS

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # Posts we already extracted, so there is something to compare against.
    rows = conn.execute("""
        SELECT i.url, i.title, i.body,
               (SELECT json_group_array(json_object(
                    'player', n.player_name, 'event', n.event,
                    'act', n.actionability, 'claim', n.claim))
                FROM nuggets n
                WHERE json_extract(n.attributions, '$[0].url') = i.url) AS got
        FROM items i
        WHERE i.source_id LIKE '%-tapi-' || '%' AND length(i.body) > 80
        ORDER BY i.fetched_at DESC LIMIT ?""", (args.n,)).fetchall()
    if not rows:
        sys.exit("  no twitterapi items stored. Run the pipeline first.")

    print(f"\n  {args.model} against production, on {len(rows)} real posts\n")

    agree_player = agree_event = agree_act = 0
    both_empty = compared = 0
    invented, missed, bad_json = [], [], 0
    times = []

    for r in rows:
        prod = json.loads(r["got"] or "[]")
        prompt = USER_TMPL.format(
            profile="NFL. Skill positions: QB, RB, WR, TE.",
            categories=", ".join(CATEGORIES), events="\n".join(EVENTS),
            source_name="beat writer", outlet="X", teams="the team",
            published="today", text=(r["title"] or "") + "\n" + r["body"][:3000])
        try:
            raw, secs = ask(args.host, args.model, SYSTEM, prompt)
            times.append(secs)
        except Exception as exc:
            print(f"    request failed: {str(exc)[:60]}")
            continue
        loc = parse(raw)
        if loc is None:
            bad_json += 1
            continue

        p_names = {key(x.get("player")) for x in prod if x.get("player")}
        l_names = {key(x.get("player")) for x in loc if x.get("player")}

        if not p_names and not l_names:
            both_empty += 1
            continue
        compared += 1

        # Players only one side found. The first is the dangerous one: a
        # claim about somebody the production model did not see at all.
        for n in l_names - p_names:
            invented.append((n, (r["body"] or "")[:70]))
        for n in p_names - l_names:
            missed.append((n, (r["body"] or "")[:70]))

        shared = p_names & l_names
        if shared:
            agree_player += 1
            pe = {key(x["player"]): x for x in prod if x.get("player")}
            le = {key(x["player"]): x for x in loc if x.get("player")}
            for n in shared:
                if pe[n].get("event") == le[n].get("event"):
                    agree_event += 1
                if pe[n].get("act") == le[n].get("actionability"):
                    agree_act += 1
                break            # one comparison per post, not per player

        if args.show_all or (l_names - p_names):
            print(f"    {(r['body'] or '')[:74]}")
            print(f"      production: "
                  f"{[(x.get('player'), x.get('event')) for x in prod] or 'nothing'}")
            print(f"      local     : "
                  f"{[(x.get('player'), x.get('event')) for x in loc] or 'nothing'}")
            print()

    print(f"\n  OF {compared} POSTS WHERE EITHER FOUND SOMETHING\n")
    if compared:
        print(f"    {agree_player:>4}  {agree_player/compared:>5.0%}  found the same player")
        print(f"    {agree_event:>4}  {agree_event/compared:>5.0%}  agreed on the event")
        print(f"    {agree_act:>4}  {agree_act/compared:>5.0%}  agreed on actionability")
    print(f"    {both_empty:>4}        both correctly found nothing")
    print(f"    {bad_json:>4}        unparseable")

    print(f"\n  {len(invented)} players the local model found and production did not")
    for n, src in invented[:6]:
        print(f"      {n[:24]:<24} {src[:52]}")
    print(f"\n  {len(missed)} players production found and the local model did not")
    for n, src in missed[:6]:
        print(f"      {n[:24]:<24} {src[:52]}")

    if times:
        avg = sum(times) / len(times)
        print(f"\n  {avg:.1f}s an item; a hundred-item run is "
              f"{avg*100/60:.0f} minutes")
    print(f"\n  Invented players are the number that decides this. Phrasing")
    print(f"  differs between any two models and neither is wrong; a claim")
    print(f"  about somebody who was not in the post is a page with a")
    print(f"  stranger's news on it.")


if __name__ == "__main__":
    main()
