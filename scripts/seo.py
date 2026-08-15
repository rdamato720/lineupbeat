#!/usr/bin/env python3
"""Shared SEO parts for the fantasy data pages.

Imported by build_projections, build_draft_value, build_coaching and
build_sos so the four pages carry the same structures rather than four
slightly different attempts at them.

WHY THIS EXISTS

A data page is mostly a table, and a table is close to invisible to a
search engine: it has the numbers and none of the questions somebody typed
to find them. The durability page already carried an FAQ block and picked
up traffic the others do not, and the difference is that it answers in
sentences.

So each page gets three things it did not have:

  FAQPage      the questions people actually search, answered in prose
  ItemList     the ranking, declared as a ranking
  cross-links  so the four pages stop being four islands

WHAT IT DELIBERATELY DOES NOT DO

Keyword stuffing, hidden text, or an FAQ written for a crawler rather than
a reader. Every answer here is one somebody would want if they asked the
question out loud, which is also the only kind of answer that survives a
search engine deciding it dislikes the pattern.
"""

from __future__ import annotations

import html
import json

SITE_URL = "https://lineupbeat.com"

# The league, by division.
#
# Four columns of eight is how every football site lays this out, and it is
# how a fan looks for his team: he knows the division before he scans the
# names. Alphabetical would be tidier and slower to use.
DIVISIONS = [
    ("AFC East", [("BUF", "Bills"), ("MIA", "Dolphins"),
                  ("NE", "Patriots"), ("NYJ", "Jets")]),
    ("AFC North", [("BAL", "Ravens"), ("CIN", "Bengals"),
                   ("CLE", "Browns"), ("PIT", "Steelers")]),
    ("AFC South", [("HOU", "Texans"), ("IND", "Colts"),
                   ("JAX", "Jaguars"), ("TEN", "Titans")]),
    ("AFC West", [("DEN", "Broncos"), ("KC", "Chiefs"),
                  ("LV", "Raiders"), ("LAC", "Chargers")]),
    ("NFC East", [("DAL", "Cowboys"), ("NYG", "Giants"),
                  ("PHI", "Eagles"), ("WAS", "Commanders")]),
    ("NFC North", [("CHI", "Bears"), ("DET", "Lions"),
                   ("GB", "Packers"), ("MIN", "Vikings")]),
    ("NFC South", [("ATL", "Falcons"), ("CAR", "Panthers"),
                   ("NO", "Saints"), ("TB", "Buccaneers")]),
    ("NFC West", [("ARI", "Cardinals"), ("LAR", "Rams"),
                  ("SF", "49ers"), ("SEA", "Seahawks")]),
]


def teams_menu(sport="nfl"):
    """The teams dropdown, in the nav on every page.

    The old strip filtered the wire and only worked on the homepage: on a
    projections page it highlighted a team and did nothing, which is worse
    than not being there. This navigates to the team page, so it means the
    same thing everywhere.
    """
    cols = []
    for div, teams in DIVISIONS:
        links = "".join(
            f'<a href="/{sport}/team/{code.lower()}/">'
            f'<img src="https://a.espncdn.com/i/teamlogos/nfl/500/'
            f'{code.lower()}.png" alt="" loading="lazy" '
            f'onerror="this.style.visibility=&quot;hidden&quot;">'
            f'<span>{name}</span></a>'
            for code, name in teams)
        cols.append(f'<div class="tmcol"><h3>{div}</h3>{links}</div>')
    return (
        '<div class="tmwrap">'
        '<button class="vbtn tmbtn" aria-expanded="false" '
        'aria-controls="tmmenu">Teams<span class="tmcar">&#9662;</span>'
        '</button>'
        f'<div class="tmmenu" id="tmmenu" hidden>{"".join(cols)}</div>'
        '</div>')


