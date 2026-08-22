"""Proof that every stage read the same passage, and that it was whole.

WHY THIS EXISTS

A helper written to shorten the SUPPRESSED list -- where a 220-character
snippet is all a reader needs -- was reused to record the RESULTS list. So the
generator was given the complete span and the reviewer and the review page
were given 220 characters of it. The reviewer then rejected interpretations
for citing "invented" facts that were simply past the cut, and the comparison
between the two passes was measuring the truncation rather than the models.

Nothing detected it, because nothing was checking. A hash is cheap and a
silent disagreement about what the evidence says is not.

WHAT IS CHECKED

Four hashes -- stored, generator input, reviewer input, and what the human was
shown -- must be equal. Any mismatch is REQUIRE_HUMAN and blocks automatic
approval; it does not get repaired, because a repaired hash proves nothing.

Separately, the passage must be whole. Segmentation can cut a window mid-word
or mid-attribution, and a passage that stops at "at quarterba" cannot be
interpreted by anyone, model or person.
"""

from __future__ import annotations

import hashlib
import re

REQUIRE_HUMAN = "REQUIRE_HUMAN"
OK = "OK"

# A conjunction or attribution left dangling means the sentence that carried
# the fact is on the other side of the cut.
DANGLING = re.compile(
    r"\b(and|but|or|because|while|although|though|however|with|that|which|"
    r"who|after|before|when|as|said|told|added|per|via|according to|"
    r"including|such as|so|then|for)\s*$", re.I)

# "... at quarterba" -- a word the cut ran through. A real final word is
# followed by punctuation, or is a short word that can legitimately end a
# clause. Anything else long and bare is a fragment.
ENDS_MIDWORD = re.compile(r"[A-Za-z]{3,}$")

TERMINAL = ".!?\"'”’)]"


