"""Check a model's answer against the passage it was given.

Nothing a provider returns is trusted. Every claim is re-checked against the
evidence text, the player registry and the same authority rules the rest of
the pipeline obeys, and any failure downgrades the answer to ABSTAIN for a
human rather than being repaired. Repairing a wrong answer would hide the
error rate, which is the number this whole exercise exists to measure.

The checks are ordered by what they protect. Substring first, because an
answer quoting text that is not in the passage is not about the passage at
all. Then identity, then the specific transfers that went wrong in review: a
pronoun's reps landing on the wrong player, an absent player inheriting his
replacement's targets, a quote speaker inheriting a claim about someone else.
"""

from __future__ import annotations

import re

from . import players as pl
from . import semantic as sem

RETURN_LANG = re.compile(
    r"(?i)\b(returned to (?:the )?practice|back (?:at|on|in) (?:the )?"
    r"(?:practice|field)|practiced (?:again|for the first time)|"
    r"began practi[cs]ing|"
    r"participated again|was (?:a )?full participant|cleared to (?:practice|return)|"
    r"activated (?:off|from)|came off (?:the )?(?:pup|nfi))\b")

NEVER_RETURN = re.compile(
    r"(?i)\b(waived|released|cut|signed|claimed off waivers|traded|"
    r"did not (?:practice|participate)|missed (?:practice|the session)|"
    r"reaggravated|re-?injured|remains? (?:out|sidelined)|"
    r"has (?:not|yet to) (?:practiced|participated))\b")

ABSENCE = re.compile(
    r"(?i)\b(with(?:out)? no |absent|did not (?:practice|participate)|"
    r"sidelined|out of practice|missing|in a walking boot|held out|"
    r"was limited)\b")

UNIT = re.compile(
    r"(?i)\b(first|second|third|1st|2nd|3rd)[-\s]team\b|\bwith the "
    r"(?:ones|twos|threes|1s|2s|3s|starters)\b")

RELAY = re.compile(
    r"(?i)\b(the athletic|espn|nfl network|cbs sports|fox sports|"
    r"pro football focus|pff)\b\s*(?:'s|’s)?|\b(?:per|according to|via)\s+"
    r"[A-Z][a-z]{2,}|\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\s+"
    r"(?:reported|wrote|tweeted)\b")

# Mechanisms that transfer opportunity. An absent player may never hold one.
OPPORTUNITY = {"FIRST_TEAM_REPS", "SECOND_TEAM_REPS", "THIRD_TEAM_REPS",
               "TARGETS", "CARRIES", "ROUTES", "SNAP_SHARE", "RED_ZONE",
               "ROLE_EXPANSION", "DEPTH_CHART"}

AVAILABILITY = {"LIMITED_PARTICIPATION", "RETURN_TO_PRACTICE", "INJURY",
                "TRANSACTION"}

# These facts are often available in an article title or other source
# metadata, but metadata is not the passage.  Generated editorial text may
# use them only when the evidence itself supplies them.
PASSAGE_CONTEXT = {
    "preseason": re.compile(r"(?i)\bpre[- ]?season\b"),
    "regular season": re.compile(r"(?i)\bregular[- ]season\b"),
    "training camp": re.compile(r"(?i)\btraining camp\b"),
    "joint practice": re.compile(r"(?i)\bjoint practices?\b"),
}

UNVERIFIED_METADATA = re.compile(
    r"(?i)\b(?:unverified (?:source )?metadata|"
    r"(?:source )?metadata (?:is|was|lists?|shows?) (?:as )?unverified|"
    r"evidence access (?:is|was|lists?|shows?) (?:as )?unverified|"
    r"evidentiary status (?:is|was) unverified)\b")

NEGATED_CONTEXT = re.compile(
    r"(?i)(?:(?:does|did|is|was|has|have|had|can|could|would|will)\s+not|"
    r"without|no evidence (?:that|of|for)?)\s+(?:\w+[\s-]+){0,6}$")

DIAGNOSIS = re.compile(
    r"(?i)\b(?:soft[- ]tissue injury|concussion|fractur(?:e|ed)|"
    r"sprain(?:ed)?|strain(?:ed)?|torn\s+(?:acl|mcl|achilles|hamstring)|"
    r"diagnos(?:is|ed)|dislocat(?:ion|ed))\b")

ATTRIBUTION = re.compile(
    r"(?i)\b(?:said|says|told|reported|according to|per|announced|"
    r"confirmed|listed|designated|ruled)\b")