TEAMS_CSS = """
/* ---- teams menu ----
   A panel of all 32, by division, because that is how somebody looks for
   his team. Opens on click rather than hover: hover menus are a coin flip
   on a laptop trackpad and do not exist on a phone at all. */
.tmwrap{position:relative}
.tmbtn{display:inline-flex; align-items:center; gap:.3rem}
.tmcar{font-size:.6em; opacity:.7; transition:transform .15s}
.tmbtn[aria-expanded="true"] .tmcar{transform:rotate(180deg)}
/* Anchored to the button's left edge but pulled back so it cannot run off
   the screen: at 44rem wide, a menu opening from a nav item two-thirds
   across the page hangs half of AFC West into nothing. */
.tmmenu{position:absolute; top:calc(100% + .5rem); left:auto; right:0;
  z-index:60;
  background:var(--paper); border:1px solid var(--rule); border-radius:10px;
  padding:1rem 1.1rem; display:grid; grid-template-columns:repeat(4, 1fr);
  gap:1rem 1.6rem; box-shadow:0 18px 50px rgba(0,0,0,.55); min-width:44rem;
  max-width:min(44rem, calc(100vw - 2rem))}
.tmmenu[hidden]{display:none}
.tmcol h3{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.07em; font-size:.64rem; color:var(--quiet);
  margin:0 0 .45rem; padding-bottom:.35rem;
  border-bottom:1px solid var(--rule)}
.tmcol a{display:flex; align-items:center; gap:.45rem; padding:.28rem 0;
  text-decoration:none; color:var(--ink); font-size:.86rem}
.tmcol a:hover{color:var(--signal)}
.tmcol img{width:1.15rem; height:1.15rem; object-fit:contain; flex:none}

@media (max-width:900px){
  .tmmenu{position:fixed; left:0; right:0; top:auto; min-width:0;
    border-radius:0; border-left:0; border-right:0;
    grid-template-columns:repeat(2, 1fr); gap:.8rem 1rem;
    max-height:70vh; overflow-y:auto}
  .tmcol a{min-height:44px}
}
"""

TEAMS_JS = """
<script>
// Click to open, escape or an outside click to close.
//
// Hover menus are a coin flip on a trackpad and do not exist on a phone,
// so this is the same interaction everywhere.
(function(){
  var btn = document.querySelector('.tmbtn');
  var menu = document.getElementById('tmmenu');
  if (!btn || !menu) return;
  function set(open){
    menu.hidden = !open;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  btn.addEventListener('click', function(e){
    e.stopPropagation();
    set(menu.hidden);
  });
  document.addEventListener('click', function(e){
    if (!menu.hidden && !menu.contains(e.target)) set(false);
  });
  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape') set(false);
  });
})();
</script>"""

# ---------------------------------------------------------------- byline
#
# Who made this, how, and when.
#
# Every field here is a fact somebody could check. There is no "reviewed
# by" line because nobody reviews the board before it publishes, and no
# fact-checked badge because there is no fact-checking process -- inventing
# either would be the one kind of claim this site is built not to make.
# When a review step exists, add REVIEWER and the line appears.
AUTHOR = "Ralph Damato"
AUTHOR_ROLE = "Built and maintains LineupBeat"
REVIEWER = None          # set when somebody actually reviews it
METHOD = "LineupBeat projection engine, version 1"

# Cloudflare Web Analytics.
#
# Defined once so the four page builders and the homepage cannot drift into
# measuring different things. It sets no cookies and needs no consent
# banner, which is the reason for choosing it over the obvious alternative.
ANALYTICS = (
    "<!-- Cloudflare Web Analytics -->"
    "<script type='module' "
    "src='https://static.cloudflareinsights.com/beacon.min.js' "
    "data-cf-beacon='{\"token\": \"351a7f1ca5a14571859dcf22cb395b89\"}'"
    "></script>"
    "<!-- End Cloudflare Web Analytics -->")

