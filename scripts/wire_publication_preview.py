#!/usr/bin/env python3
"""The final preview: exactly what a reader would see, and nothing else.

    python3 scripts/wire_publication_preview.py --build

Every earlier page was a review instrument -- validator verdicts, token
counts, confidence scores, provider metadata. None of that is published.
This page shows the reader-facing card as it would actually appear, so the
last approval is given on the words themselves rather than on a metric.

It writes nothing to wire_publications.json. Publishing remains disabled.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire.public_labels import DIRECTION_LABELS

REVIEW = Path("data/reviews/seven_final.json")
SEVEN = Path("data/wire_seven_review.json")
OUT_HTML = Path("data/wire_publication_preview.html")
OUT_JSON = Path("data/wire_publication_preview.json")

# Reviewer edits, applied to the text before it is shown.
EDITS = {"Anthony Richardson": {"direction": "NEUTRAL"}}

DIRECTION_WORD = DIRECTION_LABELS


def publishable(case, decisions):
    """Only an approved interpretation reaches a reader."""
    key = case["player"].lower().replace(" ", "-").replace(".", "")
    d = decisions.get(key)
    if not d:
        return None, "no reviewer decision"
    act = d["action"]
    if act == "INCONCLUSIVE_TECHNICAL":
        return None, "inconclusive on a technical quotation failure"
    if act == "HELD_EVIDENCE_CONFLICT":
        return None, ("held: the requested mechanism is not supported by the "
                      "passage")
    if act == "PENDING":
        return None, "awaiting a reviewer decision"
    if act.startswith("REJECT"):
        return None, f"rejected ({d.get('reason', '')})"
    # A reviewer who supplies a mechanism and the wording has made the call.
    # Chris Blair's stored assessment was a stale ABSTAIN from a run whose
    # quotation check has since been fixed; the human decision outranks it.
    # The readiness checks still run on the result.
    if d.get("mechanism") and d.get("edited_text"):
        return d, ""
    if case["decision"] != "INTERPRET":
        return None, f"{case['decision']} — nothing to publish"
    return d, ""


NEGATIVE_WORDS = re.compile(
    r"(?i)\b(concerning|worrying|demot|slipping|losing ground|troubl|"
    r"bad sign|red flag|setback|behind)\b")
POSITIVE_WORDS = re.compile(
    r"(?i)\b(encouraging|promising|breakout|boost|ascend|surging|"
    r"good sign|stepping up)\b")


PERMANENCE = re.compile(
    r"(?i)\b(has (?:taken|won|seized|claimed) (?:over |the )?(?:job|role|"
    r"starting)|is now the (?:starter|no\.? ?1|top)|permanently|"
    r"has (?:passed|overtaken|leapfrogged)|new (?:starter|no\.? ?1)|"
    r"locked (?:up|down) the)\b")

TEMPORARY_CONTEXT = re.compile(
    r"(?i)\b(one (?:practice|session|day)|single (?:practice|session|report)|"
    r"during one|that day|on the day|while|with .{0,24} (?:out|absent|"
    r"unavailable|sidelined)|does not (?:say|establish)|for how long|"
    r"joint practice|this practice|next practice|consecutive practices|"
    r"for now|just yet|no .{0,60}(?:timetable|diagnosis))\b")

PUP_ACTIVATION = re.compile(
    r"(?i)\b(activat(?:ed|ing) .* (?:pup|nfi)|coming off (?:the )?(?:pup|nfi)|"
    r"came off (?:the )?(?:pup|nfi)|off (?:the )?(?:pup|nfi)|"
    r"removed .{0,40} from (?:the )?(?:pup|nfi)|"
    r"taking .{0,40} off (?:the )?(?:pup|nfi))\b")

ABSENCE_MECHANISMS = {"LIMITED_PARTICIPATION", "INJURY", "RETURN_TO_PRACTICE"}

# Words the evidence must contain before a mechanism may claim them.
MECHANISM_EVIDENCE = {
    "INJURY": re.compile(r"(?i)\b(injur|lower[- ]body issue|upper[- ]body issue|"
                         r"groin|hamstring|ankle|knee|acl|"
                         r"achilles|concussion|surgery|strain|sprain|"
                         r"sore|tightness|hurt|banged[- ]?up)\b"),
    "RETURN_TO_PRACTICE": re.compile(
        r"(?i)\b(return(?:ed|ing)? to practice|back (?:at|on|to)|"
        r"activated|cleared|coming off (?:the )?(?:pup|nfi)|"
        r"off (?:the )?(?:pup|nfi)|"
        r"removed .{0,40} from (?:the )?(?:pup|nfi)|"
        r"taking .{0,40} off (?:the )?(?:pup|nfi))\b"),
    "FIRST_TEAM_REPS": re.compile(
        r"(?i)first[-\s]?team|with the (?:ones|1s)|starting (?:offense|receivers?)"),
    "SECOND_TEAM_REPS": re.compile(
        r"(?i)second[-\s]?team|with the (?:twos|2s)|\bQB2\b"),
    "THIRD_TEAM_REPS": re.compile(r"(?i)third[-\s]?team|with the (?:threes|3s)"),
    "RED_ZONE": re.compile(r"(?i)red[-\s]?zone|goal[-\s]?line"),
    "CARRIES": re.compile(r"(?i)carr(?:y|ies|ied)|touches|rushing attempts"),
    "TARGETS": re.compile(r"(?i)target"),
}


def readiness_failures(card: dict) -> list:
    """Every reason this card may not go in front of a reader.

    These fire on the finished text rather than on a score, because the
    failures they catch are failures of wording. A direction field changed by
    a reviewer does not rewrite the sentences underneath it, and a mechanism
    can name something the passage never said.
    """
    out = []
    text = card["commentary"]
    ev = card["evidence"]

    expected_label = DIRECTION_WORD.get(card.get("direction"))
    if expected_label and card.get("reader_label") != expected_label:
        out.append(
            f"reader label {card.get('reader_label')!r} does not match "
            f"direction {card.get('direction')}")

    conflict = framing_conflict(card["direction"], text)
    if conflict:
        out.append(conflict)

    # The mechanism must be supported by words actually in the passage.
    pat = MECHANISM_EVIDENCE.get(card["mechanism"])
    if pat and not pat.search(ev):
        out.append(f"mechanism {card['mechanism']} is not supported by the "
                   f"evidence passage")

    # An availability item must say the absence is a point in time.
    pup_return = (card["mechanism"] == "RETURN_TO_PRACTICE" and
                  PUP_ACTIVATION.search(ev))
    if (card["mechanism"] in ABSENCE_MECHANISMS and not pup_return and
            not TEMPORARY_CONTEXT.search(text)):
        out.append("availability item omits the temporary context (which "
                   "practice, and that no timetable was given)")

    # Nothing may read as a settled change unless the passage settled it.
    if PERMANENCE.search(text) and not PERMANENCE.search(ev):
        out.append("commentary implies a permanent role change the passage "
                   "does not establish")

    if card["reviewer_action"] not in ("APPROVE", "APPROVE_WITH_EDIT"):
        out.append(f"reviewer action is {card['reviewer_action']}, not an "
                   f"approval")
    return out


def framing_conflict(direction: str, text: str) -> str:
    """Does the prose argue a direction the reviewer did not set?

    A reviewer changing the direction field does not rewrite the sentences.
    Anthony Richardson was set NEUTRAL while his commentary still called the
    reps "a concerning depth-chart signal", so the badge said one thing and
    the words said another. Publishing that would be worse than publishing
    either on its own.
    """
    if direction == "NEUTRAL":
        if NEGATIVE_WORDS.search(text):
            return ("marked NEUTRAL but the wording argues a negative: "
                    f"{NEGATIVE_WORDS.search(text).group(0)!r}")
        if POSITIVE_WORDS.search(text):
            return ("marked NEUTRAL but the wording argues a positive: "
                    f"{POSITIVE_WORDS.search(text).group(0)!r}")
    if direction == "POSITIVE" and NEGATIVE_WORDS.search(text):
        return "marked POSITIVE but the wording is negative"
    if direction == "NEGATIVE" and POSITIVE_WORDS.search(text):
        return "marked NEGATIVE but the wording is positive"
    return ""


def render(cards, held) -> str:
    e = html.escape
    p = ["<title>Wire publication preview</title>", """<style>
