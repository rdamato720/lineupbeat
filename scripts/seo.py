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


def check_page(html, where=""):
    """Refuse to write a document a browser cannot parse.

    Three hundred lines vanished from the template during an edit,
    including </style> and the whole body. Everything after the unclosed
    style tag became stylesheet, so every page on the site rendered
    nothing -- and nothing complained. The response was 200, the file was
    1.2MB, the sitemap was valid, there was no console error, and the only
    way to notice was to look at the site.

    These are the cheapest possible assertions and they would all have
    fired. A build that stops is a bad afternoon; a build that ships an
    empty document is a bad week nobody notices.
    """
    problems = []
    if html.count("<style") != html.count("</style>"):
        problems.append(f"{html.count('<style')} <style> against "
                        f"{html.count('</style>')} </style>")
    if "<body" not in html:
        problems.append("no <body>")
    if "</html>" not in html:
        problems.append("no </html>")
    if html.count("<script") != html.count("</script>"):
        problems.append(f"{html.count('<script')} <script> against "
                        f"{html.count('</script>')} </script>")
    if len(html) < 2000:
        problems.append(f"only {len(html)} bytes")
    if problems:
        raise SystemExit(
            f"\n  REFUSING TO WRITE {where or 'page'}:\n"
            + "".join(f"    {x}\n" for x in problems)
            + "  Nothing written. The previous build is still in place.\n")
    return html

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
// Delegated rather than bound to the button, because on the homepage the
// nav is re-rendered and the button this would have held a reference to is
// thrown away on every state change. Looking it up at click time means a
// freshly inserted menu works without anything having to re-bind.
(function(){
  function menuFor(btn){
    var wrap = btn.closest ? btn.closest('.tmwrap') : null;
    return wrap ? wrap.querySelector('.tmmenu') : null;
  }
  function set(btn, menu, open){
    menu.hidden = !open;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  function closeAll(){
    document.querySelectorAll('.tmwrap').forEach(function(w){
      var b = w.querySelector('.tmbtn'), m = w.querySelector('.tmmenu');
      if (b && m) set(b, m, false);
    });
  }
  document.addEventListener('click', function(e){
    var btn = e.target.closest ? e.target.closest('.tmbtn') : null;
    if (btn){
      var menu = menuFor(btn);
      if (!menu) return;
      var open = menu.hidden;
      closeAll();
      set(btn, menu, open);
      e.stopPropagation();
      return;
    }
    // A click inside an open panel is somebody reading it, not dismissing.
    if (e.target.closest && e.target.closest('.tmmenu')) return;
    closeAll();
  });
  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape') closeAll();
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



# The sections, in nav order. One list feeds the desktop bar and the mobile
# drawer, so an item cannot appear in one and not the other -- which is how
# College came to be missing from a phone in the first place.
#
# (key, label, href-template)
NAV_ITEMS = [
    ("wire", "The Wire", "/"),
    ("roster", "My Roster", "/#v=roster"),
    ("data", "Fantasy Data", "/{sport}/data/"),
    ("college", "College", "/college-fantasy-football/projections/"),
    ("about", "Who We Are", "/about/"),
]


NAV_CSS = """
/* ---- header ----
   Designed at 390px and expanded, not a desktop bar squeezed down. Most
   readers are on a phone, and six section links plus a 32-team dropdown do
   not fit across one: they wrapped onto three rows and pushed the wire
   below the fold before a word of it had been read.
   So under 900px the links move into a drawer behind one button, and the
   header keeps only what has to be there -- the brand, a way to search, and
   a way to reach everything else. */

/* Nothing in the header may widen the page. A single overflowing row here
   gives every page a horizontal scrollbar, and the reader blames the page. */
.topbar{max-width:100%}
.tbrow{min-width:0}
.tbrow .logo{flex:0 0 auto; white-space:nowrap}

/* The two mobile controls. Hidden on desktop, where the real nav is
   visible and a button to reveal it would be noise. */
.navbtn{display:none; align-items:center; justify-content:center; gap:.4rem;
  min-height:44px; min-width:44px; padding:0 .6rem;
  font-family:var(--agate); text-transform:uppercase; letter-spacing:.1em;
  font-size:.74rem; font-weight:600;
  background:none; border:1px solid var(--rule); border-radius:999px;
  color:var(--ink); cursor:pointer; flex:0 0 auto}
.navbtn:hover{border-color:var(--quiet)}
.navbtn svg{width:18px; height:18px; flex:none}
.navbtn[aria-expanded="true"]{background:var(--signal); border-color:var(--signal);
  color:#0A0C08}
/* The bars become a cross when the drawer is open, so the button says what
   it will do next rather than what it did.

   transform-box is not optional here. A CSS transform on an SVG child
   rotates about the viewBox origin unless it is told otherwise, so the
   bars swung out of the icon instead of crossing in the middle of it. */
.navtoggle .bar{transition:transform .18s, opacity .18s;
  transform-box:fill-box; transform-origin:center}
.navtoggle[aria-expanded="true"] .bar1{transform:translateY(5px) rotate(45deg)}
.navtoggle[aria-expanded="true"] .bar2{opacity:0}
.navtoggle[aria-expanded="true"] .bar3{transform:translateY(-5px) rotate(-45deg)}

/* ---- the drawer ----
   Anchored under the header rather than sliding in from the side: the
   header is sticky, so a side sheet would have to cover it and then find
   somewhere else to put the close button. */
.navdrawer{display:none; border-top:1px solid var(--rule);
  background:var(--paper); max-height:calc(100vh - 3.6rem); overflow-y:auto;
  -webkit-overflow-scrolling:touch; overscroll-behavior:contain}
.navdrawer[hidden]{display:none}
.navlinks{display:flex; flex-direction:column; padding:.4rem 0 .2rem}
/* An in-app view is a button and a real page is a link, so this styles
   both. Without the reset the buttons kept the browser's grey face and My
   Roster sat in the drawer looking like a pressed key. */
.navlink{display:flex; align-items:center; width:100%; min-height:48px;
  padding:0 1rem; text-align:left; cursor:pointer;
  background:none; border:0; border-bottom:1px solid var(--rule);
  font-family:var(--agate); text-transform:uppercase; letter-spacing:.1em;
  font-size:.92rem; font-weight:600; color:var(--ink); text-decoration:none}
/* The report count, where the app supplies one. */
.navlink .n{font-family:var(--data); font-size:.68rem; margin-left:.45rem;
  color:var(--quiet); text-transform:none; letter-spacing:0}
.navlink[aria-current="page"] .n{color:inherit; opacity:.7}
.navlink:last-child{border-bottom:0}
.navlink:hover{color:var(--signal)}
.navlink[aria-current="page"]{color:var(--signal)}
/* A left rule rather than a filled pill: at this size a filled row reads as
   a button somebody is about to press, not as where he already is. */
.navlink[aria-current="page"]{box-shadow:inset 3px 0 0 var(--signal)}

/* Teams, in the drawer, behind a row of their own.
   Open, the 32 links pushed every section above them off the screen and
   put a division heading where a reader was looking for "Who We Are". A
   <details> rather than a scripted panel: it works before the JavaScript
   runs, the keyboard already knows how to operate it, and find-in-page
   opens it in current browsers. */
.navteams{border-bottom:1px solid var(--rule)}
.navteams > summary{list-style:none; cursor:pointer}
.navteams > summary::-webkit-details-marker{display:none}
.navteams > summary::marker{content:""}
.navteams[open] > summary{color:var(--signal)}
.navcar{margin-left:auto; font-size:.7em; opacity:.7;
  transition:transform .15s}
.navteams[open] .navcar{transform:rotate(180deg)}
.navtbody{padding:.2rem 1rem 1.2rem}
.navtbody h3{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.1em; font-size:.68rem; color:var(--quiet);
  margin:1rem 0 .3rem; padding-top:.8rem; border-top:1px solid var(--rule)}
.navtbody h3:first-child{border-top:0; padding-top:.2rem; margin-top:.4rem}
.navtgrid{display:grid; grid-template-columns:repeat(2, minmax(0, 1fr));
  gap:0 .8rem}
.navtgrid a{display:flex; align-items:center; gap:.5rem; min-height:44px;
  color:var(--ink); text-decoration:none; font-size:.9rem; min-width:0}
.navtgrid a:hover{color:var(--signal)}
.navtgrid img{width:1.15rem; height:1.15rem; object-fit:contain; flex:none}
.navtgrid span{overflow:hidden; text-overflow:ellipsis; white-space:nowrap}

/* ---- the search field ----
   Worth a permanent place on a laptop and not on a phone, where at 390px it
   would take the whole width and leave nowhere for the brand. It is the
   same single field either way -- rendered once, moved by CSS -- because
   two copies means two elements with one id, and the page script binds to
   the first one it finds.

   On a phone one 44px control drops it onto its own full-width row,
   focused and ready to type. */

@media (max-width:900px){
  /* Wrapping is what gives the field a row of its own to drop into. */
  .tbrow{flex-wrap:wrap; gap:.5rem}
  /* The links are in the drawer now. */
  .tbrow .views{display:none}
  .navbtn{display:inline-flex}
  .navdrawer{display:block}
  /* Pushes the buttons right and lets the brand keep its natural width. */
  .tbrow .logo{margin-right:auto}
  /* Last in the row, so it lands on the line below rather than between the
     brand and the buttons. */
  .tbrow .finder{display:none; order:9; flex:0 0 100%; max-width:none;
    margin:0 0 .15rem}
  .topbar.searchopen .tbrow .finder{display:block}
  /* The app writes a build stamp into the row. It is the first thing worth
     losing when the width is this tight. */
  .tbrow .stamp{display:none}
}

/* Between a large phone and a laptop the links fit again, so the drawer is
   not needed and the field is back in the row on its own account. */
@media (min-width:901px){
  .navdrawer{display:none}
}
"""


NAV_JS = """
<script>
// One button, one drawer, one search row -- on every page, from
// seo.site_nav(). Written to no-op where the markup is absent so a page
// that has not been rebuilt yet does not throw.
(function(){
  var bar = document.querySelector('.topbar');
  if (!bar) return;
  var toggle = bar.querySelector('.navtoggle');
  var drawer = bar.querySelector('.navdrawer');
  var sbtn = bar.querySelector('.navsearch');
  var find = bar.querySelector('.finder');

  function setDrawer(open){
    if (!toggle || !drawer) return;
    drawer.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  // The field is one element that the stylesheet moves, so this toggles a
  // class on the header rather than the element's own hidden attribute --
  // hiding it outright would hide it on a laptop too.
  function setSearch(open){
    if (!sbtn || !find) return;
    bar.classList.toggle('searchopen', open);
    sbtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  function closeAll(){ setDrawer(false); setSearch(false); }

  if (toggle && drawer){
    toggle.addEventListener('click', function(e){
      e.stopPropagation();
      var open = drawer.hidden;
      closeAll();
      setDrawer(open);
    });
  }
  if (sbtn && find){
    sbtn.addEventListener('click', function(e){
      e.stopPropagation();
      var open = !bar.classList.contains('searchopen');
      closeAll();
      setSearch(open);
      // Opening a search box and making somebody tap it again to type is
      // two taps for one intention.
      if (open){
        var input = find.querySelector('input');
        if (input) input.focus();
      }
    });
  }

  // A tap outside closes, the same as the teams menu.
  document.addEventListener('click', function(e){
    if (bar.contains(e.target)) return;
    closeAll();
  });
  // Escape closes and hands focus back to the button that opened it,
  // otherwise the next tab starts from the top of the document.
  document.addEventListener('keydown', function(e){
    if (e.key !== 'Escape') return;
    if (drawer && !drawer.hidden && toggle) toggle.focus();
    else if (bar.classList.contains('searchopen') && sbtn) sbtn.focus();
    closeAll();
  });
  // Rotating to landscape crosses the breakpoint with the drawer still
  // marked open, which left the button lit on a layout that no longer has
  // a drawer.
  var wide = window.matchMedia('(min-width:901px)');
  var onWide = function(e){ if (e.matches) closeAll(); };
  if (wide.addEventListener) wide.addEventListener('change', onWide);
  else if (wide.addListener) wide.addListener(onWide);
})();
</script>"""


def _nav_drawer(active, sport, search):
    """The panel behind the menu button: sections, then all 32 teams."""
    cur = lambda k: ' aria-current="page"' if active == k else ""
    links = "".join(
        f'<a class="navlink" href="{href.format(sport=sport)}"{cur(key)}>'
        f'{label}</a>'
        for key, label, href in NAV_ITEMS)
    cols = []
    for div, teams in DIVISIONS:
        cells = "".join(
            f'<a href="/{sport}/team/{code.lower()}/">'
            f'<img src="https://a.espncdn.com/i/teamlogos/nfl/500/'
            f'{code.lower()}.png" alt="" loading="lazy" '
            f'onerror="this.style.visibility=&quot;hidden&quot;">'
            f'<span>{name}</span></a>'
            for code, name in teams)
        cols.append(f'<h3>{div}</h3><div class="navtgrid">{cells}</div>')
    return (
        '  <div class="navdrawer" id="navdrawer" hidden>\n'
        f'    <nav class="navlinks" aria-label="All sections">{links}</nav>\n'
        '    <details class="navteams">\n'
        '      <summary class="navlink">Teams'
        '<span class="navcar" aria-hidden="true">&#9662;</span></summary>\n'
        f'      <div class="navtbody">{"".join(cols)}</div>\n'
        '    </details>\n'
        '  </div>\n')


def site_nav(active=None, sport="nfl", search=""):
    """The site header, defined once, mobile first.

    Eight builders each carried their own copy of this markup, identical
    apart from which item was current. Adding College would have meant
    eight edits, and the next product another eight, which is how three
    pages end up showing three different menus.

    The CSS and the behaviour come back with it rather than being left for
    each builder to remember. Two of them had already forgotten TEAMS_JS,
    so the teams button on the 404 and the college pages opened nothing --
    a menu is markup plus rules plus a listener, and shipping one third of
    it silently is worse than shipping none.

    `active` takes the key of the current section: wire, roster, data,
    college or about. Anything else leaves no item marked. `search` takes
    the markup for a search field, which appears in the row on a laptop and
    behind the search button on a phone; pages without one pass nothing.
    """
    cur = lambda k: ' aria-current="page"' if active == k else ""
    views = "".join(
        f'<a class="vbtn" href="{href.format(sport=sport)}"{cur(key)}>'
        f'{label}</a>'
        + (teams_menu(sport) if key == "college" else "")
        for key, label, href in NAV_ITEMS)
    return (
        f'<style>{TEAMS_CSS}{NAV_CSS}</style>\n'
        '<header class="topbar">\n'
        '  <div class="wrap tbrow">\n'
        '    <a class="logo" href="/">Lineup<em>Beat</em></a>\n'
        f'    <nav class="views" aria-label="Sections">{views}</nav>\n'
        + (f'    <div class="finder" id="navfind">{search}</div>\n'
           if search else '')
        + ('    <button class="navbtn navsearch" type="button" '
           'aria-expanded="false" aria-controls="navfind" '
           'aria-label="Find a player">'
           '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
           'stroke-width="2" aria-hidden="true">'
           '<circle cx="11" cy="11" r="7"></circle>'
           '<path d="M20 20l-3.5-3.5"></path></svg>'
           '</button>\n' if search else '')
        + '    <button class="navbtn navtoggle" type="button" '
          'aria-expanded="false" aria-controls="navdrawer">'
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          'stroke-width="2" stroke-linecap="round" aria-hidden="true">'
          '<path class="bar bar1" d="M3 7h18"></path>'
          '<path class="bar bar2" d="M3 12h18"></path>'
          '<path class="bar bar3" d="M3 17h18"></path></svg>'
          'Menu</button>\n'
          '  </div>\n'
        + _nav_drawer(active, sport, search)
        + '</header>'
        # Both listeners ship with the markup. Builders must not add
        # TEAMS_JS on top: these are delegated, so a second copy sees the
        # menu already open and closes it again, and the button does
        # nothing at all.
        + TEAMS_JS + NAV_JS
    )


# ------------------------------------------------------------ one design
#
# The homepage was rebuilt to a supplied design and the eight data pages
# were not, so the site read as two sites with the same logo. Measured at
# 1366px before this existed:
#
#                      homepage              data pages
#   heading            serif 61px / 400      serif 27px / 700, and
#                                            Barlow 34px / 600 on durability
#   control            8px corners,          999px pills, 12px,
#                      Barlow 18px / 700     and serif on one page
#   ink                #F2F1EC               #E4E7E2
#   label grey         #9BA09C               #8C9691
#
# The colours are settled in the template's :root, which every builder
# reads. This settles the other two, and it does it by naming the classes
# the builders already carry rather than by rewriting eight sets of markup:
# a restyle that needs eight coordinated edits is a restyle that half-ships.
UI_CSS = """
/* ---- controls ----
   The homepage's button, applied to the filter and format rows on every
   data page. They were 999px pills at 12px, which is a different product's
   button; the homepage's is square-cornered, taller and set in agate. */
.pbtab, .dvtab, .cotab, .sstab, .oltab, .posnav, .cmore{
  font-family:var(--agate); text-transform:uppercase;
  font-size:.95rem; font-weight:700; letter-spacing:.045em;
  border-radius:8px; padding:.55rem 1.05rem;
  border:1px solid rgba(255,255,255,.30); background:transparent;
  color:var(--ink); cursor:pointer; text-decoration:none;
  display:inline-flex; align-items:center; justify-content:center;
  transition:transform .18s ease, background .18s ease, border-color .18s ease}
.pbtab:hover, .dvtab:hover, .cotab:hover, .sstab:hover, .oltab:hover,
.posnav:hover, .cmore:hover{
  border-color:var(--signal); color:var(--ink); transform:translateY(-2px);
  text-decoration:none}
/* The pressed state is the primary button: lime, dark type. */
.pbtab[aria-pressed="true"], .dvtab[aria-pressed="true"],
.cotab[aria-pressed="true"], .sstab[aria-pressed="true"],
.oltab[aria-pressed="true"]{
  background:var(--signal); border-color:var(--signal); color:#060806}
.pbtab[aria-pressed="true"]:hover, .dvtab[aria-pressed="true"]:hover,
.cotab[aria-pressed="true"]:hover, .sstab[aria-pressed="true"]:hover,
.oltab[aria-pressed="true"]:hover{background:#d4ff4b; border-color:#d4ff4b}

/* ---- headings ----
   The homepage's h1 is the serif at 400 with tight tracking. The data
   pages set the same face at 700, which at their size reads as a
   different typeface rather than a smaller one. Same treatment, scaled
   for a page that leads with a table rather than a proposition. */
.pbwrap h1, .dvwrap h1, .sswrap h1, .cowrap h1, .olwrap h1, .cwrap h1, .chwrap h1, .nf h1,
.ppage h1, .dh1{
  font-family:var(--text); font-weight:400; letter-spacing:-.028em;
  line-height:1.04; color:var(--ink); text-transform:none;
  font-size:clamp(30px, 3.4vw, 44px)}
/* The eyebrow above it, lime agate, as on the homepage. */
.pbsublab, .cposh + .ccount, .lb-data-hook, .dhook{
  font-family:var(--agate); font-weight:700; letter-spacing:.045em;
  text-transform:uppercase; color:var(--signal)}

@media (max-width:760px){
  /* The controls stay tappable and stop eating the row. */
  .pbtab, .dvtab, .cotab, .sstab, .oltab, .posnav, .cmore{
    min-height:44px; font-size:.9rem; padding:.5rem .85rem}
}
"""


# ------------------------------------------------- projection stat columns
#
# Which stats each position shows, and in what order. One definition for
# the board, the four position pages and the chip on seven hundred player
# pages.
#
# NOTES.md: "The projections page and the player-page chips read the same
# board, so they cannot disagree." They did. The board was reordered to put
# points first and drop targets, and the chip kept the old list -- so a
# receiver's page showed a Targets figure the board no longer carried, and
# a tight end's page showed no rushing where the board showed three
# columns of it.
#
# The order is a reading order: what the position is paid for, then what
# explains it, then the accounting. Points is emitted ahead of this list by
# whatever is rendering it. Labels are title case here and uppercased by
# CSS on both surfaces.
STAT_COLUMNS = {
    "QB": [("payd", "Pass yds"), ("patd", "Pass TD"),
           ("ruatt", "Car"), ("ruyd", "Rush yds"), ("rutd", "Rush TD"),
           ("patt", "Att"), ("cmp", "Cmp"), ("int", "INT")],
    "RB": [("ruatt", "Car"), ("ruyd", "Rush yds"), ("rutd", "Rush TD"),
           ("rec", "Rec"), ("recyd", "Rec yds"), ("rectd", "Rec TD")],
    "WR": [("rec", "Rec"), ("recyd", "Rec yds"), ("rectd", "Rec TD"),
           ("ruatt", "Car"), ("ruyd", "Rush yds"), ("rutd", "Rush TD")],
    "TE": [("rec", "Rec"), ("recyd", "Rec yds"), ("rectd", "Rec TD"),
           ("ruatt", "Car"), ("ruyd", "Rush yds"), ("rutd", "Rush TD")],
}

# Stats that are whole numbers. Receptions and touchdowns are not: a
# projection is an average over seasons that did not happen.
WHOLE_STATS = {"payd", "recyd", "ruyd", "patt", "ruatt", "targets"}


# ------------------------------------------------------- wide data tables
#
# A projection board is ten to twelve columns. On a 390px phone that is
# four times the width of the screen, and the answer up to now was to hide
# most of them below 640px: the board collapsed to rank, player, team and
# a points total, and every number explaining the ranking disappeared on
# the layout most people read it in.
#
# So the table scrolls sideways instead. Nothing is hidden, rank and player
# stay put while the stats move under them, and the right edge is shaded so
# a reader can see there is more.
#
# Applied by wrapping a table in <div class="xtab"> and marking its two
# leading columns .c-rk and .c-nm. The wrapper is what scrolls, never the
# page.
SCROLLTABLE_CSS = """
/* The box that scrolls. tabindex makes it reachable by keyboard, and
   focus-visible says so, because a scroll region a mouse can reach and a
   keyboard cannot is not accessible. */
.xtab{overflow-x:auto; -webkit-overflow-scrolling:touch;
  overscroll-behavior-x:contain; max-width:100%}
.xtab:focus-visible{outline:2px solid var(--signal); outline-offset:2px}
/* The shading at the edges, and how a reader knows to swipe.
   The two linear gradients are the paper-coloured caps, painted with
   background-attachment:local so they scroll away once you are past the
   end. The two radials stay put and read as a shadow under the edge the
   content continues past. This is the same construction the durability
   table has used since it shipped. */
.xtab{background:
    linear-gradient(90deg, var(--paper) 30%, transparent),
    linear-gradient(90deg, transparent, var(--paper) 70%) 100% 0,
    radial-gradient(farthest-side at 0 50%, rgba(0,0,0,.55), transparent),
    radial-gradient(farthest-side at 100% 50%, rgba(0,0,0,.55), transparent)
      100% 0;
  background-repeat:no-repeat;
  background-size:38px 100%, 38px 100%, 15px 100%, 15px 100%;
  background-attachment:local, local, scroll, scroll}

/* A line of type telling somebody to swipe, shown only where swiping is
   what they would do. It is not decoration: the shadow alone is missed by
   plenty of people, and the columns to the right are the reason the
   ranking is what it is. */
.xhint{display:none; font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.08em; font-size:.68rem; color:var(--quiet);
  margin:.1rem 0 .45rem}
.xhint b{color:var(--signal); font-weight:600}

/* Rank and player, pinned.
   border-collapse:separate is required, not a preference: with collapse
   the browser owns the borders and drops them from a sticky cell, so the
   pinned columns lose their rules and float over the rows. The lines are
   drawn as inset shadows on the cells instead, which stick with them. */
.xtab table{border-collapse:separate; border-spacing:0; width:100%}
.xtab th, .xtab td{box-shadow:inset 0 -1px 0 var(--rule)}
/* th and td explicitly, never the bare class.
   The schedule table names its <col> elements .c-rk too, and a <col> is
   not positionable -- so a bare .c-rk selector would match it, apply
   nothing, and leave the cells unpinned with no error anywhere. */
.xtab th.c-rk, .xtab td.c-rk, .xtab th.c-nm, .xtab td.c-nm{
  position:sticky; z-index:2; background:var(--paper)}
.xtab thead th.c-rk, .xtab thead th.c-nm{z-index:3}
.xtab th.c-rk, .xtab td.c-rk{left:0}
/* Must equal the first column's width, or the two pinned columns overlap.
   Both read the same custom property for that reason, and a table with a
   wider first column -- an ADP figure rather than a rank -- overrides it
   rather than redefining the rule. */
.xtab{--rkw:2.4rem}
.xtab th.c-nm, .xtab td.c-nm{left:var(--rkw)}
.xtab th.c-rk, .xtab td.c-rk{width:var(--rkw); min-width:var(--rkw)}
/* The pinned name column carries a rule down its right edge so the join
   between what is pinned and what is moving is visible while it moves. */
.xtab th.c-nm, .xtab td.c-nm{box-shadow:inset 0 -1px 0 var(--rule),
  inset -1px 0 0 var(--rule)}
.xtab tbody tr:hover td.c-rk, .xtab tbody tr:hover td.c-nm{
  background:var(--card)}

/* Numbers never wrap. A rushing total broken across two lines stops
   being a number and starts being two. */
.xtab td, .xtab th{white-space:nowrap}
.xtab th.c-nm, .xtab td.c-nm{max-width:8.5rem; overflow:hidden; text-overflow:ellipsis}

@media (max-width:900px){
  .xhint{display:block}
  /* 13px floor. Below that the numbers are unreadable at arm's length,
     and shrinking type to fit more columns is the mistake the scroll
     container exists to avoid. */
  .xtab table{font-size:.85rem}
  .xtab th{font-size:.72rem}
  .xtab td, .xtab th{padding-left:.5rem; padding-right:.5rem}
  /* Wide enough for "Trevor Lawrence" and "Amon-Ra St. Brown" to survive,
     narrow enough that Points is still on screen before anybody swipes --
     which is the column order's whole purpose. Longer names still clip;
     the alternative is a name column that pushes the number it explains
     off the edge. */
  .xtab th.c-nm, .xtab td.c-nm{max-width:9rem}
}
"""


# --------------------------------------------------- wide tables as cards
#
# The other answer to a table too wide for a phone.
#
# Sideways scrolling is right where the columns are one kind of thing and
# a reader compares down them -- a projection board is thirty rows of the
# same eight numbers. It is wrong where a row is a small dossier about one
# player and the columns are not comparable with each other: swiping back
# and forth to assemble one player's argument is worse than reading it in
# one block.
#
# So the same table becomes a stack of cards under 760px. One table, one
# set of markup, one script: the rows are re-laid-out by CSS rather than
# rendered twice, because two renderings is two things to keep in step and
# one of them is always a release behind.
#
# Applied by putting .xcard on the table and data-lab on any cell whose
# meaning came from a column heading that is no longer on screen.
CARDTABLE_CSS = """
@media (max-width:760px){
  /* The heading row is the first casualty: its labels move onto the cells
     themselves, where they stay next to the number they name. */
  .xcard thead{display:none}
  .xcard, .xcard tbody, .xcard tr, .xcard td{display:block; width:auto}
  .xcard{border-collapse:separate; border-spacing:0}
  .xcard tr{background:var(--card); border:1px solid var(--rule);
    border-radius:12px; padding:.75rem .85rem; margin:0 0 .6rem}
  .xcard td{padding:0; border:0; text-align:left; white-space:normal}
  /* A cell with nothing in it is a blank line on a card, where in a table
     it was a tidy gap in a column. */
  .xcard td:empty{display:none}
  .xcard td[data-lab]::before{content:attr(data-lab); display:block;
    font-family:var(--agate); text-transform:uppercase; letter-spacing:.08em;
    font-size:.62rem; color:var(--quiet); margin-bottom:.1rem}
  /* The whole card is the tap target where the row was clickable. */
  .xcard tr.r{cursor:pointer}
}
"""


SCROLLHINT_JS = """
<script>
// Hide the swipe line where the table does not actually scroll.
//
// Whether it does depends on the width, the column count and the length of
// the longest name, so no media query can answer it. A phone-sized rule
// put "swipe the table" over a five-column table that fits, and telling
// somebody to do something that does nothing costs more trust than the
// hint buys.
//
// Guarded so a page with several tables installs one listener, not one per
// hint.
(function(){
  if (window.__xhint) return;
  window.__xhint = 1;
  function sync(){
    document.querySelectorAll('.xhint').forEach(function(h){
      var t = h.nextElementSibling;
      while (t && !t.classList.contains('xtab')) t = t.nextElementSibling;
      // Two pixels of slack: sub-pixel table widths report a one-pixel
      // overflow on tables that visibly do not move.
      var scrolls = t && t.scrollWidth > t.clientWidth + 2;
      h.style.display = scrolls ? '' : 'none';
    });
  }
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', sync);
  else sync();
  window.addEventListener('resize', sync);
  window.addEventListener('load', sync);
})();
</script>"""


def scroll_hint(what="the stats"):
    """The swipe line above a wide table. One wording, every board.

    Ships with the script that decides whether to show it, for the same
    reason the nav ships with its own listener: a hint nobody wired up is
    a hint that lies on the pages where the table happens to fit.
    """
    return (f'<p class="xhint">Swipe the table for {esc(what)} '
            f'<b>&rarr;</b></p>{SCROLLHINT_JS}')


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



def social_meta(title, description, canonical_url,
                image_url="https://lineupbeat.com/og.png"):
    """Open Graph and Twitter tags, defined once.

    Two builders each hardcoded a partial block: title, description, url
    and type, with no image and no Twitter tags, so a shared link showed a
    bare card. The homepage had the full set and the data pages did not,
    which is the same drift the navigation had.

    A missing image is worse than a small card, so an empty image_url
    falls back to `summary` and omits the image tags rather than pointing
    at something that will not load.
    """
    t, d = esc(title), esc(description)
    out = [
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="LineupBeat">',
        f'<meta property="og:title" content="{t}">',
        f'<meta property="og:description" content="{d}">',
        f'<meta property="og:url" content="{esc(canonical_url)}">',
    ]
    if image_url:
        out += [
            f'<meta property="og:image" content="{esc(image_url)}">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            '<meta name="twitter:card" content="summary_large_image">',
            f'<meta name="twitter:image" content="{esc(image_url)}">',
        ]
    else:
        out.append('<meta name="twitter:card" content="summary">')
    out += [
        f'<meta name="twitter:title" content="{t}">',
        f'<meta name="twitter:description" content="{d}">',
    ]
    return "\n".join(out)


# One breadcrumb, one set of page widths. The college builder grew its own
# .crumbs rule, so the same class rendered two ways depending which script
# wrote the page -- the navigation had exactly this problem.
#
# The widths were 992, 1080, 1180 and 1200 across templates, which reads
# as the left edge shifting slightly as you move between pages.
CRUMB_CSS = """
:root{--content-reading:992px;--content-data:1080px;
  --content-wide-table:1200px}
.crumbs{display:flex;flex-wrap:wrap;align-items:center;gap:.45rem;
  margin:0 0 1.1rem;font:.68rem/1 var(--agate,system-ui),sans-serif;
  letter-spacing:.08em;text-transform:uppercase}
.crumbs a{color:var(--quiet);text-decoration:none}
.crumbs a:hover{color:var(--signal)}
.crumbs b,.crumbs span[aria-current]{color:var(--ink);font-weight:600}
/* A breadcrumb link at eleven pixels is not tappable. The text stays
   small because it is a breadcrumb; the target does not. */
@media(max-width:760px){
  .crumbs{gap:.2rem}
  .crumbs a{display:inline-flex;align-items:center;min-height:44px;
    padding:0 .3rem}
  .crumbs b{display:inline-flex;align-items:center;min-height:44px}
}
/* Nothing may push the page sideways. A single wide table or an absolutely
   positioned decoration is enough to give every page a horizontal
   scrollbar on a phone, and the reader blames the page, not the element. */
html,body{max-width:100%;overflow-x:hidden}
img,svg,video,table{max-width:100%}
"""

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
