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
from wire.public_labels import DIRECTION_LABELS
from wire.store import WireStore

HOME = Path("site/index.html")
OUT = Path("data/wire_homepage_replacement.html")
OUT_JSON = Path("data/wire_homepage_replacement.json")
DECISIONS = Path("data/reviews/backfill_decisions.json")

LABEL = DIRECTION_LABELS
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


PAGE = 10         # cards visible before "Load more"; not a publication cap

COLORS = team_colors()
FALLBACK = ("#2A3136", "#6B757D")

TEAM_NAMES = {
    "ARI":"Arizona Cardinals", "ATL":"Atlanta Falcons", "BAL":"Baltimore Ravens",
    "BUF":"Buffalo Bills", "CAR":"Carolina Panthers", "CHI":"Chicago Bears",
    "CIN":"Cincinnati Bengals", "CLE":"Cleveland Browns", "DAL":"Dallas Cowboys",
    "DEN":"Denver Broncos", "DET":"Detroit Lions", "GB":"Green Bay Packers",
    "HOU":"Houston Texans", "IND":"Indianapolis Colts", "JAX":"Jacksonville Jaguars",
    "KC":"Kansas City Chiefs", "LV":"Las Vegas Raiders", "LAC":"Los Angeles Chargers",
    "LAR":"Los Angeles Rams", "MIA":"Miami Dolphins", "MIN":"Minnesota Vikings",
    "NE":"New England Patriots", "NO":"New Orleans Saints", "NYG":"New York Giants",
    "NYJ":"New York Jets", "PHI":"Philadelphia Eagles", "PIT":"Pittsburgh Steelers",
    "SEA":"Seattle Seahawks", "SF":"San Francisco 49ers", "TB":"Tampa Bay Buccaneers",
    "TEN":"Tennessee Titans", "WAS":"Washington Commanders",
}

HEADLINES = {
    "ABSENT_FROM_PRACTICE": "Misses practice",
    "LIMITED_PARTICIPATION": "Limited in practice",
    "RETURN_TO_PRACTICE": "Returns to practice",
    "INJURY": "Dealing with injury",
    "DEPTH_CHART": "Depth-chart update",
    "ROLE": "Role update",
    "FIRST_TEAM_REPS": "Works with first team",
    "SECOND_TEAM_REPS": "Works with second team",
    "THIRD_TEAM_REPS": "Works with third team",
    "RED_ZONE": "Red-zone work",
    "TARGETS": "Target competition update",
    "SNAP_SHARE": "Snap-share update",
    "ROUTES": "Route participation update",
    "FANTASY_VALUE": "Fantasy outlook update",
    "PERFORMANCE": "Practice report",
    "TRANSACTION": "Roster move",
}


def headline(c):
    return HEADLINES.get(c.get("mechanism"), "Latest update")


def date_label(iso):
    try:
        value = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def headshot(player_ref):
    """Use the same stable player image identity as the rest of the site."""
    bare = re.sub(r"^[a-z]+-", "", str(player_ref or ""))
    return (f"https://sleepercdn.com/content/nfl/players/thumb/{bare}.jpg"
            if bare else "")


def espn_headshot(espn_id):
    return (f"https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png"
            if espn_id else "")


def team_logo(team):
    value = str(team or "").lower()
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{value}.png" if value else ""


