"""Independent, non-publishing review of a semantic assessment.

Code owns identity.  The reviewer sees the registry identity as context but
is never asked whether the player's team or position is correct.  It judges
only whether the passage is about that player and whether the generated
fantasy interpretation follows from the passage.
"""

from __future__ import annotations

import hashlib
import json

PROMPT_VERSION = "wire-independent-review-2026-08-23d"
SCHEMA_VERSION = "independent-review-v2"

VERDICTS = {"AUTO_APPROVE", "HUMAN_REVIEW", "REJECT", "ABSTAIN"}

RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "subject_is_correct",
                 "evidence_classification_is_supported",
                 "mechanism_is_supported",
                 "direction_is_supported", "commentary_overstates",
                 "commentary_repeats_evidence", "inference_not_in_evidence",
                 "performance_only_no_role_information",
                 "passage_names_a_different_subject", "disagreement_summary"],
    "properties": {
        "verdict": {"type": "string", "enum": sorted(VERDICTS)},
        "subject_is_correct": {"type": "boolean"},
        "evidence_classification_is_supported": {"type": "boolean"},
        "mechanism_is_supported": {"type": "boolean"},
        "direction_is_supported": {"type": "boolean"},
        "commentary_overstates": {"type": "boolean"},
        "commentary_repeats_evidence": {"type": "boolean"},
        "inference_not_in_evidence": {"type": "boolean"},
        "performance_only_no_role_information": {"type": "boolean"},
        "passage_names_a_different_subject": {"type": "boolean"},
        "disagreement_summary": {"type": "string"},
    },
}

# Added by the trusted provider wrapper after its strict schema validation.
# These are provenance, not model-authored semantic fields.
PROVIDER_PROVENANCE_FIELDS = {
    "provider", "model", "tokens_in", "tokens_out", "cost_usd", "latency_ms",
}

SYSTEM = """You independently review one proposed fantasy-football Wire item.

The supplied player identity was resolved and validated in code. It is not a
question for you. Do not use outside roster knowledge and do not decide
whether the team or position is current.

Judge only the passage and the proposed assessment. A real claim-subject
conflict is semantic: a passage filed under D'Andre Swift may materially
describe Roschon Johnson. Flag that with passage_names_a_different_subject.

Review the proposed evidence_classification before judging the fantasy
mechanism. FIRSTHAND_OBSERVATION, DIRECT_QUOTATION and OFFICIAL_DESIGNATION
may support an interpretation. ANALYSIS_OR_OPINION, RELAYED_REPORTING and
UNCERTAIN may not. Set evidence_classification_is_supported false when the
proposed class does not match the passage. If ANALYSIS_OR_OPINION is the
supported class, a NO_FANTASY_IMPACT decision may correctly enforce the
authority boundary even when the author's opinion describes a meaningful
role.

An isolated positive practice performance does not support publication unless
it establishes meaningful role, unit, repetition, opportunity, availability,
first-team work, competition, red-zone work, or material routes, carries or
targets. One good catch, run or throw without that information is
NO_FANTASY_IMPACT.

A reserve depth-chart listing is not automatically positive. Being described
as the third running back behind two others is not actionable unless the
passage establishes a promotion, changed competition, meaningful workload or
another concrete path to opportunity.

AUTO_APPROVE means the subject, mechanism, direction and commentary are all
supported without an unstated inference. HUMAN_REVIEW means ambiguous.
REJECT means the proposed card is affirmatively unsupported. ABSTAIN means you
cannot make the review.
"""


def evidence_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def validate_response(payload: dict) -> list[str]:
    """Small deterministic check in addition to the provider's tool schema."""
    required = set(RESPONSE_SCHEMA["required"])
    allowed = set(RESPONSE_SCHEMA["properties"])
    keys = set(payload)
    errors = []
    if required - keys:
        errors.append("missing fields: " + ", ".join(sorted(required - keys)))
    if keys - allowed:
        errors.append("unexpected fields: " + ", ".join(sorted(keys - allowed)))
    if payload.get("verdict") not in VERDICTS:
        errors.append("invalid verdict")
    for key in required - {"verdict", "disagreement_summary"}:
        if key in payload and not isinstance(payload[key], bool):
            errors.append(f"{key} must be boolean")
    if "disagreement_summary" in payload and not isinstance(
            payload["disagreement_summary"], str):
        errors.append("disagreement_summary must be string")
    return errors