# Reddit conversion pixel.
#
# This only reports on traffic from paid Reddit campaigns; it does nothing
# for organic visits. It is a third-party script that sets a cookie, unlike
# the Cloudflare beacon above, so it is the one piece of tracking here that
# would need a consent banner for EU and UK visitors.
REDDIT_PIXEL = """<!-- Reddit Pixel -->
<script>
!function(w,d){if(!w.rdt){var p=w.rdt=function(){p.sendEvent?
p.sendEvent.apply(p,arguments):p.callQueue.push(arguments)};
p.callQueue=[];var t=d.createElement("script");t.src="https://www.redditstatic.com/ads/pixel.js";
t.async=!0;var s=d.getElementsByTagName("script")[0];s.parentNode.insertBefore(t,s)}}(window,document);
rdt('init','a2_jhraddsbuel0');
rdt('track','PageVisit');
</script>
<!-- End Reddit Pixel -->"""


# ---------------------------------------------------------------- tracking
#
# Reddit conversion events beyond the base pixel.
#
# Three things this has to survive. Ad blockers, which stop the pixel script
# entirely, so every call is guarded rather than assuming rdt exists.
# Repetition, because somebody comparing formats clicks a filter twenty
# times and twenty identical events tell you nothing the first one did not.
# And the fact that these fire on real user actions, so a thrown error would
# break the page it was measuring.
TRACKING_JS = """
<script>
(function(){
  var sent = {};
  // One of each per page. A filter used once is the signal; used twenty
  // times it is the same signal, more expensively.
  window.lbTrack = function(name, meta){
    try {
      if (sent[name]) return;
      sent[name] = 1;
      if (typeof rdt !== "function") return;   // blocked, or not loaded yet
      rdt("track", name, meta || {});
    } catch (e) { /* never break a page to measure it */ }
  };

  // How many pages this visit has seen. sessionStorage rather than a
  // cookie: it dies with the tab, which is the right lifetime for "did
  // they look at a second thing".
  try {
    var n = (parseInt(sessionStorage.getItem("lb_pv") || "0", 10) || 0) + 1;
    sessionStorage.setItem("lb_pv", String(n));
    if (n >= 2) window.lbTrack("second_page_view", {pages: n});
  } catch (e) {}

  // Search, debounced. A keystroke is not a search; a pause is.
  var timer;
  document.addEventListener("input", function(e){
    var el = e.target;
    if (!el || el.type !== "search") return;
    clearTimeout(timer);
    timer = setTimeout(function(){
      if ((el.value || "").trim().length >= 2) window.lbTrack("Search");
    }, 900);
  }, true);

  // Filters, sorting and row expansion, from the attributes the pages
  // already use. Catching them here rather than in five builders means a
  // new control is measured the day it ships.
  document.addEventListener("click", function(e){
    var b = e.target && e.target.closest &&
            e.target.closest("button, [role=button]");
    if (!b) return;
    var d = b.dataset || {};
    if ("sort" in d) return window.lbTrack("sort_use", {sort: d.sort});
    if ("pos" in d || "val" in d || "fmt" in d || "p" in d || "s" in d ||
        "w" in d || "f" in d)
      return window.lbTrack("filter_use");
    if (b.classList.contains("follow") || /follow/i.test(b.textContent || ""))
      return window.lbTrack("follow_player");
  }, true);

  // An expanded row: the point somebody stops scanning and starts reading.
  document.addEventListener("click", function(e){
    var tr = e.target && e.target.closest && e.target.closest("tr.r, tr.tr");
    if (tr) window.lbTrack("player_expand");
  }, true);
})();
</script>"""


# ViewContent, for the draft board specifically.
#
# Reaching the board is a different act from landing on the site, and an
# event that fires on every page is not an event.
VIEW_CONTENT = """
<script>
(function(){
  try {
    if (typeof rdt === "function")
      rdt("track", "ViewContent", {content_name: "draft_value"});
  } catch (e) {}
})();
</script>"""

