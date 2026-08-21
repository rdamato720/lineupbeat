#!/usr/bin/env python3
"""The adversarial fixtures: every case the review caught, run for real.

    python3 scripts/wire_fixtures.py            # console
    python3 scripts/wire_fixtures.py --html data/wire_fixtures.html

These are not unit tests of a helper. Each passage goes through the same
segmentation, classification and mechanism path the pipeline uses, and the
expectation is the reviewer's verdict rather than the code's current
behaviour. A fixture that starts passing for the wrong reason is still a
failure, so each records what it is guarding against.
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import claims as cl
from wire import evidence as ev
from wire import segment as seg

NO = cl.NO_FANTASY_IMPACT

FIXTURES = [
    {"id": "keon-coleman-relay",
     "guards": "relayed reporting leaking into DIRECT_QUOTATION",
     "player": "Keon Coleman", "speaker": "Ralph Ventre",
     "text": "On Thursday in Berea, Khalil Shakir and Dante Pettis added to "
             "the group of sidelined wide receivers, per Cameron Wolfe of "
             "NFL Network.",
     "expect_class": ev.RELAYED_REPORTING,
     "expect_mech": None},
    {"id": "geno-about-omar",
     "guards": "a quote about another player filed as the speaker's own account",
     "player": "Geno Smith", "speaker": "Geno Smith",
     "text": '"Omar Cooper Jr. has taken over the slot role for us," Geno '
             'Smith said after practice.',
     "expect_class": ev.DIRECT_QUOTATION,
     "expect_mech": NO},
    {"id": "hankerson-waived",
     "guards": "a waived player described as returning to practice",
     "player": "Anthony Hankerson", "speaker": "Beat Reporter",
     "text": "The team waived running back Anthony Hankerson on Tuesday "
             "afternoon.",
     "expect_class": None, "expect_mech": NO},
    {"id": "wease-missing",
     "guards": "an absent player described as returning",
     "player": "Theo Wease", "speaker": "Alain Poupart",
     "text": "Theo Wease Jr. did not practice for the third straight day.",
     "expect_class": ev.FIRSTHAND_OBSERVATION,
     "expect_mech": "LIMITED_PARTICIPATION", "expect_dir": "NEGATIVE"},
    {"id": "engram-target",
     "guards": "a reception read as a return to practice",
     "player": "Evan Engram", "speaker": "John Shipley",
     "text": "Evan Engram caught two passes over the middle during the "
             "team period.",
     "expect_class": None, "expect_mech": NO},
    {"id": "jones-not-second-team",
     "guards": "a player inheriting another player's unit from proximity",
     "player": "Daniel Jones", "speaker": "Noah Compton",
     "text": "Daniel Jones remains the named starter. Anthony Richardson "
             "ran with the second team throughout the session.",
     "expect_class": None, "expect_mech": NO},
    {"id": "richardson-second-team",
     "guards": "the correct subject still gets his own unit claim",
     "player": "Anthony Richardson", "speaker": "Noah Compton",
     "text": "Anthony Richardson ran with the second team throughout the "
             "session.",
     "expect_class": ev.FIRSTHAND_OBSERVATION,
     "expect_mech": "SECOND_TEAM_REPS"},
    {"id": "wentz-third-team",
     "guards": "third-team work reported as second team",
     "player": "Carson Wentz", "speaker": "Jason Harmon",
     "text": "Carson Wentz worked with the 3s for most of the afternoon.",
     "expect_class": ev.FIRSTHAND_OBSERVATION,
     "expect_mech": "THIRD_TEAM_REPS", "expect_dir": "NEGATIVE"},
    {"id": "mccord-no-first-team",
     "guards": "a heading donating first-team reps to the next paragraph",
     "player": "Kyle McCord", "speaker": "Bill Huber",
     "text": "THE STARTERS' DRILL\nKyle McCord found Bo Melton on a crossing "
             "route for a first down.",
     "expect_class": None, "expect_mech": NO, "segmented": True},
    {"id": "giddens-reaggravated",
     "guards": "a re-injury read as a healthy return",
     "player": "DJ Giddens", "speaker": "Noah Compton",
     "text": "DJ Giddens returned to practice and immediately reaggravated "
             "his hamstring.",
     "expect_class": ev.FIRSTHAND_OBSERVATION,
     "expect_mech": "LIMITED_PARTICIPATION", "expect_dir": "NEGATIVE"},
    {"id": "allen-team-energy",
     "guards": "a team-mood quote turned into fantasy commentary",
     "player": "Josh Allen", "speaker": "Josh Allen",
     "text": '"The energy out here has been unbelievable all camp," Josh '
             'Allen said.',
     "expect_class": ev.DIRECT_QUOTATION, "expect_mech": NO},
]


def run() -> list[dict]:
    out = []
    for f in FIXTURES:
        text = f["text"]
        if f.get("segmented"):
            # Take the span the segmenter would actually produce, so the
            # fixture proves the heading is not donating its subject.
            spans = seg.spans(text)
            text = next((s["text"] for s in spans
                         if f["player"].split()[-1] in s["text"]), text)
        klass, conf, why = ev.classify(text, reporter_voice=True)
        mech = cl.fantasy_mechanism(text, f["player"], klass,
                                    speaker=f["speaker"])
        ok_class = (f["expect_class"] is None or klass == f["expect_class"])
        ok_mech = (f["expect_mech"] is None
                   or mech["mechanism"] == f["expect_mech"])
        ok_dir = ("expect_dir" not in f
                  or mech["direction"] == f["expect_dir"])
        out.append({**f, "text_used": text, "got_class": klass,
                    "got_mech": mech["mechanism"], "got_dir": mech["direction"],
                    "detail": mech["detail"], "reason": why,
                    "pass": ok_class and ok_mech and ok_dir})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html")
    args = ap.parse_args()
    rows = run()
    bad = [r for r in rows if not r["pass"]]
    for r in rows:
        print(f"  [{'ok ' if r['pass'] else 'FAIL'}] {r['id']:<26}"
              f"{r['got_class']:<22}{r['got_mech']:<26}{r['got_dir']}")
        if not r["pass"]:
            print(f"         expected class {r['expect_class']} "
                  f"mech {r['expect_mech']}")
    print(f"\n  {len(rows) - len(bad)}/{len(rows)} fixtures pass")

    if args.html:
        e = html.escape
        parts = ["<title>Wire adversarial fixtures</title>", """<style>
