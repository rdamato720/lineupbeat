#!/usr/bin/env python3
"""The priority batch: 23 gold cases and 7 newly discovered real segments.

    python3 scripts/wire_claude_batch.py --run

Runs the smoke test first and refuses to continue if it fails, because a run
begun on a broken key yields a page of abstentions that read as model caution
rather than an authentication error.

Gold cases are graded against hand-written expectations and reported case by
case. The seven real cases are not graded -- they have no labels yet -- and
are put in front of a human. Unlabelled material is never counted as a pass.
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import players as pl
from wire import semantic as sem
from wire import semantic_validate as sv
from wire.providers.claude import ClaudeSemanticProvider

CORPUS = Path("data/wire_eval_corpus.json")
OUT_JSON = Path("data/wire_claude_batch.json")
OUT_HTML = Path("data/wire_claude_batch.html")

# The seven real cases are chosen to cover what the reviewer asked to see:
# an injury, a return, unit work at each level, a beneficiary, a quotation
# and a relayed report.
WANT_REAL = [
    ("an availability absence", lambda x: "not participating" in x["text"].lower()
     or "did not practice" in x["text"].lower()),
    ("a return to practice", lambda x: "returned to practice" in x["text"].lower()
     or "back at practice" in x["text"].lower()),
    ("first-team work", lambda x: "first-team" in x["text"].lower()
     or "with the ones" in x["text"].lower()),
    ("second-team work", lambda x: "second-team" in x["text"].lower()
     or "second team" in x["text"].lower()),
    ("third-team work", lambda x: "third-team" in x["text"].lower()
     or "with the threes" in x["text"].lower() or " 3s" in x["text"].lower()),
    ("a direct quotation", lambda x: '"' in x["text"] and "said" in x["text"].lower()),
    ("a relayed report", lambda x: any(
        w in x["text"].lower() for w in ("according to", "per ", "reported"))),
]


FANTASY_POS = {"QB", "RB", "WR", "TE"}


def pick_real(items, n=7):
    """Real cases for review. Fantasy positions only.

    The first pass returned five defensive players and a kicker, which the
    layer is not permitted to interpret at all -- a review sample the
    reviewer cannot act on is wasted reviewer time.
    """
    items = [x for x in items
             if x["kind"] != "UNLABELLED"
             or x["players"][0]["position"] in FANTASY_POS]
    chosen, used = [], set()
    for label, test in WANT_REAL:
        if len(chosen) >= n:
            break
        for x in items:
            if x["id"] in used or x["kind"] != "UNLABELLED":
                continue
            if test(x):
                chosen.append({**x, "why_chosen": label})
                used.add(x["id"])
                break
    for x in items:
        if len(chosen) >= n:
            break
        if x["kind"] == "UNLABELLED" and x["id"] not in used:
            chosen.append({**x, "why_chosen": "additional real segment"})
            used.add(x["id"])
    return chosen[:n]


def render(gold_rows, real_rows, summary) -> str:
    e = html.escape
    p = ["<title>Claude priority batch</title>", """<style>
