#!/usr/bin/env python3
"""Offline regressions for the scheduled monitor and phone approval gate."""

from __future__ import annotations

import copy
import json
import unittest
import types
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# The mobile boundary tests are offline and never invoke source capture. Keep
# them runnable in a minimal checkout where optional crawler dependencies have
# not yet been installed; CI installs the real packages before this suite.
sys.modules.setdefault("feedparser", types.SimpleNamespace())
sys.modules.setdefault("trafilatura", types.SimpleNamespace())

from wire import mobile_approval as mobile
from wire import mobile_dedupe
from wire.public_labels import DIRECTION_LABELS
from wire.mobile_draft import OpenAIMobileDraftProvider, redundant_outlet_lead
import wire_mobile_inbox as inbox
import wire_mobile_draft as mobile_draft_script
import wire_publication_preview as publication_preview


def card() -> dict:
    return {
        "player": "Bijan Robinson", "player_id": "00-test",
        "team": "ATL", "position": "RB", "content_type": "REPORTING",
        "direction": "NEUTRAL", "mechanism": "OTHER", "strength": "LOW",
        "horizon": "SHORT_TERM", "projection_action": "NONE",
        "reader_label": "Worth noting",
        "public_summary": (
            "Jane Doe reported Bijan Robinson worked in a new package Monday."),
        "evidence": (
            "At Monday practice, Bijan Robinson took several snaps in a package "
            "the offense had not shown earlier in camp."),
        "commentary": (
            "The report adds a watch-list data point; role and workload remain "
            "unconfirmed."),
        "source": "Example News", "author": "Jane Doe",
        "date": "2026-08-24T12:00:00+00:00",
        "url": "https://example.com/report", "ownership": "INDEPENDENT",
        "evidence_candidate_id": "mobile:test:1",
        "reviewer_action": "PENDING", "public_summary_approved_by": "",
        "commentary_approved_by": "", "approved_at": "",
        "commentary_origin": "MODEL_DRAFT", "model_original_commentary": "",
        "readiness_failures": [],
    }


