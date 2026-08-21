#!/usr/bin/env python3
"""Build /nfl/wire from data/wire_publications.json, and nothing else.

    python3 scripts/build_wire.py --base https://lineupbeat.com
    python3 scripts/build_wire.py --validate-only

This page reads one file. It does not open the candidate table, the review
queue, the raw evidence rows, or any model output that a reviewer has not
approved -- and it cannot, because the only path in is the published JSON,
which only a reviewer writes to.

Validation runs before anything is written and the build stops if it fails.
A malformed or unapproved record must not reach a reader, and failing the
build is the only way to be sure it does not: a page that renders what it
can and drops the rest publishes a silent subset.

The reporter's evidence and Lineup Beat's reading of it are separate blocks
in the markup, not one paragraph with a change of tone. That separation is
the product.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seo

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PUBS = ROOT / "data" / "wire_publications.json"
SPORT = "nfl"
CANONICAL = "/nfl/wire/"

TITLE = "NFL Training Camp & Beat Reporter Wire"
DESC = ("Verified reporting from NFL beat writers, separated from LineupBeat's "
        "fantasy interpretation. Every published item is reviewed before it "
        "appears.")
INTRO = ("Verified reporting from NFL beat writers, separated from Lineup "
         "Beat's fantasy interpretation. Every published item is reviewed "
         "before it appears here.")
DISCLOSURE = ("<b>How to read the Wire:</b> Reporter findings describe the "
              "underlying evidence. Lineup Beat impact is our fantasy "
              "interpretation of that evidence. A report appearing here does "
              "not automatically change our projections.")
EMPTY = ("No reviewed reports are available yet. Check back as new beat "
         "reporting is verified.")

LABELS = {"POSITIVE": "Trending up", "NEGATIVE": "Trending down",
          "NEUTRAL": "Worth noting", "UNCLEAR": "Worth noting"}
DIR_CLASS = {"POSITIVE": "up", "NEGATIVE": "down"}

FANTASY_POS = {"QB", "RB", "WR", "TE"}
# States that may never render, whatever else a record says.
FORBIDDEN_STATES = {"PENDING", "HOLD", "ABSTAIN", "NO_FANTASY_IMPACT",
                    "REJECTED", "SUPERSEDED", "INVALIDATED",
                    "INCONCLUSIVE_TECHNICAL", "HELD_EVIDENCE_CONFLICT"}

REQUIRED = ("publication_id", "player_name", "team", "position", "direction",
            "reader_label", "mechanism", "reporter_found",
            "lineupbeat_impact", "source", "author", "url",
            "source_ownership", "projection_action", "reviewer_action")


def esc(s):
    return html.escape(str(s or ""), quote=True)


def eastern_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def validate(payload: dict) -> list[str]:
    """Every reason this file may not be published. Empty means it may."""
    bad = []
    if not isinstance(payload, dict):
        return ["publications file is not an object"]
    pubs = payload.get("publications")
    if pubs is None or not isinstance(pubs, list):
        return ["publications file has no publications list"]
    if payload.get("count") != len(pubs):
        bad.append(f"count {payload.get('count')} != {len(pubs)} records")

    seen = set()
    for i, p in enumerate(pubs):
        who = p.get("player_name") or f"record {i}"
        for field in REQUIRED:
            if not p.get(field):
                bad.append(f"{who}: missing {field}")
        pid = p.get("publication_id")
        if pid in seen:
            bad.append(f"{who}: duplicate publication_id {pid}")
        seen.add(pid)
        if str(p.get("reviewer_action", "")).upper() in FORBIDDEN_STATES:
            bad.append(f"{who}: state {p['reviewer_action']} may never render")
        if not str(p.get("reviewer_action", "")).startswith("APPROVE"):
            bad.append(f"{who}: not reviewer-approved")
        if p.get("position") not in FANTASY_POS:
            bad.append(f"{who}: {p.get('position')} is not a fantasy position")
        if p.get("direction") not in LABELS:
            bad.append(f"{who}: unknown direction {p.get('direction')}")
        if p.get("reader_label") != LABELS.get(p.get("direction")):
            bad.append(f"{who}: label {p.get('reader_label')!r} does not match "
                       f"direction {p.get('direction')}")
        if not str(p.get("url", "")).startswith("https://"):
            bad.append(f"{who}: source url is not https")
        if p.get("reporter_found") == p.get("lineupbeat_impact"):
            bad.append(f"{who}: evidence and commentary are the same text")
        if p.get("source_ownership") not in ("INDEPENDENT", "TEAM_OWNED"):
            bad.append(f"{who}: unknown source_ownership")
    return bad


def load(path: Path = PUBS) -> tuple[dict, list[dict]]:
    if not path.exists():
        return {"count": 0, "publications": []}, []
    payload = json.loads(path.read_text())
    problems = validate(payload)
    if problems:
        raise ValueError("; ".join(problems[:6]))
    pubs = list(payload["publications"])
    # Newest first, with the publication id as the deterministic tie-break so
    # two cards stamped the same second cannot swap places between builds.
    pubs.sort(key=lambda p: (str(p.get("published_date", "")),
                             str(p.get("publication_id", ""))), reverse=True)
    return payload, pubs


PAGE_CSS = """
.wire-intro{max-width:62ch}
.wire-note{border:1px solid var(--rule); border-left:3px solid var(--signal);
  border-radius:8px; padding:12px 15px; margin:18px 0 6px; max-width:62ch;
  font-size:.85rem; line-height:1.5; color:var(--quiet)}
