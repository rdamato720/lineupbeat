#!/usr/bin/env python3
"""Fast accuracy audit. Build a review deck, grade it by keyboard, get numbers.

    python scripts/audit.py --sport nfl --n 200      # build audit/review.html
    open audit/review.html                            # grade it, ~30 minutes
    python scripts/audit.py --score audit/results.json

The 200-nugget check is the gate that decides whether this product works, and
it is the one most likely to get skipped when you are moving fast. So it is
built to be fast: one nugget at a time, source link one click away, four keys,
no mouse. Stratified sampling so you are not grading 200 items from one team.

Grade keys:
    1  correct         everything right
    2  wrong player    resolution failure, the expensive kind
    3  wrong category  or wrong actionability tier
    4  not real news   extractor hallucinated significance
    space              skip, cannot tell without more context

The number that matters is resolution accuracy. Below ~97% you have a trust
problem no interface fixes, and the fix is aliases and prompt, not features.
"""

from __future__ import annotations

import argparse
import html
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatwire.store import Store

ROOT = Path(__file__).resolve().parent.parent


def stratified(rows: list[dict], n: int, seed: int = 7) -> list[dict]:
    """Sample proportionally across (team, category) so the audit reflects the
    feed rather than whichever team had a loud week."""
    rng = random.Random(seed)
    buckets: dict[tuple, list] = defaultdict(list)
    for r in rows:
        buckets[(r["team"], r["category"])].append(r)

    for b in buckets.values():
        rng.shuffle(b)

    out, keys = [], sorted(buckets, key=lambda k: -len(buckets[k]))
    while len(out) < n and any(buckets[k] for k in keys):
        for k in keys:
            if buckets[k] and len(out) < n:
                out.append(buckets[k].pop())
    rng.shuffle(out)
    return out


TEMPLATE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Beatwire audit</title>
<style>
:root{--bg:#ECEEF0;--card:#fff;--ink:#14181C;--rule:#CFD5DA;--quiet:#6B757D;--sig:#A82015;--ok:#1B6B3A}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 ui-sans-serif,system-ui,sans-serif;
     display:flex;flex-direction:column;min-height:100vh}
header{padding:.8rem 1rem;border-bottom:1px solid var(--rule);display:flex;gap:1rem;align-items:baseline;
       font-size:.8rem;letter-spacing:.06em;text-transform:uppercase}
.bar{flex:1;height:4px;background:var(--rule);border-radius:2px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--sig);width:0;transition:width .2s}
main{flex:1;display:flex;align-items:center;justify-content:center;padding:1.5rem 1rem}
.card{background:var(--card);border:1px solid var(--rule);max-width:38rem;width:100%;padding:1.4rem}
.who{font-size:1.5rem;font-weight:700;margin:0 0 .1rem}
.meta{color:var(--quiet);font-size:.8rem;text-transform:uppercase;letter-spacing:.07em;margin-bottom:1rem}
.claim{font-size:1.15rem;margin:0 0 1.2rem}
.src a{color:var(--sig)}
.unres{display:inline-block;border:1px dashed var(--rule);color:var(--quiet);
       font-size:.7rem;padding:.1rem .35rem;text-transform:uppercase;letter-spacing:.07em}
footer{border-top:1px solid var(--rule);padding:.7rem 1rem;display:flex;gap:.5rem;flex-wrap:wrap;
       justify-content:center;background:var(--card)}
kbd{background:var(--bg);border:1px solid var(--rule);border-bottom-width:2px;border-radius:3px;
    padding:.1rem .35rem;font:inherit;font-size:.78rem}
.k{font-size:.8rem;color:var(--quiet)}
#done{display:none;padding:2rem;max-width:40rem;margin:0 auto}
#done h2{margin-top:0}
table{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.9rem}
td,th{text-align:left;padding:.35rem .5rem;border-bottom:1px solid var(--rule)}
.big{font-size:2rem;font-weight:700}
.good{color:var(--ok)} .bad{color:var(--sig)}
textarea{width:100%;height:9rem;font-family:ui-monospace,monospace;font-size:.75rem}
</style>

<header>
  <span id="pos">0 / 0</span>
  <span class="bar"><i id="prog"></i></span>
  <span id="acc"></span>
</header>

<main id="main"><div class="card" id="card"></div></main>
<div id="done"></div>

<footer>
  <span class="k"><kbd>1</kbd> correct</span>
  <span class="k"><kbd>2</kbd> wrong player</span>
  <span class="k"><kbd>3</kbd> wrong category</span>
  <span class="k"><kbd>4</kbd> not real news</span>
  <span class="k"><kbd>space</kbd> skip</span>
  <span class="k"><kbd>u</kbd> undo</span>
</footer>

<script>
const ITEMS = __ITEMS__;
let i = 0; const marks = [];

function render(){
  if(i >= ITEMS.length) return finish();
  const n = ITEMS[i];
  document.getElementById("pos").textContent = (i+1) + " / " + ITEMS.length;
  document.getElementById("prog").style.width = (i/ITEMS.length*100) + "%";
  const graded = marks.filter(m=>m!=="skip");
  const right = marks.filter(m=>m==="correct").length;
  document.getElementById("acc").textContent =
    graded.length ? Math.round(right/graded.length*100) + "% clean" : "";

  const src = n.attributions.map(a =>
    `<a href="${a.url}" target="_blank" rel="noopener">${a.source_name}</a>`).join(" · ");
  document.getElementById("card").innerHTML = `
    <p class="who">${n.player_name} ${n.resolved ? "" : '<span class="unres">unmatched</span>'}</p>
    <p class="meta">${n.team} &middot; ${n.category.replace("_"," ")} &middot;
       tier ${n.actionability} &middot; conf ${n.confidence}</p>
    <p class="claim">${n.claim}</p>
    <p class="src">${src}</p>`;
}