:root{--bg:#faf9f7;--ink:#171a15;--quiet:#5d6157;--rule:#dcd9d2;
--ok:#2f6b3a;--bad:#a4342a}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;--rule:#2c2f27;--ok:#7fbf8a;--bad:#e08a7f}}
:root[data-theme="dark"]{--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;
--rule:#2c2f27;--ok:#7fbf8a;--bad:#e08a7f}
body{background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,
BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:28px}
.wrap{max-width:860px;margin:0 auto}
.f{border:1px solid var(--rule);border-radius:9px;padding:15px;margin:16px 0}
.ok{color:var(--ok);font-weight:700}.bad{color:var(--bad);font-weight:700}
.q{color:var(--quiet);font-size:.85rem}
blockquote{border-left:3px solid var(--rule);margin:8px 0;padding-left:12px}
table{border-collapse:collapse;font-size:.84rem;margin-top:6px}
td{padding:1px 14px 1px 0}
</style>""", '<div class="wrap"><h1>Adversarial fixtures</h1>',
        f'<p class="q">{len(rows) - len(bad)} of {len(rows)} pass. Each case '
        'is a defect found in review, run through the real segmentation, '
        'classification and mechanism path.</p>']
        for r in rows:
            parts.append('<div class="f">')
            parts.append(f'<span class="{"ok" if r["pass"] else "bad"}">'
                         f'{"PASS" if r["pass"] else "FAIL"}</span> '
                         f'<b>{e(r["id"])}</b>')
            parts.append(f'<p class="q">Guards against: {e(r["guards"])}</p>')
            parts.append(f'<blockquote>{e(r["text_used"])}</blockquote>')
            parts.append('<table>'
                         f'<tr><td>player</td><td>{e(r["player"])}</td></tr>'
                         f'<tr><td>classification</td><td>{e(r["got_class"])}</td></tr>'
                         f'<tr><td>mechanism</td><td>{e(r["got_mech"])}</td></tr>'
                         f'<tr><td>direction</td><td>{e(r["got_dir"])}</td></tr>'
                         f'<tr><td>why</td><td>{e(r["detail"])}</td></tr>'
                         '</table></div>')
        parts.append("</div>")
        Path(args.html).write_text("\n".join(parts) + "\n")
        print(f"  wrote {args.html}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
