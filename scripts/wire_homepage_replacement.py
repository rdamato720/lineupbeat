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
import re
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


def payload_of(text):
    i = text.find("const DATA = ")
    if i < 0:
        return None
    j = text.find("\n", i)
    try:
        return json.loads(text[i + len("const DATA = "):j].rstrip(";"))
    except ValueError:
        return None


def ago(iso):
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if not t.tzinfo:
        t = t.replace(tzinfo=timezone.utc)
    h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    return "just now" if h < 1 else f"{int(h)}h ago" if h < 24 else f"{int(h//24)}d ago"


TEMPLATE = Path("site/template.html")


def team_colors():
    """The homepage's own palette, read from the homepage.

    Parsed rather than copied. A second hand-maintained copy of 32 colour
    pairs is a copy that drifts, and the left border of a Wire card has to
    be the same blue as the left border of every other card on the page.
    """
    m = re.search(r"const TEAM_COLORS = \{(.*?)\n\};", TEMPLATE.read_text(),
                  re.S)
    out = {}
    if m:
        for code, c1, c2 in re.findall(
                r'(\w+):\s*\[\s*"(#[0-9A-Fa-f]{6})"\s*,\s*"(#[0-9A-Fa-f]{6})"',
                m.group(1)):
            out[code] = (c1, c2)
    return out


PAGE = 8          # cards visible before "Load more"; not a publication cap

COLORS = team_colors()
FALLBACK = ("#2A3136", "#6B757D")


def headshot(player_ref):
    """Sleeper keys its CDN on the site's own player id, as headshotURL does."""
    bare = re.sub(r"^[a-z]+-", "", str(player_ref or ""))
    return (f"https://sleepercdn.com/content/nfl/players/thumb/{bare}.jpg"
            if bare else "")


def espn_headshot(espn_id):
    return (f"https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png"
            if espn_id else "")


def team_logo(team):
    t = str(team or "").lower()
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{t}.png" if t else ""


CSS = """
/* The Wire section reuses the homepage's own card, not a second design.
   `.tile` supplies the dark ground, the 8px radius, the team-coloured left
   border and the masked headshot; everything here is the delta a Wire card
   needs -- two columns rather than three, and text that is never clamped,
   because a reviewer approved that wording in full. */
#lbwire{padding:34px 0 10px}
#lbwire .shead{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
#lbwire .shead h2{margin:0}
#lbwire .sub{margin:2px 0 16px}
#lbwire .wfilters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}
#lbwire .wfilters button,#lbwire .wfilters select{font:inherit;font-size:.78rem;
padding:6px 12px;background:transparent;color:var(--quiet,#8f938a);
border:1px solid var(--rule,#262a22);border-radius:99px;cursor:pointer}
#lbwire .wfilters button.on{color:var(--signal,#C6F53C);
border-color:var(--signal,#C6F53C)}

/* Two across, one on a phone. Never three: a Wire card carries two blocks of
   prose where a tile carries one line, and at a third of the width the
   evidence and the interpretation stop being readable side by side. */
#lbwire .tiles{grid-template-columns:repeat(2,minmax(0,1fr))}
@media(max-width:900px){#lbwire .tiles{grid-template-columns:1fr}}

#lbwire .tile{cursor:default;min-height:0;padding:.95rem 1.05rem 1.05rem}
/* The tile clamps its claim to three lines and its heading to 16 characters.
   Approved Wire wording is published whole. */
#lbwire .tile h4,#lbwire .tile p{max-width:none}
#lbwire .tile p{-webkit-line-clamp:none;display:block;overflow:visible}
#lbwire .tile:hover{background:#101315}
#lbwire .tile .shot{top:.9rem;width:6.4rem;height:6.4rem}
#lbwire .tile h4{font-size:1.18rem;margin:0 0 .5rem}
#lbwire .tile .tkick{flex-wrap:wrap;white-space:normal;row-gap:.3rem;
padding-right:5.5rem}

#lbwire .wb{font-family:var(--agate,inherit);font-size:.58rem;
letter-spacing:.08em;text-transform:uppercase;font-weight:600;
padding:2px 7px;border-radius:99px;border:1px solid var(--rule,#262a22);
color:var(--quiet,#8f938a)}
#lbwire .wb.rank{color:var(--signal,#C6F53C);border-color:rgba(198,245,60,.45)}
#lbwire .wb.up{color:#7fbf8a;border-color:rgba(127,191,138,.5)}
#lbwire .wb.down{color:#e08a7f;border-color:rgba(224,138,127,.5)}
#lbwire .wlab{font-family:var(--agate,inherit);font-size:.58rem;
letter-spacing:.11em;text-transform:uppercase;color:var(--quiet,#8f938a);
font-weight:600;margin:.9rem 0 .35rem}
#lbwire .wrep{font-size:.9rem;line-height:1.5;color:#C9D0CC;margin:0}
#lbwire .wsrc{color:var(--quiet,#8f938a);font-size:.74rem;line-height:1.5;
margin:.55rem 0 0}
#lbwire .wsrc a{color:var(--quiet,#8f938a)}
#lbwire .wown{color:#d6a55a}
#lbwire .wsplit{border:0;border-top:1px solid var(--rule,#262a22);
margin:1rem 0 0}
/* The interpretation is ours, so it carries our accent, and it is visually a
   different kind of statement from the evidence above it. */
#lbwire .wimp{background:#171b13;border-left:3px solid var(--signal,#C6F53C);
border-radius:0 8px 8px 0;padding:.7rem .85rem;margin-top:.75rem;
font-size:.9rem;line-height:1.5;color:#C9D0CC}
#lbwire .wfoot{font-family:var(--agate,inherit);color:var(--quiet,#8f938a);
font-size:.58rem;letter-spacing:.06em;text-transform:uppercase;
margin:.75rem 0 0}
"""


