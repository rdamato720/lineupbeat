#!/usr/bin/env python3
"""The private fantasy-spin review: readable page plus machine-readable JSON.

    python3 scripts/wire_fantasy_review.py --build --min 30

Nothing here publishes, writes to wire_publications.json, touches
projections, rankings or stat lines, or approves anything. It selects real
stored evidence, shows the whole passage a reviewer needs, and puts Lineup
Beat's reading of it in a separate block that can never be mistaken for
something the reporter wrote.

Categories are filled from real evidence or reported as unavailable. A
category with no supporting evidence is stated plainly rather than
illustrated with something invented, because the point of this review is to
find out what the pipeline actually produces.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import evidence as ev
from wire import fantasy as fz
from wire import players as pl
from wire import registry as artreg
from wire import si
from wire.store import WireStore

OUT_JSON = Path("data/wire_fantasy_review.json")
OUT_HTML = Path("data/wire_fantasy_review.html")

WANTED = [
    ("position QB", lambda i: i["position"] == "QB"),
    ("position RB", lambda i: i["position"] == "RB"),
    ("position WR", lambda i: i["position"] == "WR"),
    ("position TE", lambda i: i["position"] == "TE"),
    ("impact POSITIVE", lambda i: i["fantasy_impact"] == "POSITIVE"),
    ("impact NEGATIVE", lambda i: i["fantasy_impact"] == "NEGATIVE"),
    ("impact NEUTRAL", lambda i: i["fantasy_impact"] == "NEUTRAL"),
    ("impact UNCLEAR", lambda i: i["fantasy_impact"] == "UNCLEAR"),
    ("strength LOW", lambda i: i["impact_strength"] == "LOW"),
    ("strength MEDIUM", lambda i: i["impact_strength"] == "MEDIUM"),
    ("strength HIGH", lambda i: i["impact_strength"] == "HIGH"),
    ("horizon IMMEDIATE", lambda i: i["impact_horizon"] == "IMMEDIATE"),
    ("horizon SHORT_TERM", lambda i: i["impact_horizon"] == "SHORT_TERM"),
    ("horizon SEASON_LONG", lambda i: i["impact_horizon"] == "SEASON_LONG"),
    ("horizon UNKNOWN", lambda i: i["impact_horizon"] == "UNKNOWN"),
    ("action NONE", lambda i: i["projection_action"] == "NONE"),
    ("action REVIEW", lambda i: i["projection_action"] == "REVIEW"),
    ("action UPDATE_RECOMMENDED",
     lambda i: i["projection_action"] == "UPDATE_RECOMMENDED"),
    ("firsthand observation",
     lambda i: i["evidence_classification"] == "FIRSTHAND_OBSERVATION"),
    ("direct quotation",
     lambda i: i["evidence_classification"] == "DIRECT_QUOTATION"),
    ("relayed reporting",
     lambda i: i["evidence_classification"] == "RELAYED_REPORTING"),
    ("independent reporting", lambda i: i["source_ownership"] == "INDEPENDENT"),
    ("official team transaction or injury",
     lambda i: i["source_ownership"] == "TEAM_OWNED"),
    ("duplicate reports of one development",
     lambda i: i["duplicate_reports"] > 0),
    ("multiple independent reports", lambda i: i["independent_source_count"] > 1),
    ("conflicting evidence", lambda i: i["conflicting"]),
]

# What the evidence, by its own shape, does not establish. Stated for every
# item, because the limitation is the part a reader most needs and the part
# a confident sentence most easily hides.
LIMITS = {
    "FIRST_TEAM_REPS": "First-team reps do not confirm a starting job.",
    "SECOND_TEAM_REPS": "Second-team work in camp is not a settled depth chart.",
    "RED_ZONE": "No scoring-area workload share was reported.",
    "TARGETS": "No target share was established.",
    "CARRIES": "No carry share was established.",
    "SNAP_SHARE": "No snap percentage was reported.",
    "ROUTES": "No route participation rate was reported.",
    "DEPTH_CHART": "A depth-chart mention is not a formally announced change.",
    "INJURY": "No injury timetable was provided.",
    "RETURN_TO_PRACTICE": "Returning to practice does not confirm a game-day role.",
    "LIMITED_PARTICIPATION": "Limited participation does not establish a timetable.",
    "COACH_QUOTATION": "This is a coach quote rather than observed usage.",
    "PLAYER_QUOTATION": "This is the player's own account rather than observed usage.",
    "PERFORMANCE": "One notable play is not a role change.",
    "ROLE_EXPANSION": "Camp usage does not always survive into the season.",
    "ROLE_REDUCTION": "A quiet practice is not a demotion.",
    "OTHER": "This does not yet establish a change in role or usage.",
}

PROJECTION_ASSUMPTIONS = {
    "FIRST_TEAM_REPS": ["Depth-chart position", "Snap share"],
    "SECOND_TEAM_REPS": ["Depth-chart position", "Snap share"],
    "RED_ZONE": ["Goal-line role", "Touchdown rate"],
    "TARGETS": ["Target share", "Route participation"],
    "CARRIES": ["Carry share", "Rushing attempts"],
    "SNAP_SHARE": ["Snap share"],
    "ROUTES": ["Route participation", "Target share"],
    "DEPTH_CHART": ["Depth-chart position", "Snap share", "Expected games"],
    "INJURY": ["Injury availability", "Expected games"],
    "RETURN_TO_PRACTICE": ["Injury availability", "Expected games"],
    "LIMITED_PARTICIPATION": ["Injury availability"],
    "COACH_QUOTATION": ["Depth-chart position"],
    "PLAYER_QUOTATION": ["Depth-chart position"],
    "PERFORMANCE": [],
    "ROLE_EXPANSION": ["Snap share", "Target share", "Carry share"],
    "ROLE_REDUCTION": ["Snap share", "Carry share"],
    "OTHER": [],
}

HIGH_RULE = ("official material act reported by the club, which is the "
             "authoritative source for its own transactions")


def build(store, limit: int) -> list[dict]:
    reg = pl.load()
    sources = {s.source_id: s for s in artreg.load()}
    auth = si.load_authors()
    rows = {r["candidate_id"]: dict(r) for r in store.evidence()}

    suppressed_file = Path("data/wire_fantasy_suppressed.json")
    suppressed = (json.loads(suppressed_file.read_text())["items"]
                  if suppressed_file.exists() else [])

    items = []
    for imp in store.impacts():
        if imp["review_status"] in ("INVALIDATED", "REJECTED", "SUPERSEDED"):
            continue
        ids = json.loads(imp["evidence_candidate_ids"])
        support = [rows[i] for i in ids if i in rows]
        if not support:
            continue
        lead = sorted(support,
                      key=lambda r: {"FIRSTHAND_OBSERVATION": 0,
                                     "DIRECT_QUOTATION": 1,
                                     "ANALYSIS_OR_OPINION": 2,
                                     "RELAYED_REPORTING": 3,
                                     "UNCERTAIN": 4}.get(r["evidence_class"], 9))[0]
        src = sources.get(lead["source_id"])
        team_owned = (lead["source_ownership"] or "") == "TEAM_OWNED"

        # Conflicting evidence: the supporting spans do not point one way.
        dirs = {fz.direction(r["evidence_text"]) for r in support}
        conflicting = len({d for d in dirs if d in ("POSITIVE", "NEGATIVE")}) > 1

        acls = ((auth.get("teams", {}).get(lead["team"], {}) or {})
                .get("authors", {})
                .get(lead["source_author_or_channel"], {})
                .get("classification", "UNKNOWN"))

        dup_reports = store.conn.execute(
            "SELECT COUNT(*) c FROM wire_evidence WHERE duplicate_of = ?",
            (lead["candidate_id"],)).fetchone()["c"]

        items.append({
            "fantasy_impact_id": imp["fantasy_impact_id"],
            "player_name": imp["player_name"], "player_id": imp["player_id"],
            "team": imp["team"], "position": imp["position"],
            "registry_version": lead["registry_version"],
            "identity_confidence": lead["resolution_confidence"],
            "evidence_confidence": lead["classification_confidence"],
            "evidence_text": lead["evidence_text"],
            "evidence_classification": lead["evidence_class"],
            "classification_reason": lead["classification_reasons"],
            "publication": (src.source_name if src else lead["source_id"]),
            "author": lead["source_author_or_channel"],
            "article_title": lead["source_title"],
            "published_at": lead["published_at"],
            "article_url": lead["source_url"],
            "source_ownership": lead["source_ownership"] or "INDEPENDENT",
            "source_class": (src.source_class if src else ""),
            "author_classification": acls,
            "origin_reporter": lead["origin_reporter"] or "",
            "origin_outlet": lead["origin_outlet"] or "",
            "origin_url": lead["origin_url"] or "",
            "underlying_report_id": lead["underlying_report_id"] or "",
            "fantasy_impact": imp["fantasy_impact"],
            "impact_strength": imp["impact_strength"],
            "impact_horizon": imp["impact_horizon"],
            "role_signal": imp["role_signal"],
            "projection_action": imp["projection_action"],
            "lineupbeat_commentary": imp["lineupbeat_commentary"],
            "reasoning": imp["reasoning"],
            "independent_source_count": imp["independent_source_count"],
            "team_owned_source_count": sum(
                1 for r in support
                if (r["source_ownership"] or "") == "TEAM_OWNED"),
            "supporting_evidence_ids": ids,
            "evidence_group_ids": json.loads(imp["evidence_group_ids"]),
            "article_ids": json.loads(imp["source_article_ids"]),
            "underlying_report_ids": sorted(
                {r["underlying_report_id"] for r in support
                 if r["underlying_report_id"]}),
            "duplicate_reports": dup_reports,
            "conflicting": conflicting,
            "review_status": imp["review_status"],
            "generator": imp["generator"],
            "why_it_matters": (
                f"The evidence is a {lead['evidence_class'].replace('_', ' ').lower()} "
                f"describing {imp['role_signal'].replace('_', ' ').lower()} for "
                f"{imp['player_name']}, which bears on the opportunity side of his "
                f"value rather than on efficiency. It is graded "
                f"{imp['impact_strength']} because it rests on "
                f"{imp['source_count']} span(s) from "
                f"{imp['independent_source_count']} independent reporter(s)"
                + (" and a team-owned source, which cannot corroborate the club"
                   if team_owned else "") + "."),
            "not_claiming": [LIMITS.get(imp["role_signal"], LIMITS["OTHER"])]
            + (["This comes from a team-owned source."] if team_owned else [])
            + (["The supporting reports conflict."] if conflicting else [])
            + (["Multiple articles repeat one original report."]
               if dup_reports else [])
            + (["No projection change is justified yet."]
               if imp["projection_action"] == "NONE" else []),
            "projection_assumptions": (
                PROJECTION_ASSUMPTIONS.get(imp["role_signal"], [])
                if imp["projection_action"] in ("REVIEW", "UPDATE_RECOMMENDED")
                else []),
            "high_rule": (HIGH_RULE if imp["impact_strength"] == "HIGH" else ""),
        })

    # Spread across teams and positions before topping up, so a sample of
    # thirty is not one team's camp reported thirty ways.
    buckets = defaultdict(list)
    for it in sorted(items, key=lambda x: x["fantasy_impact_id"]):
        buckets[(it["team"], it["position"])].append(it)
    picked, i = [], 0
    while len(picked) < limit:
        added = False
        for key in sorted(buckets):
            if i < len(buckets[key]):
                picked.append(buckets[key][i])
                added = True
                if len(picked) >= limit:
                    break
        if not added:
            break
        i += 1
    # Make sure every rare category present in the data survives the spread.
    chosen = {p["fantasy_impact_id"] for p in picked}
    for _, test in WANTED:
        if any(test(p) for p in picked):
            continue
        extra = next((x for x in items
                      if test(x) and x["fantasy_impact_id"] not in chosen), None)
        if extra:
            picked.append(extra)
            chosen.add(extra["fantasy_impact_id"])
    # Suppressed cases are part of the review: the reviewer needs to see
    # where the layer correctly declines to speak, not only where it speaks.
    for sup in suppressed[:max(0, limit - len(picked)) + 12]:
        row = next((rows[c] for c in sup.get("evidence_candidate_ids", [])
                    if c in rows), None)
        picked.append({
            "fantasy_impact_id": f"suppressed:{sup['player_id']}",
            "suppressed": True,
            "player_name": sup["player_name"], "player_id": sup["player_id"],
            "team": sup["team"], "position": sup["position"],
            "registry_version": (row or {}).get("registry_version", ""),
            "identity_confidence": (row or {}).get("resolution_confidence", 0),
            "evidence_confidence": (row or {}).get("classification_confidence", 0),
            "evidence_text": sup.get("evidence_text", "")
            or (row or {}).get("evidence_text", ""),
            "evidence_classification": (row or {}).get("evidence_class", ""),
            "classification_reason": (row or {}).get("classification_reasons", ""),
            "publication": (row or {}).get("source_id", ""),
            "author": (row or {}).get("source_author_or_channel", ""),
            "article_title": (row or {}).get("source_title", ""),
            "published_at": (row or {}).get("published_at", ""),
            "article_url": (row or {}).get("source_url", ""),
            "source_ownership": (row or {}).get("source_ownership", "INDEPENDENT"),
            "source_class": "", "author_classification": "",
            "origin_reporter": "", "origin_outlet": "", "origin_url": "",
            "underlying_report_id": "",
            "fantasy_impact": "NO_FANTASY_IMPACT", "impact_strength": "-",
            "impact_horizon": "-", "role_signal": "NO_FANTASY_IMPACT",
            "projection_action": "NONE",
            "lineupbeat_commentary": "",
            "reasoning": sup["reason"],
            "independent_source_count": 0, "team_owned_source_count": 0,
            "supporting_evidence_ids": sup.get("evidence_candidate_ids", []),
            "evidence_group_ids": [], "article_ids": [],
            "underlying_report_ids": [], "duplicate_reports": 0,
            "conflicting": False, "review_status": "SUPPRESSED",
            "generator": fz.GENERATOR,
            "why_it_matters": "No fantasy interpretation is offered.",
            "not_claiming": [sup["reason"]],
            "projection_assumptions": [], "high_rule": ""})
    return picked


def render_html(items: list[dict], gaps: list[str]) -> str:
    e = html.escape
    out = ["<title>Lineup Beat fantasy-spin review</title>", """<style>