# X conversion pixel.
#
# Like the Reddit one, this only reports on traffic from paid campaigns and
# does nothing for organic visits. Second third-party script setting a
# cookie, which is what brings a consent banner closer for EU and UK
# readers -- the Cloudflare beacon sets none, and that is why there is not
# one today.
X_PIXEL = """
<!-- X conversion tracking base code -->
<script>
!function(e,t,n,s,u,a){e.twq||(s=e.twq=function(){s.exe?s.exe.apply(s,arguments):s.queue.push(arguments);
},s.version='1.1',s.queue=[],u=t.createElement(n),u.async=!0,u.src='https://static.ads-twitter.com/uwt.js',
a=t.getElementsByTagName(n)[0],a.parentNode.insertBefore(u,a))}(window,document,'script');
twq('config','reect');
</script>
<!-- End X conversion tracking base code -->"""

# Everything, for pages that want it.
TRACKING = ANALYTICS + "\n" + REDDIT_PIXEL + X_PIXEL + TRACKING_JS
SPORT = "nfl"


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


# The other data pages, for the cross-link strip. Each page filters itself
# out, so one list serves all of them.
DATA_PAGES = [
    ("projections", f"/{SPORT}/projections/", "Projections",
     "Full-season points in three scoring formats, with the stat line "
     "behind each number."),
    ("draft-value", f"/{SPORT}/draft-value/", "ADP & draft value",
     "Where the market is drafting each player against where we rank him."),
    ("coaching", f"/{SPORT}/coaching/", "Offensive coaching",
     "Who actually calls each offense and which positions that favors."),
    ("strength-of-schedule", f"/{SPORT}/strength-of-schedule/",
     "Strength of schedule",
     "Opponent record and fantasy points allowed, by position."),
    ("ol-rb", f"/{SPORT}/offensive-line-rb-performance/",
     "OL & RB performance",
     "How well each line blocked, and what the back added beyond it."),
    ("durability", f"/{SPORT}/durability/", "Durability",
     "How many games each player has really given since 2018."),
]


def related_html(current: str) -> str:
    """A strip of links to the other data pages.

    Four pages that never mention each other are four pages a reader leaves
    after one visit, and four pages a crawler treats as unrelated. Naming
    what is behind each link is the difference between navigation and a
    list of words.
    """
    items = [x for x in DATA_PAGES if x[0] != current]
    cards = "".join(
        f'<a class="relcard" href="{href}">'
        f'<h3>{esc(title)}</h3><p>{esc(blurb)}</p></a>'
        for _k, href, title, blurb in items)
    return (f'\n  <section class="related">\n'
            f'    <h2 class="relh">More fantasy data</h2>\n'
            f'    <div class="relgrid">{cards}</div>\n'
            f'  </section>\n')


RELATED_CSS = """
/* ---- related pages ----
   Four data pages that never mention each other are four pages a reader
   leaves after one visit. */
.related{margin:2.6rem 0 0; border-top:1px solid var(--rule);
  padding-top:1.4rem}
.relh{font-family:var(--agate); text-transform:uppercase; letter-spacing:.07em;
  font-size:.78rem; color:var(--quiet); margin:0 0 .8rem}
.relgrid{display:grid; grid-template-columns:repeat(4, 1fr); gap:.7rem}
@media (max-width:900px){ .relgrid{grid-template-columns:repeat(2, 1fr)} }
@media (max-width:560px){ .relgrid{grid-template-columns:1fr} }
.relcard{display:block; background:var(--card); border:1px solid var(--rule);
  border-radius:8px; padding:.75rem .85rem; text-decoration:none}
.relcard:hover{border-color:var(--signal)}
.relcard h3{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.05em; font-size:.72rem; color:var(--ink); margin:0}
.relcard:hover h3{color:var(--signal)}
.relcard p{margin:.3rem 0 0; font-size:.78rem; line-height:1.45;
  color:var(--quiet)}

/* ---- FAQ ----
   Prose, because a table has the numbers and none of the questions
   somebody typed to find them. */
.faq{margin:2.2rem 0 0}
.faqh{font-family:var(--agate); text-transform:uppercase; letter-spacing:.07em;
  font-size:.78rem; color:var(--quiet); margin:0 0 .9rem}
.faq details{border-bottom:1px solid var(--rule); padding:.7rem 0}
.faq summary{font-size:.92rem; color:var(--ink); cursor:pointer;
  list-style:none; font-weight:500}
.faq summary::-webkit-details-marker{display:none}
.faq summary::before{content:"+"; color:var(--signal); margin-right:.5rem;
  font-family:var(--data)}
.faq details[open] summary::before{content:"\\2212"}
.faq p{margin:.5rem 0 0 1.1rem; font-size:.86rem; line-height:1.6;
  color:var(--quiet); max-width:74ch}
.faq p b{color:var(--ink)}
"""


