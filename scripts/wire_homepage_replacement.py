#!/usr/bin/env python3
"""The homepage replacement, rendered in context. Preview only.

    python3 scripts/wire_homepage_replacement.py --build

Takes the real homepage, removes the temporary "Latest from the Wire"
module, and replaces the ALL REPORTS section with THE NFL WIRE. The old
section is generated client-side, so the preview neutralises that renderer
rather than adding a second grid beside it: showing both would be the one
outcome the replacement is meant to avoid.

Writes to data/, never to site/. Nothing here deploys.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from wire import display
from wire.store import WireStore

HOME = Path("site/index.html")
OUT = Path("data/wire_homepage_replacement.html")
OUT_JSON = Path("data/wire_homepage_replacement.json")
DECISIONS = Path("data/reviews/backfill_decisions.json")

LABEL = {"POSITIVE": "Trending up", "NEGATIVE": "Trending down",
         "NEUTRAL": "Worth noting", "UNCLEAR": "Worth noting"}
ROLE_LABEL = {"PRIMARY": "Latest practice report",
              "SUPPORTING_OBSERVATION": "Earlier practice observation",
              "OFFICIAL_DESIGNATION": "Club participation report"}


def esc(s):
    return html.escape(str(s or ""), quote=True)


def ago(iso):
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if not t.tzinfo:
        t = t.replace(tzinfo=timezone.utc)
    h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    return "just now" if h < 1 else f"{int(h)}h ago" if h < 24 else f"{int(h//24)}d ago"


CSS = """
/* The replacement section, in the homepage's own dark treatment. Two across
   at 1000px and up, one below, never three. Cards grow with their content;
   the approved analysis is never clamped. */