function mark(v){ if(i >= ITEMS.length) return; marks[i] = v; i++; render(); }

function finish(){
  document.getElementById("main").style.display = "none";
  document.querySelector("footer").style.display = "none";
  document.getElementById("prog").style.width = "100%";

  const graded = marks.filter(m=>m && m!=="skip");
  const c = k => marks.filter(m=>m===k).length;
  const resolutionErrors = c("wrong_player");
  const resAcc = graded.length ? (graded.length-resolutionErrors)/graded.length*100 : 0;
  const clean  = graded.length ? c("correct")/graded.length*100 : 0;

  const rows = [
    ["Correct", c("correct")],
    ["Wrong player", c("wrong_player")],
    ["Wrong category or tier", c("wrong_category")],
    ["Not real news", c("not_news")],
    ["Skipped", c("skip")],
  ].map(([k,v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");

  const payload = ITEMS.map((n,idx) => ({
    dedupe_key:n.dedupe_key, player:n.player_name, team:n.team,
    category:n.category, resolved:n.resolved, verdict:marks[idx]||"skip"
  }));

  const d = document.getElementById("done");
  d.style.display = "block";
  d.innerHTML = `
    <h2>Audit complete</h2>
    <p class="big ${resAcc>=97?"good":"bad"}">${resAcc.toFixed(1)}% resolution accuracy</p>
    <p>${resAcc>=97
        ? "Above the bar. Ship it."
        : "Below 97%. Fix aliases and the prompt before anything else."}</p>
    <p class="big">${clean.toFixed(1)}% fully clean</p>
    <table>${rows}</table>
    <p>Save this as <code>audit/results.json</code>, then run
       <code>python scripts/audit.py --score audit/results.json</code></p>
    <textarea readonly>${JSON.stringify(payload,null,1)}</textarea>`;
}

addEventListener("keydown", e => {
  const k = {"1":"correct","2":"wrong_player","3":"wrong_category","4":"not_news"}[e.key];
  if(k) return mark(k);
  if(e.key === " "){ e.preventDefault(); return mark("skip"); }
  if(e.key === "u" && i > 0){ i--; marks.length = i; render(); }
});
render();
</script>
"""


def build(sport: str, n: int, db: str) -> Path:
    store = Store(db)
    rows = store.feed(sport=sport, limit=100000)
    if not rows:
        sys.exit(f"No nuggets for '{sport}'. Run the pipeline first.")
    if len(rows) < n:
        print(f"  only {len(rows)} nuggets available, auditing all of them")
        n = len(rows)

    sample = stratified(rows, n)
    out = ROOT / "audit"
    out.mkdir(exist_ok=True)
    path = out / "review.html"
    path.write_text(TEMPLATE.replace("__ITEMS__", json.dumps(sample)))

    teams = len({r["team"] for r in sample})
    cats = Counter(r["category"] for r in sample)
    unres = sum(1 for r in sample if not r["resolved"])
    print(f"  {len(sample)} nuggets across {teams} teams -> {path}")
    print(f"  categories: {dict(cats)}")
    print(f"  unresolved in sample: {unres}")
    print(f"\n  open it, grade with the number keys, ~30 minutes")
    return path


def score(path: str) -> int:
    data = json.loads(Path(path).read_text())
    graded = [d for d in data if d["verdict"] != "skip"]
    if not graded:
        sys.exit("  nothing graded")

    counts = Counter(d["verdict"] for d in graded)
    res_acc = (len(graded) - counts["wrong_player"]) / len(graded) * 100
    clean = counts["correct"] / len(graded) * 100

    print(f"\n  graded {len(graded)} of {len(data)}")
    print(f"  resolution accuracy   {res_acc:5.1f}%   (gate: 97%)")
    print(f"  fully clean           {clean:5.1f}%")
    for k, v in counts.most_common():
        print(f"    {k:<18} {v}")

    # Where the failures cluster tells you what to fix first.
    bad = [d for d in graded if d["verdict"] == "wrong_player"]
    if bad:
        print("\n  resolution failures by team:")
        for team, n in Counter(d["team"] for d in bad).most_common(8):
            print(f"    {team:<6} {n}")
        print("  A single team dominating usually means a roster or team-code "
              "problem, not a prompt problem. Run doctor.")

    weak = [d for d in graded if d["verdict"] == "wrong_category"]
    if weak:
        print("\n  category failures by category:")
        for cat, n in Counter(d["category"] for d in weak).most_common(6):
            print(f"    {cat:<14} {n}")
        print("  Fix these in the profile's high_value / low_value lists "
              "before touching the system prompt.")

    print()
    if res_acc >= 97:
        print("  PASS. Resolution is above the bar.")
        return 0
    print("  FAIL. Fix aliases and the prompt before building anything else.")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--score", help="path to results.json")
    args = ap.parse_args()

    if args.score:
        sys.exit(score(args.score))
    if not args.sport:
        ap.error("--sport required unless scoring")
    build(args.sport, args.n, args.db)


if __name__ == "__main__":
    main()
