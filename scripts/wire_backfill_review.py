#!/usr/bin/env python3
"""The 48-hour backfill review page. Every legitimate candidate, no padding.

    python3 scripts/wire_backfill_review.py --build

Ordered the way a fantasy manager needs it: availability first, then
depth-chart, then reps, then scoring-area work, then volume, then
quotations. Within each group, newest first, independent firsthand ahead of
the rest.

Suppressed items are summarised separately with their reasons rather than
hidden, because a suppression nobody can see is indistinguishable from a
filter that silently broke.

Nothing here publishes.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import semantic as sem
from wire_backfill import PRIORITY, priority_of

STATE = Path("data/wire_backfill.json")
OUT_HTML = Path("data/wire_backfill_review.html")
OUT_JSON = Path("data/wire_backfill_review.json")

REASONS = ("REJECT_UNSUPPORTED", "REJECT_OVERSTATED",
           "REJECT_NOT_FANTASY_RELEVANT", "REJECT_WRONG_HORIZON",
           "REJECT_WRONG_STRENGTH", "REJECT_WRONG_PLAYER",
           "REJECT_WRONG_DIRECTION", "REJECT_WRONG_UNIT", "REJECT_DUPLICATE")


def esc(s):
    return html.escape(str(s or ""), quote=True)


def rank(item):
    a = item["assessment"]
    return (priority_of(a["fantasy_mechanism"]),
            0 if a["evidence_classification"] == "FIRSTHAND_OBSERVATION" else 1,
            0 if item.get("ownership") == "INDEPENDENT" else 1,
            -(a.get("confidence") or 0),
            str(item.get("published_at", ""))[::-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()
    state = json.loads(STATE.read_text())
    results = state.get("results", [])
    # Only genuine candidates: an abstention is not a proposal, and a
    # NO_FANTASY_IMPACT is the layer declining to speak.
    live = [r for r in results if r["assessment"]["decision"] == "INTERPRET"]
    other = [r for r in results if r["assessment"]["decision"] != "INTERPRET"]
    live.sort(key=rank)

    for r in live:
        r["review_id"] = "bf:" + hashlib.sha256(
            (r["candidate"]["candidate_id"]).encode()).hexdigest()[:16]

    groups = {}
    for r in live:
        label = PRIORITY[priority_of(r["assessment"]["fantasy_mechanism"])][0]
        groups.setdefault(label, []).append(r)

    e = esc
    p = ["<title>48-hour backfill review</title>", """<style>
