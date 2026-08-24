"""The one sentence a Wire card shows in place of the reporter's passage.

WHY THIS IS A FIELD AND NOT A TRUNCATION

The stored evidence is what Claude read, what the validator checked and what
a reviewer approved. It is the record. The card needs something shorter, and
the obvious shortcut -- print the first 180 characters -- produces a sentence
that stops mid-clause, keeps whatever unrelated player happened to be in the
first line, and silently changes what the reporter said. So the summary is a
separate, separately approved field, and `looks_truncated()` exists to catch
anyone who reaches for the shortcut anyway.

WHAT IT MAY SAY

One sentence, in our words, stating only the fantasy-relevant fact. No
quotation, no article narrative, no speculation, and nothing inferred that
the reporter did not report -- not a timetable, not a depth-chart move, not
an injury the source did not name. If the fact cannot be stated without
inferring, the card is not ready to publish.

The summary never replaces the evidence. `wire_evidence.evidence_text` and
the publication's `reporter_found` keep the full passage for review, for
audit and for every future model call.
"""

from __future__ import annotations

import re
import unicodedata

MAX_CHARS = 180

# Hedges and forecasts. A summary states what happened; anything that reaches
# into what it might mean belongs in the Lineup Beat block, where it is
# labelled as our reading rather than the reporter's observation.
#
# With one exception, below: an expectation a named person stated is a fact
# about what that person said. "Steichen said he expects him back soon" is
# reporting; "he is expected back soon" is us guessing with the speaker
# filed off. ATTRIBUTION marks the first kind so it is not refused as the
# second, and a hedge that appears before any attribution still fails.
SPECULATION = re.compile(
    r"\b(may|might|could|should|likely|unlikely|expects?|expected|"
    r"anticipat\w+|projects?|projected|suggests?|hints?|appears? to|"
    r"seems?|reportedly|apparently|is set to|on track|poised|"
    r"in line to|figures to|profiles? as)\b", re.I)

# A timetable or a depth-chart verdict is an inference unless the reporter
# stated it, and the card cannot tell the difference. Kept out of the public
# sentence entirely; the mechanism field already carries the direction.
INFERRED = re.compile(
    r"\b(day[- ]to[- ]day|week[- ]to[- ]week|out for|return date|"
    r"timetable|weeks? away|games? away|questionable|doubtful|"
    r"ruled out|starter'?s? job|won the job|passed \w+ on the depth|"
    r"depth chart|has overtaken|now the (starter|backup|WR\d|RB\d))\b", re.I)

ATTRIBUTION = re.compile(
    r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+"
    r"(?:said|told|explained|announced|confirmed|added|reported)\b")

ANALYSIS_ATTRIBUTION = re.compile(
    r"(?i)\b[A-Z][a-z]+\s+argu(?:e|es|ed)\b|"
    r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\s+argu(?:e|es|ed)\b|"
    r"\b(?:fantasy )?on si\b|\b(?:the article|the author|the analysis)\b"
    r".{0,45}\b(?:argu(?:e|es|ed)|calls?|views?|ranks?|lists?|highlights?|"
    r"recommends?|prefers?|identifies?|says?|expects?|projects?)\b")

QUOTE = re.compile(r"[\"“”]|(?<!\w)'(?=\w[^']{12,})")

# One sentence: at most one terminal mark, and it ends the string. An
# abbreviation like "Jr." is not a sentence break, so the check looks for a
# terminal mark followed by whitespace and a capital.
SENTENCE_BREAK = re.compile(r"[.!?]\s+[A-Z(]")

ABBREV = re.compile(r"\b(Jr|Sr|St|Mr|Dr|No|vs|Inc|Co)\.\s*$", re.I)


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c))


def surname(player_name: str) -> str:
    parts = [p for p in _fold(player_name).split()
             if p.lower().strip(".") not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return parts[-1] if parts else ""


def looks_truncated(summary: str, evidence: str) -> bool:
    """True when the summary is a slice of the passage rather than a rewrite.

    Catches the first-N-characters shortcut and the copy-one-sentence-out
    shortcut, both of which produce a field that is not in our words and is
    not a summary of anything.
    """
    a = " ".join(_fold(summary).lower().split())
    b = " ".join(_fold(evidence or "").lower().split())
    if not a or not b:
        return False
    core = a.rstrip(".").rstrip()
    return bool(core) and core in b


def validate(summary: str, player_name: str = "", evidence: str = "",
             content_type: str = "REPORTING",
             allow_contextual_subject: bool = False) -> list[str]:
    """Everything wrong with this sentence. Empty means it may be published."""
    bad = []
    s = (summary or "").strip()
    if not s:
        return ["no public_evidence_summary"]

    if len(s) > MAX_CHARS:
        bad.append(f"{len(s)} characters; the limit is {MAX_CHARS}")
    if not s.endswith((".", "!", "?")):
        bad.append("does not end a sentence")
    if SENTENCE_BREAK.search(s) and not ABBREV.search(s[:SENTENCE_BREAK.search(s).end()]):
        bad.append("more than one sentence")
    if QUOTE.search(s):
        bad.append("contains a quotation; the summary is in our words")

    if content_type == "FANTASY_ANALYSIS":
        if not ANALYSIS_ATTRIBUTION.search(s):
            bad.append("fantasy analysis is not explicitly attributed")
    else:
        # A hedge is allowed only downstream of a named attribution, and only
        # then: the speaker has to be on the record before the expectation is.
        attributed_from = None
        a = ATTRIBUTION.search(s)
        if a:
            attributed_from = a.end()
        for m in SPECULATION.finditer(s):
            if attributed_from is not None and m.start() >= attributed_from:
                continue
            bad.append(f"speculative language {m.group(0)!r}")
            break
        m = INFERRED.search(s)
        if m:
            bad.append(f"inferred timetable or depth-chart movement {m.group(0)!r}")

    who = surname(player_name)
    if (who and who.lower() not in _fold(s).lower()
            and not allow_contextual_subject):
        bad.append(f"does not name {who}")

    if looks_truncated(s, evidence):
        bad.append("is a slice of the stored evidence, not a summary of it")
    return bad


def check_publication(pub: dict) -> list[str]:
    """Validate a publication's summary, and that the evidence survived."""
    bad = validate(pub.get("public_evidence_summary", ""),
                   pub.get("player_name", ""),
                   pub.get("reporter_found", ""),
                   pub.get("content_type", "REPORTING"),
                   bool(pub.get("summary_subject_context")))
    if not (pub.get("reporter_found") or "").strip():
        bad.append("the stored evidence is missing; the summary may not "
                   "replace it")
    if not pub.get("public_evidence_summary_approved_by"):
        bad.append("the summary has no recorded human approval")
    return bad
