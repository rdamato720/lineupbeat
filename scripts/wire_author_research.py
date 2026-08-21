#!/usr/bin/env python3
"""Read an author's articles and record what the bodies actually show.

    python3 scripts/wire_author_research.py --team SF --min-articles 6
    python3 scripts/wire_author_research.py --author "Josh Reed" --team BAL --read 12

Headlines say where to look. Only the body says whether the reporter was
there, and this reads the body. Nothing here writes to the author registry:
it produces the evidence a classification is made from, so that a decision
can be re-checked later against the same articles.

The bar, applied to everyone including authors approved in an earlier pass:
recurring direct-access evidence, not one isolated phrase.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trafilatura

from wire import si
from wire.capture import _get
from wire.evidence import RELAY

# The reporter placing himself at the event. Deliberately narrow: "on the
# field" and "attendance" are dropped because an analyst writes both.
PRESENCE = re.compile(
    r"(?i)\b(i saw|i watched|i counted|by my count|from what i saw|we saw|"
    r"this reporter|i asked|when i asked|told me|said when asked)\b")
LOCATION = re.compile(
    r"(?i)\b(training facility|practice field|the podium|locker room|"
    r"levi'?s stadium|foxboro|berea|1 ?bills drive|baptist health|"
    r"team facility|media session|sideline)\b")
REPS = re.compile(
    r"(?i)\b((?:first|second|1st|2nd|no\.? ?[12])[- ]team (?:reps?|snaps?|"
    r"offense|defense|line)|took (?:the )?(?:first|second)[- ]team|"
    r"split reps|rotated (?:in|with)|with the (?:ones|twos|starters))\b")
COUNTED = re.compile(
    r"(?i)\b(\d+ (?:of|for) \d+|went \d+[- ]\d+|"
    r"(?:caught|had|took) \d+ (?:passes|targets|carries|reps|snaps)|"
    r"on \d+ (?:snaps|reps|targets|carries)|by my count)\b")
PRESSER = re.compile(
    r"(?i)\b(told reporters|said (?:on|after|following)|press conference|"
    r"speaking (?:to|with) (?:reporters|the media)|in his media session|"
    r"when asked about)\b")
QUESTION = re.compile(
    r"(?i)\b(i asked|when i asked|asked (?:him|her|about) (?:whether|if|how)|"
    r"asked (?:the )?(?:coach|shanahan|mcdaniel|campbell)\b)")
PARTICIPATION = re.compile(
    r"(?i)\b(did not (?:practice|participate)|was (?:limited|held out)|"
    r"back at practice|returned to practice|missed (?:practice|his second)|"
    r"non[- ]participant|full participant)\b")
OPINION = re.compile(
    r"(?i)\b(i think|in my (?:view|opinion)|my guess|i'?d (?:argue|say)|"
    r"should (?:draft|start|sit|trade)|the problem is|here'?s why)\b")

QUALIFYING_FORMATS = [
    ("joint-practice", re.compile(r"(?i)joint practice")),
    ("practice notebook", re.compile(r"(?i)(notebook|practice (report|notes))")),
    ("observations", re.compile(r"(?i)observations?")),
    ("camp report", re.compile(r"(?i)(training camp|camp (report|diary))")),
    ("press conference", re.compile(r"(?i)(everything .{2,30} said|said about|"
                                    r"press conference)")),
    ("injury/participation", re.compile(r"(?i)(injury (report|update)|"
                                        r"participation|attendance)")),
    ("depth chart / reps", re.compile(r"(?i)(depth chart|first[- ]team|reps)")),
]


def read_author(team: str, author: str, arts: list, read: int,
                pause: float = 0.3) -> dict:
    """Read up to `read` of this author's articles and tally the evidence."""
    rec = {
        "author": author, "team": team, "articles_available": len(arts),
        "articles_read": 0, "presence": 0, "location": 0, "reps": 0,
        "counted": 0, "presser": 0, "questioning": 0, "participation": 0,
        "opinion_only": 0, "relay": 0, "formats": Counter(), "samples": [],
        "phrases": [], "chars": [],
    }
    for a in arts[:read]:
        try:
            st, html, _ = _get(a["canonical_url"], timeout=35)
        except Exception:
            continue
        if not (isinstance(st, int) and st == 200 and html):
            continue
        text = trafilatura.extract(html, include_comments=False,
                                   include_tables=False,
                                   favor_precision=True) or ""
        if len(text) < 400:
            continue
        rec["articles_read"] += 1
        rec["chars"].append(len(text))
        rec["samples"].append(a["canonical_url"])
        hits = 0
        for key, pat in (("presence", PRESENCE), ("location", LOCATION),
                         ("reps", REPS), ("counted", COUNTED),
                         ("presser", PRESSER), ("questioning", QUESTION),
                         ("participation", PARTICIPATION)):
            m = pat.search(text)
            if m:
                rec[key] += 1
                if key in ("presence", "reps", "counted", "participation"):
                    hits += 1
                    if len(rec["phrases"]) < 8:
                        rec["phrases"].append(m.group(0).strip()[:40])
        if RELAY.search(text):
            rec["relay"] += 1
        if hits == 0 and OPINION.search(text):
            rec["opinion_only"] += 1
        for name, pat in QUALIFYING_FORMATS:
            if pat.search(a["headline"]) or pat.search(text[:600]):
                rec["formats"][name] += 1
        time.sleep(pause)
    rec["formats"] = dict(rec["formats"])
    return rec