CSS = """
/* A compact news card: player, headline, team/date, report, analysis. */
#wire{padding:34px 0 10px}
#wire .shead{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
#wire .shead h2{margin:0}
#wire .sub{margin:2px 0 16px}
#wire .wfilters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}
#wire .wfilters button,#wire .wfilters select{font:inherit;font-size:.78rem;
padding:6px 12px;background:transparent;color:var(--quiet,#8f938a);
border:1px solid var(--rule,#262a22);border-radius:99px;cursor:pointer}
#wire .wfilters button.on{color:var(--signal,#C6F53C);
border-color:var(--signal,#C6F53C)}

/* One card per row, at every width.
   Two columns put the reporting, the attribution and our analysis into a
   half-width measure, which cramped all three and made the interpretation
   look like a caption. A single column gives the analysis the room it earns;
   the max-width keeps the line length readable rather than letting it run
   the width of a desktop. */
#wire .tiles{display:block;max-width:56rem}
#wire .tile{display:block;margin:0 0 .75rem;cursor:default;min-height:0;
padding:1.15rem 7.5rem 1.2rem 1.25rem;border-left:3px solid var(--c1,#C6F53C)}
#wire .tile h4,#wire .tile p{max-width:none}
#wire .tile p{-webkit-line-clamp:none;display:block;overflow:visible}
#wire .tile:hover{background:#101315}
#wire .tile .shot{position:absolute;top:1rem;right:.45rem;width:6.4rem;
height:6.4rem;object-fit:contain;object-position:bottom right}
#wire .wplayer{font-size:1.12rem;font-weight:750;color:#fff;margin:0 0 .15rem}
#wire .wheadline{font-size:1.12rem;font-weight:700;line-height:1.3;margin:0 0 .45rem}
#wire .wheadline a{color:var(--signal,#C6F53C);text-decoration:none}
#wire .wmeta{display:flex;gap:.45rem;align-items:baseline;flex-wrap:wrap;
font-family:var(--agate,inherit);font-size:.7rem;color:var(--quiet,#8f938a);
margin:0 0 .85rem}
#wire .wteamlogo{width:19px;height:19px;object-fit:contain;align-self:center}
#wire .wpos{font-weight:750;color:#fff}
#wire .wdate{font-weight:650;color:#c8cec9;margin:.8rem 0}
#wire .wlab{font-family:var(--agate,inherit);font-size:.57rem;
letter-spacing:.11em;text-transform:uppercase;color:var(--signal,#C6F53C);
font-weight:700;margin:1rem 0 .3rem}

/* What changed: one sentence, deliberately quieter than the block below it.
   It is the occasion for the card, not the point of it. */
#wire .wrep{font-size:.96rem;line-height:1.55;color:#E4E9E5;margin:0;
max-width:62ch}

/* Lineup Beat impact: the reason the card exists, so it is the thing the eye
   lands on -- larger than the sentence above it, on our accent, in the
   page's reading face rather than the quieter agate. */
#wire .wimp{font-size:.96rem;line-height:1.55;color:#E4E9E5;max-width:62ch;
margin:0}

/* Attribution last, and smallest. */
#wire .wsrc{color:var(--quiet,#8f938a);font-size:.73rem;line-height:1.5;
margin:.85rem 0 0}
#wire .wsrc a{color:var(--quiet,#8f938a)}
#wire .wown{color:#d6a55a}
#wire .more{margin-top:.4rem}
@media(max-width:640px){#wire .tile{padding-right:5.8rem}
#wire .tile .shot{width:5rem;height:5rem;opacity:.88}}
"""