:root{--bg:#faf9f7;--ink:#171a15;--quiet:#5d6157;--rule:#dcd9d2;--own:#8a5a1b}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;--rule:#2c2f27;--own:#d6a55a}}
:root[data-theme="dark"]{--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;
--rule:#2c2f27;--own:#d6a55a}
body{background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,
BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:26px}
.wrap{max-width:900px;margin:0 auto}
h2{font-size:1rem;margin:30px 0 4px;text-transform:uppercase;
letter-spacing:.08em;font-size:.78rem;color:var(--quiet)}
.item{border:1px solid var(--rule);border-radius:10px;padding:17px;margin:14px 0}
.lab{font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;
color:var(--quiet);font-weight:700;margin:13px 0 5px}
.rep{border-left:3px solid var(--rule);padding-left:12px;font-size:.95rem}
.lb{border-left:3px solid var(--own);padding-left:12px;font-size:.95rem}
.q{color:var(--quiet);font-size:.81rem}
.tag{display:inline-block;border:1px solid var(--rule);border-radius:99px;
padding:1px 9px;font-size:.7rem;margin:2px 4px 2px 0;color:var(--quiet)}
.own{color:var(--own);border-color:var(--own)}
table{border-collapse:collapse;font-size:.82rem}td{padding:1px 13px 1px 0;
vertical-align:top}
button{font:inherit;font-size:.81rem;padding:5px 11px;margin-right:6px;
border:1px solid var(--rule);border-radius:7px;background:transparent;
color:var(--ink);cursor:pointer}button.on{border-color:var(--own);
color:var(--own);font-weight:700}
select,textarea{font:inherit;font-size:.84rem;background:transparent;
color:var(--ink);border:1px solid var(--rule);border-radius:7px;padding:6px;
margin-top:6px;width:100%;box-sizing:border-box}
#panel{position:fixed;right:14px;bottom:14px;background:var(--bg);
border:1px solid var(--rule);border-radius:10px;padding:12px;max-width:320px;
font-size:.78rem;z-index:9}
#out{width:100%;height:100px;font-family:ui-monospace,monospace;font-size:.67rem}
.sup{border:1px dashed var(--rule);border-radius:9px;padding:14px;margin:18px 0;
color:var(--quiet);font-size:.86rem}
</style>""", '<div class="wrap">', "<h1>48-hour backfill review</h1>"]
    w = state["window"]
    c = state.get("claude", {})
    p.append(f'<p class="q">Window {e(w["from"])} to {e(w["to"])}, publisher '
             f'time. Model {e(c.get("model"))}, prompt {e(sem.PROMPT_VERSION)}, '
             f'schema {e(sem.SCHEMA_VERSION)}. {len(live)} candidate(s) for '
             f'review; nothing is published.</p>')

    for label, _ in PRIORITY:
        items = groups.get(label, [])
        if not items:
            continue
        p.append(f"<h2>{e(label)} &mdash; {len(items)}</h2>")
        for r in items:
            a = r["assessment"]
            cd = r["candidate"]
            own = r.get("ownership") == "TEAM_OWNED"
            p.append('<div class="item">')
            p.append(f'<b>{e(cd["player_name"])}</b> <span class="q">'
                     f'{e(cd["team"])} {e(cd["position"])}</span> '
                     f'<span class="tag">{e(a["fantasy_mechanism"])}</span>'
                     f'<span class="tag">{e(a["direction"])}</span>'
                     f'<span class="tag">{e(a["impact_strength"])}/'
                     f'{e(a["impact_horizon"])}</span>'
                     f'<span class="tag{" own" if own else ""}">'
                     f'{"Official team source" if own else "Independent"}</span>')
            p.append(f'<p class="q">admitted: {e(r.get("relevance_reason",""))}</p>')
            p.append('<div class="lab">What the reporter found</div>')
            p.append(f'<div class="rep">{e(cd["evidence_text"])}</div>')
            p.append(f'<p class="q">{e(r.get("author"))}, {e(r.get("source_name"))} '
                     f'&middot; {e(str(r.get("published_at",""))[:10])}<br>'
                     f'<a href="{e(r.get("source_url"))}">source</a></p>')
            p.append('<div class="lab">Lineup Beat impact (Claude, unapproved)</div>')
            p.append(f'<div class="lb">{e(a["fantasy_commentary"])}</div>')
            if a.get("limitations"):
                p.append('<p class="q">' + "; ".join(e(x) for x in a["limitations"]) + "</p>")
            p.append(f'<p class="q">quote: {e(a["supporting_quote"][:150])}<br>'
                     f'validator: {e("; ".join(a["validation_failures"]) or "clean")}'
                     f' &middot; projection action {e(a["projection_action"])}</p>')
            p.append(f'<div class="ctl" data-id="{e(r["review_id"])}">'
                     '<button data-act="APPROVE">Approve</button>'
                     '<button data-act="APPROVE_WITH_EDIT">Approve with edit</button>'
                     '<button data-act="REJECT">Reject</button>'
                     f'<div class="edit" hidden><textarea rows="3">'
                     f'{e(a["fantasy_commentary"])}</textarea></div>'
                     '<div class="rej" hidden><select>'
                     + "".join(f"<option>{x}</option>" for x in REASONS)
                     + '</select></div>'
                     '<textarea class="note" rows="2" placeholder="reviewer note">'
                     '</textarea></div></div>')

    # Suppressions, summarised rather than hidden.
    det = state.get("deterministic", {})
    sup = Counter(x["assessment"]["fantasy_mechanism"] for x in other)
    p.append('<div class="sup"><b>Suppressed, and why</b><br>'
             'Deterministic filters, before any model call:<ul>'
             + "".join(f"<li>{e(k.replace('_',' '))}: {v}</li>"
                       for k, v in sorted(det.items(), key=lambda x: -x[1]))
             + f"</ul>Model outcomes that produced no candidate: "
               f"{len(other)} ({e(str(dict(sup)))})</ul></div>")

    p.append("""
<div id="panel"><b>Decisions</b> <span id="n">0</span>
<textarea id="out" readonly></textarea>
<button onclick="navigator.clipboard.writeText(document.getElementById('out').value)">
Copy JSON</button>
<button onclick="localStorage.removeItem('lb_bf');location.reload()">Clear</button></div>
<script>
const KEY='lb_bf';let D=JSON.parse(localStorage.getItem(KEY)||'{}');
function save(){localStorage.setItem(KEY,JSON.stringify(D));
 document.getElementById('n').textContent=Object.keys(D).length;
 document.getElementById('out').value=JSON.stringify(
  {reviewed_at:new Date().toISOString(),batch:'backfill-48h',decisions:D},null,1);}
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

    OUT_HTML.write_text("\n".join(p) + "\n")
    OUT_JSON.write_text(json.dumps(
        {"window": w, "claude": c, "candidates": live,
         "not_candidates": other, "deterministic": det,
         "published": False}, indent=1, default=str) + "\n")
    print(f"  {len(live)} candidate(s) for review, {len(other)} produced none")
    for label, _ in PRIORITY:
        if groups.get(label):
            print(f"    {len(groups[label]):>3}  {label}")
    print(f"  teams {len({r['candidate']['team'] for r in live})}  "
          f"positions {dict(Counter(r['candidate']['position'] for r in live))}")
    print(f"  wrote {OUT_HTML} and {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