def verdict(rec: dict) -> tuple[str, str]:
    """The classification the evidence supports, and why.

    Direct-access evidence has to recur. A single phrase in a single article
    is an anecdote, and the standard that demoted an author approved on a
    headline is the same one applied here.
    """
    read = rec["articles_read"]
    if read < 6:
        return ("UNKNOWN",
                f"only {read} full article(s) available to read; the standard "
                f"requires at least six")
    direct = rec["presence"] + rec["reps"] + rec["counted"] + rec["participation"]
    access = rec["presser"] + rec["questioning"]
    recurring = sum(1 for k in ("presence", "reps", "counted", "participation")
                    if rec[k] >= 2)
    relay_rate = rec["relay"] / read
    if relay_rate >= 0.5:
        return ("AGGREGATION",
                f"relays another outlet in {rec['relay']}/{read} articles read")
    if direct >= 4 and recurring >= 2 and (access >= 1 or direct >= 6):
        return ("FIRSTHAND_APPROVED",
                f"recurring direct-access evidence across {read} articles: "
                f"presence {rec['presence']}, first/second-team reps "
                f"{rec['reps']}, counted plays {rec['counted']}, "
                f"participation reporting {rec['participation']}, "
                f"press-conference access {rec['presser']}, direct "
                f"questioning {rec['questioning']}; relays another outlet in "
                f"{rec['relay']}/{read}")
    if direct >= 1 or access >= 1:
        return ("REPORTING",
                f"reports, but direct-access evidence does not recur: "
                f"presence {rec['presence']}, reps {rec['reps']}, counted "
                f"{rec['counted']}, participation {rec['participation']}, "
                f"presser {rec['presser']} across {read} articles read "
                f"({recurring} marker type(s) appearing twice or more)")
    return ("ANALYSIS_ONLY",
            f"no direct-access evidence in {read} articles read; "
            f"opinion-led in {rec['opinion_only']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True)
    ap.add_argument("--author")
    ap.add_argument("--read", type=int, default=6)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--pages", type=int, default=6)
    ap.add_argument("--json")
    args = ap.parse_args()

    slug = si.CODE_TO_SLUG[args.team]
    raw, _ = si.discover_team(slug, pages=args.pages)
    keep = [r for r in raw if si.team_in_url(r["canonical_url"]) == slug]
    by = defaultdict(list)
    for r in keep:
        if r["author"]:
            by[r["author"]].append(r)

    targets = ([args.author] if args.author
               else [a for a, _ in Counter(
                   {k: len(v) for k, v in by.items()}).most_common(args.top)])
    out = []
    for author in targets:
        arts = by.get(author, [])
        if not arts:
            print(f"  {author}: no team-segment articles found")
            continue
        rec = read_author(args.team, author, arts, args.read)
        cls, why = verdict(rec)
        rec["classification"], rec["reason"] = cls, why
        out.append(rec)
        print(f"\n  {args.team}  {author}   -> {cls}")
        print(f"    read {rec['articles_read']}/{rec['articles_available']}  "
              f"presence {rec['presence']}  location {rec['location']}  "
              f"reps {rec['reps']}  counted {rec['counted']}")
        print(f"    presser {rec['presser']}  questioning {rec['questioning']}  "
              f"participation {rec['participation']}  relay {rec['relay']}  "
              f"opinion-only {rec['opinion_only']}")
        print(f"    formats: {rec['formats']}")
        if rec["phrases"]:
            print(f"    phrases: {'; '.join(rec['phrases'][:4])}")
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
