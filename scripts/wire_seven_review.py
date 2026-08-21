#!/usr/bin/env python3
"""The seven real cases, in full, for human grading.

    python3 scripts/wire_seven_review.py --build

These have no labels. Nothing here is graded, scored or published: the
reviewer's decisions become the labels. Everything needed to judge a case is
on the page -- the complete passage untruncated, the source and its
ownership, the original reporter behind any rewrite, every player the
registry matched, Claude's full structured assessment including pronoun
antecedents and player relationships, and the deterministic validator's
verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import evidence as ev
from wire import semantic as sem
from wire.store import WireStore

BATCH = Path("data/wire_claude_batch.json")
OUT_HTML = Path("data/wire_seven_review.html")
OUT_JSON = Path("data/wire_seven_review.json")

REASONS = ("REJECT_UNSUPPORTED", "REJECT_OVERSTATED",
           "REJECT_NOT_FANTASY_RELEVANT", "REJECT_WRONG_HORIZON",
           "REJECT_WRONG_STRENGTH", "REJECT_WRONG_PLAYER",
           "REJECT_WRONG_DIRECTION", "REJECT_WRONG_UNIT", "REJECT_DUPLICATE")

# What the reviewer said to look at, shown against the case it belongs to.
WATCH = {
    "Geno Smith": "INJURY/POSITIVE from relayed reporting — is the direction "
                  "right, and should a relayed report support an "
                  "interpretation at all?",
    "Anthony Richardson": "is second-team work genuinely negative, or merely "
                          "descriptive of an open competition?",
    "Eli Heidenreich": "do these carries represent meaningful usage, or an "
                       "isolated practice touch?",
    "Quinn Ewers": "is there an actual availability report here, or roster "
                   "speculation?",
    "Chris Blair": "is abstention appropriate?",
    "Joe Burrow": "is abstention appropriate?",
    "Mark Andrews": "does NO_FANTASY_IMPACT correctly suppress "
                    "non-actionable information?",
}


def enrich(row: dict, store: WireStore) -> dict:
    """Pull the stored evidence row so origin and ownership travel with it."""
    cid = row["id"].split("real:")[-1]
    r = store.conn.execute(
        "SELECT * FROM wire_evidence WHERE candidate_id = ?", (cid,)).fetchone()
    out = dict(row)
    if r:
        out.update({
            "article_title": r["source_title"], "article_url": r["source_url"],
            "published_at": r["published_at"],
            "origin_reporter": r["origin_reporter"] or "",
            "origin_outlet": r["origin_outlet"] or "",
            "origin_url": r["origin_url"] or "",
            "underlying_report_id": r["underlying_report_id"] or "",
            "duplicate_of": r["duplicate_of"] or "",
            "stored_classification": r["evidence_class"],
            "evidence_candidate_id": cid,
        })
    origin = ev.origin_of(row["text"])
    out.setdefault("origin_reporter", "")
    out["detected_origin"] = origin
    out["relay_detected"] = bool(ev.RELAY.search(row["text"]))
    out["review_id"] = "seven:" + hashlib.sha256(
        (row["id"] + row["player"]).encode()).hexdigest()[:16]
    out["watch"] = WATCH.get(row["player"], "")
    out["input_hash"] = sem.input_hash(row["text"], [])
    out["output_hash"] = sem.output_hash(
        {k: row.get(k) for k in ("decision", "mechanism", "direction",
                                 "commentary")})
    return out


def render(rows, meta) -> str:
    e = html.escape
    p = ["<title>Seven real cases</title>", """<style>