QUOTE_FAILURE = "supporting_quote is not an exact substring of the evidence"

SMART = {"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
         "\u2013": "-", "\u2014": "-", "\u2026": "..."}


def _norm(s: str) -> str:
    """Fold typography, then whitespace. Nothing else.

    Publishers use curly quotes and dashes; a model reproducing a passage
    faithfully may return straight ones. That difference says nothing about
    whether the quotation came from the passage, which is what this check
    exists to prove.
    """
    t = s or ""
    for a, b in SMART.items():
        t = t.replace(a, b)
    return " ".join(t.lower().split())


def _quote_in(quote: str, segment: str) -> bool:
    """Is this quotation drawn from the passage, word for word?

    A dangling delimiter is tolerated and nothing else is. Segmentation cuts
    mid-quotation, so a passage can end without its closing quotation mark
    and a model reproducing it adds one back: the Joe Burrow quotation was
    132 characters against a 131-character passage, identical but for a
    trailing '"'. The words must still match exactly.
    """
    q, seg = _norm(quote), _norm(segment)
    if q in seg:
        return True
    trimmed = q.strip("\"' ")
    return bool(trimmed) and trimmed in seg


def _words(text: str) -> set:
    """Tokens with punctuation stripped.

    pl.norm leaves commas attached, so "included Sam LaPorta, Mekhi Wingo"
    tokenised as "laporta," and the surname check failed. Every name in a
    list failed it -- and a list is exactly how a practice absence is
    reported, so the bug silently suppressed the most actionable evidence
    the wire produces.
    """
    return {w.strip(".,;:!?()[]'\"") for w in pl.norm(text or "").split()}


def _last(name: str) -> str:
    n = pl.norm(name or "").split()
    return n[-1].strip(".,;:!?()[]'\"") if n else ""


def _asserts_context(text: str, pattern: re.Pattern) -> bool:
    """True when a context phrase is asserted, not explicitly disclaimed."""
    for match in pattern.finditer(text or ""):
        prefix = (text or "")[max(0, match.start() - 100):match.start()]
        if NEGATED_CONTEXT.search(prefix):
            continue
        return True
    return False


def _sentence_at(text: str, offset: int) -> str:
    """The sentence-like clause containing offset, for local attribution."""
    before = max(text.rfind(mark, 0, offset) for mark in ".!?\n") + 1
    ends = [text.find(mark, offset) for mark in ".!?\n"]
    ends = [end for end in ends if end >= 0]
    after = min(ends) if ends else len(text)
    return text[before:after]