.wire-filters{display:flex; gap:10px; flex-wrap:wrap; margin:22px 0 6px}
.wire-filters select{font:inherit; font-size:.82rem; padding:7px 10px;
  background:var(--card); color:var(--ink); border:1px solid var(--rule);
  border-radius:8px}
.wire-count{color:var(--quiet); font-size:.8rem; margin:10px 0 0;
  font-family:var(--agate); text-transform:uppercase; letter-spacing:.07em}
.wire-list{margin:18px 0 0}
.wcard{border:1px solid var(--rule); border-radius:11px; padding:18px 19px;
  margin:0 0 16px; background:var(--card)}
.wcard-top{display:flex; align-items:baseline; gap:10px; flex-wrap:wrap}
.wcard-name{font-weight:700; font-size:1.08rem}
.wcard-meta{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.07em; font-size:.72rem; color:var(--quiet)}
.wdir{font-family:var(--agate); text-transform:uppercase; font-weight:700;
  letter-spacing:.07em; font-size:.68rem; padding:2px 9px; border-radius:99px;
  border:1px solid var(--rule); color:var(--quiet)}
.wdir.up{color:#2f6b3a; border-color:#2f6b3a}
.wdir.down{color:#a4342a; border-color:#a4342a}
@media(prefers-color-scheme:dark){.wdir.up{color:#7fbf8a;border-color:#7fbf8a}
  .wdir.down{color:#e08a7f;border-color:#e08a7f}}
.wlab{font-family:var(--agate); text-transform:uppercase; font-weight:700;
  letter-spacing:.09em; font-size:.65rem; color:var(--quiet); margin:15px 0 5px}
.wrep{border-left:3px solid var(--rule); padding-left:13px; font-size:.96rem}
.wimp{border-left:3px solid var(--signal); padding-left:13px; font-size:.96rem}
.wsrc{color:var(--quiet); font-size:.79rem; margin-top:9px}
.wsrc a{color:var(--quiet); text-decoration:underline}
.wown{display:inline-block; border:1px solid var(--rule); border-radius:99px;
  padding:1px 8px; font-size:.68rem; margin-left:4px}
.wown.team{color:#8a5a1b; border-color:#8a5a1b}
@media(prefers-color-scheme:dark){.wown.team{color:#d6a55a;border-color:#d6a55a}}
.wfoot{color:var(--quiet); font-size:.75rem; margin-top:11px;
  font-family:var(--agate); text-transform:uppercase; letter-spacing:.06em}
.wempty{border:1px dashed var(--rule); border-radius:10px; padding:26px;
  text-align:center; color:var(--quiet); margin:24px 0}
"""

FILTER_JS = """
(function(){
  var sel=document.querySelectorAll('.wire-filters select');
  var cards=[].slice.call(document.querySelectorAll('.wcard'));
  var count=document.getElementById('wirecount');
  function apply(){
    var t=document.getElementById('fteam').value,
        p=document.getElementById('fpos').value,
        d=document.getElementById('fdir').value, n=0;
    cards.forEach(function(c){
      var ok=(!t||c.dataset.team===t)&&(!p||c.dataset.pos===p)&&
             (!d||c.dataset.dir===d);
      c.hidden=!ok; if(ok)n++;
    });
    if(count)count.textContent=n+(n===1?' report':' reports');
  }
  sel.forEach(function(s){s.addEventListener('change',apply);});
  apply();
})();
"""


def card_html(p: dict) -> str:
    own = p["source_ownership"] == "TEAM_OWNED"
    unapproved = p.get("_preview")
    dircls = DIR_CLASS.get(p["direction"], "")
    date = str(p.get("published_date", ""))[:10]
    foot = f"Evidence strength {esc(p.get('strength','LOW')).lower()} &middot; " \
           f"{esc(p.get('horizon','UNKNOWN')).replace('_',' ').lower()} horizon"
    if p.get("projection_action") == "NONE":
        foot += " &middot; No projection change"
    return f"""<article class="wcard" data-team="{esc(p['team'])}"
         data-pos="{esc(p['position'])}" data-dir="{esc(p['direction'])}">
  <div class="wcard-top">
    <span class="wcard-name">{esc(p['player_name'])}</span>
    <span class="wcard-meta">{esc(p['team'])} {esc(p['position'])}</span>
    <span class="wdir {dircls}">{esc(p['reader_label'])}</span>
    {'<span class="wdir" style="color:#a4342a;border-color:#a4342a">preview &middot; not approved</span>' if unapproved else ''}
  </div>
  <div class="wlab">What the reporter found</div>
  <div class="wrep">{esc(p['reporter_found'])}</div>
  <p class="wsrc">{esc(p['author'])}, {esc(p['source'])}{' &middot; ' if date else ''}{esc(date)}
    <span class="wown{' team' if own else ''}">{'Official team source' if own else 'Independent'}</span>
    <br><a href="{esc(p['url'])}" rel="nofollow noopener" target="_blank">Read the original report</a></p>
  <div class="wlab">Lineup Beat impact</div>
  <div class="wimp">{esc(p['lineupbeat_impact'])}</div>
  <p class="wfoot">{foot}</p>
</article>"""


def homepage_module(pubs: list[dict], limit: int = 3) -> str:
    """The three most recent cards, for the homepage. Same source, same rules."""
    if not pubs:
        return ""
    items = "".join(
        f"""<a class="fdcard" href="/nfl/wire/">
      <h3>{esc(p['player_name'])} <span class="wcard-meta">{esc(p['team'])} {esc(p['position'])}</span></h3>
      <p>{esc(p['lineupbeat_impact'][:150])}{'&hellip;' if len(p['lineupbeat_impact'])>150 else ''}</p>
      <span class="fdcardbtn">{esc(p['reader_label'])}</span></a>""" for p in pubs[:limit])
    return f"""
<!-- Latest from the Wire. Built by scripts/build_wire.py from
     data/wire_publications.json; it renders nothing a reviewer has not
     approved, and it is rebuilt rather than edited. -->
<section class="wrap fdata" id="wiremod">
  <div class="fdhead">
    <h2>Latest from the Wire</h2>
    <p class="sub">Beat reporting, reviewed, with our fantasy read kept separate.</p>
  </div>
  <div class="fdgrid">{items}</div>
  <p style="margin-top:14px"><a class="fdcardbtn" href="/nfl/wire/">View the full Wire</a></p>
</section>"""


def inject_homepage(pubs: list[dict], index: Path) -> bool:
    """Place the module below the fantasy tools deck, replacing any prior one."""
    if not index.exists():
        return False
    text = index.read_text()
    start, end = "<!-- WIRE MODULE START -->", "<!-- WIRE MODULE END -->"
    block = start + homepage_module(pubs) + end
    if start in text and end in text:
        head = text.split(start)[0]
        tail = text.split(end, 1)[1]
        index.write_text(head + block + tail)
        return True
    anchor = '<section class="wrap fdata" id="fdata">'
    if anchor not in text:
        return False
    close = text.index(anchor)
    close = text.index("</section>", close) + len("</section>")
    index.write_text(text[:close] + "\n" + block + text[close:])
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://lineupbeat.com")
    ap.add_argument("--out")
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--preview-backfill", action="store_true",
                    help="render approved cards plus the backfill candidates, "
                         "each marked unapproved. Preview only; never the "
                         "production build.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        payload, pubs = load()
    except ValueError as e:
        print(f"  publication validation FAILED: {e}")
        print("  refusing to build /nfl/wire")
        return 1
    print(f"  {len(pubs)} approved publication(s) validated")
    for p in pubs:
        print(f"    {p['publication_id']}  {p['player_name']:<20}"
              f"{p['team']} {p['position']:<4}{p['reader_label']}")
    if args.validate_only:
        return 0

    # A preview may show what unapproved candidates would look like. The
    # production build never can: publishable() is the only path into pubs,
    # and this flag is not available to it.
    preview_extra = []
    if args.preview_backfill:
        bf = Path("data/wire_backfill_review.json")
        if bf.exists():
            for r in json.loads(bf.read_text())["candidates"]:
                a, c, f = r["assessment"], r["candidate"], r.get("full", {})
                preview_extra.append({
                    "publication_id": "preview:" + c["candidate_id"][:12],
                    "player_name": c["player_name"], "team": c["team"],
                    "position": c["position"], "direction": a["direction"],
                    "reader_label": LABELS.get(a["direction"], "Worth noting"),
                    "mechanism": a["fantasy_mechanism"],
                    "strength": a["impact_strength"],
                    "horizon": a["impact_horizon"],
                    "projection_action": a["projection_action"],
                    "reporter_found": f.get("evidence_text") or c["evidence_text"],
                    "lineupbeat_impact": a["fantasy_commentary"],
                    "source": f.get("publication", ""),
                    "author": f.get("reporter", ""),
                    "published_date": f.get("published_at", ""),
                    "url": f.get("canonical_url", ""),
                    "source_ownership": f.get("ownership", "INDEPENDENT"),
                    "reviewer_action": "PREVIEW_UNAPPROVED",
                    "_preview": True})
        pubs = pubs + preview_extra
        print(f"  preview: {len(preview_extra)} unapproved candidate(s) added")

    canonical = args.base.rstrip("/") + CANONICAL
    teams = sorted({p["team"] for p in pubs})
    positions = [x for x in ("QB", "RB", "WR", "TE")
                 if any(p["position"] == x for p in pubs)]
    directions = [d for d in ("POSITIVE", "NEGATIVE", "NEUTRAL")
                  if any(p["direction"] == d for p in pubs)]

    if pubs:
        filters = f"""<div class="wire-filters">
  <select id="fteam" aria-label="Filter by team"><option value="">All teams</option>
    {''.join(f'<option value="{esc(t)}">{esc(t)}</option>' for t in teams)}</select>
  <select id="fpos" aria-label="Filter by position"><option value="">All positions</option>
    {''.join(f'<option value="{esc(x)}">{esc(x)}</option>' for x in positions)}</select>
  <select id="fdir" aria-label="Filter by direction"><option value="">Any direction</option>
    {''.join(f'<option value="{esc(d)}">{esc(LABELS[d])}</option>' for d in directions)}</select>
</div>
<p class="wire-count" id="wirecount">{len(pubs)} report{'' if len(pubs)==1 else 's'}</p>"""
        body_cards = f'<div class="wire-list">{"".join(card_html(p) for p in pubs)}</div>'
        script = f"<script>{FILTER_JS}</script>"
    else:
        filters = ""
        body_cards = f'<div class="wempty">{esc(EMPTY)}</div>'
        script = ""

    crumbs = seo.breadcrumbs([("Home", "/"), ("NFL", f"/{SPORT}/data/"),
                              ("Wire", CANONICAL)])
    body = f"""<main class="wrap">
{crumbs}
<h1>The NFL Wire</h1>
<p class="wire-intro">{esc(INTRO)}</p>
<div class="wire-note">{DISCLOSURE}</div>
{filters}
{body_cards}
</main>"""

    ldjson = seo.graph(
        {"@type": "CollectionPage", "name": TITLE, "description": DESC,
         "url": canonical,
         "dateModified": eastern_now().strftime("%Y-%m-%d")},
        {k: v for k, v in seo.breadcrumb_schema(
            [("LineupBeat", "/"), ("NFL", f"/{SPORT}/data/"),
             ("Wire", CANONICAL)], args.base).items() if k != "@context"}
        if hasattr(seo, "breadcrumb_schema") else None,
        seo.ORGANISATION)

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap" rel="stylesheet">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(TITLE)}</title>
<meta name="description" content="{esc(DESC)}">
<link rel="canonical" href="{esc(canonical)}">
{seo.social_meta(TITLE, DESC, canonical)}
<script type="application/ld+json">{ldjson}</script>
<style>{seo.NAV_CSS}{seo.UI_CSS}{seo.CRUMB_CSS}{PAGE_CSS}</style>
</head>
<body>
{seo.site_nav("nflwire")}
{body}
{script}
{seo.TRACKING}
</body>
</html>"""

    if args.dry_run:
        print("  --dry-run, nothing written")
        return 0

    if args.preview_backfill and not args.out:
        args.out = "data/wire_page_preview.html"
    out = Path(args.out) if args.out else SITE / SPORT / "wire" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(seo.check_page(page, str(out)))
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out            # an --out outside the repo is fine to report raw
    print(f"  wrote {shown} ({len(page):,} bytes)")

    if args.preview_backfill:
        print("  preview written; the live page and homepage are untouched")
        return 0
    if inject_homepage(pubs, SITE / "index.html"):
        print(f"  homepage module: {min(len(pubs), 3)} card(s)")
    else:
        print("  homepage not built yet; module skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
