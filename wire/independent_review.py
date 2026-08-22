"""A second, independent opinion on a generated fantasy assessment.

DARK LAUNCH. Nothing here can publish, approve or alter a record. It writes a
verdict beside the generator's, and a person reads both.

WHY IT IS SEPARATE FROM THE GENERATOR

Asking the same model, with the same prompt, whether it was right produces
agreement, not review. So this is a different prompt with a different job: it
is shown the evidence and the proposed interpretation, and it is asked to
find the reason the interpretation should NOT stand. The generator argues
for; this argues against; a person decides.

It is deliberately asymmetric. AUTO_APPROVE is the hardest verdict to earn --
it requires that the mechanism, direction and subject all follow from the
passage with nothing inferred -- and HUMAN_REVIEW is the default when
anything is unclear. A reviewer that mostly says "looks fine" is not a
reviewer, it is a rubber stamp with a token cost.
"""

from __future__ import annotations

REVIEW_SCHEMA_VERSION = "independent-review-v1"
REVIEW_PROMPT_VERSION = "wire-independent-review-2026-08-22c"

AUTO_APPROVE = "AUTO_APPROVE"
HUMAN_REVIEW = "HUMAN_REVIEW"
REJECT = "REJECT"
ABSTAIN = "ABSTAIN"
VERDICTS = (AUTO_APPROVE, HUMAN_REVIEW, REJECT, ABSTAIN)

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "subject_is_correct", "mechanism_is_supported",
                 "direction_is_supported", "commentary_overstates",
                 "commentary_repeats_evidence", "inference_not_in_evidence",
                 "performance_only_no_role_information",
                 "passage_names_a_different_subject",
                 "disagreement_summary", "confidence"],
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "subject_is_correct": {"type": "boolean"},
        "mechanism_is_supported": {"type": "boolean"},
        "direction_is_supported": {"type": "boolean"},
        "commentary_overstates": {"type": "boolean"},
        "commentary_repeats_evidence": {"type": "boolean"},
        "inference_not_in_evidence": {"type": "boolean"},
        # True when the passage shows a good play and nothing about role,
        # unit, repetition, opportunity or availability.
        "performance_only_no_role_information": {"type": "boolean"},
        # Diagnostic only. Never a rejection signal, never counted in an
        # automation metric. Kept because it is occasionally informative
        # about a passage, and discarded whenever it is about a roster.
        "passage_names_a_different_subject": {"type": "boolean"},
        "disagreement_summary": {"type": "string", "maxLength": 400},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

SYSTEM = """You are reviewing a fantasy-football evidence interpretation that
another system produced. You are not the author of it and you are not being
asked to improve it. Your job is to find the reason it should not stand.

You are shown a passage of beat reporting and an interpretation of it. Decide
whether the interpretation follows from the passage ALONE.

Answer these separately, and answer about the passage, not about football in
general:

subject_is_correct       Is the interpretation about the player it names, or
                         about somebody else the passage happens to mention?
                         A quotation is usually spoken BY one person ABOUT
                         another; the subject is who it is about.
mechanism_is_supported   Does the passage actually show that mechanism? A
                         reception is not a target share. One practice is not
                         a depth-chart change.
direction_is_supported   Does the passage support the direction, or is the
                         direction an assumption about what it will lead to?
commentary_overstates    Does the commentary claim more than the passage
                         carries?
commentary_repeats_evidence  Does the commentary merely restate the passage
                         instead of interpreting it?
inference_not_in_evidence    Does the interpretation add a timetable, a
                         diagnosis, a depth-chart position or a cause that
                         the passage does not state?

Then give a verdict:

AUTO_APPROVE   Everything above is clean and nothing is inferred. This is the
               hardest verdict to earn. Do not give it to be agreeable.
HUMAN_REVIEW   Defensible but something is unclear, borderline or arguable.
               This is the correct default whenever you hesitate.
REJECT         The interpretation does not follow from the passage.
ABSTAIN        The passage is too garbled or too incomplete to judge.

PRACTICE PERFORMANCE IS NOT, BY ITSELF, PUBLISHABLE

A good play is not a fantasy development. A catch, a run, a completion, a
"big day" or a strong session is NO_FANTASY_IMPACT unless the passage also
connects it to at least one of:

  first-team work;
  repeated meaningful usage;
  a role competition;
  depth-chart movement;
  red-zone opportunity;
  material routes, carries or targets;
  a change in availability.

If the passage describes only that a player performed well, say so and do not
approve it. Three cards were proposed on isolated practice performance and a
human rejected two outright and rewrote the third.

PLAYER IDENTITY IS ALREADY SETTLED. DO NOT REVIEW IT.

The player id, name, team and position come from a roster registry and have
already been validated in code. They are not your problem and they are not in
question. It is 2026; players have moved; what you remember about a roster is
not evidence and is not wanted here.

NEVER say a player does or does not play for a team. Never reject, downgrade
or comment on an item because of where you believe a player plays. A previous
run rejected a real development on the grounds that "Kyler Murray does not
play for Minnesota" -- he does, on this registry -- and that objection was
worthless.

THE IDENTITY QUESTION YOU DO ANSWER

Is this passage actually ABOUT the named player, or about somebody else who
appears in it? That is subject_is_correct, and it is a question about the
words in front of you, not about rosters. A passage filed under one player
that materially describes another -- attributed to D'Andre Swift while
describing Roschon Johnson's activation and roster battle -- is a real
subject conflict and belongs in HUMAN_REVIEW.

Judge the passage, never the roster.

Be specific in disagreement_summary. "Looks fine" is not a review."""