def validate(a: sem.SemanticAssessment, segment: str, players: list,
             registry, meta: dict | None = None) -> list[str]:
    """Reasons this answer may not be used. Empty means it stands."""
    meta = meta or {}
    bad: list[str] = []
    seg_norm = _norm(segment)

    if a.decision not in sem.DECISIONS:
        bad.append(f"unknown decision {a.decision!r}")

    # Ground every generated editorial field before any decision-specific
    # early return.  ABSTAIN and NO_FANTASY_IMPACT still reach review output,
    # so invented context in either outcome must remain visible as a
    # validation failure rather than escaping because no card was proposed.
    generated = " ".join([
        a.fantasy_commentary or "",
        a.why_it_matters or "",
        " ".join(a.limitations or []),
        a.abstention_reason or "",
    ])
    if (UNVERIFIED_METADATA.search(generated)
            and not meta.get("evidence_access")):
        bad.append("generated text claims unverified source metadata that "
                   "was not supplied")
    for label, pattern in PASSAGE_CONTEXT.items():
        if (_asserts_context(generated, pattern)
                and not pattern.search(segment)):
            bad.append(f"generated text adds {label} context absent from the "
                       "evidence")

    if a.decision == sem.ABSTAIN:
        return bad                      # abstention needs no further proof

    # 1. The quote must be in the passage. An answer that cites text the
    #    passage does not contain is not an answer about this passage.
    if not a.supporting_quote:
        bad.append("no supporting_quote")
    elif not _quote_in(a.supporting_quote, segment):
        bad.append(QUOTE_FAILURE)

    # A response that says INTERPRET while naming NO_FANTASY_IMPACT as its
    # mechanism is telling us two different things. Read it as the answer it
    # gave about the football, not the label it put on the envelope.
    if (a.decision == sem.INTERPRET
            and a.fantasy_mechanism == "NO_FANTASY_IMPACT"):
        a.decision = sem.NO_FANTASY_IMPACT

    if (a.decision == sem.INTERPRET
            and a.fantasy_mechanism in {"PERFORMANCE", "OTHER"}):
        # Do not repair a wrong model judgement: that would hide its error
        # rate.  Fail closed to ABSTAIN in enforce().  The prompt tells the
        # model to return NO_FANTASY_IMPACT when performance establishes no
        # concrete role, usage, opportunity or availability mechanism.
        bad.append(f"{a.fantasy_mechanism} is not a publishable fantasy "
                   "mechanism; use a concrete mechanism or "
                   "NO_FANTASY_IMPACT")

    if a.decision == sem.NO_FANTASY_IMPACT:
        return bad

    supporting_classes = {
        "FIRSTHAND_OBSERVATION", "DIRECT_QUOTATION",
        "OFFICIAL_DESIGNATION",
    }
    if (a.decision == sem.INTERPRET
            and a.evidence_classification not in supporting_classes):
        bad.append(f"{a.evidence_classification} may not support a fantasy "
                   "interpretation")

    # A reporter can observe a player leave hurt, but a diagnosis needs a
    # named attribution or official designation.  In a mixed passage, a quote
    # from another player cannot lend authority to a later unattributed
    # medical assertion.
    if a.evidence_classification == "FIRSTHAND_OBSERVATION":
        diagnosis = DIAGNOSIS.search(a.supporting_quote or segment)
        if diagnosis:
            sentence = _sentence_at(a.supporting_quote or segment,
                                    diagnosis.start())
            if not ATTRIBUTION.search(sentence):
                bad.append("unattributed diagnosis classified as firsthand")

    # 2. Identity. The subject must be a real registry player, matched in
    #    this passage, with the team and position we stored.
    by_id = {p["player_id"]: p for p in players}
    pid = a.claim_subject_player_id
    if not pid:
        bad.append("INTERPRET with no claim subject id")
        return bad
    if pid not in by_id:
        bad.append(f"claim subject {pid} was not matched in this passage")
        return bad
    if registry is not None and pid not in registry.by_id:
        bad.append(f"claim subject {pid} is not in the player registry")
        return bad
    ctx = by_id[pid]
    reg_player = registry.by_id.get(pid) if registry else None
    if reg_player is not None:
        if reg_player.team != ctx.get("team"):
            bad.append(f"registry team {reg_player.team} != stored "
                       f"{ctx.get('team')}")
        if reg_player.position != ctx.get("position"):
            bad.append(f"registry position {reg_player.position} != stored "
                       f"{ctx.get('position')}")

    # The id and the name must agree. A response whose name says one player
    # and whose id says another is not a near miss to be resolved in the
    # model's favour -- it is an answer we cannot attribute, and picking
    # either half would be guessing which one it meant.
    claimed_name = a.claim_subject_player_name or ""
    if claimed_name and _last(claimed_name) != _last(ctx.get("player_name", "")):
        bad.append(f"claim subject name {claimed_name!r} does not match the "
                   f"player its id points to ({ctx.get('player_name')!r})")
        return bad

    subject_last = _last(ctx.get("player_name", ""))

    # 3. The subject must actually appear in the quoted evidence, or be
    #    linked to it by a pronoun the model resolved and we can see.
    named_in_quote = subject_last and subject_last in _words(a.supporting_quote)
    pronoun_ok = False
    for pa in a.pronoun_antecedents:
        if _last(pa.get("resolved_to", "")) != subject_last:
            continue
        support = _norm(pa.get("supporting_text", ""))
        if support and support in seg_norm:
            pronoun_ok = True
    if not (named_in_quote or pronoun_ok):
        bad.append("the claim subject is neither named in the supporting "
                   "quote nor linked by a validated pronoun")

    # 4. A quote speaker may not inherit a claim about somebody else.
    speaker_last = _last(a.quote_speaker or "")
    if (a.evidence_classification == "DIRECT_QUOTATION" and speaker_last
            and speaker_last == subject_last):
        quoted = " ".join(re.findall(r"[\"“]([^\"“”]{6,})"
                                     r"[\"”]", segment))
        others = [p for p in players if _last(p["player_name"]) != subject_last
                  and _last(p["player_name"]) in _words(quoted)]
        if others:
            bad.append("the speaker is credited with a claim his own words "
                       "make about another player")

    # 5. Absence must not be read as opportunity, and the absent player must
    #    not hold the work that moved to his replacement.
    roles = {_last(m.get("player_name", "")): m.get("relationship")
             for m in a.mentioned_players}
    subject_role = roles.get(subject_last)
    if subject_role == "ABSENT_PLAYER" and a.fantasy_mechanism in OPPORTUNITY:
        bad.append(f"an absent player cannot hold {a.fantasy_mechanism}")
    if subject_role == "ABSENT_PLAYER" and a.direction == "POSITIVE":
        bad.append("an absent player was given a positive direction")

    # 6. Availability direction, checked against the words rather than the
    #    label. A waived or missing player is never a return.
    if a.fantasy_mechanism == "RETURN_TO_PRACTICE":
        if not RETURN_LANG.search(segment):
            bad.append("RETURN_TO_PRACTICE without explicit return language")
        if NEVER_RETURN.search(a.supporting_quote or segment):
            bad.append("RETURN_TO_PRACTICE over language that means the "
                       "opposite (waived, missing, reaggravated)")
        if a.direction == "NEGATIVE":
            bad.append("RETURN_TO_PRACTICE marked NEGATIVE")
    if a.fantasy_mechanism == "LIMITED_PARTICIPATION" and a.direction == "POSITIVE":
        bad.append("an absence or limitation marked POSITIVE")

    # 7. A unit claim needs unit language in the quote, not merely nearby.
    if a.fantasy_mechanism in ("FIRST_TEAM_REPS", "SECOND_TEAM_REPS",
                               "THIRD_TEAM_REPS"):
        if not UNIT.search(a.supporting_quote or ""):
            bad.append("a unit claim whose supporting quote contains no unit "
                       "language")

    # 8. Relayed reporting stays relayed.
    if RELAY.search(segment) and a.evidence_classification in (
            "FIRSTHAND_OBSERVATION", "DIRECT_QUOTATION"):
        bad.append("relayed reporting classified as firsthand or a direct "
                   "quotation")

    # 9. Commentary may not introduce facts. Numbers are the cheap tell.
    # Only statistics count. An ordinal -- "No. 1 offense", "second-team",
    # "11 personnel" -- is describing a unit, not asserting a measurement,
    # and banning it rejected correct commentary because one sampling wrote
    # "No. 1 quarterback" where another wrote "first-string".
    seg_numbers = set(re.findall(r"\b\d+\b", segment))
    STAT = re.compile(
        r"\b(\d+)\s*(?:%|percent|targets?|carries|catches|receptions?|"
        r"yards?|touchdowns?|snaps?|reps?|games?|weeks?|days?|points?)\b"
        r"|\b(\d{2,})\b", re.I)
    for m in STAT.finditer(a.fantasy_commentary or ""):
        n = m.group(1) or m.group(2)
        if n and n not in seg_numbers:
            bad.append(f"commentary states a figure ({n}) absent from the "
                       f"evidence")
    # Banning the word "projection" outright rejected commentary that
    # correctly said a projection should NOT change, which is exactly the
    # sentence we want. What is forbidden is asserting a change or a ranking,
    # not disclaiming one.
    banned = re.compile(r"(?i)\b(adp|ranking|ranked|sleeper|bust|"
                        r"start him|sit him|waiver wire)\b")
    asserted_projection = re.compile(
        r"(?i)(?<!no )(?<!not )(?<!does not )(?<!without )"
        r"\b(we (?:have )?(?:raised|lowered|changed|updated)|"
        r"projection (?:has|was) (?:changed|updated|raised|lowered)|"
        r"projected points (?:rise|fall|increase|decrease))\b")
    if banned.search(a.fantasy_commentary or ""):
        bad.append("commentary refers to rankings, ADP or waiver advice")
    if asserted_projection.search(a.fantasy_commentary or ""):
        bad.append("commentary asserts a projection change")
    for phrase in ("worth monitoring", "may affect his value",
                   "opportunity side of his value"):
        if phrase in (a.fantasy_commentary or "").lower():
            bad.append(f"commentary uses the banned filler {phrase!r}")

    # 10. Corroboration and ceilings, unchanged from the deterministic rules.
    if meta.get("duplicate_of") and a.impact_strength != "LOW":
        bad.append("a duplicate of another report was given more than LOW")
    if (meta.get("source_ownership") == "TEAM_OWNED"
            and a.impact_strength == "HIGH"
            and a.fantasy_mechanism != "TRANSACTION"):
        bad.append("a team-owned observation was given HIGH")

    # HIGH survives from the deterministic rules and was not carried into
    # this validator when the model took over: Claude gave HIGH to a single
    # practice observation of Anthony Richardson running the second team.
    # One reporter, one article, is not a material confirmation whatever the
    # observation is.
    if a.impact_strength == "HIGH":
        official = a.fantasy_mechanism == "TRANSACTION" or re.search(
            r"(?i)\b(placed (?:\w+ )?on (?:ir|injured reserve)|waived|released|"
            r"signed|activated|named (?:the )?starter|ruled out for the "
            r"(?:season|year)|torn (?:acl|achilles))\b", segment)
        corroborated = int(meta.get("independent_source_count") or 1) >= 2
        if not (official or corroborated):
            bad.append("HIGH from a single uncorroborated observation with no "
                       "official act")

    # A fantasy interpretation is for fantasy positions. Defensive players,
    # linemen and kickers are team context.
    if reg_player is not None and reg_player.position not in (
            "QB", "RB", "WR", "TE"):
        bad.append(f"{reg_player.position} may not receive individual "
                   f"fantasy commentary")
    if a.impact_strength == "HIGH" and a.projection_action != "UPDATE_RECOMMENDED":
        pass                              # allowed: HIGH need not force action
    if a.projection_action == "UPDATE_RECOMMENDED" and a.impact_strength == "LOW":
        bad.append("UPDATE_RECOMMENDED on LOW evidence")
    return bad