:root{--bg:#faf9f7;--ink:#171a15;--quiet:#5d6157;--rule:#dcd9d2;--own:#8a5a1b;
--warn:#a4342a}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;--rule:#2c2f27;--own:#d6a55a;
--warn:#e08a7f}}
:root[data-theme="dark"]{--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;
--rule:#2c2f27;--own:#d6a55a;--warn:#e08a7f}
body{background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,
BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:26px}
.wrap{max-width:900px;margin:0 auto}
.item{border:1px solid var(--rule);border-radius:10px;padding:18px;margin:22px 0}
.lab{font-size:.68rem;letter-spacing:.09em;text-transform:uppercase;
color:var(--quiet);font-weight:700;margin:14px 0 5px}
.rep{border-left:3px solid var(--rule);padding-left:12px}
.lb{border-left:3px solid var(--own);padding-left:12px}
.watch{border:1px dashed var(--warn);border-radius:8px;padding:9px 12px;
color:var(--warn);font-size:.86rem;margin-top:10px}
.q{color:var(--quiet);font-size:.83rem}
.tag{display:inline-block;border:1px solid var(--rule);border-radius:99px;
padding:1px 9px;font-size:.71rem;margin:2px 4px 2px 0;color:var(--quiet)}
.own{color:var(--own);border-color:var(--own);font-weight:600}
table{border-collapse:collapse;font-size:.83rem}td{padding:2px 14px 2px 0;
vertical-align:top}
code{font-size:.73rem;color:var(--quiet);word-break:break-all}
button{font:inherit;font-size:.82rem;padding:5px 12px;margin-right:6px;
border:1px solid var(--rule);border-radius:7px;background:transparent;
color:var(--ink);cursor:pointer}button.on{border-color:var(--own);
color:var(--own);font-weight:700}
select,textarea{font:inherit;font-size:.85rem;background:transparent;
color:var(--ink);border:1px solid var(--rule);border-radius:7px;padding:6px;
margin-top:6px;width:100%;box-sizing:border-box}
#panel{position:fixed;right:14px;bottom:14px;background:var(--bg);
border:1px solid var(--rule);border-radius:10px;padding:12px;max-width:330px;
font-size:.79rem;z-index:9;box-shadow:0 3px 14px rgba(0,0,0,.14)}
#out{width:100%;height:110px;font-family:ui-monospace,monospace;font-size:.68rem}
ul{margin:5px 0;padding-left:20px}li{font-size:.88rem}
</style>""", '<div class="wrap">', "<h1>Seven real cases</h1>",
    f'<p class="q">Ungraded. Your decisions become the labels. '
    f'Provider <b>claude</b> &middot; model {e(meta["model"])} &middot; '
    f'prompt {e(sem.PROMPT_VERSION)} &middot; schema {e(sem.SCHEMA_VERSION)} '
    f'&middot; corpus {e(meta["corpus_version"])}. Nothing here is '
    f'published and no projection is touched.</p>']

    for r in rows:
        own = r.get("ownership") == "TEAM_OWNED"
        p.append('<div class="item">')
        p.append(f'<h2>{e(r["player"])} <span class="q">{e(r["team"])} '
                 f'{e(r["position"])}</span></h2>')
        if r["watch"]:
            p.append(f'<div class="watch"><b>You asked to check:</b> '
                     f'{e(r["watch"])}</div>')

        p.append('<div class="lab">What the reporter found (complete passage)</div>')
        p.append(f'<div class="rep">{e(r["text"])}</div>')

        p.append('<div class="lab">Source</div>')
        p.append(f'<p class="q">{e(r.get("source_name",""))} &middot; '
                 f'{e(r.get("author") or "no byline")} &middot; '
                 f'{e(str(r.get("published_at",""))[:10])}<br>'
                 f'{e(r.get("article_title","")[:120])}<br>'
                 f'<code>{e(r.get("article_url",""))}</code><br>'
                 f'<span class="tag{" own" if own else ""}">'
                 f'{"Official team source" if own else "Independent"}</span>'
                 f'<span class="tag">stored class: '
                 f'{e(r.get("stored_classification",""))}</span>'
                 f'<span class="tag">chosen as: {e(r["why_chosen"])}</span></p>')

        det = r["detected_origin"]
        if r["relay_detected"] or det.get("origin_outlet") or det.get("origin_reporter"):
            who = (det.get("origin_reporter") or det.get("origin_outlet")
                   or "not identified")
            p.append('<div class="lab">Relayed reporting</div>')
            p.append(f'<p class="q"><span class="tag own">Relayed reporting '
                     f'&mdash; original source: {e(who)}</span>'
                     + (f'<br>underlying report id: '
                        f'<code>{e(r.get("underlying_report_id",""))}</code>'
                        if r.get("underlying_report_id") else "")
                     + '</p>')

        p.append('<div class="lab">Claude assessment</div>')
        p.append('<table>'
                 f'<tr><td>decision</td><td>{e(r["decision"])}</td>'
                 f'<td>mechanism</td><td>{e(r["mechanism"])}</td></tr>'
                 f'<tr><td>claim subject</td><td>{e(r["subject"] or "-")}</td>'
                 f'<td>direction</td><td>{e(r["direction"])}</td></tr>'
                 f'<tr><td>strength</td><td>{e(r["strength"])}</td>'
                 f'<td>horizon</td><td>{e(r["horizon"])}</td></tr>'
                 f'<tr><td>exact supporting quote</td><td colspan="3">'
                 f'{e(r["quote"])}</td></tr>'
                 '</table>')

        p.append('<div class="lab">Deterministic validator</div>')
        if r["validation"]:
            p.append('<ul>' + "".join(f'<li class="q">{e(v)}</li>'
                                      for v in r["validation"]) + '</ul>')
        else:
            p.append('<p class="q">clean &mdash; exact substring present, '
                     'identity validated, subject-mechanism agreement, '
                     'directionality, unit language, relay classification and '
                     'authority ceilings all passed.</p>')

        if r["commentary"]:
            p.append('<div class="lab">Lineup Beat impact (Claude, unapproved)</div>')
            p.append(f'<div class="lb">{e(r["commentary"])}</div>')
        if r.get("limitations"):
            p.append('<div class="lab">Limitations</div><ul>'
                     + "".join(f'<li class="q">{e(x)}</li>'
                               for x in r["limitations"]) + '</ul>')

        p.append(f'<p class="q">evidence id <code>'
                 f'{e(r.get("evidence_candidate_id",""))}</code> &middot; '
                 f'input <code>{e(r["input_hash"])}</code> &middot; output '
                 f'<code>{e(r["output_hash"])}</code></p>')

        p.append(f'<div class="ctl" data-id="{e(r["review_id"])}">'
                 '<button data-act="APPROVE">Approve</button>'
                 '<button data-act="APPROVE_WITH_EDIT">Approve with edit</button>'
                 '<button data-act="REJECT">Reject</button>'
                 f'<div class="edit" hidden><textarea rows="3">'
                 f'{e(r["commentary"])}</textarea></div>'
                 '<div class="rej" hidden><select>'
                 + "".join(f"<option>{x}</option>" for x in REASONS)
                 + '</select></div>'
                 '<textarea class="note" rows="2" '
                 'placeholder="reviewer note"></textarea></div>')
        p.append('</div>')

    p.append("""
<div id="panel"><b>Decisions</b> <span id="n">0</span>
<textarea id="out" readonly></textarea>
<button onclick="navigator.clipboard.writeText(document.getElementById('out').value)">
Copy JSON</button>
<button onclick="localStorage.removeItem('lb_seven');location.reload()">Clear</button>
<div class="q" style="margin-top:5px">Paste into a file, then
<code>wire_fantasy_review_apply.py</code></div></div>
<script>
const KEY='lb_seven',M=""" + json.dumps({
        "provider": "claude", "model": meta["model"],
        "prompt_version": sem.PROMPT_VERSION,
        "schema_version": sem.SCHEMA_VERSION,
        "corpus_version": meta["corpus_version"]}) + """;
let D=JSON.parse(localStorage.getItem(KEY)||'{}');
function save(){localStorage.setItem(KEY,JSON.stringify(D));
 document.getElementById('n').textContent=Object.keys(D).length;
 document.getElementById('out').value=JSON.stringify(
  Object.assign({reviewed_at:new Date().toISOString()},M,{decisions:D}),null,1);}
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
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()
    batch = json.loads(BATCH.read_text())
    corpus = json.loads(Path("data/wire_eval_corpus.json").read_text())
    store = WireStore()
    rows = [enrich(r, store) for r in batch["real"]]
    meta = {"model": batch["summary"]["model"],
            "corpus_version": corpus["schema_version"],
            "prompt_version": sem.PROMPT_VERSION,
            "schema_version": sem.SCHEMA_VERSION}
    OUT_JSON.write_text(json.dumps(
        {"meta": meta, "graded": False,
         "note": "ungraded; reviewer decisions become the labels",
         "cases": rows}, indent=1, default=str) + "\n")
    OUT_HTML.write_text(render(rows, meta) + "\n")
    print(f"  {len(rows)} cases")
    for r in rows:
        print(f"    {r['player']:<20}{r['team']} {r['position']:<4}"
              f"{r['decision']}/{r['mechanism']}/{r['direction']}  "
              f"{'RELAY' if r['relay_detected'] else '':<6}"
              f"{'validator: ' + str(len(r['validation'])) + ' note(s)' if r['validation'] else 'validator clean'}")
    print(f"  wrote {OUT_HTML} and {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