:root{--bg:#faf9f7;--ink:#171a15;--quiet:#5d6157;--rule:#dcd9d2;--own:#8a5a1b;
--up:#2f6b3a;--down:#a4342a}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;--rule:#2c2f27;--own:#d6a55a;
--up:#7fbf8a;--down:#e08a7f}}
:root[data-theme="dark"]{--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;
--rule:#2c2f27;--own:#d6a55a;--up:#7fbf8a;--down:#e08a7f}
body{background:var(--bg);color:var(--ink);font:17px/1.6 -apple-system,
BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:30px}
.wrap{max-width:640px;margin:0 auto}
h1{font-size:1.4rem;margin-bottom:4px}
.sub{color:var(--quiet);font-size:.87rem;margin-bottom:26px}
.card{border:1px solid var(--rule);border-radius:12px;padding:20px;margin:20px 0}
.who{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.name{font-size:1.12rem;font-weight:700}
.pos{color:var(--quiet);font-size:.82rem;text-transform:uppercase;
letter-spacing:.06em}
.dir{font-size:.72rem;letter-spacing:.07em;text-transform:uppercase;
font-weight:700;padding:2px 9px;border-radius:99px;border:1px solid}
.up{color:var(--up);border-color:var(--up)}
.down{color:var(--down);border-color:var(--down)}
.flat{color:var(--quiet);border-color:var(--rule)}
.lab{font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;
color:var(--quiet);font-weight:700;margin:16px 0 6px}
.rep{border-left:3px solid var(--rule);padding-left:13px;font-size:.97rem}
.lb{border-left:3px solid var(--own);padding-left:13px;font-size:.97rem}
.src{color:var(--quiet);font-size:.8rem;margin-top:9px}
.src a{color:var(--quiet)}
.own{color:var(--own);font-weight:600}
.held{border:1px dashed var(--rule);border-radius:10px;padding:14px;
margin:16px 0;color:var(--quiet);font-size:.88rem}
</style>""", '<div class="wrap">', "<h1>The Wire — publication preview</h1>",
    '<p class="sub">Exactly what a reader would see. Nothing is published; '
    'wire_publications.json is untouched and projections are unchanged.</p>']

    for c in cards:
        d = c["direction"]
        cls = "up" if d == "POSITIVE" else "down" if d == "NEGATIVE" else "flat"
        p.append('<div class="card">')
        p.append(f'<div class="who"><span class="name">{e(c["player"])}</span>'
                 f'<span class="pos">{e(c["team"])} {e(c["position"])}</span>'
                 f'<span class="dir {cls}">{e(DIRECTION_WORD.get(d, d))}</span>'
                 f'</div>')
        p.append('<div class="lab">What the reporter found</div>')
        # Readers see the named-human-approved summary. The complete source
        # passage remains in ``evidence`` for audit and publication checks.
        reporting = c.get("public_summary") or c["evidence"]
        p.append(f'<div class="rep">{e(reporting)}</div>')
        own = c["ownership"] == "TEAM_OWNED"
        p.append(f'<p class="src">{e(c["author"] or "Staff")}, '
                 f'{e(c["source"])}{" &middot; " if c["date"] else ""}'
                 f'{e(c["date"][:10])}'
                 + (' &middot; <span class="own">Official team source</span>'
                    if own else "")
                 + f'<br><a href="{e(c["url"])}">{e(c["url"][:88])}</a></p>')
        p.append('<div class="lab">Lineup Beat impact</div>')
        p.append(f'<div class="lb">{e(c["commentary"])}</div>')
        if c.get("readiness_failures"):
            p.append('<p class="src" style="color:var(--down)">'
                     '<b>Not publishable as written:</b></p><ul>'
                     + "".join(f'<li style="color:var(--down);font-size:.85rem">'
                               f'{e(f)}</li>' for f in c["readiness_failures"])
                     + "</ul>")
        p.append('</div>')

    if held:
        p.append('<div class="held"><b>Held back from this preview</b><ul>'
                 + "".join(f"<li>{e(h['player'])} &mdash; {e(h['why'])}</li>"
                           for h in held) + "</ul></div>")
    p.append("</div>")
    return "\n".join(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()

    review = json.loads(REVIEW.read_text())
    seven = json.loads(SEVEN.read_text())
    decisions = review["decisions"]

    cards, held = [], []
    for case in seven["cases"]:
        d, why = publishable(case, decisions)
        if d is None:
            held.append({"player": case["player"], "why": why})
            continue
        direction = d.get("direction", case["direction"])
        commentary = d.get("edited_text") or case["commentary"]
        cards.append({
            "player": case["player"], "team": case["team"],
            "position": case["position"], "direction": direction,
            "mechanism": d.get("mechanism", case["mechanism"]),
            "strength": d.get("strength", case["strength"]),
            "horizon": d.get("horizon", case["horizon"]),
            "projection_action": d.get("projection_action", "NONE"),
            "reader_label": d.get("reader_label", ""),
            "model_original_commentary": case["commentary"],
            "evidence": case["text"], "commentary": commentary,
            "source": case.get("source_name", ""),
            "author": case.get("author", ""),
            "date": str(case.get("published_at", "")),
            "url": case.get("article_url", ""),
            "ownership": case.get("ownership", "INDEPENDENT"),
            "evidence_candidate_id": case.get("evidence_candidate_id", ""),
            "reviewer_action": d["action"],
        })
        cards[-1]["readiness_failures"] = readiness_failures(cards[-1])

    OUT_JSON.write_text(json.dumps(
        {"published": False, "note": "preview only; nothing written to "
                                     "wire_publications.json",
         "reviewer": review["reviewer"], "model": review["model"],
         "prompt_version": review["prompt_version"],
         "corpus_version": review["corpus_version"],
         "readiness": ("PASS" if cards and not any(c["readiness_failures"]
                                                   for c in cards) else "FAIL"),
         "cards": cards, "held_back": held}, indent=1) + "\n")
    OUT_HTML.write_text(render(cards, held) + "\n")

    blocked = [c for c in cards if c["readiness_failures"]]
    ready = [c for c in cards if not c["readiness_failures"]]
    print(f"  {len(cards)} approved card(s); {len(ready)} pass readiness, "
          f"{len(blocked)} blocked")
    for c in blocked:
        print(f"    BLOCKED {c['player']}:")
        for f in c["readiness_failures"]:
            print(f"       - {f}")
    for c in cards:
        print(f"    {c['player']:<20}{c['team']} {c['position']:<4}"
              f"{c['direction']:<10}{c['reviewer_action']}")
    print(f"  {len(held)} held back:")
    for h in held:
        print(f"    {h['player']:<20}{h['why']}")
    print(f"  wrote {OUT_HTML} and {OUT_JSON}")
    print("  publishing remains disabled")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