def evaluate_with_retry(provider, segment, meta, players, registry):
    """Assess, and on an inexact quotation only, retry the quotation once.

    A wrong quotation is a transcription failure, not a judgement failure --
    Chris Blair and Joe Burrow both abstained on it while their football
    reading was never examined. Every other validation failure still
    abstains without a second chance, because those are judgements and a
    retry would be asking the model to try again until it agrees with us.

    Both attempts are stored.
    """
    a = provider.evaluate(segment, meta, players)
    first_fails = validate(a, segment, players, registry, meta)
    a.attempts = [{"attempt": 1, "supporting_quote": a.supporting_quote,
                   "validation_failures": list(first_fails),
                   "tokens_in": a.tokens_in, "tokens_out": a.tokens_out,
                   "latency_ms": a.latency_ms}]

    only_quote = first_fails and all(f == QUOTE_FAILURE for f in first_fails)
    if only_quote and hasattr(provider, "retry_quote"):
        quote, info = provider.retry_quote(segment, a)
        a.retry_attempted = True
        a.retry_reason = QUOTE_FAILURE
        if quote:
            a.supporting_quote = quote
            a.tokens_in += info.get("input_tokens", 0)
            a.tokens_out += info.get("output_tokens", 0)
            a.latency_ms += info.get("latency_ms", 0)
            a.cost_usd += (info.get("input_tokens", 0) * 3.00 / 1_000_000
                           + info.get("output_tokens", 0) * 15.00 / 1_000_000)
        a.attempts.append({"attempt": 2, "supporting_quote": quote or "",
                           "error": info.get("error", ""),
                           "tokens_in": info.get("input_tokens", 0),
                           "tokens_out": info.get("output_tokens", 0),
                           "latency_ms": info.get("latency_ms", 0)})

    a = enforce(a, segment, players, registry, meta)
    if getattr(a, "retry_attempted", False) and a.validation_failures:
        # Still wrong after the one retry: pending for a person, not a
        # third attempt and not a rules-engine substitute.
        a.abstention_reason = (
            f"quote retry did not produce an exact substring; "
            f"{a.abstention_reason or ''}")[:400]
    return a


def enforce(a: sem.SemanticAssessment, segment: str, players: list,
            registry, meta: dict | None = None) -> sem.SemanticAssessment:
    """Validate, and on any failure downgrade to ABSTAIN for a human.

    Never repaired. A repaired answer is an unmeasured answer, and the error
    rate is the whole point.
    """
    fails = validate(a, segment, players, registry, meta)
    a.validation_failures = fails
    if fails:
        a.decision = sem.ABSTAIN
        a.abstention_reason = "; ".join(fails[:3])
        a.fantasy_mechanism = "NO_FANTASY_IMPACT"
        a.projection_action = "NONE"
    return a