def faq_html(pairs) -> str:
    """Visible FAQ. Open by default is wrong; findable is not.

    The answers are in the markup whether or not the details element is
    open, so a crawler reads all of them while a reader sees a tidy list.
    That is the honest version of the pattern -- the content is genuinely
    there, not injected after a click.
    """
    items = "".join(
        f"<details><summary>{esc(q)}</summary><p>{a}</p></details>"
        for q, a in pairs)
    return (f'\n  <section class="faq">\n'
            f'    <h2 class="faqh">Common questions</h2>\n'
            f'    {items}\n  </section>\n')


def faq_schema(pairs):
    """FAQPage, matching the visible text exactly.

    Schema that does not match what is on the page is the thing search
    engines penalise, so both come from the same list.
    """
    import re
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer",
                                "text": re.sub(r"<[^>]+>", "", a)}}
            for q, a in pairs],
    }


def byline_html(updated, data_through=None, method=None):
    """The block a reader looks at to decide whether to believe a number.

    A named person, a stated method, and two dates that mean different
    things: when the underlying data ends, and when the page was last
    rebuilt. Publishing one date for both invites the reader to assume the
    market moved when only the build did.
    """
    rows = [f'<div><dt>Projections by</dt>'
            f'<dd><span itemprop="author">{esc(AUTHOR)}</span></dd></div>']
    if REVIEWER:
        rows.append(f'<div><dt>Reviewed by</dt><dd>{esc(REVIEWER)}</dd></div>')
    rows.append(f'<div><dt>Method</dt>'
                f'<dd>{esc(method or METHOD)}</dd></div>')
    if data_through:
        rows.append(f'<div><dt>Data through</dt>'
                    f'<dd>{esc(data_through)}</dd></div>')
    rows.append(f'<div><dt>Last updated</dt>'
                f'<dd><time datetime="{updated:%Y-%m-%d}">'
                f'{updated:%B %-d, %Y}</time></dd></div>')
    return (f'\n  <dl class="byline">\n    {"".join(rows)}\n'
            f'    <div class="bltrust">'
            f'<a href="/about/">Why trust LineupBeat</a></div>\n'
            f'  </dl>\n')