# The section's own behaviour. Filtering and paging are one function because
# they interact: "Load more" must reveal the next cards *that pass the current
# filter*, and changing a filter must reset the page. Two independent handlers
# get that wrong in both directions.
BEHAVIOUR = """
(function(){
  var sec = document.getElementById("lbwire");
  if(!sec) return;
  var cards = [].slice.call(sec.querySelectorAll("article.tile.wire"));
  var more  = document.getElementById("wmore");
  var team  = document.getElementById("wteam");
  var PAGE  = %d;
  var shown = PAGE, dir = "all", pos = "";

  function apply(){
    var n = 0, hits = 0;
    cards.forEach(function(c){
      var ok = (dir === "all" || c.dataset.dir === dir)
            && (!pos || c.dataset.pos === pos)
            && (!team || !team.value || c.dataset.team === team.value);
      if(!ok){ c.hidden = true; return; }
      hits++;
      c.hidden = n++ >= shown;
    });
    if(more){
      var left = Math.max(0, hits - shown);
      more.hidden = left === 0;
      var span = more.querySelector("span");
      if(span) span.textContent = left + " more";
    }
    var count = sec.querySelector(".shead .n");
    if(count) count.textContent = hits + (hits === 1 ? " reviewed report"
                                                     : " reviewed reports");
  }

  sec.querySelectorAll(".wfilters button[data-f]").forEach(function(b){
    b.addEventListener("click", function(){
      sec.querySelectorAll(".wfilters button[data-f]").forEach(function(x){
        x.classList.remove("on"); });
      b.classList.add("on");
      dir = b.dataset.f; shown = PAGE; apply();
    });
  });
  sec.querySelectorAll(".wfilters button[data-p]").forEach(function(b){
    b.addEventListener("click", function(){
      var was = b.classList.contains("on");
      sec.querySelectorAll(".wfilters button[data-p]").forEach(function(x){
        x.classList.remove("on"); });
      if(!was){ b.classList.add("on"); pos = b.dataset.p; } else { pos = ""; }
      shown = PAGE; apply();
    });
  });
  if(team) team.addEventListener("change", function(){ shown = PAGE; apply(); });
  if(more) more.addEventListener("click", function(){ shown += PAGE; apply(); });
  apply();
})();
""" % PAGE


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
    c1, c2 = COLORS.get(str(c.get("team", "")).upper(), FALLBACK)

    badges = f'<span class="wb">{esc(c["mechanism"].replace("_"," ").title())}</span>'
    if c.get("display_position_rank"):
        badges += f'<span class="wb rank">{esc(c["display_position_rank"])}</span>'
    if c.get("display_adp") is not None:
        badges += f'<span class="wb">ADP {esc(round(float(c["display_adp"]), 1))}</span>'
    if c.get("display_projected_points") is not None:
        badges += f'<span class="wb">{esc(c["display_projected_points"])} proj</span>'
    badges += f'<span class="wb {cls}">{esc(c["reader_label"])}</span>'

    # Real art, with the page's own failure chain behind it: Sleeper, then
    # ESPN, then the image is removed. Initials are what a reader sees only
    # when a player genuinely has no photo at either source.
    shot = headshot(c.get("display_player_ref"))
    face = (f'<img class="shot" loading="lazy" alt="" src="{esc(shot)}" '
            f'data-fallback="{esc(espn_headshot(c.get("display_espn")))}" '
            f'onerror="faceFail(this)">' if shot else "")
    logo = team_logo(c.get("team"))
    logo_html = (f'<img loading="lazy" alt="" src="{esc(logo)}" '
                 f'onerror="logoFail(this)">' if logo else "")

    return f"""<article class="tile wire" style="--c1:{c1};--c2:{c2}"
   data-team="{esc(c['team'])}" data-pos="{esc(c['position'])}"
   data-dir="{esc(d)}">
  {face}
  <div class="tkick">
    {logo_html}
    <span class="tcat">{esc(c['team'])} {esc(c['position'])}</span>
    {badges}
    <span class="tago">{esc(ago(c.get('published_at')))}</span>
  </div>
  <h4>{esc(c['player_name'])}</h4>
  <div class="wlab">What the reporter found</div>
  <p class="wrep">{esc(c['reporter_found'])}</p>
  {sources_html(c)}
  <hr class="wsplit">
  <div class="wlab">Lineup Beat impact</div>
  <p class="wimp">{esc(c['lineupbeat_impact'])}</p>
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
            # The publication now carries the stable id itself; the evidence
            # lookup stays as the fallback for records written before it did.
            **p, "player_id": (p.get("player_id")
                               or (row["player_id"] if row else "")),
            "published_at": p.get("published_date")}))

    # A card that has been published arrives from the publication file and
    # must not arrive again from the approved-candidates list. Before they
    # were published the two sets were disjoint; the moment they were
    # written, every approved card counted twice and the section rendered
    # nine reports for five.
    published_names = {c["player_name"] for c in out}

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
            if c["player_name"] in published_names:
                continue          # already carried by the publication file
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
    ap.add_argument("--apply", action="store_true",
                    help="write the replacement into site/index.html. The "
                         "production path; the preview is the default.")
    args = ap.parse_args()
    cards = collect()

    teams = sorted({c["team"] for c in cards})

    # Every approved report renders. There is no publication cap: the batch
    # below governs how many are *visible* before the reader asks for more,
    # and every card is in the HTML either way, which the crawler needs and
    # the filters rely on.
    grid = "".join(card(c) for c in cards)
    hidden = max(0, len(cards) - PAGE)
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
  <div class="tiles wire">{grid}</div>
  {f'<button class="more" id="wmore">Load more reports <span>{hidden} more</span></button>' if hidden else ''}
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

    # 2. The feed payload is left alone.
    #
    # Stripping the nuggets to stop shipping retired All Reports data also
    # emptied Recent News and Moving Now, which read the same collection --
    # renderLive() and trending() both call nuggets(). The Wire replaces one
    # renderer, not the feed underneath it. feed.json keeps powering Recent
    # News, Moving Now, My Roster and search; wire_publications.json powers
    # the Wire section and nothing else reads it.
    #
    # A player a Wire reviewer rejected may still appear in Recent News: that
    # is a legitimate X-wire report about him, not a hidden Wire card, and
    # the two systems are separate on purpose.
    stripped = {"nuggets_removed": 0, "bytes_saved": 0,
                "players_preserved": 0, "feed_preserved": True}
    _d = payload_of(home)
    if _d:
        stripped["nuggets_preserved"] = sum(
            len(sp.get("nuggets") or []) for sp in _d.get("sports", {}).values())
        stripped["players_preserved"] = len(_d.get("players") or [])

    # 3. Neutralise the client-side ALL REPORTS renderer. Adding the new grid
    #    without this would show both, which is the outcome the replacement
    #    exists to prevent.
    old_render = 'const restSec = document.createElement("section");'
    replaced_all_reports = old_render in home
    if replaced_all_reports:
        home = home.replace(
            old_render,
            'if (window.__LB_WIRE_REPLACEMENT__) { return; }\n'
            '  const restSec = document.createElement("section");', 1)

    # 4. Insert the replacement, once. Applying twice appended a second
    #    section and rendered every card again -- nine cards for five
    #    reports -- so the block is fenced and replaced in place when it is
    #    already there. A build step that is not idempotent will be run
    #    twice eventually.
    START, END = "<!-- LB WIRE REPLACEMENT START -->", \
                 "<!-- LB WIRE REPLACEMENT END -->"
    block = (f'{START}\n<script>window.__LB_WIRE_REPLACEMENT__=true;</script>\n'
             f'{section}\n<script>{BEHAVIOUR}</script>\n{END}')
    if START in home and END in home:
        head = home.split(START)[0]
        tail = home.split(END, 1)[1]
        home = head + block + tail
    else:
        marker = '<main id="feed"></main>'
        if marker in home:
            home = home.replace(marker, f'{block}\n{marker}', 1)
    if "id=\"lbwire-css\"" not in home:
        home = home.replace(
            "</head>", f'<style id="lbwire-css">{CSS}</style></head>', 1)

    if args.apply:
        # The production path. site/index.html is gitignored and rebuilt by
        # CI every run, so this is applied after the homepage is generated
        # rather than committed.
        HOME.write_text(home)
        print(f"  applied to {HOME}")
    OUT.write_text(home)
    OUT_JSON.write_text(json.dumps(
        {"published": False, "count_shown": len(cards),
         "removed_latest_from_the_wire_module": removed_module,
         "all_reports_renderer_disabled": replaced_all_reports,
         "retired_feed": stripped,
         "cards": cards}, indent=1, default=str) + "\n")

    print(f"  {len(cards)} card(s) in the replacement section")
    for c in cards:
        print(f"    {c['player_name']:<16}{c['team']} {c['position']:<3}"
              f"{c['reader_label']:<14}{c['mechanism']:<24}"
              f"rank={c.get('display_position_rank','-'):<7}"
              f"adp={c.get('display_adp','-')}")
    print(f"  Latest-from-the-Wire module removed: {removed_module}")
    print(f"  old All Reports renderer disabled:  {replaced_all_reports}")
    print(f"  legacy feed preserved: "
          f"{stripped.get('nuggets_preserved', 0)} report(s) still powering "
          f"Recent News and Moving Now")
    print(f"  roster rows preserved for photos, ADP and search: "
          f"{stripped['players_preserved']}")
    print(f"  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