def build_prompt(evidence: str, proposed: dict, identity: dict | None = None) -> str:
    """The registry identity, the passage, then the proposal.

    Identity comes first and is labelled authoritative, because the failure it
    prevents is the model rejecting a real 2026 development on the grounds
    that it remembers the player on another team.
    """
    idn = identity or {}
    return (
        "PLAYER IDENTITY -- ALREADY VALIDATED IN CODE, NOT UNDER REVIEW\n"
        "-------------------------------------------------------------\n"
        f"stable player id : {idn.get('player_id', '')}\n"
        f"current team     : {idn.get('team', '')}\n"
        f"position         : {idn.get('position', '')}\n"
        f"roster snapshot  : {idn.get('registry_version', '')}\n"
        f"registry check   : {idn.get('registry_check', 'VERIFIED')}\n"
        f"source team      : {idn.get('source_team', '')}\n"
        f"article          : {idn.get('canonical_url', '')}\n\n"
        "PASSAGE AS REPORTED\n"
        "-------------------\n"
        f"{evidence.strip()}\n\n"
        "PROPOSED INTERPRETATION\n"
        "-----------------------\n"
        f"player:      {proposed.get('player_name', '')} "
        f"({proposed.get('team', '')} {proposed.get('position', '')})\n"
        f"mechanism:   {proposed.get('fantasy_mechanism', '')}\n"
        f"direction:   {proposed.get('direction', '')}\n"
        f"strength:    {proposed.get('impact_strength', '')}\n"
        f"horizon:     {proposed.get('impact_horizon', '')}\n"
        f"commentary:  {proposed.get('fantasy_commentary', '')}\n\n"
        "Does this interpretation follow from the passage alone?")


def enforce(verdict_payload: dict, *, identity_resolved: bool = True) -> dict:
    """Deterministic rules applied AFTER the model, over the top of it.

    The model's verdict is an opinion. These are not.

      * A passage the reviewer says may be about somebody else can never be
        auto-approved. It goes to a human. It is NOT rejected on that basis
        alone -- the reviewer can misread a passage as easily as the
        generator can, and a wrong rejection is as costly as a wrong
        approval, just quieter.
      * A roster objection has no effect at all. Identity is settled in code
        and the model is no longer asked about it; if one arrives anyway it
        is recorded and ignored.
      * An identity the registry cannot resolve blocks automatic publication,
        whatever the model said, because there is no verified player to
        publish about.

    Returns a copy with `verdict`, `enforced` and `enforcement_reasons` set,
    and the model's own answer preserved as `model_verdict`.
    """
    out = dict(verdict_payload or {})
    model_verdict = out.get("verdict")
    out["model_verdict"] = model_verdict
    reasons = []

    if not identity_resolved:
        out["verdict"] = HUMAN_REVIEW
        reasons.append("registry identity unresolved; automatic publication "
                       "is blocked and no verdict is taken from the model")
        out["blocks_automatic_publication"] = True

    elif out.get("passage_names_a_different_subject") is True \
            or out.get("subject_is_correct") is False:
        if model_verdict == AUTO_APPROVE:
            out["verdict"] = HUMAN_REVIEW
            reasons.append("claim-subject conflict: auto-approval blocked, "
                           "routed to human review")
        elif model_verdict == REJECT:
            reasons.append("claim-subject conflict noted; the rejection is "
                           "the model's own, not produced by this rule")

    # A roster objection, if one somehow arrives, changes nothing.
    if out.get("identity_conflicts_with_supplied_registry") is True:
        reasons.append("stale roster objection recorded and ignored; identity "
                       "is validated in code")

    out["enforced"] = out.get("verdict") != model_verdict
    out["enforcement_reasons"] = reasons
    out.setdefault("blocks_automatic_publication",
                   out.get("verdict") != AUTO_APPROVE)
    return out