def sha256(text: str) -> str:
    """Hash of exactly this text and nothing else.

    Never mix a player id, a prompt, a model name or any other metadata into
    an evidence hash. The question an evidence hash answers is "did every
    stage read the same words", and an identifier folded in makes two stages
    that read identical text look different -- which is what made 37 of 60
    generator hashes unverifiable: semantic.input_hash appends player ids, so
    it cannot double as proof of what the passage said. That hash still
    exists and still has its job; it is simply not this one.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def request_sha256(payload) -> str:
    """Hash of a complete serialized model request. Optional, and separate.

    Useful for proving two runs sent the same prompt, system text and schema.
    Deliberately a different field from the evidence hash so that a prompt
    revision -- which should change this -- cannot be mistaken for evidence
    drift, which should never change.
    """
    import json as _json
    blob = payload if isinstance(payload, str) else _json.dumps(
        payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def completeness(text: str, source_body: str | None = None
                 ) -> tuple[bool, list[str]]:
    """(is_complete, reasons). Reasons name what is wrong, never guess a fix.

    `source_body` makes the check boundary-aware, and it matters more than it
    sounds. A publisher who ends a paragraph without a full stop -- "...best
    suited for immediate depth roles" followed by a newline -- produces a
    passage that IS complete and merely looks cut. Refusing it blocks real
    reporting for the publisher's punctuation. What is never acceptable is a
    cut THROUGH a word, and that is what the source tells us apart: look at
    the character that follows the passage in the article body.
    """
    t = (text or "").rstrip()
    bad, notes = [], []
    if not t:
        return False, ["empty"]

    # Where does this passage sit in the article, and what follows it?
    after = None
    if source_body:
        i = source_body.find(t)
        if i >= 0:
            after = source_body[i + len(t):i + len(t) + 1] or "\n"

    if t[-1] not in TERMINAL:
        at_boundary = after in ("\n", "\r", "") if after is not None else False
        if at_boundary:
            pass          # the publisher ended a paragraph without a full stop
        elif after is not None and after.isalnum():
            bad.append(f"cut through a word -- the article continues "
                       f"{after!r}: {t[-24:]!r}")
        elif ENDS_MIDWORD.search(t):
            bad.append(f"ends mid-word or without punctuation: {t[-24:]!r}")
        else:
            bad.append(f"no terminal punctuation: {t[-24:]!r}")

    # A dangling conjunction only dangles if nothing closes the sentence.
    #
    # This tested t.rstrip(TERMINAL), which deletes the full stop before
    # looking -- so "...they back this team," Glenn said.' became
    # '...Glenn said' and matched. That is the single most common shape in
    # beat reporting, a complete attributed quotation, and the rule refused
    # 95 of them. Only look when the passage really has no terminal mark.
    if t[-1] not in TERMINAL and DANGLING.search(t):
        bad.append(f"ends on a dangling conjunction or attribution: "
                   f"{t[-24:]!r}")

    # Unbalanced quotation marks are NOT on their own a truncation.
    #
    # A window that begins or ends inside a longer quotation carries one mark
    # without its partner, and the prose inside it can still be a complete,
    # readable sentence -- "...playing for No. 3.”" and "...appears wide
    # open." both are. Refusing on the count alone rejected 184 rows, mostly
    # windowing artefacts.
    #
    # What actually matters is whether the passage stops mid-sentence. So an
    # imbalance is recorded as a NOTE, and only becomes a refusal when the
    # passage also fails to close its final sentence -- which is the case
    # where a reader genuinely cannot tell where the quotation ended.
    straight = t.count('"')
    curly_open, curly_close = t.count("“"), t.count("”")
    imbalance = []
    if straight % 2 == 1:
        imbalance.append("unmatched straight quotation mark")
    if curly_open != curly_close:
        imbalance.append(f"unmatched curly quotation marks "
                         f"({curly_open} open, {curly_close} close)")
    if imbalance:
        # Same boundary logic the punctuation check uses, and for the same
        # reason: a caption line -- '"...really cool." - TE Jack Endries on
        # catching his first career touchdown' -- ends at the end of the
        # article with no full stop and an opening quote in a prior window.
        # It is complete and readable. Blocking it applied one rule to the
        # punctuation and a different, stricter one to the quotation marks.
        at_boundary = after in ("\n", "\r", "") if after is not None else False
        if t[-1] not in TERMINAL and not at_boundary:
            bad.extend(imbalance)
        else:
            notes.extend(x + " (window artefact; the passage ends cleanly)"
                         for x in imbalance)

    # A capitalised token at the very end with no punctuation is usually half
    # a name: "... and running back Racha".
    m = re.search(r"\b([A-Z][a-z]+)$", t)
    if m and t[-1] not in TERMINAL and not (
            source_body and after in ("\n", "\r", "")):
        bad.append(f"ends on what looks like a partial name: {m.group(1)!r}")

    return (not bad), (bad + notes)


def check(stored: str, *, generator_input: str | None = None,
          reviewer_input: str | None = None,
          human_display: str | None = None,
          start: int | None = None, end: int | None = None,
          generator_request: str | None = None,
          reviewer_request: str | None = None,
          source_body: str | None = None) -> dict:
    """The integrity record for one candidate.

    Every field the reviewer asked to see, plus the verdict. A missing input
    is not treated as matching: a stage that cannot say what it read is a
    stage that has to be re-run.
    """
    h = sha256(stored)
    complete, why = completeness(stored, source_body)
    rec = {
        "evidence_sha256": h,
        "evidence_chars": len(stored or ""),
        "segment_start": start,
        "segment_end": end,
        "evidence_complete": complete,
        "incompleteness_reasons": why,
        "generator_input_evidence_sha256":
            sha256(generator_input) if generator_input is not None else None,
        "reviewer_input_evidence_sha256":
            sha256(reviewer_input) if reviewer_input is not None else None,
        "human_display_evidence_sha256":
            sha256(human_display) if human_display is not None else None,
        # Optional, and NOT part of the four-way evidence comparison.
        "generator_request_sha256":
            request_sha256(generator_request) if generator_request else None,
        "reviewer_request_sha256":
            request_sha256(reviewer_request) if reviewer_request else None,
    }
    # Exactly the four evidence hashes. The request hashes are excluded by
    # name rather than by pattern, so adding another *_sha256 field later
    # cannot quietly join the equality test.
    FOUR = ("evidence_sha256", "generator_input_evidence_sha256",
            "reviewer_input_evidence_sha256", "human_display_evidence_sha256")
    seen = {k: rec[k] for k in FOUR}
    mismatched = sorted(k for k, v in seen.items() if v is not None and v != h)
    missing = sorted(k for k, v in seen.items() if v is None)
    rec["hashes_match"] = not mismatched and not missing
    rec["hash_mismatches"] = mismatched
    rec["hashes_not_recorded"] = missing
    rec["status"] = OK if (rec["hashes_match"] and complete) else REQUIRE_HUMAN
    rec["blocks_automatic_approval"] = rec["status"] == REQUIRE_HUMAN
    return rec