:root{--bg:#faf9f7;--ink:#171a15;--quiet:#5d6157;--rule:#dcd9d2;
--ok:#2f6b3a;--bad:#a4342a;--own:#8a5a1b}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;--rule:#2c2f27;--ok:#7fbf8a;
--bad:#e08a7f;--own:#d6a55a}}
:root[data-theme="dark"]{--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;
--rule:#2c2f27;--ok:#7fbf8a;--bad:#e08a7f;--own:#d6a55a}
body{background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,
BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:26px}
.wrap{max-width:920px;margin:0 auto}
.item{border:1px solid var(--rule);border-radius:10px;padding:16px;margin:18px 0}
.ok{color:var(--ok);font-weight:700}.bad{color:var(--bad);font-weight:700}
.q{color:var(--quiet);font-size:.84rem}
.lab{font-size:.68rem;letter-spacing:.09em;text-transform:uppercase;
color:var(--quiet);font-weight:700;margin:12px 0 4px}
.rep{border-left:3px solid var(--rule);padding-left:12px}
.lb{border-left:3px solid var(--own);padding-left:12px}
table{border-collapse:collapse;font-size:.82rem}td{padding:1px 14px 1px 0;
vertical-align:top}
code{font-size:.75rem;color:var(--quiet);word-break:break-all}
button{font:inherit;font-size:.8rem;padding:5px 11px;margin-right:6px;
border:1px solid var(--rule);border-radius:7px;background:transparent;
color:var(--ink);cursor:pointer}button.on{border-color:var(--own);
color:var(--own);font-weight:700}
select,textarea{font:inherit;font-size:.84rem;background:transparent;
color:var(--ink);border:1px solid var(--rule);border-radius:7px;padding:6px;
margin-top:6px;width:100%;box-sizing:border-box}
#panel{position:fixed;right:14px;bottom:14px;background:var(--bg);
border:1px solid var(--rule);border-radius:10px;padding:12px;max-width:320px;
font-size:.78rem;z-index:9}
#out{width:100%;height:100px;font-family:ui-monospace,monospace;font-size:.68rem}
</style>""", '<div class="wrap">', "<h1>Claude priority batch</h1>"]
    p.append(f'<p class="q">Provider <b>claude</b> &middot; model '
             f'{e(summary["model"])} &middot; schema {e(sem.SCHEMA_VERSION)} '
             f'&middot; prompt {e(sem.PROMPT_VERSION)}. Nothing here is '
             f'published.</p>')
    p.append(f'<p class="q">Gold: {summary["gold_correct"]}/{summary["gold_n"]} '
             f'correct &middot; precision {e(summary["precision"])} &middot; '
             f'recall {e(summary["recall"])} &middot; cost '
             f'${summary["cost_usd"]:.4f} &middot; median '
             f'{summary["median_latency_ms"]}ms.</p>')

    p.append("<h2>Gold cases</h2>")
    for g in gold_rows:
        p.append('<div class="item">')
        p.append(f'<span class="{"ok" if g["pass"] else "bad"}">'
                 f'{"PASS" if g["pass"] else "FAIL"}</span> <b>{e(g["id"])}</b>')
        p.append(f'<p class="q">guards: {e(g["guards"])}</p>')
        p.append(f'<div class="rep">{e(g["text"])}</div>')
        p.append('<table>'
                 f'<tr><td>expected</td><td>{e(json.dumps(g["expected"]))}</td></tr>'
                 f'<tr><td>claude decision</td><td>{e(g["decision"])}</td></tr>'
                 f'<tr><td>claim subject</td><td>{e(g["subject"] or "-")}</td></tr>'
                 f'<tr><td>mechanism</td><td>{e(g["mechanism"])}</td></tr>'
                 f'<tr><td>direction</td><td>{e(g["direction"])}</td></tr>'
                 f'<tr><td>supporting quote</td><td>{e(g["quote"][:180])}</td></tr>'
                 f'<tr><td>validation</td><td>{e("; ".join(g["validation"]) or "clean")}</td></tr>'
                 f'<tr><td>abstention</td><td>{e(g["abstention"] or "-")}</td></tr>'
                 f'<tr><td>tokens / cost / latency</td><td>{g["tokens_in"]}/'
                 f'{g["tokens_out"]} &middot; ${g["cost"]:.5f} &middot; '
                 f'{g["latency"]}ms</td></tr>'
                 f'<tr><td>errors</td><td>{e(", ".join(g["errors"]) or "none")}</td></tr>'
                 '</table></div>')

    p.append("<h2>Real cases &mdash; ungraded, for your review</h2>")
    p.append('<p class="q">These have no labels. They are not counted as '
             'passes; your decisions become the labels.</p>')
    for r in real_rows:
        p.append(f'<div class="item"><b>{e(r["player"])}</b> '
                 f'<span class="q">{e(r["team"])} {e(r["position"])} &middot; '
                 f'chosen as {e(r["why_chosen"])}</span>')
        p.append('<div class="lab">What the reporter found</div>')
        p.append(f'<div class="rep">{e(r["text"])}</div>')
        p.append(f'<p class="q">{e(r["source_name"])} &middot; '
                 f'{e(r["author"] or "no byline")} &middot; '
                 f'{e(r["ownership"])}<br><code>{e(r["url"])}</code></p>')
        p.append('<div class="lab">Claude assessment</div>')
        p.append('<table>'
                 f'<tr><td>decision</td><td>{e(r["decision"])}</td>'
                 f'<td>mechanism</td><td>{e(r["mechanism"])}</td></tr>'
                 f'<tr><td>subject</td><td>{e(r["subject"] or "-")}</td>'
                 f'<td>direction</td><td>{e(r["direction"])}</td></tr>'
                 f'<tr><td>strength</td><td>{e(r["strength"])}</td>'
                 f'<td>horizon</td><td>{e(r["horizon"])}</td></tr>'
                 f'<tr><td>quote</td><td colspan="3">{e(r["quote"][:200])}</td></tr>'
                 f'<tr><td>validation</td><td colspan="3">'
                 f'{e("; ".join(r["validation"]) or "clean")}</td></tr>'
                 '</table>')
        if r["commentary"]:
            p.append('<div class="lab">Lineup Beat impact</div>')
            p.append(f'<div class="lb">{e(r["commentary"])}</div>')
        if r["limitations"]:
            p.append('<div class="lab">Limitations</div><ul>'
                     + "".join(f"<li class='q'>{e(x)}</li>"
                               for x in r["limitations"]) + "</ul>")
        p.append(f'<div class="ctl" data-id="{e(r["id"])}">'
                 '<button data-act="APPROVE">Approve</button>'
                 '<button data-act="APPROVE_WITH_EDIT">Approve with edit</button>'
                 '<button data-act="REJECT">Reject</button>'
                 f'<div class="edit" hidden><textarea rows="3">'
                 f'{e(r["commentary"])}</textarea></div>'
                 '<div class="rej" hidden><select>'
                 + "".join(f"<option>{x}</option>" for x in
                           ("REJECT_UNSUPPORTED", "REJECT_OVERSTATED",
                            "REJECT_NOT_FANTASY_RELEVANT", "REJECT_WRONG_HORIZON",
                            "REJECT_WRONG_STRENGTH", "REJECT_WRONG_PLAYER",
                            "REJECT_WRONG_DIRECTION", "REJECT_WRONG_UNIT",
                            "REJECT_DUPLICATE"))
                 + '</select></div>'
                 '<textarea class="note" rows="2" placeholder="reviewer note">'
                 '</textarea></div></div>')

    p.append("""
<div id="panel"><b>Decisions</b> <span id="n">0</span>
<textarea id="out" readonly></textarea>
<button onclick="navigator.clipboard.writeText(document.getElementById('out').value)">
Copy JSON</button>
<button onclick="localStorage.removeItem('lb_claude');location.reload()">Clear</button></div>
<script>
const KEY='lb_claude';let D=JSON.parse(localStorage.getItem(KEY)||'{}');
function save(){localStorage.setItem(KEY,JSON.stringify(D));
 document.getElementById('n').textContent=Object.keys(D).length;
 document.getElementById('out').value=JSON.stringify(
  {reviewed_at:new Date().toISOString(),provider:'claude',decisions:D},null,1);}
document.querySelectorAll('.ctl').forEach(c=>{const id=c.dataset.id,
 ed=c.querySelector('.edit'),rj=c.querySelector('.rej'),nt=c.querySelector('.note');
 c.querySelectorAll('button').forEach(b=>b.onclick=()=>{
  c.querySelectorAll('button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');const a=b.dataset.act;
  ed.hidden=a!=='APPROVE_WITH_EDIT';rj.hidden=a!=='REJECT';
  D[id]={action:a,edited_text:a==='APPROVE_WITH_EDIT'?ed.querySelector('textarea').value:'',
   reason:a==='REJECT'?rj.querySelector('select').value:'',note:nt.value};save();});
 nt.oninput=()=>{if(D[id]){D[id].note=nt.value;save();}};
 if(D[id]){const b=c.querySelector(`button[data-act="${D[id].action}"]`);
  if(b)b.classList.add('on');}});
save();</script>""")
    p.append("</div>")
    return "\n".join(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--real", type=int, default=7)
    ap.add_argument("--skip-smoke", action="store_true")
    args = ap.parse_args()

    if not args.skip_smoke:
        rc = subprocess.run([sys.executable, "scripts/wire_claude_smoke.py"],
                            capture_output=True, text=True)
        print(rc.stdout.rstrip())
        if rc.returncode != 0:
            print(f"  smoke test failed (exit {rc.returncode}); "
                  f"the Claude batch will not run")
            return rc.returncode

    corpus = json.loads(CORPUS.read_text())
    gold = [x for x in corpus["items"] if x["kind"] == "GOLD"]
    real = pick_real(corpus["items"], args.real)
    reg = pl.load()
    prov = ClaudeSemanticProvider()

    from wire_semantic_eval import grade

    gold_rows, costs, lats = [], [], []
    for item in gold:
        a = prov.evaluate(item["text"], item["metadata"], item["players"])
        a = sv.enforce(a, item["text"], item["players"], reg, item["metadata"])
        g = grade(item, a)
        costs.append(a.cost_usd)
        lats.append(a.latency_ms)
        gold_rows.append({
            "id": item["id"], "guards": item["guards"], "text": item["text"],
            "expected": item["expected"], "decision": a.decision,
            "subject": a.claim_subject_player_name,
            "mechanism": a.fantasy_mechanism, "direction": a.direction,
            "quote": a.supporting_quote, "validation": a.validation_failures,
            "abstention": a.abstention_reason, "tokens_in": a.tokens_in,
            "tokens_out": a.tokens_out, "cost": a.cost_usd,
            "latency": a.latency_ms, "errors": g["errors"], "pass": g["correct"]})

    real_rows = []
    for item in real:
        a = prov.evaluate(item["text"], item["metadata"], item["players"])
        a = sv.enforce(a, item["text"], item["players"], reg, item["metadata"])
        costs.append(a.cost_usd)
        lats.append(a.latency_ms)
        p0 = item["players"][0]
        real_rows.append({
            "id": item["id"], "why_chosen": item["why_chosen"],
            "text": item["text"], "player": p0["player_name"],
            "team": p0["team"], "position": p0["position"],
            "source_name": item["metadata"].get("source_name", ""),
            "author": item["metadata"].get("author", ""),
            "ownership": item["metadata"].get("source_ownership", ""),
            "url": item["metadata"].get("article_url", ""),
            "decision": a.decision, "mechanism": a.fantasy_mechanism,
            "subject": a.claim_subject_player_name, "direction": a.direction,
            "strength": a.impact_strength, "horizon": a.impact_horizon,
            "quote": a.supporting_quote, "validation": a.validation_failures,
            "commentary": a.fantasy_commentary,
            "limitations": a.limitations})

    graded = [g for g in gold_rows]
    interp = [g for g in graded if g["decision"] == "INTERPRET"]
    should = [g for g in graded if g["expected"]["decision"] == "INTERPRET"]
    err = Counter(e for g in graded for e in g["errors"])
    summary = {
        "model": prov.model,
        "gold_n": len(graded),
        "gold_correct": sum(1 for g in graded if g["pass"]),
        "precision": f"{sum(1 for g in interp if g['pass'])}/{len(interp)}",
        "recall": f"{sum(1 for g in should if g['pass'])}/{len(should)}",
        "errors": dict(err),
        "abstentions": sum(1 for g in graded if g["decision"] == "ABSTAIN"),
        "validation_failures": sum(1 for g in graded if g["validation"]),
        "cost_usd": sum(costs),
        "median_latency_ms": int(statistics.median(lats or [0])),
        "p95_latency_ms": int(sorted(lats or [0])[max(0, int(len(lats) * .95) - 1)]),
        "real_ungraded": len(real_rows),
    }
    OUT_JSON.write_text(json.dumps(
        {"summary": summary, "gold": gold_rows, "real": real_rows},
        indent=1, default=str) + "\n")
    OUT_HTML.write_text(render(gold_rows, real_rows, summary) + "\n")

    print(f"\n  gold {summary['gold_correct']}/{summary['gold_n']} correct")
    print(f"  precision {summary['precision']}  recall {summary['recall']}")
    for k, v in sorted(err.items(), key=lambda x: -x[1]):
        print(f"    {k:<26}{v}")
    print(f"  abstentions {summary['abstentions']}  "
          f"validation failures {summary['validation_failures']}")
    print(f"  cost ${summary['cost_usd']:.4f}  median "
          f"{summary['median_latency_ms']}ms  p95 {summary['p95_latency_ms']}ms")
    print(f"  {len(real_rows)} real cases for review (ungraded)")
    print(f"  wrote {OUT_JSON} and {OUT_HTML}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