# The section's own behaviour. Filtering and paging are one function because
# they interact: "Load more" must reveal the next cards *that pass the current
# filter*, and changing a filter must reset the page. Two independent handlers
# get that wrong in both directions.
BEHAVIOUR = """
(function(){
  var sec = document.getElementById("wire");
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
    """Reporter, publication, link. Last on the card and smallest on it.

    The card's job is to say what changed and what we make of it; the
    attribution is what lets a reader check us, which is important and is not
    the headline. Ownership is still named, because a club's own report is
    authoritative for its designation and is not independent corroboration.
    """
    srcs = c.get("sources")
    if not srcs:
        own = c.get("source_ownership") == "TEAM_OWNED"
        bits = [esc(c.get("author")), esc(c.get("source"))]
        line = " &middot; ".join(b for b in bits if b)
        tag = (' <span class="wown">Official team source</span>' if own else "")
        return (f'<p class="wsrc">{line}{tag} &middot; '
                f'<a href="{esc(c.get("url"))}" rel="nofollow noopener" '
                f'target="_blank">Read report</a></p>')
    rows = "".join(
        f'<div>{esc(ROLE_LABEL.get(s["role"], s["role"]))}: '
        f'{esc(s.get("author"))} &middot; {esc(s.get("publication"))} &middot; '
        f'{esc(str(s.get("published_at",""))[:10])}'
        f'{" <span class=\"wown\">Official team source</span>" if s.get("ownership")=="TEAM_OWNED" else ""}'
        f' &middot; <a href="{esc(s.get("url"))}" rel="nofollow noopener" '
        f'target="_blank">Read report</a></div>' for s in srcs)
    ind = c.get("independent_source_count") or 0
    tn = c.get("team_owned_source_count") or 0
    note = (f'{ind} independent reporter' + ('' if ind == 1 else 's')
            + (f' and {tn} club report' + ('' if tn == 1 else 's') if tn else ''))
    return ('<p class="wsrc">' + rows +
            f'<div style="margin-top:4px">{esc(note)}. The club report is '
            f'authoritative for its own designation and is not independent '
            f'corroboration.</div></p>')


def card(c):
    """One reviewed report.

    The hierarchy is deliberate and reads top to bottom: who this is and what
    he is worth, what changed, what we make of it, who reported it. Our
    reading is the largest block on the card because it is the only part a
    reader cannot get anywhere else.
    """
    d = c["direction"]
    c1, c2 = COLORS.get(str(c.get("team", "")).upper(), FALLBACK)

    # The public sentence, never the stored passage. build_wire.py refuses to
    # publish a card without an approved one, so there is no fallback here --
    # a fallback would quietly put the reporter's full paragraph back on the
    # page the first time somebody forgot to write a summary.
    summary = c.get("public_evidence_summary") or ""
    shot = headshot(c.get("display_player_ref"))
    face = (f'<img class="shot" loading="lazy" alt="" src="{esc(shot)}" '
            f'data-fallback="{esc(espn_headshot(c.get("display_espn")))}" '
            f'onerror="faceFail(this)">' if shot else "")
    logo = team_logo(c.get("team"))

    return f"""<article class="tile wire" style="--c1:{c1};--c2:{c2}"
   data-publication-id="{esc(c['publication_id'])}"
   data-team="{esc(c['team'])}" data-pos="{esc(c['position'])}"
   data-dir="{esc(d)}">
  {face}
  <h4 class="wplayer">{esc(c['player_name'])}</h4>
  <h5 class="wheadline"><a href="{esc(c.get('url'))}" rel="nofollow noopener"
    target="_blank">{esc(headline(c))}</a></h5>
  <p class="wmeta"><span class="wpos">{esc(c['position'])}</span>
    <img class="wteamlogo" loading="lazy" alt="" src="{esc(logo)}"
      onerror="logoFail(this)">
    <span>{esc(TEAM_NAMES.get(c['team'], c['team']))}</span></p>
  <p class="wdate">{esc(date_label(c.get('published_at')))}</p>
  <p class="wrep">{esc(summary)}</p>
  <div class="wlab">Analysis</div>
  <p class="wimp">{esc(c['lineupbeat_impact'])}</p>
  {sources_html(c)}
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
    published_candidate_ids = {
        str(c.get("evidence_candidate_id") or "") for c in out
        if c.get("evidence_candidate_id")}
    published_events = {
        (c["player_name"], c.get("url") or "") for c in out}

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
            event = (c["player_name"], f.get("canonical_url") or "")
            if (c["candidate_id"] in published_candidate_ids
                    or event in published_events):
                continue          # exact event is already in the publication file
            out.append(display.decorate({
                "publication_id": "approved-candidate:" + c["candidate_id"],
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
    ap.add_argument("--source", type=Path, default=HOME,
                    help="homepage shell to update; defaults to site/index.html. "
                         "The approval workflow uses the tracked preview so it "
                         "can refresh review artifacts without building or deploying.")
    args = ap.parse_args()
    cards = collect()

    publications = json.loads(Path("data/wire_publications.json").read_text()) \
        .get("publications", [])
    expected_ids = [str(item.get("publication_id") or "") for item in publications]
    rendered_ids = [str(item.get("publication_id") or "") for item in cards]
    if (len(rendered_ids) != len(expected_ids)
            or len(set(rendered_ids)) != len(rendered_ids)
            or set(rendered_ids) != set(expected_ids)):
        print("  publication/render count FAILED: "
              f"{len(expected_ids)} approved, {len(rendered_ids)} rendered")
        return 1

    teams = sorted({c["team"] for c in cards})

    # Every approved report renders. There is no publication cap: the batch
    # below governs how many are *visible* before the reader asks for more,
    # and every card is in the HTML either way, which the crawler needs and
    # the filters rely on.
    grid = "".join(card(c) for c in cards)
    hidden = max(0, len(cards) - PAGE)
    section = f"""<section class="wrap sec" id="wire">
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
</section>"""

    if not args.source.exists():
        print(f"  homepage source not built: {args.source}")
        return 1
    home = args.source.read_text()

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
    #    The guard is injected once. It used to be keyed on the line it sits
    #    above, which is still there after the injection -- so a second build
    #    added a second copy and a third added a third. Keyed on the guard
    #    itself instead.
    #
    #    It sits at the top of renderFeed rather than just above the All
    #    Reports section. Placed lower, the function still ran far enough to
    #    render the feed's own empty state -- a grey "Nothing in this window,
    #    turn off a filter to see more" box, advising a reader to unfilter a
    #    list that is retired and will never appear. The whole legacy feed
    #    render is skipped now, which is what "replaced" was supposed to mean.
    #    The controls go with it. renderFeed is the only thing that ever
    #    showed them -- every other view hides them -- and the only thing
    #    they filtered was the retired list. Left visible they are a row of
    #    chips above the Wire that change nothing a reader can see.
    old_render = 'function renderFeed(){\n'
    #    Scoped to the default view. renderFeed also draws My Roster, which
    #    is the same renderer filtered to the reader's own players -- guarding
    #    the whole function emptied that page. Only the wire view is replaced.
    #    It clears the feed on the way out. Returning early without doing so
    #    left whatever the previous view had rendered sitting under the Wire
    #    -- go to My Roster and back and its empty state was still there.
    guard = ('if (window.__LB_WIRE_REPLACEMENT__ '
             '&& state.view === "wire" && !state.player) {\n'
             '    var _c = document.getElementById("controls");\n'
             '    if (_c) _c.style.display = "none";\n'
             '    var _f = document.getElementById("feed");\n'
             '    if (_f) _f.innerHTML = "";\n'
             '    return;\n  }')
    replaced_all_reports = guard in home or old_render in home
    if guard not in home and old_render in home:
        home = home.replace(
            old_render, 'function renderFeed(){\n  ' + guard + '\n', 1)

    #    And the section itself is hidden away from the homepage view. It is
    #    static markup inserted once, so without this it sat under My Roster
    #    and under every player page too.
    show = ('  var _w = document.getElementById("wire");\n'
            '  if (_w) _w.hidden = !!state.player || state.view !== "wire";\n')
    if "_w.hidden" not in home:
        home = home.replace("  renderPos();\n  renderViews();\n",
                            show + "  renderPos();\n  renderViews();\n", 1)

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
    if "id=\"wire-css\"" in home:
        home = re.sub(
            r'<style id="wire-css">.*?</style>',
            f'<style id="wire-css">{CSS}</style>', home, count=1,
            flags=re.S)
    else:
        home = home.replace(
            "</head>", f'<style id="wire-css">{CSS}</style></head>', 1)

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
