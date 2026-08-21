#!/usr/bin/env python3
"""Two-column homepage preview of the replacement section. Nothing is live.

    python3 scripts/wire_homepage_preview.py --build

Shows what the replacement for ALL REPORTS would look like: the existing
dark card treatment, the team logo, the position-rank and ADP badges joined
display-only by player_id, and the reporter's evidence and our commentary in
unmistakably separate panels.

Two across at 1000px and up, one below. Never three. Cards grow with their
content and the approved commentary is never clamped.

The approved publication is shown as it would render. The backfill
candidates are shown as they *would* render if approved, and are labelled
so on every card -- a preview that looks identical to the live thing is how
an unapproved card gets mistaken for a decision.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import display
from wire.store import WireStore

OUT = Path("data/wire_homepage_preview.html")
OUT_JSON = Path("data/wire_homepage_preview.json")

LABEL = {"POSITIVE": "Trending up", "NEGATIVE": "Trending down",
         "NEUTRAL": "Worth noting", "UNCLEAR": "Worth noting"}


def esc(s):
    return html.escape(str(s or ""), quote=True)


def ago(iso: str) -> str:
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if not t.tzinfo:
        t = t.replace(tzinfo=timezone.utc)
    h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    if h < 1:
        return "just now"
    if h < 24:
        return f"{int(h)}h ago"
    return f"{int(h // 24)}d ago"


CSS = """
:root{--bg:#0b0d0a;--card:#131611;--ink:#e9e7e1;--quiet:#8f938a;
--rule:#262a22;--signal:#c8f05a;--up:#7fbf8a;--down:#e08a7f;--own:#d6a55a}
body{background:var(--bg);color:var(--ink);margin:0;padding:26px;
font:16px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.wrap{max-width:1180px;margin:0 auto}
.sechead{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;
margin:0 0 4px}
.sechead h2{font-size:1.5rem;margin:0;letter-spacing:-.01em}
.count{font-size:.72rem;letter-spacing:.09em;text-transform:uppercase;
color:var(--signal);font-weight:700}
.sub{color:var(--quiet);font-size:.9rem;margin:0 0 18px}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px}
.filters button,.filters select{font:inherit;font-size:.79rem;padding:6px 12px;
background:transparent;color:var(--quiet);border:1px solid var(--rule);
border-radius:99px;cursor:pointer}
.filters button.on{color:var(--signal);border-color:var(--signal)}
/* Two across, never three. Below 1000px it is one. */
.grid{display:grid;grid-template-columns:1fr;gap:18px;align-items:start}
@media(min-width:1000px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
.c{background:var(--card);border:1px solid var(--rule);border-radius:14px;
padding:18px 19px;display:flex;flex-direction:column}
.chead{display:flex;align-items:center;gap:9px;flex-wrap:wrap;
margin-bottom:10px}
.logo{width:26px;height:26px;border-radius:6px;background:#20241c;
display:grid;place-items:center;font-size:.62rem;font-weight:700;
color:var(--quiet);flex:none}
.badge{font-size:.63rem;letter-spacing:.08em;text-transform:uppercase;
font-weight:700;padding:2px 8px;border-radius:99px;border:1px solid var(--rule);
color:var(--quiet)}
.badge.rank{color:var(--signal);border-color:var(--signal)}
.badge.up{color:var(--up);border-color:var(--up)}
.badge.down{color:var(--down);border-color:var(--down)}
.time{margin-left:auto;color:var(--quiet);font-size:.72rem}
.name{font-size:1.22rem;font-weight:700;margin:0 0 2px}
.lab{font-size:.62rem;letter-spacing:.11em;text-transform:uppercase;
color:var(--quiet);font-weight:700;margin:15px 0 6px}
.rep{font-size:.93rem;color:#d6d4ce}
.src{color:var(--quiet);font-size:.76rem;margin-top:9px}
.src a{color:var(--quiet)}
.own{color:var(--own)}
/* The divider that keeps the two claims apart. */
.split{border:0;border-top:1px solid var(--rule);margin:16px 0 0}
.imp{background:#171b13;border-left:3px solid var(--signal);
border-radius:0 8px 8px 0;padding:12px 14px;margin-top:12px;font-size:.93rem}
.foot{color:var(--quiet);font-size:.71rem;letter-spacing:.05em;
text-transform:uppercase;margin-top:12px}
.pending{color:var(--own);border:1px dashed var(--own);border-radius:6px;
padding:1px 7px;font-size:.62rem;letter-spacing:.07em;text-transform:uppercase}
"""


def card(item, pending=False) -> str:
    d = item["direction"]
    cls = "up" if d == "POSITIVE" else "down" if d == "NEGATIVE" else ""
    rank = item.get("display_position_rank")
    adp = item.get("display_adp")
    pts = item.get("display_projected_points")
    badges = f'<span class="badge">{esc(item["mechanism"].replace("_"," ").title())}</span>'
    if rank:
        badges += f'<span class="badge rank">{esc(rank)}</span>'
    if adp is not None:
        badges += f'<span class="badge">ADP {esc(round(float(adp),1))}</span>'
    if pts is not None:
        badges += f'<span class="badge">{esc(pts)} proj</span>'
    badges += f'<span class="badge {cls}">{esc(LABEL.get(d,d))}</span>'
    own = item.get("ownership") == "TEAM_OWNED"
    return f"""<article class="c" data-team="{esc(item['team'])}"
   data-pos="{esc(item['position'])}" data-dir="{esc(d)}">
  <div class="chead">
    <span class="logo">{esc(item['team'])}</span>
    {badges}
    {'<span class="pending">preview &middot; not approved</span>' if pending else ''}
    <span class="time">{esc(ago(item.get('published_at')))}</span>
  </div>
  <p class="name">{esc(item['player_name'])}
     <span class="badge">{esc(item['position'])}</span></p>
  <div class="lab">What the reporter found</div>
  <div class="rep">{esc(item['reporter_found'])}</div>
  <p class="src">{esc(item.get('author'))}, {esc(item.get('source'))}
     <span class="{'own' if own else ''}">
     {'Official team source' if own else 'Independent'}</span><br>
     <a href="{esc(item.get('url'))}" rel="nofollow noopener">Read the original report</a></p>
  <hr class="split">
  <div class="lab">Lineup Beat impact</div>
  <div class="imp">{esc(item['lineupbeat_impact'])}</div>
  <p class="foot">Evidence {esc(item.get('strength','LOW')).lower()} &middot;
     {esc(item.get('horizon','UNKNOWN')).replace('_',' ').lower()} &middot;
     {'No projection change' if item.get('projection_action')=='NONE' else esc(item.get('projection_action'))}</p>
</article>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()
    store = WireStore()

    approved = []
    for p in json.loads(Path("data/wire_publications.json").read_text())["publications"]:
        row = store.conn.execute(
            "SELECT player_id FROM wire_evidence WHERE candidate_id = ?",
            (p.get("evidence_candidate_id", ""),)).fetchone()
        approved.append(display.decorate({
            **p, "player_id": row["player_id"] if row else "",
            "published_at": p.get("published_date")}))

    pending = []
    bf = Path("data/wire_backfill_review.json")
    if bf.exists():
        for r in json.loads(bf.read_text())["candidates"]:
            a, c = r["assessment"], r["candidate"]
            pending.append(display.decorate({
                "player_id": a.get("claim_subject_player_id") or "",
                "player_name": c["player_name"], "team": c["team"],
                "position": c["position"], "direction": a["direction"],
                "mechanism": a["fantasy_mechanism"],
                "strength": a["impact_strength"], "horizon": a["impact_horizon"],
                "projection_action": a["projection_action"],
                "reporter_found": c["evidence_text"],
                "lineupbeat_impact": a["fantasy_commentary"],
                "source": r.get("source_name"), "author": r.get("author"),
                "url": r.get("source_url"), "published_at": r.get("published_at"),
                "ownership": r.get("ownership")}))

    visible = approved            # the count is approved cards only
    e = esc
    body = "".join(card(x) for x in approved) + \
           "".join(card(x, pending=True) for x in pending)
    page = f"""<title>Homepage Wire preview</title>
<style>{CSS}</style>
<div class="wrap">
<div class="sechead"><h2>THE NFL WIRE</h2>
  <span class="count">{len(visible)} reviewed report{'' if len(visible)==1 else 's'}</span></div>
<p class="sub">Verified beat reporting with Lineup Beat's fantasy impact.</p>
<div class="filters">
  <button class="on">All reports</button><button>Trending up</button>
  <button>Trending down</button><button>Worth noting</button>
  <select><option>Team</option></select>
  <button>QB</button><button>RB</button><button>WR</button><button>TE</button>
</div>
<p class="sub">Below: {len(approved)} approved card{'' if len(approved)==1 else 's'},
  then {len(pending)} backfill candidate{'' if len(pending)==1 else 's'} shown
  as they would render if approved. The count above deliberately counts only
  approved reports.</p>
<div class="grid">{body}</div>
</div>"""
    OUT.write_text(page + "\n")
    OUT_JSON.write_text(json.dumps(
        {"published": False, "approved_visible": len(approved),
         "pending_preview": len(pending),
         "count_shown": len(visible),
         "cards": approved + pending}, indent=1, default=str) + "\n")
    print(f"  approved cards {len(approved)}, backfill preview {len(pending)}")
    print(f"  headline count shows {len(visible)} (approved only)")
    joined = sum(1 for x in approved + pending if x.get("display_join") == "player_id")
    print(f"  display join by player_id succeeded for {joined}/"
          f"{len(approved)+len(pending)}")
    print(f"  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
