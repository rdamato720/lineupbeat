"""The deterministic baseline, wrapped in the semantic interface.

This is what the models are measured against. It is kept honestly: no new
regular expressions were added to make it look better, because the point of
the comparison is to show where surface patterns stop working. It is
expected to fail the pronoun and beneficiary cases -- those failures are the
argument for the layer above it.
"""

from __future__ import annotations

import time

from .. import claims as cl
from .. import evidence as ev
from .. import semantic as sem


class RulesSemanticProvider(sem.FantasySemanticProvider):
    name = "rules"
    model = "deterministic"

    def evaluate(self, evidence_segment, article_metadata, matched_players):
        t0 = time.time()
        meta = article_metadata or {}
        players = matched_players or []
        a = sem.SemanticAssessment(
            provider=self.name, model=self.model,
            input_hash=sem.input_hash(evidence_segment, players))

        klass, conf, why = ev.classify(
            evidence_segment,
            reporter_voice=bool(meta.get("reporter_voice")),
            auto_captions=False, multi_speaker=False)
        a.evidence_classification = klass
        a.confidence = conf

        subject = None
        mech = {"mechanism": cl.NO_FANTASY_IMPACT, "direction": "NEUTRAL",
                "detail": ""}
        for p in players:
            got = cl.fantasy_mechanism(
                evidence_segment, p["player_name"], klass,
                speaker=meta.get("author", ""))
            if got["mechanism"] != cl.NO_FANTASY_IMPACT:
                subject, mech = p, got
                break

        a.mentioned_players = [
            {"player_id": p["player_id"], "player_name": p["player_name"],
             "relationship": ("CLAIM_SUBJECT"
                              if subject and p["player_id"] == subject["player_id"]
                              else "OTHER")}
            for p in players]

        if subject is None:
            a.decision = sem.NO_FANTASY_IMPACT
            a.supporting_quote = evidence_segment[:200]
            a.fantasy_mechanism = "NO_FANTASY_IMPACT"
            a.abstention_reason = mech["detail"]
        else:
            a.decision = sem.INTERPRET
            a.claim_subject_player_id = subject["player_id"]
            a.claim_subject_player_name = subject["player_name"]
            a.fantasy_mechanism = mech["mechanism"]
            a.direction = mech["direction"]
            # The rules engine has no notion of which sentence carried the
            # claim, so it offers the whole segment. The substring check
            # passes trivially; every other check still applies.
            a.supporting_quote = evidence_segment
            a.fantasy_commentary = ""
            a.why_it_matters = mech["detail"]
        a.latency_ms = int((time.time() - t0) * 1000)
        a.output_hash = sem.output_hash(a.to_dict())
        return a