:root{--bg:#faf9f7;--ink:#171a15;--quiet:#5d6157;--rule:#dcd9d2;--own:#8a5a1b}
:root:not([data-theme="light"]){}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;--rule:#2c2f27;--own:#d6a55a}}
:root[data-theme="dark"]{--bg:#12140f;--ink:#e9e7e1;--quiet:#9a9d93;
--rule:#2c2f27;--own:#d6a55a}
body{background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,
BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:28px}
.wrap{max-width:900px;margin:0 auto}
.item{border:1px solid var(--rule);border-radius:10px;padding:18px;margin:22px 0}
h1{font-size:1.5rem} h2{font-size:1.05rem;margin:0 0 4px}
.meta{color:var(--quiet);font-size:.83rem}
.sec{margin:14px 0 0}
.lab{font-size:.7rem;letter-spacing:.09em;text-transform:uppercase;
color:var(--quiet);font-weight:700;margin-bottom:5px}
.rep{border-left:3px solid var(--rule);padding-left:12px}
.lb{border-left:3px solid var(--own);padding-left:12px}
.tag{display:inline-block;border:1px solid var(--rule);border-radius:99px;
padding:1px 9px;font-size:.72rem;margin:2px 4px 2px 0;color:var(--quiet)}
.own{color:var(--own);border-color:var(--own);font-weight:600}
ul{margin:5px 0;padding-left:20px} li{font-size:.9rem}
code{font-size:.76rem;color:var(--quiet);word-break:break-all}
table{border-collapse:collapse;font-size:.8rem}
td{padding:1px 12px 1px 0;vertical-align:top}
.gap{border:1px dashed var(--rule);border-radius:8px;padding:12px;
color:var(--quiet);font-size:.9rem}
.sup{opacity:.72}
.ctl{margin-top:14px;padding-top:12px;border-top:1px dashed var(--rule)}
button{font:inherit;font-size:.82rem;padding:5px 12px;margin-right:6px;
border:1px solid var(--rule);border-radius:7px;background:transparent;
color:var(--ink);cursor:pointer}
button.on{border-color:var(--own);color:var(--own);font-weight:700}
select,textarea{font:inherit;font-size:.85rem;background:transparent;
color:var(--ink);border:1px solid var(--rule);border-radius:7px;padding:6px;
margin-top:6px;width:100%;max-width:100%;box-sizing:border-box}
#panel{position:fixed;right:14px;bottom:14px;background:var(--bg);
border:1px solid var(--rule);border-radius:10px;padding:12px;max-width:330px;
font-size:.8rem;box-shadow:0 3px 14px rgba(0,0,0,.14);z-index:9}
#out{width:100%;height:110px;font-family:ui-monospace,monospace;font-size:.7rem}
</style>""", '<div class="wrap">',
    "<h1>Lineup Beat fantasy-spin review</h1>",
    f'<p class="meta">{len(items)} items from real stored evidence. '
    'Nothing here is published. "What the reporter found" is the source\'s '
    'reporting; "Lineup Beat impact" is our own interpretation and is never '
    'presented as anything the reporter said.</p>']
    if gaps:
        out.append('<div class="gap"><b>Categories with no supporting evidence '
                   'in the current corpus</b> — stated rather than filled with '
                   'invented examples:<ul>'
                   + "".join(f"<li><b>{e(g)}</b> — {e(why)}</li>"
                             for g, why in gaps) + "</ul></div>")
    for it in items:
        own = it["source_ownership"] == "TEAM_OWNED"
        sup = it.get("suppressed")
        out.append(f'<div class="item{" sup" if sup else ""}">')
        if sup:
            out.append('<p class="meta"><b>NO FANTASY IMPACT</b> — the layer '
                       'declines to comment. Shown so the suppression can be '
                       'reviewed too.</p>')
        out.append(f'<h2>{e(it["player_name"])} '
                   f'<span class="meta">{e(it["team"])} {e(it["position"])}</span></h2>')
        out.append(f'<p class="meta">id {e(it["player_id"])} &middot; registry '
                   f'{e(str(it["registry_version"]))} &middot; identity confidence '
                   f'{it["identity_confidence"]:.2f}</p>')
        out.append('<div class="sec"><div class="lab">What the reporter found</div>')
        out.append(f'<div class="rep">{e(it["evidence_text"])}</div></div>')
        out.append('<div class="sec"><div class="lab">Source</div>'
                   f'<p class="meta">{e(it["publication"])} &middot; '
                   f'{e(it["author"] or "no byline")} &middot; '
                   f'{e(it["article_title"][:110])} &middot; '
                   f'{e(str(it["published_at"])[:10])}<br>'
                   f'<code>{e(it["article_url"])}</code><br>'
                   f'<span class="tag">{e(it["source_class"] or "?")}</span>'
                   f'<span class="tag{" own" if own else ""}">'
                   f'{"Official team source" if own else "Independent"}</span>'
                   f'<span class="tag">author: {e(it["author_classification"])}</span>')
        if it["evidence_classification"] == "RELAYED_REPORTING":
            origin = it["origin_reporter"] or it["origin_outlet"] or "unnamed"
            out.append(f'<br><span class="tag own">Relayed reporting — '
                       f'original source: {e(origin)}</span>')
        out.append('</p></div>')
        out.append('<div class="sec"><div class="lab">Evidence classification</div>'
                   f'<p class="meta">{e(it["evidence_classification"])} — '
                   f'{e(str(it["classification_reason"]))}</p></div>')
        out.append('<div class="sec"><div class="lab">Lineup Beat impact</div>'
                   f'<div class="lb">{e(it["lineupbeat_commentary"])}</div></div>')
        out.append('<div class="sec"><div class="lab">Structured assessment</div>'
                   '<table>'
                   f'<tr><td>impact</td><td>{e(it["fantasy_impact"])}</td>'
                   f'<td>strength</td><td>{e(it["impact_strength"])}</td></tr>'
                   f'<tr><td>horizon</td><td>{e(it["impact_horizon"])}</td>'
                   f'<td>role signal</td><td>{e(it["role_signal"])}</td></tr>'
                   f'<tr><td>projection action</td><td>{e(it["projection_action"])}</td>'
                   f'<td>evidence confidence</td><td>{it["evidence_confidence"]:.2f}</td></tr>'
                   f'<tr><td>independent sources</td><td>{it["independent_source_count"]}</td>'
                   f'<td>team-owned sources</td><td>{it["team_owned_source_count"]}</td></tr>'
                   '</table>'
                   f'<p class="meta">evidence {e(", ".join(it["supporting_evidence_ids"][:4]))}'
                   f'<br>groups {e(", ".join(it["evidence_group_ids"][:3]))}'
                   f'<br>articles {e(", ".join(a[:70] for a in it["article_ids"][:2]))}'
                   + (f'<br>underlying reports {e(", ".join(it["underlying_report_ids"]))}'
                      if it["underlying_report_ids"] else "") + '</p></div>')
        if it["high_rule"]:
            out.append(f'<p class="meta"><b>HIGH permitted by:</b> '
                       f'{e(it["high_rule"])}</p>')
        out.append('<div class="sec"><div class="lab">Why it matters</div>'
                   f'<p>{e(it["why_it_matters"])}</p></div>')
        out.append('<div class="sec"><div class="lab">What we are not claiming</div>'
                   '<ul>' + "".join(f"<li>{e(x)}</li>" for x in it["not_claiming"])
                   + '</ul></div>')
        if it["projection_assumptions"]:
            out.append('<div class="sec"><div class="lab">Projection review</div>'
                       '<p class="meta">Assumptions a projection analyst may want '
                       'to inspect. No value is changed automatically.</p><ul>'
                       + "".join(f"<li>{e(x)}</li>" for x in it["projection_assumptions"])
                       + '</ul></div>')
        # Per-item controls. Decisions live in localStorage and are exported
        # as JSON for wire_fantasy_review_apply.py; nothing publishes from
        # this page, and the original generated text is never overwritten.
        fid = it["fantasy_impact_id"]
        out.append(f'<div class="ctl" data-id="{e(fid)}">'
                   f'<button data-act="APPROVE">Approve</button>'
                   f'<button data-act="APPROVE_WITH_EDIT">Approve with edit</button>'
                   f'<button data-act="REJECT">Reject</button>'
                   f'<div class="edit" hidden><textarea rows="3" '
                   f'placeholder="edited commentary">'
                   f'{e(it["lineupbeat_commentary"])}</textarea></div>'
                   f'<div class="rej" hidden><select>'
                   + "".join(f'<option>{r}</option>' for r in
                             ("REJECT_UNSUPPORTED", "REJECT_OVERSTATED",
                              "REJECT_NOT_FANTASY_RELEVANT",
                              "REJECT_WRONG_HORIZON", "REJECT_WRONG_STRENGTH",
                              "REJECT_WRONG_PLAYER", "REJECT_DUPLICATE"))
                   + '</select></div></div>')
        out.append('</div>')
    out.append("""
<div id="panel"><b>Decisions</b> <span id="n">0</span>
<textarea id="out" readonly></textarea>
<button onclick="navigator.clipboard.writeText(document.getElementById('out').value)">
Copy JSON</button>
<button onclick="localStorage.removeItem('lb_decisions');location.reload()">Clear</button>
<div style="color:var(--quiet);margin-top:5px">Paste into a file, then run
<code>wire_fantasy_review_apply.py</code></div></div>
<script>
const KEY='lb_decisions';
let D=JSON.parse(localStorage.getItem(KEY)||'{}');
function save(){localStorage.setItem(KEY,JSON.stringify(D));
 document.getElementById('n').textContent=Object.keys(D).length;
 document.getElementById('out').value=JSON.stringify(
   {reviewed_at:new Date().toISOString(),decisions:D},null,1);}
document.querySelectorAll('.ctl').forEach(c=>{
 const id=c.dataset.id, ed=c.querySelector('.edit'), rj=c.querySelector('.rej');
 c.querySelectorAll('button').forEach(b=>b.onclick=()=>{
  c.querySelectorAll('button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');
  const act=b.dataset.act;
  ed.hidden = act!=='APPROVE_WITH_EDIT';
  rj.hidden = act!=='REJECT';
  D[id]={action:act,
         edited_text: act==='APPROVE_WITH_EDIT'? ed.querySelector('textarea').value : '',
         reason: act==='REJECT'? rj.querySelector('select').value : ''};
  save();});
 if(ed) ed.querySelector('textarea').oninput=()=>{
   if(D[id]&&D[id].action==='APPROVE_WITH_EDIT'){
     D[id].edited_text=ed.querySelector('textarea').value;save();}};
 if(rj) rj.querySelector('select').onchange=()=>{
   if(D[id]&&D[id].action==='REJECT'){
     D[id].reason=rj.querySelector('select').value;save();}};
 if(D[id]){const b=c.querySelector(`button[data-act="${D[id].action}"]`);
   if(b)b.classList.add('on');}
});
save();
</script>""")
    out.append("</div>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--min", type=int, default=30)
    args = ap.parse_args()

    store = WireStore()
    items = build(store, args.min)
    gaps = [name for name, test in WANTED if not any(test(i) for i in items)]
    # Two different kinds of absence, and conflating them would hide a
    # design decision behind what looks like thin data.
    BY_DESIGN = {
        "relayed reporting":
            "unfillable by design: relayed reporting may never support a "
            "fantasy interpretation, so it cannot appear as an item's lead "
            "evidence. Relayed spans exist in the corpus and are excluded "
            "upstream.",
    }
    gaps = [(g, BY_DESIGN.get(g, "no qualifying evidence in the current corpus"))
            for g in gaps]

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(
        {"generated_items": len(items),
         "categories_without_evidence": gaps,
         "note": "private review material; nothing here is published",
         "items": items}, indent=1) + "\n")
    OUT_HTML.write_text(render_html(items, gaps) + "\n")

    print(f"  {len(items)} review items")
    print(f"  teams {len({i['team'] for i in items})}, "
          f"positions {sorted({i['position'] for i in items})}")
    for f in ("fantasy_impact", "impact_strength", "impact_horizon",
              "projection_action", "evidence_classification",
              "source_ownership"):
        print(f"    {f:<26}{dict(Counter(i[f] for i in items))}")
    if gaps:
        print(f"  categories not represented ({len(gaps)}):")
        for g, why in gaps:
            print(f"      {g}: {why[:66]}")
    print(f"  wrote {OUT_JSON} and {OUT_HTML}")
    print("  nothing published; wire_publications.json untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