class MobileWireTests(unittest.TestCase):
    def manifest(self):
        return mobile.make_manifest(
            [card()], generated_at="2026-08-24T12:00:00+00:00",
            publication_sha256="a" * 64, publication_count=72,
            source_batch_sha256="b" * 64,
            model_calls=1, cost_usd=0.001)

    def test_manifest_round_trip_binds_exact_wording(self):
        manifest = self.manifest()
        encoded = mobile.encode_manifest(manifest)
        self.assertEqual(mobile.decode_manifest(encoded), manifest)
        changed = copy.deepcopy(manifest)
        changed["cards"][0]["commentary"] = "Different text."
        with self.assertRaisesRegex(ValueError, "hash"):
            mobile.encode_manifest(changed)

    def test_phone_commands_are_closed_and_support_edits(self):
        parsed = mobile.parse_commands(
            "approve 1,3\nreject 2\nedit 3 | New summary. | New impact.", 3)
        self.assertEqual(parsed["approved"], [1, 3])
        self.assertEqual(parsed["rejected"], [2])
        self.assertEqual(parsed["edits"][3]["commentary"], "New impact.")
        with self.assertRaises(ValueError):
            mobile.parse_commands("publish everything", 3)
        with self.assertRaises(ValueError):
            mobile.parse_commands("approve 4", 3)

    def test_only_ralph_on_a_trusted_labeled_issue_can_approve(self):
        event = {
            "action": "created", "sender": {"login": "rdamato720"},
            "issue": {"number": 12, "user": {"login": "github-actions[bot]"},
                      "labels": [{"name": "wire-inbox"}],
                      "body": mobile.encode_manifest(self.manifest())},
            "comment": {"id": 99, "body": "approve all"},
        }
        issue, comment = mobile.validate_event(event)
        self.assertEqual(issue["number"], 12)
        self.assertEqual(comment, "approve all")
        unauthorized = copy.deepcopy(event)
        unauthorized["sender"]["login"] = "someone-else"
        with self.assertRaisesRegex(ValueError, "not authorized"):
            mobile.validate_event(unauthorized)
        pull_request = copy.deepcopy(event)
        pull_request["issue"]["pull_request"] = {"url": "https://example.com"}
        with self.assertRaisesRegex(ValueError, "issue"):
            mobile.validate_event(pull_request)

    def test_issue_body_contains_exact_copy_evidence_and_commands(self):
        manifest = self.manifest()
        body = inbox.render(manifest)
        self.assertIn(card()["public_summary"], body)
        self.assertIn(card()["commentary"], body)
        self.assertIn(card()["evidence"], body)
        self.assertIn("approve all", body)
        self.assertEqual(mobile.decode_manifest(body), manifest)

    def test_draft_provider_is_structured_non_publishing_copy(self):
        response = {
            "decision": "CARD", "content_type": "REPORTING",
            "public_summary": card()["public_summary"],
            "lineupbeat_impact": card()["commentary"],
            "direction": "NEUTRAL", "mechanism": "OTHER",
            "strength": "LOW", "horizon": "SHORT_TERM",
            "limitations": ["workload unknown"], "confidence": 0.7,
            "reason": "useful context",
        }
        prompts = []

        def transport(prompt):
            prompts.append(prompt)
            return response, {"input_tokens": 100, "output_tokens": 50}

        provider = OpenAIMobileDraftProvider(transport=transport)
        result, meta = provider.draft(
            card()["evidence"],
            {"author": "Jane Doe", "source_name": "Example News",
             "ownership": "INDEPENDENT", "published_at": card()["date"],
             "source_url": card()["url"]},
            {"player": "Bijan Robinson", "player_id": "00-test",
             "team": "ATL", "position": "RB"})
        self.assertEqual(result["decision"], "CARD")
        self.assertEqual(meta["provider"], "openai")
        self.assertGreater(meta["cost_usd"], 0)
        self.assertIn("COMPLETE EVIDENCE", prompts[0])

    def test_rejected_fringe_players_are_filtered_before_provider_spend(self):
        rows = [
            {"candidate_id": "jaden", "player": "Jaden Bradley",
             "player_id": "jaden", "team": "WAS", "position": "WR",
             "evidence": "Jaden Bradley was a bottom-of-roster receiver who stood out."},
            {"candidate_id": "kaytron", "player": "Kaytron Allen",
             "player_id": "kaytron", "team": "WAS", "position": "RB",
             "evidence": "Kaytron Allen continues to impress this preseason."},
            {"candidate_id": "nick", "player": "Nick Nash",
             "player_id": "nick", "team": "WAS", "position": "WR",
             "evidence": "Nick Nash was a bottom-of-roster receiver who stood out."},
            {"candidate_id": "sam", "player": "Sam Hartman",
             "player_id": "sam", "team": "WAS", "position": "QB",
             "evidence": "Athan Kaliakmanis took control of the developmental competition with Sam Hartman."},
        ]
        registry = {"players": {"kaytron": {
            "relevance_tier": "WATCHLIST",
            "relevance_reason": "outside the core draft boundary"}}}
        kept, suppressed = mobile_draft_script.relevance_filter(rows, registry)
        self.assertEqual(kept, [])
        self.assertEqual({row["player"] for row in suppressed},
                         {"Jaden Bradley", "Kaytron Allen", "Nick Nash",
                          "Sam Hartman"})

    def test_material_role_still_passes_mobile_draftability_gate(self):
        row = {"candidate_id": "kaytron", "player": "Kaytron Allen",
               "player_id": "kaytron", "team": "WAS", "position": "RB",
               "evidence": "Kaytron Allen led Washington's first-team backfield in carries."}
        registry = {"players": {"kaytron": {
            "relevance_tier": "WATCHLIST",
            "relevance_reason": "outside the core draft boundary"}}}
        kept, suppressed = mobile_draft_script.relevance_filter([row], registry)
        self.assertEqual(kept, [row])
        self.assertEqual(suppressed, [])

    def test_outlet_name_is_not_repeated_at_start_of_summary(self):
        self.assertTrue(redundant_outlet_lead(
            "Sports Illustrated's John said Kaytron Allen impressed.",
            "Sports Illustrated -- WAS"))
        self.assertFalse(redundant_outlet_lead(
            "John said Kaytron Allen impressed.",
            "Sports Illustrated -- WAS"))
        candidate = {
            "player": "Kaytron Allen", "player_id": "00-test",
            "team": "WAS", "position": "RB", "source_name": "Sports Illustrated -- WAS",
            "author": "John", "ownership": "INDEPENDENT",
            "published_at": "2026-08-25T12:00:00Z",
            "source_url": "https://example.com/report",
            "candidate_id": "mobile:test:outlet",
            "evidence": "John said Kaytron Allen continues to impress during the preseason.",
        }
        result = {
            "content_type": "REPORTING", "direction": "POSITIVE",
            "mechanism": "PERFORMANCE", "strength": "LOW",
            "horizon": "SHORT_TERM",
            "public_summary": "Sports Illustrated's John said Kaytron Allen continues to impress.",
            "lineupbeat_impact": "The praise does not establish a fantasy role.",
            "limitations": [], "confidence": 0.7,
        }
        proposed = mobile_draft_script.card_from(candidate, result)
        self.assertIn("public summary redundantly starts with the cited outlet",
                      proposed["readiness_failures"])

    def test_same_player_same_event_is_deduped(self):
        first = card()
        first.update({"mechanism": "RETURN_TO_PRACTICE", "direction": "POSITIVE",
                      "date": "2026-08-24T12:00:00Z",
                      "evidence": "Bijan Robinson joined full 11 on 11 team periods."})
        second = {**first, "evidence_candidate_id": "mobile:test:2",
                  "date": "2026-08-24T14:00:00Z",
                  "evidence": "Robinson was a full participant in 11 on 11 work."}
        duplicate, detail = mobile_dedupe.duplicate(first, second)
        self.assertTrue(duplicate)
        self.assertIn("eleven_on_eleven", detail["shared_markers"])

    def test_materially_changed_status_is_not_deduped(self):
        limited = card()
        limited.update({"mechanism": "LIMITED_PARTICIPATION",
                        "direction": "NEUTRAL",
                        "date": "2026-08-24T12:00:00Z",
                        "evidence": "Bijan Robinson was limited to individual drills."})
        full = {**limited, "evidence_candidate_id": "mobile:test:2",
                "mechanism": "RETURN_TO_PRACTICE",
                "direction": "POSITIVE", "date": "2026-08-24T15:00:00Z",
                "evidence": "Bijan Robinson was a full participant in team periods."}
        self.assertFalse(mobile_dedupe.duplicate(limited, full)[0])

    def test_different_injuries_and_old_events_remain_separate(self):
        ankle = card()
        ankle.update({"mechanism": "INJURY", "direction": "NEGATIVE",
                      "date": "2026-08-24T12:00:00Z",
                      "evidence": "Bijan Robinson left with an ankle injury."})
        knee = {**ankle, "evidence_candidate_id": "mobile:test:2",
                "date": "2026-08-24T14:00:00Z",
                "evidence": "Bijan Robinson was evaluated for a knee injury."}
        old_ankle = {**ankle, "evidence_candidate_id": "mobile:test:3",
                     "date": "2026-08-23T12:00:00Z"}
        self.assertFalse(mobile_dedupe.duplicate(ankle, knee)[0])
        self.assertFalse(mobile_dedupe.duplicate(ankle, old_ankle)[0])

    def test_stronger_more_specific_card_wins_pending_duplicate(self):
        weak = card()
        weak.update({"mechanism": "RETURN_TO_PRACTICE", "direction": "POSITIVE",
                     "strength": "LOW", "date": "2026-08-24T12:00:00Z",
                     "evidence": "Bijan Robinson returned to team periods."})
        strong = {**weak, "strength": "HIGH", "date": "2026-08-24T13:00:00Z",
                  "evidence": "Bijan Robinson was a full participant in 11 on 11 team periods."}
        self.assertGreater(mobile_dedupe.quality(strong),
                           mobile_dedupe.quality(weak))

    def test_unclear_direction_uses_worth_noting_public_label(self):
        self.assertEqual(DIRECTION_LABELS["UNCLEAR"], "Worth noting")
        unclear = card()
        unclear.update({"direction": "UNCLEAR", "reader_label": "Unclear"})
        failures = publication_preview.readiness_failures(unclear)
        self.assertTrue(any("reader label" in failure for failure in failures))
        unclear["reader_label"] = "Worth noting"
        failures = publication_preview.readiness_failures(unclear)
        self.assertFalse(any("reader label" in failure for failure in failures))

    def test_capture_only_is_source_scoped_and_skips_extraction(self):
        cli = (ROOT / "beatwire" / "cli.py").read_text()
        pipeline = (ROOT / "beatwire" / "pipeline.py").read_text()
        self.assertIn('r.add_argument("--capture-only"', cli)
        self.assertIn('r.add_argument("--kind"', cli)
        self.assertIn("sources = [s for s in sources if s.kind in kinds]",
                      pipeline)
        self.assertIn("if fresh and not capture_only:", pipeline)
        self.assertIn("Registry(sport, load_players=not capture_only)", pipeline)
        registry = (ROOT / "beatwire" / "registry.py").read_text()
        self.assertIn("if load_players else []", registry)

    def test_workflows_are_capped_and_human_gated(self):
        monitor = (ROOT / ".github/workflows/wire-monitor.yml").read_text()
        approval = (ROOT / ".github/workflows/wire-mobile-approve.yml").read_text()
        self.assertIn("WIRE_MOBILE_AUTODRAFT == 'true'", monitor)
        self.assertNotIn('cron: "7,37 * * * *"', monitor)
        self.assertIn('cron: "7,37 0-3 * * *"', monitor)
        self.assertIn('cron: "7,37 11-23 * * *"', monitor)
        self.assertIn("--max-calls \"$MOBILE_MAX_CALLS\"", monitor)
        self.assertIn("MOBILE_MAX_CALLS: 20", monitor)
        self.assertIn("--cap \"$MOBILE_RUN_CAP\"", monitor)
        self.assertIn("--capture-only", monitor)
        self.assertIn("wire-mobile-x.db", monitor)
        self.assertIn("for attempt in 1 2 3", monitor)
        self.assertIn("git fetch origin main", monitor)
        self.assertIn("git stash push --include-untracked", monitor)
        self.assertIn("git rebase origin/main", monitor)
        self.assertIn("git rebase --abort || true", monitor)
        self.assertLess(monitor.index("git stash push --include-untracked"),
                        monitor.index("git rebase origin/main"))
        self.assertLess(monitor.index("git rebase origin/main"),
                        monitor.index("git push origin HEAD:main"))
        self.assertNotIn("wire_publish.py", monitor)
        draft = (ROOT / "scripts" / "wire_mobile_draft.py").read_text()
        self.assertIn('"nfl", load_players=False', draft)
        self.assertIn('"unreviewed_count"', draft)
        self.assertIn("github.actor == 'rdamato720'", approval)
        self.assertIn("wire_mobile_approve.py", approval)
        self.assertIn("--publish", approval)
        self.assertIn("skip_fetch=true", approval)
        self.assertNotIn("wire_homepage_replacement.py", approval)
        self.assertNotIn("data/wire_homepage_replacement", approval)
        self.assertLess(approval.index("git push origin HEAD:main"),
                        approval.index("gh workflow run refresh.yml"))
        apply_script = (ROOT / "scripts" / "wire_mobile_approve.py").read_text()
        self.assertIn("visible issue wording does not match", apply_script)

    def test_article_candidates_receive_reserved_calls_before_newer_x(self):
        rows = [
            {"candidate_id": "x-new", "origin": "X",
             "published_at": "2026-08-26T18:10:00+00:00"},
            {"candidate_id": "local", "origin": "ARTICLE",
             "published_at": "2026-08-26T18:00:00+00:00"},
            {"candidate_id": "x-old", "origin": "X",
             "published_at": "2026-08-26T17:50:00+00:00"},
        ]
        ordered = mobile_draft_script.prioritize_candidates(rows, max_calls=20)
        self.assertEqual([row["candidate_id"] for row in ordered],
                         ["local", "x-new", "x-old"])

    def test_inclusive_review_is_not_limited_to_si_sources(self):
        source = (ROOT / "scripts" / "wire_inclusive_review.py").read_text()
        self.assertIn("s.active and s.adapter and not s.paid", source)
        self.assertNotIn("registry.SI_ONSI,", source)

    def test_zero_card_batches_are_banked_with_diagnostics(self):
        workflow = (ROOT / ".github" / "workflows" /
                    "wire-monitor.yml").read_text()
        self.assertIn(
            'steps.draft.outcome }}" = "success" ] && [ -f data/wire_mobile_batch.json',
            workflow)
        script = (ROOT / "scripts" / "wire_mobile_draft.py").read_text()
        self.assertIn('"held_for_review"', script)
        self.assertIn('"article_sources_without_candidates"', script)

    def test_obj_isolated_red_zone_highlight_is_not_card_worthy(self):
        candidate = {
            "evidence": (
                "Odell Beckham Jr. caught a touchdown in the back of the end "
                "zone. Jaxson Dart later had red zone success with OBJ.")}
        result = {
            "decision": "CARD", "mechanism": "RED_ZONE",
            "public_summary": (
                "Odell Beckham Jr. caught a back-end-zone touchdown and had "
                "another red-zone reception."),
            "lineupbeat_impact": (
                "The catches are a modest positive for his scoring-use outlook, "
                "but they were isolated practice plays and do not establish a "
                "regular role or target volume."),
        }
        failures = mobile_draft_script.event_quality_failures(candidate, result)
        self.assertTrue(any("isolated practice" in failure for failure in failures))
        self.assertTrue(any("editorial jargon" in failure for failure in failures))

    def test_qb1_practice_recap_is_not_a_depth_chart_change(self):
        candidate = {"evidence": (
            "Jaxson Dart threw four touchdowns in one goal-line period and ran "
            "for another. Another good day for Giants QB1.")}
        result = {
            "decision": "CARD", "mechanism": "DEPTH_CHART",
            "public_summary": (
                "Jaxson Dart accounted for five touchdowns in goal-line work."),
            "lineupbeat_impact": (
                "The session supports short-term starting-QB momentum, but it "
                "does not establish a season-long role."),
        }
        failures = mobile_draft_script.event_quality_failures(candidate, result)
        self.assertTrue(any("depth-chart" in failure for failure in failures))
        self.assertTrue(any("editorial jargon" in failure for failure in failures))

    def test_multi_practice_performance_trend_can_reach_review(self):
        candidate = {"evidence": (
            "Jalen Hurts threw 13 interceptions across 16 practices after "
            "totaling 15 over the prior four training camps.")}
        result = {
            "decision": "CARD", "mechanism": "PERFORMANCE",
            "public_summary": (
                "Jalen Hurts has thrown 13 interceptions across 16 practices."),
            "lineupbeat_impact": (
                "The multi-practice turnover trend is worth monitoring before "
                "Week 1, even though camp results do not guarantee game results."),
        }
        self.assertEqual(
            mobile_draft_script.event_quality_failures(candidate, result), [])

    def test_nabers_early_exit_remains_card_worthy(self):
        candidate = {"evidence": (
            "Malik Nabers looked very good running and made a leaping catch. "
            "He did not finish practice and walked inside with the Giants' "
            "return-to-play coordinator.")}
        result = {
            "decision": "CARD", "mechanism": "INJURY",
            "public_summary": (
                "Malik Nabers left practice early with the Giants' "
                "return-to-play coordinator."),
            "lineupbeat_impact": (
                "Coming off ACL surgery, the early exit is worth monitoring. "
                "The report does not say he suffered a setback."),
        }
        self.assertEqual(
            mobile_draft_script.event_quality_failures(candidate, result), [])


if __name__ == "__main__":
    unittest.main()