BYLINE_CSS = """
/* Inline links in prose, on a phone.
   A link inside a sentence should not be 44px tall -- that would break the
   line. What it needs is a bigger hit area than its text box, which extra
   vertical padding gives without moving anything, since the line box is
   already taller than the glyphs. */
@media (max-width:760px){
  .abwrap p a, .abcard h3 a, .pjmore a, .dvonly a, .bltrust a,
  .abwho dd a, .dvfoot p a, .dvfoot a, .pjmore > a, .meta a,
  .ssmeth p a, .cofoot a, .faq p a{
    display:inline-block;
    min-height:44px;
    line-height:44px;
    /* The box grows, the text stays put: a negative margin pulls the line
       back to where it was so the paragraph does not open up. */
    margin-top:-11px;
    margin-bottom:-11px;
    vertical-align:baseline;
  }
}

/* ---- byline ----
   Small, factual, above the data. It is not a masthead; it is the four
   things somebody needs to decide whether to believe the numbers under
   it. */
.byline{display:flex; gap:1.4rem; flex-wrap:wrap; align-items:baseline;
  margin:1rem 0 0; padding:.7rem 0; border-top:1px solid var(--rule);
  border-bottom:1px solid var(--rule)}
.byline div{display:flex; gap:.35rem; align-items:baseline}
.byline dt{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.06em; font-size:.62rem; color:var(--quiet)}
.byline dd{margin:0; font-size:.8rem; color:var(--ink)}
.bltrust{margin-left:auto}
.bltrust a{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.06em; font-size:.66rem; color:var(--signal);
  text-decoration:none; border-bottom:1px solid var(--rule)}
.bltrust a:hover{border-color:var(--signal)}
@media (max-width:700px){
  .byline{gap:.5rem 1.1rem}
  .bltrust{margin-left:0; width:100%; margin-top:.3rem}
}
"""


def dataset_extras(temporal=None, spatial="United States"):
    """The fields that make a Dataset citable rather than merely present.

    A model deciding whether to attribute a number wants to know who
    published it, when, under what terms, and whether it may be reused.
    Leaving those blank does not make the answer safer -- it makes the
    citation less likely, because an unattributed number is easier to
    restate than to credit.
    """
    out = {
        "publisher": {"@id": f"{SITE_URL}/#org"},
        "isAccessibleForFree": True,
        "creditText": "LineupBeat",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "spatialCoverage": spatial,
        "inLanguage": "en-US",
    }
    if temporal:
        out["temporalCoverage"] = temporal
    return out


def itemlist_schema(name, url, items):
    """A ranking, declared as one.

    items: [(position, name, url_or_None)]
    """
    return {
        "@type": "ItemList",
        "name": name,
        "url": url,
        "numberOfItems": len(items),
        "itemListOrder": "https://schema.org/ItemListOrderDescending",
        "itemListElement": [
            {"@type": "ListItem", "position": pos, "name": nm,
             **({"url": SITE_URL + u} if u else {})}
            for pos, nm, u in items],
    }


def breadcrumbs(trail):
    """trail: [(name, path_or_None)]"""
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": nm,
             "item": SITE_URL + (path or "/")}
            for i, (nm, path) in enumerate(trail)],
    }


ORGANISATION = {
    "@type": "Organization",
    "@id": f"{SITE_URL}/#org",
    "name": "LineupBeat",
    "url": SITE_URL + "/",
    "description": ("Local NFL beat reporting matched to fantasy relevant "
                    "players, with projections, ADP, coaching and schedule "
                    "data."),
    # A contact point in the schema as well as the footer. Search engines
    # weigh being able to identify who is behind a site, and a data site
    # that cannot be written to is harder to trust.
    "email": "hello@lineupbeat.com",
    # Who to name when a number from this site is quoted.
    "alternateName": "LineupBeat Fantasy Football",
    "knowsAbout": ["fantasy football", "NFL", "fantasy football projections",
                   "average draft position", "NFL injuries",
                   "NFL strength of schedule"],
    "contactPoint": {
        "@type": "ContactPoint",
        "email": "hello@lineupbeat.com",
    # Who to name when a number from this site is quoted.
    "alternateName": "LineupBeat Fantasy Football",
    "knowsAbout": ["fantasy football", "NFL", "fantasy football projections",
                   "average draft position", "NFL injuries",
                   "NFL strength of schedule"],
        "contactType": "customer support",
    },
}


def graph(*nodes):
    """One @graph rather than several loose blocks.

    Separate script tags describe separate things; a graph says they are
    the same page seen from different angles, which is what they are.
    """
    return json.dumps({"@context": "https://schema.org",
                       "@graph": [n for n in nodes if n]},
                      separators=(",", ":"))
