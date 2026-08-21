"""Cut an article into segments a claim can safely live inside.

A sentence window that crosses a structural boundary invents relationships
that the article never asserted. One span in the reviewed batch merged Josh
Allen's quotation, about Dalton Kincaid, with a section heading and then a
paragraph about Keon Coleman's walking boot -- three different people -- and
the result was filed as Keon Coleman's own account of his role. It also cut
mid-clause at the word "per", which removed "per Cameron Wolfe of NFL
Network" from the text, so the relay detector could not see the attribution
it was there to catch.

So segmentation runs first and windows never cross a boundary. A boundary is
anything that means "a new item starts here":

    a blank line or a line break between paragraphs
    a section heading (GOOD / NOT SO GOOD, ALL CAPS, "-- Heading")
    a bullet or a dash-led note
    a numbered observation ("1.", "-Safety Kevin Winston Jr. was flying...")
    a dated tracker entry ("Aug. 15:", "Day 16:")
    a live-blog timestamp ("11:42 a.m.")
    a transaction line ("Waived: ...", "Signed: ...")
    a byline or footer biography

The cost of over-segmenting is a claim that loses a little context. The cost
of under-segmenting is a claim about the wrong person, which is the failure
this exists to prevent.
"""

from __future__ import annotations

import re

# A line that starts a new item. Tested against the whole line, stripped.
BOUNDARY = re.compile(
    r"^\s*(?:"
    r"[-–—•·*]\s+"                    # bullet or dash lead
    r"|\d{1,2}[.)]\s+"                                    # 1. 2) numbered
    r"|(?:day|practice)\s*(?:no\.?\s*)?\d+\s*[:\-–]" # Day 16:  Practice No. 11 -
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}\s*[:\-]"
    r"|\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)"             # live-blog timestamp
    r"|(?:waived|signed|released|claimed|activated|placed)\s*[:\-]"
    r"|(?:good|not so good|the good|the bad)\b.{0,60}$"   # GOOD / NOT SO GOOD
    r")", re.I)

# Section labels that introduce the passage after them. "NOT SO GOOD --
# More Bills' WRs on shelf" is the line that let a Josh Allen quotation
# reach a Keon Coleman paragraph.
SECTION = re.compile(
    r"(?i)^\s*(?:the )?(?:good|not so good|bad|ugly|winners?|losers?|"
    r"takeaways?|observations?|notes?|quick hits?|what we (?:saw|learned))"
    r"\b[^.!?]{0,70}$")

# A heading: short, no terminal punctuation, often capitalised or dash-led.
HEADING = re.compile(r"^\s*(?:[-–—]+\s*)?[A-Z][^.!?]{2,80}$")

# Footer furniture that must never join a claim.
FOOTER = re.compile(
    r"(?i)^\s*(?:[a-z .'\-]+ (?:is|has been) (?:a|the) "
    r"(?:senior |staff |beat )?(?:writer|reporter|editor|columnist)\b"
    r"|follow (?:him|her|us|@)"
    r"|(?:you can )?(?:reach|contact|email) (?:him|her|[a-z]+ at)"
    r"|subscribe|sign up|download the"
    r"|(?:more|related|read more)\s*[:›>]"
    r"|\(?photo(?: credit)?[:\)]"
    r")")

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"“(]?[A-Z0-9])")


def lines(text: str) -> list[str]:
    """Article text as lines, with runs of blanks collapsed."""
    out = []
    for raw in (text or "").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if line:
            out.append(line)
    return out


def segments(text: str) -> list[dict]:
    """Structural segments, each a place a claim may live.

    Consecutive plain paragraphs are kept separate rather than merged: two
    paragraphs about two players are two subjects, and joining them is how a
    quotation acquires the wrong speaker.
    """
    out: list[dict] = []
    for idx, line in enumerate(lines(text)):
        if FOOTER.search(line):
            continue                       # never evidence, never context
        kind = "paragraph"
        if SECTION.match(line):
            kind = "heading"
        elif BOUNDARY.match(line):
            kind = "item"
        elif HEADING.match(line) and len(line) < 90:
            kind = "heading"
        out.append({"index": idx, "kind": kind, "text": line})
    return out


def spans(text: str, before: int = 1, after: int = 1) -> list[dict]:
    """Candidate passages: sentence windows that never leave their segment.

    Headings are dropped rather than windowed. They carry no claim of their
    own and their only effect on a window is to attach one item's subject to
    the next item's sentence.
    """
    out: list[dict] = []
    for seg in segments(text):
        if seg["kind"] == "heading":
            continue
        sents = [s.strip() for s in SENT_SPLIT.split(seg["text"]) if s.strip()]
        if not sents:
            continue
        if len(sents) == 1:
            out.append({"location": f"seg{seg['index']}",
                        "text": sents[0], "kind": seg["kind"],
                        "segment_index": seg["index"]})
            continue
        for i in range(len(sents)):
            lo, hi = max(0, i - before), min(len(sents), i + after + 1)
            out.append({"location": f"seg{seg['index']}s{i}",
                        "text": " ".join(sents[lo:hi]).strip(),
                        "kind": seg["kind"],
                        "segment_index": seg["index"]})
    return out
