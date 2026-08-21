#!/usr/bin/env python3
"""Build the locked evaluation corpus.

    python3 scripts/wire_eval_corpus.py --build

Two kinds of item. GOLD items carry a hand-written expected answer -- every
defect found in review, plus the cases the reviewer named -- and are what
accuracy is measured on. UNLABELLED items are real segments sampled for
coverage; they are in the corpus so providers can be run over realistic
material and so a human can label them, and they are never counted as
accuracy until somebody does.

The split is stated in the output rather than blurred, because a corpus that
reports its own unlabelled items as passes is how a layer gets approved on
its own opinion of itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import registry as artreg
from wire.store import WireStore

OUT = Path("data/wire_eval_corpus.json")

# Every case the reviewer named, plus the fixtures from the earlier pass.
# expected.decision / mechanism / direction / subject are the graded fields.
GOLD = [
 {"id": "keon-coleman-relay",
  "text": "On Thursday in Berea, Khalil Shakir and Dante Pettis added to the "
          "group of sidelined wide receivers, per Cameron Wolfe of NFL Network.",
  "players": [("Keon Coleman", "BUF", "WR")],
  "expected": {"decision": "NO_FANTASY_IMPACT", "classification": "RELAYED_REPORTING"},
  "guards": "relayed reporting leaking into a quotation or firsthand claim"},

 {"id": "washington-thomas-targets",
  "text": "With no Parker Washington on the field, the No. 1 target for "
          "Trevor Lawrence on Tuesday was pretty easily Brian Thomas Jr.",
  "players": [("Parker Washington", "JAX", "WR"), ("Brian Thomas", "JAX", "WR")],
  "expected": {"decision": "INTERPRET", "subject": "Parker Washington",
               "mechanism": "LIMITED_PARTICIPATION", "direction": "NEGATIVE"},
  "also_valid_subject": "Brian Thomas",
  "guards": "an absent player inheriting the targets that went to somebody else"},

 {"id": "thomas-beneficiary",
  "text": "With no Parker Washington on the field, the No. 1 target for "
          "Trevor Lawrence on Tuesday was pretty easily Brian Thomas Jr.",
  "players": [("Brian Thomas", "JAX", "WR")],
  "expected": {"decision": "INTERPRET", "subject": "Brian Thomas",
               "mechanism": "TARGETS", "direction": "POSITIVE"},
  "guards": "the beneficiary of an absence being suppressed"},

 {"id": "washington-trammell-personnel",
  "text": "Austin Trammell moved into the first-team 11 personnel package "
          "with Parker Washington out.",
  "players": [("Parker Washington", "JAX", "WR"), ("Austin Trammell", "JAX", "WR")],
  "expected": {"decision": "INTERPRET", "subject": "Parker Washington",
               "mechanism": "LIMITED_PARTICIPATION", "direction": "NEGATIVE"},
  "also_valid_subject": "Austin Trammell",
  "guards": "an absent player described as receiving first-team work"},

 {"id": "trammell-first-team",
  "text": "Austin Trammell moved into the first-team 11 personnel package "
          "with Parker Washington out.",
  "players": [("Austin Trammell", "JAX", "WR")],
  "expected": {"decision": "INTERPRET", "subject": "Austin Trammell",
               "mechanism": "FIRST_TEAM_REPS", "direction": "POSITIVE"},
  "guards": "a conditional first-team opportunity being suppressed"},

 {"id": "mccarthy-price-pronoun",
  "text": "JJ McCarthy had a big completion to Myles Price downfield. He "
          "took the majority of the second-team reps on Wednesday.",
  "players": [("Myles Price", "MIN", "WR")],
  "expected": {"decision": "NO_FANTASY_IMPACT"},
  "guards": "a pronoun's reps landing on the wrong player"},

 {"id": "mccarthy-second-team",
  "text": "JJ McCarthy had a big completion to Myles Price downfield. He "
          "took the majority of the second-team reps on Wednesday.",
  "players": [("J.J. McCarthy", "MIN", "QB")],
  "expected": {"decision": "INTERPRET", "subject": "J.J. McCarthy",
               "mechanism": "SECOND_TEAM_REPS"},
  "guards": "the real pronoun antecedent being suppressed"},

 {"id": "geno-about-omar",
  "text": '"Omar Cooper Jr. has taken over the slot role for us," Geno '
          'Smith said after practice.',
  "players": [("Geno Smith", "NYJ", "QB")],
  "expected": {"decision": "NO_FANTASY_IMPACT"},
  "guards": "a quote speaker inheriting a claim about another player"},

 {"id": "allen-team-energy",
  "text": '"The energy out here has been unbelievable all camp," Josh Allen '
          'said.',
  "players": [("Josh Allen", "BUF", "QB")],
  "expected": {"decision": "NO_FANTASY_IMPACT"},
  "guards": "a team-mood quote becoming fantasy commentary"},

 {"id": "hankerson-waived",
  "text": "The team waived running back Anthony Hankerson on Tuesday.",
  "players": [("Anthony Hankerson", "CLE", "RB")],
  "expected": {"decision": "INTERPRET", "subject": "Anthony Hankerson",
               "mechanism": "TRANSACTION", "direction": "NEGATIVE"},
  "forbidden_mechanism": "RETURN_TO_PRACTICE",
  "guards": "a waived player described as returning to practice"},

 {"id": "wease-absent",
  "text": "Theo Wease Jr. did not practice for the third straight day.",
  "players": [("Theo Wease", "MIA", "WR")],
  "expected": {"decision": "INTERPRET", "subject": "Theo Wease",
               "mechanism": "LIMITED_PARTICIPATION", "direction": "NEGATIVE"},
  "guards": "an absence read as a return"},

 {"id": "giddens-reinjury",
  "text": "DJ Giddens returned to practice and immediately reaggravated his "
          "hamstring.",
  "players": [("DJ Giddens", "IND", "RB")],
  "expected": {"decision": "INTERPRET", "subject": "DJ Giddens",
               "mechanism": "LIMITED_PARTICIPATION", "direction": "NEGATIVE"},
  "forbidden_mechanism": "RETURN_TO_PRACTICE",
  "guards": "a re-injury read as a healthy return"},

 {"id": "engram-adjacent-return",
  "text": "Evan Engram caught two passes over the middle. Travis Etienne "
          "was back at practice after a day off.",
  "players": [("Evan Engram", "DEN", "TE")],
  "expected": {"decision": "NO_FANTASY_IMPACT"},
  "forbidden_mechanism": "RETURN_TO_PRACTICE",
  "guards": "adjacent return language attaching to the wrong player"},

 {"id": "jones-richardson-unit",
  "text": "Daniel Jones remains the named starter. Anthony Richardson ran "
          "with the second team throughout the session.",
  "players": [("Daniel Jones", "IND", "QB")],
  "expected": {"decision": "NO_FANTASY_IMPACT"},
  "forbidden_mechanism": "SECOND_TEAM_REPS",
  "guards": "a player inheriting another player's unit"},

 {"id": "richardson-second-team",
  "text": "Anthony Richardson led the Colts' second-team offense down the "
          "field for a field goal score as time expired.",
  "players": [("Anthony Richardson", "IND", "QB")],
  "expected": {"decision": "INTERPRET", "subject": "Anthony Richardson",
               "mechanism": "SECOND_TEAM_REPS"},
  "guards": "a genuine unit claim being suppressed"},

 {"id": "wentz-third-team",
  "text": "Carson Wentz worked with the 3s for most of the afternoon.",
  "players": [("Carson Wentz", "MIN", "QB")],
  "expected": {"decision": "INTERPRET", "subject": "Carson Wentz",
               "mechanism": "THIRD_TEAM_REPS", "direction": "NEGATIVE"},
  "forbidden_mechanism": "SECOND_TEAM_REPS",
  "guards": "third-team work reported as second team"},

 {"id": "mccord-adjacent-heading",
  "text": "Kyle McCord found Bo Melton on a crossing route for a first down.",
  "players": [("Kyle McCord", "GB", "QB")],
  "expected": {"decision": "NO_FANTASY_IMPACT"},
  "forbidden_mechanism": "FIRST_TEAM_REPS",
  "guards": "a starters heading donating first-team reps to the next line"},

 {"id": "jacobs-return",
  "text": "Returned to practice: RB Josh Jacobs (groin), edge Lukas Van Ness "
          "(shoulder).",
  "players": [("Josh Jacobs", "GB", "RB")],
  "expected": {"decision": "INTERPRET", "subject": "Josh Jacobs",
               "mechanism": "RETURN_TO_PRACTICE", "direction": "POSITIVE"},
  "guards": "a genuine return being suppressed"},

 {"id": "laporta-absent",
  "text": "Those not participating at practice on Thursday included Sam "
          "LaPorta, Mekhi Wingo and Brian Branch.",
  "players": [("Sam LaPorta", "DET", "TE")],
  "expected": {"decision": "INTERPRET", "subject": "Sam LaPorta",
               "mechanism": "LIMITED_PARTICIPATION", "direction": "NEGATIVE"},
  "guards": "an availability signal in a list being suppressed"},

 {"id": "stidham-mixed-units",
  "text": "Jarrett Stidham started Game 1 in Atlanta, playing the entire "
          "first half with the first- and second-team offensive units.",
  "players": [("Jarrett Stidham", "DEN", "QB")],
  "expected": {"decision": "INTERPRET", "subject": "Jarrett Stidham",
               "mechanism": "FIRST_TEAM_REPS"},
  "guards": "mixed first/second-team work being suppressed entirely"},

 {"id": "hollins-two-minute",
  "text": "If you look at any of the Patriots' two-minute drills to close "
          "each training camp practice this summer, it does not matter which "
          "offensive unit is on the field. Chances are you will see Mack "
          "Hollins running routes.",
  "players": [("Mack Hollins", "NE", "WR")],
  "expected": {"decision": "INTERPRET", "subject": "Mack Hollins",
               "mechanism": "ROUTES"},
  "guards": "overstating a recurring situational role as a defined role"},

 {"id": "isolated-highlight",
  "text": "Amon-Ra St. Brown caught a short touchdown in the red-zone period.",
  "players": [("Amon-Ra St. Brown", "DET", "WR")],
  "expected": {"decision": "NO_FANTASY_IMPACT"},
  "guards": "an isolated play treated as an opportunity change"},

 {"id": "penalty-no-impact",
  "text": "Nate Boerkircher was flagged for holding on the second play of "
          "the team period.",
  "players": [("Nate Boerkircher", "DEN", "TE")],
  "expected": {"decision": "NO_FANTASY_IMPACT"},
  "guards": "a penalty becoming fantasy commentary"},
]


def resolve(reg, name, team, pos):
    hits, how = reg.resolve(name, team, pos)
    if len(hits) == 1:
        p = hits[0]
        return {"player_id": p.player_id, "player_name": p.full_name,
                "team": p.team, "position": p.position}
    return {"player_id": f"unresolved:{name}", "player_name": name,
            "team": team, "position": pos}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--sample", type=int, default=90)
    args = ap.parse_args()

    from wire import players as pl
    reg = pl.load()
    store = WireStore()
    sources = {s.source_id: s for s in artreg.load()}

    items = []
    for g in GOLD:
        items.append({
            "id": g["id"], "kind": "GOLD", "text": g["text"],
            "guards": g["guards"],
            "players": [resolve(reg, n, t, p) for n, t, p in g["players"]],
            "metadata": {"team": g["players"][0][1], "source_name": "fixture",
                         "author": "fixture", "source_ownership": "INDEPENDENT",
                         "reporter_voice": True},
            "expected": g["expected"],
            "forbidden_mechanism": g.get("forbidden_mechanism"),
            "also_valid_subject": g.get("also_valid_subject"),
        })

    # Real segments, stratified so the corpus covers what the pipeline meets.
    buckets = defaultdict(list)
    for r in store.evidence():
        if r["review_status"] != "PENDING" or not r["player_id"]:
            continue
        src = sources.get(r["source_id"])
        buckets[(r["evidence_class"], r["position"])].append(r)
    picked, i = [], 0
    while len(picked) < args.sample:
        added = False
        for key in sorted(buckets):
            if i < len(buckets[key]):
                picked.append(buckets[key][i])
                added = True
                if len(picked) >= args.sample:
                    break
        if not added:
            break
        i += 1
    for r in picked:
        src = sources.get(r["source_id"])
        items.append({
            "id": f"real:{r['candidate_id']}", "kind": "UNLABELLED",
            "text": r["evidence_text"],
            "guards": "",
            "players": [{"player_id": r["player_id"],
                         "player_name": r["player_name"],
                         "team": r["team"], "position": r["position"]}],
            "metadata": {
                "team": r["team"], "article_title": r["source_title"],
                "published_at": r["published_at"],
                "source_name": (src.source_name if src else r["source_id"]),
                "author": r["source_author_or_channel"],
                "source_ownership": r["source_ownership"] or "INDEPENDENT",
                "evidence_access": (src.evidence_access if src else ""),
                "duplicate_of": r["duplicate_of"],
                "underlying_report_id": r["underlying_report_id"],
                "reporter_voice": False},
            "expected": None, "forbidden_mechanism": None,
            "also_valid_subject": None,
        })

    gold = [x for x in items if x["kind"] == "GOLD"]
    payload = {"schema_version": "eval-v1",
               "note": "GOLD items carry hand-written expected answers and are "
                       "what accuracy is measured on. UNLABELLED items are real "
                       "segments awaiting human labels and are never counted as "
                       "correct until labelled.",
               "gold_items": len(gold), "unlabelled_items": len(items) - len(gold),
               "items": items}
    if args.build:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"  {len(items)} corpus items: {len(gold)} GOLD, "
          f"{len(items) - len(gold)} UNLABELLED")
    print(f"  gold mechanisms: "
          f"{dict(Counter(g['expected'].get('mechanism', g['expected']['decision']) for g in gold))}")
    print(f"  unlabelled positions: "
          f"{dict(Counter(x['players'][0]['position'] for x in items if x['kind'] == 'UNLABELLED'))}")
    unres = [x for x in gold for p in x["players"]
             if p["player_id"].startswith("unresolved:")]
    if unres:
        print(f"  gold items with an unresolved player: {len(unres)}")
    if args.build:
        print(f"  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
