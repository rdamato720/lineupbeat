#!/usr/bin/env python3
"""Run a local model against the same articles and see what it gets wrong.

    # on the machine running ollama
    ollama pull qwen2.5:14b
    ollama serve

    # here
    python3 scripts/local_test.py --host http://192.168.1.50:11434 --n 25
    python3 scripts/local_test.py --host http://localhost:11434 --hard

WHY THIS AND NOT A GUESS

Moving extraction off the API would remove the largest running cost. Whether
that is a good trade depends entirely on quality, and quality here is not a
benchmark score: it is whether a model can tell reporting from reference,
and a knee from a torn ACL.

Every one of those distinctions cost a real mistake to find. Tucker Kraft's
knee became a torn ACL. Mike Evans's March signing was filed as news from
twenty hours ago. A sack-record feature produced a trade that had not
happened. Washington announcing a completed signing read as the deal
breaking.

So the test is those cases, on the same prompt, side by side. If a local
model handles them it is viable. If it invents an ACL it is not, and no
amount of speed makes up for it.

    --hard   only the cases that have already caught us out
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The failures, in the words that produced them. A model that gets these
# right is one you can move to; the rest is throughput.
HARD_CASES = [
    ("Kraft opens camp on PUP. Tucker Kraft begins training camp on the "
     "physically unable to perform list as he continues rehab from last "
     "season's knee injury.",
     "knee, PUP",
     "must not say torn, ACL, or season-ending"),
    ("The Buccaneers have entered unfamiliar territory without Mike Evans in "
     "training camp. Evans spent 12 seasons in Tampa Bay before beginning a "
     "new chapter with the San Francisco 49ers this offseason. Emeka Egbuka "
     "said it is a little weird not having him around.",
     "Egbuka on the room feeling different",
     "must not file Evans's move as news"),
    ("Can Myles Garrett Lead the Rams to the NFL Sack Record? The Rams have "
     "never had more than 57 sacks in a season, but after adding Garrett "
     "could LA make a run at history? Garrett joined the Rams after "
     "recording 23 sacks in 2025.",
     "nothing, or a context note",
     "must not produce a trade or signing"),
    ("Washington Commanders formally announce Stefon Diggs signing. The "
     "veteran receiver passed his physical and then signed his one-year "
     "contract. Washington formally announced the acquisition on Friday "
     "morning. This formally secures Diggs' services following a Wednesday "
     "pact that will pay up to $12 million.",
     "the signing completed, announced Friday",
     "must not read as the deal breaking"),
    ("Source: The Colts and RB Jonathan Taylor have agreed to a two-year, "
     "$44 million extension with $39 million guaranteed.",
     "extension, actionability 3",
     "a contract is season-shaping even though it changes no lineup today"),
    ("Reacted in real time to the Stefon Diggs news with @LoganPaulsenNFL. "
     "What this signing means for the WR room. Listen now on @team980.",
     "nothing",
     "a show promo is not reporting"),
]


def ask_local(host, model, system, prompt, timeout=180):
    body = json.dumps({
        "model": model,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return d.get("response", ""), time.time() - t0


def parse(text):
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--model", default="qwen2.5:14b")
    ap.add_argument("--hard", action="store_true",
                    help="only the cases that have caught us out before")
    ap.add_argument("--n", type=int, default=15,
                    help="how many real articles to also run")
    ap.add_argument("--db", default="beatwire.db")
    args = ap.parse_args()

    from beatwire.extract import SYSTEM, USER_TMPL
    from beatwire.models import CATEGORIES, EVENTS

    try:
        urllib.request.urlopen(f"{args.host.rstrip('/')}/api/tags", timeout=10)
    except Exception as exc:
        sys.exit(f"  cannot reach ollama at {args.host}: {str(exc)[:60]}\n"
                 f"  on that machine: ollama serve\n"
                 f"  and allow the network: OLLAMA_HOST=0.0.0.0 ollama serve")

    print(f"\n  {args.model} at {args.host}\n")
    print(f"  THE CASES THAT HAVE CAUGHT US OUT\n")

    times, failures = [], 0
    for text, want, rule in HARD_CASES:
        prompt = USER_TMPL.format(
            profile="NFL. Skill positions: QB, RB, WR, TE.",
            categories=", ".join(CATEGORIES),
            events="\n".join(EVENTS),
            source_name="beat writer", outlet="test",
            teams="the team", published="today",
            text=text)
        try:
            raw, secs = ask_local(args.host, args.model, SYSTEM, prompt)
        except Exception as exc:
            print(f"    FAILED: {str(exc)[:60]}")
            failures += 1
            continue
        times.append(secs)
        got = parse(raw)
        print(f"    source : {text[:74]}…")
        print(f"    want   : {want}")
        print(f"    rule   : {rule}")
        if got is None:
            print(f"    got    : UNPARSEABLE  {raw[:70]!r}")
            failures += 1
        elif not got:
            print(f"    got    : nothing")
        else:
            for g in got[:3]:
                print(f"    got    : [{g.get('actionability')}] "
                      f"{g.get('player')} / {g.get('event')} / "
                      f"{str(g.get('claim'))[:56]}")
        print(f"    {secs:.1f}s\n")

    # real articles, for throughput and to see it on ordinary input
    if args.n:
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT title, body FROM items WHERE length(body) > 300 "
            "ORDER BY fetched_at DESC LIMIT ?", (args.n,)).fetchall()
        print(f"  {len(rows)} REAL ITEMS\n")
        ok = 0
        for r in rows:
            prompt = USER_TMPL.format(
                profile="NFL. Skill positions: QB, RB, WR, TE.",
                categories=", ".join(CATEGORIES),
                events="\n".join(EVENTS),
                source_name="beat writer", outlet="test",
                teams="the team", published="today",
                text=(r["title"] or "") + "\n" + r["body"][:4000])
            try:
                raw, secs = ask_local(args.host, args.model, SYSTEM, prompt)
                times.append(secs)
                got = parse(raw)
                ok += got is not None
                print(f"    {secs:>5.1f}s  {len(got) if got is not None else 'BAD':>3}"
                      f"  {(r['title'] or '')[:58]}")
            except Exception as exc:
                print(f"    failed: {str(exc)[:50]}")
        print(f"\n    {ok}/{len(rows)} returned valid json")

    if times:
        avg = sum(times) / len(times)
        print(f"\n  {avg:.1f}s an item on average")
        print(f"  a hundred-item run would take {avg * 100 / 60:.0f} minutes")
    print(f"\n  Read the hard cases yourself. The question is not whether it")
    print(f"  produces json -- it is whether it invents a torn ACL, files a")
    print(f"  March signing as today's news, or turns a sack-record feature")
    print(f"  into a trade. Those cost real mistakes to find, and a model")
    print(f"  that repeats them is not cheaper.")


if __name__ == "__main__":
    main()