def build_prompt(evidence_text: str, identity: dict, assessment: dict) -> str:
    identity = {k: identity.get(k) for k in
                ("player_id", "player_name", "team", "position")}
    proposed = {k: assessment.get(k) for k in
                ("decision", "claim_subject_player_id",
                 "claim_subject_player_name", "supporting_quote",
                 "evidence_classification",
                 "fantasy_mechanism", "direction", "impact_strength",
                 "impact_horizon", "projection_action",
                 "fantasy_commentary", "why_it_matters", "limitations")}
    return ("VERIFIED REGISTRY IDENTITY (context, not under review)\n" +
            json.dumps(identity, ensure_ascii=False, sort_keys=True) +
            "\n\nCOMPLETE EVIDENCE\n\"\"\"" + evidence_text +
            "\"\"\"\n\nPROPOSED ASSESSMENT\n" +
            json.dumps(proposed, ensure_ascii=False, sort_keys=True))


def enforce(model_payload: dict, *, identity_resolved: bool,
            integrity_ok: bool, proposed_assessment: dict | None = None) -> dict:
    """Apply non-model safety rules while preserving the model verdict."""
    allowed = set(RESPONSE_SCHEMA["properties"])
    semantic_payload = {key: value for key, value in model_payload.items()
                        if key in allowed}
    schema_errors = validate_response(semantic_payload)
    unexpected = (set(model_payload) - allowed - PROVIDER_PROVENANCE_FIELDS)
    if unexpected:
        schema_errors.append(
            "unexpected fields: " + ", ".join(sorted(unexpected)))
    original = model_payload.get("verdict", "ABSTAIN")
    effective = original if original in VERDICTS else "ABSTAIN"
    reasons = []
    if schema_errors:
        effective = "HUMAN_REVIEW"
        reasons.extend(schema_errors)
    if not identity_resolved:
        effective = "HUMAN_REVIEW"
        reasons.append("registry identity unresolved; model call prohibited")
    if not integrity_ok:
        effective = "HUMAN_REVIEW"
        reasons.append("evidence integrity not verified")
    # AUTO_APPROVE is a conjunction, not an independent label.  The first
    # live OpenAI review returned AUTO_APPROVE while also saying the passage
    # contained performance only and no role information.  Preserve the
    # model's contradictory verdict for audit, but never let it become the
    # effective verdict.
    auto_approval_blockers = (
        (model_payload.get("subject_is_correct") is not True,
         "reviewer did not confirm the claim subject"),
        (model_payload.get("evidence_classification_is_supported") is not True,
         "reviewer did not confirm the evidence classification"),
        (model_payload.get("mechanism_is_supported") is not True,
         "reviewer did not confirm the fantasy mechanism"),
        (model_payload.get("direction_is_supported") is not True,
         "reviewer did not confirm the direction"),
        (model_payload.get("commentary_overstates") is True,
         "reviewer says the commentary overstates the evidence"),
        (model_payload.get("inference_not_in_evidence") is True,
         "reviewer found an inference absent from the evidence"),
        (model_payload.get("performance_only_no_role_information") is True
         and (proposed_assessment or {}).get("decision")
             != "NO_FANTASY_IMPACT",
         "performance-only evidence with no role information blocks automatic "
         "approval of an interpretation"),
        (model_payload.get("passage_names_a_different_subject") is True,
         "claim-subject conflict blocks automatic approval"),
    )
    if effective == "AUTO_APPROVE":
        blockers = [reason for blocked, reason in auto_approval_blockers if blocked]
        if blockers:
            effective = "HUMAN_REVIEW"
            reasons.extend(blockers)
    # Never manufacture REJECT. A model REJECT remains visibly the model's.
    return {**model_payload, "model_verdict": original,
            "effective_verdict": effective,
            "enforcement_reasons": reasons,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION}