#lbwire{padding:34px 0 10px}
#lbwire .shead{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
#lbwire .shead h2{margin:0}
#lbwire .n{font-family:var(--agate,inherit);font-size:.72rem;
letter-spacing:.09em;text-transform:uppercase;color:var(--signal,#c8f05a);
font-weight:700}
#lbwire .sub{margin:2px 0 16px}
#lbwire .wfilters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}
#lbwire .wfilters button,#lbwire .wfilters select{font:inherit;font-size:.78rem;
padding:6px 12px;background:transparent;color:var(--quiet,#8f938a);
border:1px solid var(--rule,#262a22);border-radius:99px;cursor:pointer}
#lbwire .wfilters button.on{color:var(--signal,#c8f05a);
border-color:var(--signal,#c8f05a)}
#lbwire .wgrid{display:grid;grid-template-columns:1fr;gap:18px;
align-items:start}
@media(min-width:1000px){#lbwire .wgrid{grid-template-columns:repeat(2,minmax(0,1fr))}}
#lbwire .wc{background:var(--card,#131611);border:1px solid var(--rule,#262a22);
border-radius:14px;padding:18px 19px;display:flex;flex-direction:column}
#lbwire .wtop{display:flex;align-items:center;gap:9px;flex-wrap:wrap;
margin-bottom:10px}
#lbwire .wlogo{width:28px;height:28px;border-radius:7px;
background:#20241c;display:grid;place-items:center;font-size:.6rem;
font-weight:700;color:var(--quiet,#8f938a);flex:none}
#lbwire .wpic{width:34px;height:34px;border-radius:50%;background:#1c2018;
display:grid;place-items:center;font-size:.66rem;font-weight:700;
color:var(--quiet,#8f938a);flex:none}
#lbwire .wb{font-size:.63rem;letter-spacing:.08em;text-transform:uppercase;
font-weight:700;padding:2px 8px;border-radius:99px;
border:1px solid var(--rule,#262a22);color:var(--quiet,#8f938a)}
#lbwire .wb.rank{color:var(--signal,#c8f05a);border-color:var(--signal,#c8f05a)}
#lbwire .wb.up{color:#7fbf8a;border-color:#7fbf8a}
#lbwire .wb.down{color:#e08a7f;border-color:#e08a7f}
#lbwire .wtime{margin-left:auto;color:var(--quiet,#8f938a);font-size:.72rem}
#lbwire .wname{font-size:1.22rem;font-weight:700;margin:0 0 2px}
#lbwire .wlab{font-size:.62rem;letter-spacing:.11em;text-transform:uppercase;
color:var(--quiet,#8f938a);font-weight:700;margin:15px 0 6px}
#lbwire .wrep{font-size:.94rem}
#lbwire .wsrc{color:var(--quiet,#8f938a);font-size:.76rem;margin-top:9px}
#lbwire .wsrc a{color:var(--quiet,#8f938a)}
#lbwire .wown{color:#d6a55a}
#lbwire .wsplit{border:0;border-top:1px solid var(--rule,#262a22);margin:16px 0 0}
#lbwire .wimp{background:#171b13;border-left:3px solid var(--signal,#c8f05a);
border-radius:0 8px 8px 0;padding:12px 14px;margin-top:12px;font-size:.94rem}
#lbwire .wfoot{color:var(--quiet,#8f938a);font-size:.71rem;letter-spacing:.05em;
text-transform:uppercase;margin-top:12px}
"""


def sources_html(c):
    srcs = c.get("sources")
    if not srcs:
        own = c.get("source_ownership") == "TEAM_OWNED"
        return (f'<p class="wsrc">{esc(c.get("author"))}, {esc(c.get("source"))}'
                f' <span class="{"wown" if own else ""}">'
                f'{"Official team source" if own else "Independent"}</span><br>'
                f'<a href="{esc(c.get("url"))}" rel="nofollow noopener" '
                f'target="_blank">Read the original report</a></p>')
    rows = "".join(
        f'<div>{esc(ROLE_LABEL.get(s["role"], s["role"]))}: '
        f'{esc(s.get("author"))}, {esc(s.get("publication"))} &middot; '
        f'{esc(str(s.get("published_at",""))[:10])} '
        f'<span class="{"wown" if s.get("ownership")=="TEAM_OWNED" else ""}">'
        f'{"Official team source" if s.get("ownership")=="TEAM_OWNED" else "Independent"}'
        f'</span> &middot; <a href="{esc(s.get("url"))}" rel="nofollow noopener" '
        f'target="_blank">source</a></div>' for s in srcs)
    ind = c.get("independent_source_count") or 0
    tn = c.get("team_owned_source_count") or 0
    note = (f'{ind} independent reporter' + ('' if ind == 1 else 's')
            + (f' and {tn} club report' + ('' if tn == 1 else 's') if tn else ''))
    return ('<p class="wsrc">' + rows +
            f'<div style="margin-top:5px">{esc(note)}. The club report is '
            f'authoritative for its own designation and is not independent '
            f'corroboration.</div></p>')


def card(c):
    d = c["direction"]
    cls = "up" if d == "POSITIVE" else "down" if d == "NEGATIVE" else ""
    badges = f'<span class="wb">{esc(c["mechanism"].replace("_"," ").title())}</span>'
    if c.get("display_position_rank"):
        badges += f'<span class="wb rank">{esc(c["display_position_rank"])}</span>'
    if c.get("display_adp") is not None:
        badges += f'<span class="wb">ADP {esc(round(float(c["display_adp"]),1))}</span>'
    if c.get("display_projected_points") is not None:
        badges += f'<span class="wb">{esc(c["display_projected_points"])} proj</span>'
    badges += f'<span class="wb {cls}">{esc(c["reader_label"])}</span>'
    initials = "".join(w[0] for w in c["player_name"].split()[:2]).upper()
    return f"""<article class="wc" data-team="{esc(c['team'])}"
   data-pos="{esc(c['position'])}" data-dir="{esc(d)}">
  <div class="wtop">
    <span class="wlogo">{esc(c['team'])}</span>
    <span class="wpic">{esc(initials)}</span>
    {badges}
    <span class="wtime">{esc(ago(c.get('published_at')))}</span>
  </div>
  <p class="wname">{esc(c['player_name'])} <span class="wb">{esc(c['position'])}</span></p>
  <div class="wlab">What the reporter found</div>
  <div class="wrep">{esc(c['reporter_found'])}</div>
  {sources_html(c)}
  <hr class="wsplit">
  <div class="wlab">Lineup Beat impact</div>
  <div class="wimp">{esc(c['lineupbeat_impact'])}</div>
  <p class="wfoot">Evidence {esc(c.get('strength','LOW')).lower()} &middot;
    {esc(c.get('horizon','UNKNOWN')).replace('_',' ').lower()} &middot;
    {'No projection change' if c.get('projection_action')=='NONE' else esc(c.get('projection_action'))}</p>
</article>"""


def collect():
    """The approved set: live publications plus reviewer-approved candidates."""
    store = WireStore()
    out = []
    for p in json.loads(Path("data/wire_publications.json").read_text())["publications"]:
        row = store.conn.execute(
            "SELECT player_id FROM wire_evidence WHERE candidate_id = ?",
            (p.get("evidence_candidate_id", ""),)).fetchone()
        out.append(display.decorate({
            **p, "player_id": row["player_id"] if row else "",
            "published_at": p.get("published_date")}))

    dec = {}
    if DECISIONS.exists():
        for _k, d in json.loads(DECISIONS.read_text())["decisions"].items():
            dec[d["subject"]] = d
    bf = Path("data/wire_backfill_review.json")
    if bf.exists():
        for r in json.loads(bf.read_text())["candidates"]:
            a, c, f = r["assessment"], r["candidate"], r.get("full", {})
            d = dec.get(c["player_name"])
            if not d or not str(d.get("action", "")).startswith("APPROVE"):
                continue
            out.append(display.decorate({
                "player_id": a.get("claim_subject_player_id") or f.get("player_id", ""),
                "player_name": c["player_name"], "team": c["team"],
                "position": c["position"],
                "direction": d["direction"], "mechanism": d["mechanism"],
                "reader_label": d["reader_label"], "strength": d["strength"],
                "horizon": d["horizon"],
                "projection_action": d["projection_action"],
                "reporter_found": d.get("reporter_found") or f.get("evidence_text"),
                "lineupbeat_impact": d["edited_text"],
                "sources": d.get("sources"),
                "independent_source_count": d.get("independent_source_count"),
                "team_owned_source_count": d.get("team_owned_source_count"),
                "source": f.get("publication"), "author": f.get("reporter"),
                "url": f.get("canonical_url"),
                "published_at": f.get("published_at"),
                "source_ownership": f.get("ownership", "INDEPENDENT"),
                "reviewer_action": d["action"]}))
    out.sort(key=lambda x: (str(x.get("published_at", "")),
                            str(x.get("publication_id", x["player_name"]))),
             reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()
    cards = collect()

    teams = sorted({c["team"] for c in cards})
    grid = "".join(card(c) for c in cards)
    section = f"""<section class="wrap sec" id="lbwire">
  <div class="shead">
    <h2>THE NFL WIRE</h2>
    <span class="n">{len(cards)} reviewed report{'' if len(cards)==1 else 's'}</span>
  </div>
  <p class="sub">Verified beat reporting with Lineup Beat's fantasy impact.</p>
  <div class="wfilters">
    <button class="on" data-f="all">All reports</button>
    <button data-f="POSITIVE">Trending up</button>
    <button data-f="NEGATIVE">Trending down</button>
    <button data-f="NEUTRAL">Worth noting</button>
    <select id="wteam"><option value="">Team</option>
      {''.join(f'<option>{esc(t)}</option>' for t in teams)}</select>
    <button data-p="QB">QB</button><button data-p="RB">RB</button>
    <button data-p="WR">WR</button><button data-p="TE">TE</button>
  </div>
  <div class="wgrid">{grid}</div>
  <p class="sub" style="margin-top:14px">
    <a href="/nfl/wire/">View the full Wire</a></p>
</section>"""

    if not HOME.exists():
        print("  site/index.html not built; run the site build first")
        return 1
    home = HOME.read_text()

    # 1. Remove the temporary module so the same reports cannot appear twice.
    removed_module = False
    if "<!-- WIRE MODULE START -->" in home:
        head, rest = home.split("<!-- WIRE MODULE START -->", 1)
        home = head + rest.split("<!-- WIRE MODULE END -->", 1)[1]
        removed_module = True

    # 2. Neutralise the client-side ALL REPORTS renderer. Adding the new grid
    #    without this would show both, which is the outcome the replacement
    #    exists to prevent.
    old_render = 'const restSec = document.createElement("section");'
    replaced_all_reports = old_render in home
    if replaced_all_reports:
        home = home.replace(
            old_render,
            'if (window.__LB_WIRE_REPLACEMENT__) { return; }\n'
            '  const restSec = document.createElement("section");', 1)

    # 3. Insert the replacement immediately before the old feed mount.
    marker = '<main id="feed"></main>'
    if marker in home:
        home = home.replace(
            marker,
            f'<script>window.__LB_WIRE_REPLACEMENT__=true;</script>\n'
            f'{section}\n{marker}', 1)
    home = home.replace("</head>", f"<style>{CSS}</style></head>", 1)

    OUT.write_text(home)
    OUT_JSON.write_text(json.dumps(
        {"published": False, "count_shown": len(cards),
         "removed_latest_from_the_wire_module": removed_module,
         "all_reports_renderer_disabled": replaced_all_reports,
         "cards": cards}, indent=1, default=str) + "\n")

    print(f"  {len(cards)} card(s) in the replacement section")
    for c in cards:
        print(f"    {c['player_name']:<16}{c['team']} {c['position']:<3}"
              f"{c['reader_label']:<14}{c['mechanism']:<24}"
              f"rank={c.get('display_position_rank','-'):<7}"
              f"adp={c.get('display_adp','-')}")
    print(f"  Latest-from-the-Wire module removed: {removed_module}")
    print(f"  old All Reports renderer disabled:  {replaced_all_reports}")
    print(f"  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
