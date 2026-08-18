#!/usr/bin/env python3
"""Generate crawlable pages, a sitemap, and robots.txt.

    python3 scripts/build_pages.py
    python3 scripts/build_pages.py --base https://lineupbeat.com --dry-run

WHY THIS EXISTS

The site is one URL. Everything else is a hash fragment -- #p=nfl-11638 --
and a hash is not an address as far as a crawler is concerned. Content is
also injected by JavaScript into an empty div: Google will execute that,
inconsistently and late, and most other crawlers will not execute it at all.

So a site producing exactly the content that wins long-tail search -- player
news, updated daily, from named reporters -- currently offers search engines
a single page with no words in it.

This writes a real HTML file per player who has reports, with his claims in
the markup before any script runs. The interactive app still loads on top, so
a human gets the full experience and a crawler gets the text either way.

WHAT IT DELIBERATELY DOES NOT DO

  - No page for a player with no reports. Three thousand near-empty pages is
    a thin-content problem, not an SEO strategy.
  - No invented copy. The page says what the beat said, and nothing more, so
    there is nothing here a reader would find misleading.
  - No keyword stuffing in titles. "Breece Hall news and beat reports" is what
    the page is; anything more elaborate reads as spam to a person, which is
    eventually how it reads to a search engine.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seo


def trim(s, limit):
    """Cut at a word boundary, never mid-name.

    A title cut to the character lands on "Christian McCaffr", which looks
    like a bug. Dropping the last whole word costs a little meaning and
    keeps the name intact, and the name is what somebody searched for.
    """
    s = (s or "").strip()
    if len(s) <= limit:
        return s
    cut = s[:limit].rstrip()
    if " " in cut:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,.|-")


ROOT = Path(__file__).resolve().parent.parent


def eastern_now():
    """The date a reader in the league's own time zone would call today.

    UTC rolls over at 8pm Eastern, so a page built in the evening was
    stamped tomorrow and looked a day ahead of the data it was showing.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return eastern_now() - timedelta(hours=4)

SITE = ROOT / "site"

# Sport lives in the URL: /nfl/aj-brown/, /nfl/team/nyj/.
#
# Added before launch rather than after, because retrofitting a URL scheme
# means redirects and lost link equity. A second sport slots in beside the
# first instead of forcing a migration of it.
SPORT = "nfl"
TEMPLATE = ROOT / "site" / "template.html"


def site_chrome(section=None):
    """Take the stylesheet, header and footer from the app template.

    These pages were using a small stylesheet of their own, which meant a
    reader arriving from search got something that did not look like the
    site. Reading the template at build time means one design: change the
    header on the homepage and every player page follows, with no second
    copy to keep in sync.
    """
    import re
    if not TEMPLATE.exists():
        return "", "", ""
    src = TEMPLATE.read_text()
    css = re.search(r"<style>(.*?)</style>", src, re.S)
    foot = re.search(r"<footer.*?</footer>", src, re.S)
    # The app header carries a search box and a JS-populated nav. A static
    # page gets the same bar and typography without the machinery.
    # The same bar as the homepage. The nav there is built by the app's
    # JavaScript and the search box needs its index, neither of which exists
    # on a static page -- so the nav is a real link and the search filters
    # the table in front of you, which on a two-hundred-row board is more
    # use than a site-wide lookup anyway.
    header = seo.site_nav(section, SPORT).replace(
        '</nav>\n',
        '</nav>\n'
        '    <div class="finder">\n'
        '      <input id="pfind" type="search" '
        'placeholder="Find a player"\n'
        '             autocomplete="off" aria-label="Find a player">\n'
        '    </div>\n')

    return (css.group(1) if css else ""), header, (foot.group(0) if foot else "")


APP_CSS, APP_HEADER, APP_FOOTER = site_chrome()

# Analytics on every page this builder writes.
#
# Appended to the shared footer rather than to each template: there are
# several page shells here, and patching one meant player pages, the hub
# and durability silently went unmeasured.
APP_CSS = (APP_CSS or "") + seo.TEAMS_CSS
APP_FOOTER = APP_FOOTER + seo.TEAMS_JS + (
    "\n<!-- Cloudflare Web Analytics -->"
    "<script type='module' "
    "src='https://static.cloudflareinsights.com/beacon.min.js' "
    "data-cf-beacon='{\"token\": \"351a7f1ca5a14571859dcf22cb395b89\"}'"
    "></script><!-- End Cloudflare Web Analytics -->"
    "\n<!-- X conversion tracking base code -->\n<script>\n!function(e,t,n,s,u,a){e.twq||(s=e.twq=function(){s.exe?s.exe.apply(s,arguments):s.queue.push(arguments);\n},s.version='1.1',s.queue=[],u=t.createElement(n),u.async=!0,u.src='https://static.ads-twitter.com/uwt.js',\na=t.getElementsByTagName(n)[0],a.parentNode.insertBefore(u,a))}(window,document,'script');\ntwq('config','reect');\n</script>\n<!-- End X conversion tracking base code -->"
    "\n<script>\n(function(){\n  var sent = {};\n  // One of each per page. A filter used once is the signal; used twenty\n  // times it is the same signal, more expensively.\n  window.lbTrack = function(name, meta){\n    try {\n      if (sent[name]) return;\n      sent[name] = 1;\n      if (typeof rdt !== \"function\") return;   // blocked, or not loaded yet\n      rdt(\"track\", name, meta || {});\n    } catch (e) { /* never break a page to measure it */ }\n  };\n\n  // How many pages this visit has seen. sessionStorage rather than a\n  // cookie: it dies with the tab, which is the right lifetime for \"did\n  // they look at a second thing\".\n  try {\n    var n = (parseInt(sessionStorage.getItem(\"lb_pv\") || \"0\", 10) || 0) + 1;\n    sessionStorage.setItem(\"lb_pv\", String(n));\n    if (n >= 2) window.lbTrack(\"second_page_view\", {pages: n});\n  } catch (e) {}\n\n  // Search, debounced. A keystroke is not a search; a pause is.\n  var timer;\n  document.addEventListener(\"input\", function(e){\n    var el = e.target;\n    if (!el || el.type !== \"search\") return;\n    clearTimeout(timer);\n    timer = setTimeout(function(){\n      if ((el.value || \"\").trim().length >= 2) window.lbTrack(\"Search\");\n    }, 900);\n  }, true);\n\n  // Filters, sorting and row expansion, from the attributes the pages\n  // already use. Catching them here rather than in five builders means a\n  // new control is measured the day it ships.\n  document.addEventListener(\"click\", function(e){\n    var b = e.target && e.target.closest &&\n            e.target.closest(\"button, [role=button]\");\n    if (!b) return;\n    var d = b.dataset || {};\n    if (\"sort\" in d) return window.lbTrack(\"sort_use\", {sort: d.sort});\n    if (\"pos\" in d || \"val\" in d || \"fmt\" in d || \"p\" in d || \"s\" in d ||\n        \"w\" in d || \"f\" in d)\n      return window.lbTrack(\"filter_use\");\n    if (b.classList.contains(\"follow\") || /follow/i.test(b.textContent || \"\"))\n      return window.lbTrack(\"follow_player\");\n  }, true);\n\n  // An expanded row: the point somebody stops scanning and starts reading.\n  document.addEventListener(\"click\", function(e){\n    var tr = e.target && e.target.closest && e.target.closest(\"tr.r, tr.tr\");\n    if (tr) window.lbTrack(\"player_expand\");\n  }, true);\n})();\n</script>"
    "\n<!-- Reddit Pixel -->\n<script>\n!function(w,d){if(!w.rdt){var p=w.rdt=function(){p.sendEvent?\np.sendEvent.apply(p,arguments):p.callQueue.push(arguments)};\np.callQueue=[];var t=d.createElement(\"script\");t.src=\"https://www.redditstatic.com/ads/pixel.js\";\nt.async=!0;var s=d.getElementsByTagName(\"script\")[0];s.parentNode.insertBefore(t,s)}}(window,document);\nrdt('init','a2_jhraddsbuel0');\nrdt('track','PageVisit');\n</script>\n<!-- End Reddit Pixel -->")
# The same bar with Fantasy Data marked, for the pages that live under it.
# A player page is wire content and gets the plain one; the hub and the
# boards get the marker, so the highlight means where you are rather than
# which script built the page.
_, DATA_HEADER, _ = site_chrome("data")
_, ABOUT_HEADER, _ = site_chrome("about")

TEAM_C2 = {
    "ARI":"#FFB612","ATL":"#A5ACAF","BAL":"#9E7C0C","BUF":"#C60C30","CAR":"#BFC0BF",
    "CHI":"#C83803","CIN":"#000000","CLE":"#FF3C00","DAL":"#869397","DEN":"#FB4F14",
    "DET":"#B0B7BC","GB":"#FFB612","HOU":"#A71930","IND":"#A2AAAD","JAX":"#D7A22A",
    "KC":"#FFB81C","LV":"#A5ACAF","LAC":"#FFC20E","LAR":"#FFA300","MIA":"#FC4C02",
    "MIN":"#FFC62F","NE":"#C60C30","NO":"#D3BC8D","NYG":"#A71930","NYJ":"#FFFFFF",
    "PHI":"#A5ACAF","PIT":"#FFB612","SF":"#B3995D","SEA":"#69BE28","TB":"#FF7900",
    "TEN":"#4B92DB","WAS":"#FFB612",
}
TEAM_COLORS = {
    "ARI":"#97233F","ATL":"#A71930","BAL":"#241773","BUF":"#00338D","CAR":"#0085CA",
    "CHI":"#0B162A","CIN":"#FB4F14","CLE":"#FF3C00","DAL":"#003594","DEN":"#FB4F14",
    "DET":"#0076B6","GB":"#203731","HOU":"#03202F","IND":"#002C5F","JAX":"#006778",
    "KC":"#E31837","LV":"#A5ACAF","LAC":"#0080C6","LAR":"#003594","MIA":"#008E97",
    "MIN":"#4F2683","NE":"#002244","NO":"#D3BC8D","NYG":"#0B2265","NYJ":"#125740",
    "PHI":"#004C54","PIT":"#FFB612","SF":"#AA0000","SEA":"#69BE28","TB":"#D50A0A",
    "TEN":"#4B92DB","WAS":"#FFB612",
}
POS_NAMES = {"QB":"Quarterback","RB":"Running back","WR":"Wide receiver",
             "TE":"Tight end","FB":"Fullback","K":"Kicker"}

# The same set the wire publishes for. A page exists to hold reports, and
# no report about a guard will ever pass the nugget filter.
PUBLISHED_POSITIONS = {"QB", "RB", "FB", "WR", "TE"}

TEAM_NAMES = {
    "ARI":"Arizona Cardinals","ATL":"Atlanta Falcons","BAL":"Baltimore Ravens",
    "BUF":"Buffalo Bills","CAR":"Carolina Panthers","CHI":"Chicago Bears",
    "CIN":"Cincinnati Bengals","CLE":"Cleveland Browns","DAL":"Dallas Cowboys",
    "DEN":"Denver Broncos","DET":"Detroit Lions","GB":"Green Bay Packers",
    "HOU":"Houston Texans","IND":"Indianapolis Colts","JAX":"Jacksonville Jaguars",
    "KC":"Kansas City Chiefs","LV":"Las Vegas Raiders","LAC":"Los Angeles Chargers",
    "LAR":"Los Angeles Rams","MIA":"Miami Dolphins","MIN":"Minnesota Vikings",
    "NE":"New England Patriots","NO":"New Orleans Saints","NYG":"New York Giants",
    "NYJ":"New York Jets","PHI":"Philadelphia Eagles","PIT":"Pittsburgh Steelers",
    "SF":"San Francisco 49ers","SEA":"Seattle Seahawks","TB":"Tampa Bay Buccaneers",
    "TEN":"Tennessee Titans","WAS":"Washington Commanders",
}


def slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (s or "").lower())
    return re.sub(r"[\s_]+", "-", s).strip("-")


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def ago(iso):
    """Relative time. A wire reads by recency, so "3h ago" carries more than a
    date does; the full date stays underneath for anyone who wants it."""
    try:
        d = (eastern_now()
             - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return ""
    if d < 3600:
        return f"{max(1, int(d // 60))}m ago"
    if d < 86400:
        return f"{int(d // 3600)}h ago"
    return f"{int(d // 86400)}d ago"


def when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%B %-d, %Y")
    except (TypeError, ValueError):
        return ""


PAGE_CSS = """
/* Additions only. The app stylesheet does the heavy lifting; these are the
   few things a player page needs that the wire does not. */
.ppage{padding-top:1rem;padding-bottom:3rem}
/* Breadcrumb, replacing a lone nav pill that read as decoration. Says where
   you are, gives Google a hierarchy to render in the result, and earns its
   line in a way one tab did not. */
.finder input{min-height:44px}
}
.crumbs a:hover{color:var(--signal)}
.crumbs span{color:var(--rule)}
.crumbs b{color:var(--ink);font-weight:600}
/* The header links are anchors here, not the app's buttons, so they pick up
   the default underline. Match the app's chrome instead. */
.topbar .logo,.topbar .vbtn{text-decoration:none}
.vbtn[aria-current="page"]{color:#0A0C08; background:var(--signal); border-color:var(--signal)}
.topbar .logo:hover,.topbar .vbtn:hover{text-decoration:none;
  color:var(--signal)}
/* `.hero` is the app masthead -- background, yard lines, big padding. The
   player hero is its own class so none of that bleeds in. */
/* Team wash, same construction as a featured card on the wire: a primary
   gradient with a secondary radial in the corner. The second colour is what
   keeps a Raiders or Bears hero from reading as plain grey -- primaries in
   this league are often near-black and the identity lives in the accent. */
.phero{position:relative;overflow:hidden;isolation:isolate;
  display:flex;gap:1.35rem;align-items:center;padding:1.6rem 1.5rem;
  border-radius:12px;border:1px solid rgba(255,255,255,.09)}
.phero::before{content:"";position:absolute;inset:0;z-index:-2;opacity:.92;
  background:
    radial-gradient(90% 70% at 94% 6%, __C2__ 0%, transparent 58%),
    linear-gradient(152deg, __ACCENT__ 0%, __ACCENT__ 24%, #0B0D0F 88%)}
.phero::after{content:"";position:absolute;inset:0;z-index:-1;
  background:linear-gradient(180deg, transparent 40%, rgba(8,10,7,.72) 100%)}
.shot{width:104px;height:104px;border-radius:50%;object-fit:cover;
  background:rgba(0,0,0,.35);flex:none;
  border:2px solid rgba(255,255,255,.14)}
.ppage h1{font-family:var(--agate,system-ui);text-transform:uppercase;
  font-size:2.1rem;line-height:.98;margin:0 0 .45rem;letter-spacing:.01em;
  font-weight:600}
/* inline-flex on the text, not the row: a flex container with a wrapping
   label left the team mark stranded on its own line at phone width. */
.who{color:rgba(255,255,255,.82);font:.78rem/1.45 var(--agate,system-ui),sans-serif;
  letter-spacing:.08em;text-transform:uppercase;margin:0}
.who .tlogo{vertical-align:-3px;margin-right:.4rem}
.tlogo{width:18px;height:18px;object-fit:contain;flex:none}
/* ---- projection ----
   A panel, not a chip. The number is why a lot of people open the page, and
   a season total needs the line under it to be worth anything. */
.proj{background:var(--card); border:1px solid var(--rule); border-radius:10px;
  padding:.9rem 1rem; margin:1.4rem 0 0}
.pjhead{display:flex; align-items:baseline; gap:.6rem}
.pjhead h2{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.07em; font-size:.72rem; color:var(--quiet); margin:0;
  border:0; padding:0}
/* The rank in the accent, not the team colour.
   __ACCENT__ is whatever the club wears, and a navy pill on a near-black
   panel is invisible -- Chicago's RB18 could not be read at all. Team
   colour belongs on the header banner, where there is a gradient behind
   it to sit against. */
.pjrank{font-family:var(--data); font-size:.72rem; color:var(--signal);
  border:1px solid var(--signal); border-radius:4px; padding:.05rem .4rem;
  font-weight:600; letter-spacing:.02em}
.pjfmts{display:flex; gap:1.4rem; flex-wrap:wrap; margin:.6rem 0 0}
.pjf span{display:block; font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.06em; font-size:.62rem; color:var(--quiet)}
/* The numbers in the accent, because they are what the panel is for.
   The team colour was reserved for the first format only, which made PPR
   look like the real one and the other two like footnotes. They are three
   readings of the same projection. */
.pjf b{font-family:var(--data); font-size:1.35rem; color:var(--signal);
  font-weight:600; line-height:1.15}
.pjline{display:flex; gap:1.1rem; flex-wrap:wrap; margin:.9rem 0 0;
  padding-top:.8rem; border-top:1px solid var(--rule)}
.pjs span{display:block; font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.05em; font-size:.58rem; color:var(--quiet)}
.pjs b{font-family:var(--data); font-size:.86rem; color:var(--signal);
  font-weight:600}
.pjmore{margin:.8rem 0 0; font-size:.76rem; color:var(--quiet)}
.pjmore a{color:var(--quiet); text-decoration:underline}
.pjmore a:hover{color:__ACCENT__}
/* Inline links in prose need a thumb-sized hit area without opening up the
   paragraph: the box grows and a negative margin pulls the line back. */
@media (max-width:760px){
  .pjmore a, .meta a{display:inline-block; min-height:44px;
    line-height:44px; margin-top:-11px; margin-bottom:-11px}
}
.pjnote{margin:.8rem 0 0; font-size:.76rem; color:var(--quiet)}
.pjnote a{color:var(--quiet); text-decoration:underline}
.pjnote a:hover{color:__ACCENT__}

.chips{display:flex;flex-wrap:wrap;gap:.4rem;margin:1rem 0 0}
.chip{font:.72rem/1 var(--agate,system-ui),sans-serif;letter-spacing:.06em;
  text-transform:uppercase;color:var(--quiet);border:1px solid var(--rule);
  border-radius:999px;padding:.42rem .7rem}
.chip b{color:var(--ink);font-weight:600}
.ppage h2{font:.72rem/1 var(--agate,system-ui),sans-serif;letter-spacing:.1em;
  text-transform:uppercase;color:var(--quiet);margin:2.25rem 0 .6rem}
.ppage article{background:var(--panel);border-radius:8px;
  padding:.9rem 1.1rem 1rem;margin-bottom:.55rem;
  border-left:3px solid __ACCENT__}
.rtop{display:flex;align-items:center;justify-content:space-between;
  gap:.6rem;margin-bottom:.5rem}
.rcat{font:.58rem/1 var(--agate,system-ui),sans-serif;letter-spacing:.09em;
  text-transform:uppercase;font-weight:600;color:__C2__}
.rago{font:.62rem/1 var(--data,ui-monospace),monospace;color:var(--quiet)}
.claim{margin:0 0 .5rem;font-size:1rem;line-height:1.55}
.meta{color:var(--quiet);font:.7rem/1.4 var(--agate,system-ui),sans-serif;
  letter-spacing:.05em;text-transform:uppercase;margin:0}
.meta a{color:var(--quiet);text-decoration:underline}
.meta a:hover{color:var(--signal)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(13rem,1fr));
  gap:.5rem}
.grid a{display:block;padding:.75rem .9rem;border:1px solid var(--rule);
  border-radius:8px;color:var(--ink);text-decoration:none}
.grid a:hover{border-color:var(--signal)}
.grid span{display:block;color:var(--quiet);
  font:.72rem/1.3 var(--agate,system-ui),sans-serif;margin-top:.2rem}
@media(max-width:34rem){
  .phero{flex-direction:column;text-align:center}
  .ppage h1{font-size:1.55rem}
  /* ---- projection ----
   A panel, not a chip. The number is why a lot of people open the page, and
   a season total needs the line under it to be worth anything. */
.proj{background:var(--card); border:1px solid var(--rule); border-radius:10px;
  padding:.9rem 1rem; margin:1.4rem 0 0}
.pjhead{display:flex; align-items:baseline; gap:.6rem}
.pjhead h2{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.07em; font-size:.72rem; color:var(--quiet); margin:0;
  border:0; padding:0}
/* The rank in the accent, not the team colour.
   __ACCENT__ is whatever the club wears, and a navy pill on a near-black
   panel is invisible -- Chicago's RB18 could not be read at all. Team
   colour belongs on the header banner, where there is a gradient behind
   it to sit against. */
.pjrank{font-family:var(--data); font-size:.72rem; color:var(--signal);
  border:1px solid var(--signal); border-radius:4px; padding:.05rem .4rem;
  font-weight:600; letter-spacing:.02em}
.pjfmts{display:flex; gap:1.4rem; flex-wrap:wrap; margin:.6rem 0 0}
.pjf span{display:block; font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.06em; font-size:.62rem; color:var(--quiet)}
/* The numbers in the accent, because they are what the panel is for.
   The team colour was reserved for the first format only, which made PPR
   look like the real one and the other two like footnotes. They are three
   readings of the same projection. */
.pjf b{font-family:var(--data); font-size:1.35rem; color:var(--signal);
  font-weight:600; line-height:1.15}
.pjline{display:flex; gap:1.1rem; flex-wrap:wrap; margin:.9rem 0 0;
  padding-top:.8rem; border-top:1px solid var(--rule)}
.pjs span{display:block; font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.05em; font-size:.58rem; color:var(--quiet)}
.pjs b{font-family:var(--data); font-size:.86rem; color:var(--signal);
  font-weight:600}
.pjmore{margin:.8rem 0 0; font-size:.76rem; color:var(--quiet)}
.pjmore a{color:var(--quiet); text-decoration:underline}
.pjmore a:hover{color:__ACCENT__}
/* Inline links in prose need a thumb-sized hit area without opening up the
   paragraph: the box grows and a negative margin pulls the line back. */
@media (max-width:760px){
  .pjmore a, .meta a{display:inline-block; min-height:44px;
    line-height:44px; margin-top:-11px; margin-bottom:-11px}
}
.pjnote{margin:.8rem 0 0; font-size:.76rem; color:var(--quiet)}
.pjnote a{color:var(--quiet); text-decoration:underline}
.pjnote a:hover{color:__ACCENT__}

.chips{justify-content:center}
}
"""

PAGE_FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap" rel="stylesheet">')

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
{fonts}
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="LineupBeat">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}">
{og_image}
<meta name="twitter:card" content="summary">
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="32x32" href="/icon-32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/icon-16.png">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<meta name="theme-color" content="#0A0C08">
{structured}
__CSS__
</head>
<body>
__HEADER__
<div class="wrap ppage">
{body}
__FOOTER__
</body>
</html>
"""


def _render(page, accent, c2="#C6F24E", section=None):
    """Swap in the real stylesheet, header and footer.

    CSS lives outside .format() because a stylesheet is full of braces and
    every one would have to be doubled otherwise -- unreadable, and it breaks
    the moment somebody edits the CSS without knowing why.
    """
    # Both halves are raw CSS, so the tags belong here rather than in either
    # constant -- otherwise the stylesheet prints as text at the top of the
    # page, which is exactly what happened.
    css = ("<style>" + (APP_CSS or "")
           + PAGE_CSS.replace("__ACCENT__", accent).replace("__C2__", c2)
           + "</style>")
    return (page
            .replace("__CSS__", css)
            .replace("__HEADER__",
                     {"data": DATA_HEADER,
                      "about": ABOUT_HEADER}.get(section, APP_HEADER))
            .replace("__FOOTER__", APP_FOOTER))


def page_description(name, who, nuggets):
    """A description that reads as a sentence in a search result.

    The newest claim alone came out at 61 characters -- "Brown was limited
    with what the team called general soreness" -- which reads as a fragment
    torn out of context and tells a searcher nothing about what the page is.
    Google also tends to write its own when the tag is too thin.
    """
    n = len(nuggets)
    if not n:
        # A projected player with nothing filed yet. Describe what the page
        # actually holds rather than promising reports it does not have.
        pr = PROJECTIONS.get(slug(name))
        if pr:
            return (f"{who}. Projected for {pr['ppr']:.1f} PPR points this "
                    f"season, {pr['pos']}{pr['rank']}. Beat reports appear "
                    f"here as they are filed.")[:158]
        return f"{who}. Beat reports appear here as they are filed."
    lead = (nuggets[0]["claim"] or "").rstrip(".")
    tail = (f"{n} beat reports on {name}, newest first, each linked to the "
            f"reporter who filed it.")
    body = f"{who}. {lead}. {tail}"
    return body[:158].rsplit(" ", 1)[0] if len(body) > 160 else body


# Loaded once, from the built projections page, so a player page and the
# board can never quote different numbers. Reading the spreadsheet again
# would be a second source of truth.
PROJECTIONS = {}


def load_projections():
    """Whatever the projections page was built from, by slug."""
    f = SITE / SPORT / "projections" / "index.html"
    if not f.exists():
        return {}
    m = re.search(r"const PB = (\{.*?\});", f.read_text(), re.S)
    if not m:
        return {}
    try:
        board = json.loads(m.group(1))
    except ValueError:
        return {}
    out = {}
    for pos, rows in board.items():
        for r in rows:
            if r.get("p") is None:
                continue
            # The board already resolved this player to a page, so use the
            # slug it landed on rather than deriving one again. Re-slugging
            # the display name gives "luther-burden-iii" for a page that
            # lives at "luther-burden": the suffix is in the projection
            # sheet and not in the roster, and two derivations of the same
            # thing will always eventually disagree.
            key = r.get("id") or slug(r["n"])
            out[key] = {"ppr": r["p"], "half": r.get("h"),
                        "std": r.get("s"), "rank": r.get("r"),
                        "pos": pos,
                        # The stat line behind the number. A season total on
                        # its own is a claim; the line under it is the
                        # reasoning, and it is the difference between a
                        # figure to trust and one to take on faith.
                        "line": {k: r.get(k) for k in
                                 ("targets", "rec", "recyd", "rectd",
                                  "ruatt", "ruyd", "rutd", "patt", "cmp",
                                  "payd", "patd", "int", "fl")}}
    return out


# Which stats to show, and what to call them, per position.
STAT_LABELS = {
    "QB": [("patt", "Att"), ("cmp", "Cmp"), ("payd", "Pass yds"),
           ("patd", "Pass TD"), ("int", "INT"), ("ruatt", "Car"),
           ("ruyd", "Rush yds"), ("rutd", "Rush TD")],
    "RB": [("ruatt", "Car"), ("ruyd", "Rush yds"), ("rutd", "Rush TD"),
           ("targets", "Tgt"), ("rec", "Rec"), ("recyd", "Rec yds"),
           ("rectd", "Rec TD")],
    "WR": [("targets", "Tgt"), ("rec", "Rec"), ("recyd", "Rec yds"),
           ("rectd", "Rec TD"), ("ruatt", "Car"), ("ruyd", "Rush yds")],
    "TE": [("targets", "Tgt"), ("rec", "Rec"), ("recyd", "Rec yds"),
           ("rectd", "Rec TD")],
}
WHOLE = {"payd", "recyd", "ruyd", "patt", "ruatt", "targets"}


def projection_block(name, pos):
    """The projection, with the line it came from.

    A number on its own asks to be believed. The line under it can be
    checked against what somebody already thinks about the player, which is
    the whole reason to show it rather than a bare total.
    """
    pr = PROJECTIONS.get(slug(name))
    if not pr:
        return ""
    # The sheet's own tab, not the roster's position.
    #
    # The roster had Josh Allen as LB, so the rank read "LB1" and the stat
    # line came back empty because there is no LB row in the label map. The
    # board put him on the QB sheet; that is the position the projection is
    # actually for.
    pos = (pr.get("pos") or pos or "").upper()
    line = pr.get("line") or {}
    cells = []
    for key, label in STAT_LABELS.get(pos, []):
        v = line.get(key)
        if v is None:
            continue
        shown = f"{round(v):,}" if key in WHOLE else f"{v:.1f}"
        cells.append(f'<div class="pjs"><span>{esc(label)}</span>'
                     f'<b>{shown}</b></div>')

    fmts = "".join(
        f'<div class="pjf"><span>{lab}</span><b>{pr[k]:.1f}</b></div>'
        for k, lab in (("ppr", "PPR"), ("half", "Half"), ("std", "Standard"))
        if pr.get(k) is not None)

    return (
        f'\n  <section class="proj">\n'
        f'    <div class="pjhead">\n'
        f'      <h2>2026 projection</h2>\n'
        f'      <span class="pjrank">{esc(pos)}{pr.get("rank") or ""}'
        f'</span>\n'
        f'    </div>\n'
        f'    <div class="pjfmts">{fmts}</div>\n'
        + (f'    <div class="pjline">{"".join(cells)}</div>\n' if cells else "")
        # A link into the board, from every page that has a projection.
        #
        # Seven hundred pages pointing at one is the strongest internal
        # signal the site has, and it was going unused. The positional page
        # rather than the index, because that is the one competing for a
        # winnable query.
        + (f'    <p class="pjmore">'
           f'<a href="/{SPORT}/projections/{pos.lower()}/">'
           f'All {esc(pos)} projections</a> &middot; '
           f'<a href="/{SPORT}/draft-value/">draft value against ADP</a>'
           f'</p>\n' if pos else "")
        + f'  </section>\n')


def player_page(p, nuggets, base):
    name, team = p["name"], p["team"]
    pos, meta = p["pos"], p.get("meta") or {}
    url = f"{base}/{SPORT}/{slug(name)}/"
    accent = TEAM_COLORS.get(team, "#C6F24E")
    c2 = TEAM_C2.get(team, "#C6F24E")
    shot = (f"https://sleepercdn.com/content/nfl/players/thumb/"
            f"{p['id'].replace('nfl-','')}.jpg")

    who = POS_NAMES.get(pos, pos or "Player")
    if team:
        who += f" for the {TEAM_NAMES.get(team, team)}"

    chips = []
    if meta.get("depth_pos") and str(meta.get("depth_order") or "").strip():
        chips.append(f'<span class="chip">Depth <b>{esc(meta["depth_pos"])}'
                     f'{esc(meta["depth_order"])}</b></span>')
    if str(meta.get("adp") or "").strip():
        chips.append(f'<span class="chip">ADP <b>{esc(meta["adp"])}</b></span>')
    if str(meta.get("age") or "").strip():
        chips.append(f'<span class="chip">Age <b>{esc(meta["age"])}</b></span>')
    y = str(meta.get("years_exp") or "").strip()
    if y:
        chips.append(f'<span class="chip">'
                     + ("Rookie" if y == "0" else f"Year <b>{int(float(y))+1}</b>")
                     + '</span>')
    if str(meta.get("injury_status") or "").strip():
        chips.append(f'<span class="chip">Status '
                     f'<b>{esc(meta["injury_status"])}</b></span>')

    # No projection chips.
    #
    # There is a projection panel below with the same number and the stat
    # line behind it. A chip saying PPR 361.1 four inches above a panel
    # saying PPR 361.1 is not emphasis, it is the page disagreeing with
    # itself about where the number lives.

    arts = []
    for n in nuggets:
        try:
            attrs = json.loads(n.get("attributions") or "[]")
        except (json.JSONDecodeError, TypeError):
            attrs = []
        a = attrs[0] if attrs else {}
        link = a.get("url")
        credit = a.get("source_name") or a.get("outlet") or "beat report"
        extra = ""
        if len(attrs) > 1:
            extra = f" and {len(attrs)-1} other" + ("s" if len(attrs) > 2 else "")
        cite = (f'<a href="{esc(link)}" rel="nofollow noopener">{esc(credit)}</a>'
                if link else esc(credit))
        cat = (n.get("category") or "").replace("_", " ")
        arts.append(
            f'  <article>\n'
            f'    <div class="rtop"><span class="rcat">{esc(cat)}</span>'
            f'<span class="rago">{esc(ago(n["published_at"]))}</span></div>\n'
            f'    <p class="claim">{esc(n["claim"])}</p>\n'
            f'    <p class="meta">{esc(when(n["published_at"]))} &middot; '
            f'{cite}{esc(extra)}</p>\n  </article>')

    ld = {"@context": "https://schema.org", "@type": "Person", "name": name,
          "url": url, "image": shot, "jobTitle": who}
    # A page that changes daily should say when it last changed. Google reads
    # it for freshness; without it a wire looks like a static profile.
    if nuggets:
        ld["subjectOf"] = {"@type": "CollectionPage",
                           "name": f"{name} beat reports", "url": url,
                           "dateModified": nuggets[0]["published_at"][:19]}
    # Google renders breadcrumbs in the result itself, which is worth more
    # than the nav pill this replaced: it shows the page's place in the site
    # instead of a bare URL, and it improves click-through.
    crumb_ld = {"@context": "https://schema.org", "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "LineupBeat",
                     "item": base + "/"}]}
    if team:
        crumb_ld["itemListElement"].append(
            {"@type": "ListItem", "position": 2,
             "name": TEAM_NAMES.get(team, team),
             "item": f"{base}/{SPORT}/team/{slug(team)}/"})
    crumb_ld["itemListElement"].append(
        {"@type": "ListItem",
         "position": len(crumb_ld["itemListElement"]) + 1,
         "name": name, "item": url})
    if team:
        ld["memberOf"] = {"@type": "SportsTeam",
                          "name": TEAM_NAMES.get(team, team)}

    crumb = (
        f'  <nav class="crumbs" aria-label="Breadcrumb">'
        f'<a href="/">LineupBeat</a>'
        + (f'<span>/</span><a href="/{SPORT}/team/{slug(team)}/">'
           f'{esc(TEAM_NAMES.get(team, team))}</a>' if team else "")
        + f'<span>/</span><b>{esc(name)}</b></nav>\n')

    body = (crumb + f'  <div class="phero">\n    <img class="shot" src="{esc(shot)}" '
            f'alt="{esc(name)}" loading="lazy" width="84" height="84">\n'
            f'    <div>\n      <h1>{esc(name)}</h1>\n'
            f'      <p class="who">'
            + (f'<img class="tlogo" src="https://a.espncdn.com/i/teamlogos/'
               f'nfl/500/{team.lower()}.png" alt="" loading="lazy" '
               f'width="18" height="18">' if team else "")
            + f'{esc(who)}</p>\n    </div>\n  </div>\n'
            + (f'  <div class="chips">{"".join(chips)}</div>\n' if chips else "")
            + projection_block(name, pos)
            + (f'  <h2>{len(nuggets)} beat report'
               f'{"s" if len(nuggets) != 1 else ""}, newest first</h2>\n'
               if nuggets else
               '  <h2>No beat reports yet</h2>\n'
               '  <p class="dlede">Nothing has been filed about this player '
               'since the wire started watching. The projection above is '
               'what the board has him down for.</p>\n')
            + "\n".join(arts))

    return _render(PAGE.format(
        fonts=PAGE_FONTS,
        title=trim(esc(f"{name} news, beat reports and updates | LineupBeat"), 60),
        description=trim(esc(page_description(name, who, nuggets)), 155),
        canonical=esc(url), og_type="profile",
        og_image=(f'<meta property="og:image" content="{esc(shot)}">'
                  f'<meta name="twitter:image" content="{esc(shot)}">'),
        structured=(f'<script type="application/ld+json">{json.dumps(ld)}</script>'
                    f'<script type="application/ld+json">'
                    f'{json.dumps(crumb_ld)}</script>'),
        body=body), accent, c2)


def team_page(team, players, count, base):
    full = TEAM_NAMES.get(team, team)
    url = f"{base}/{SPORT}/team/{slug(team)}/"
    accent = TEAM_COLORS.get(team, "#C6F24E")
    c2 = TEAM_C2.get(team, "#C6F24E")
    logo = f"https://a.espncdn.com/i/teamlogos/nfl/500/{team.lower()}.png"
    # A section called "Players in the news" listing a player with no
    # reports says the opposite of its own heading. They stay on the
    # roster; they are not news.
    in_news = [(n, c) for n, c in players if c > 0]
    cards = "\n".join(
        f'    <a href="/{SPORT}/{slug(n)}/">{esc(n)}<span>{c} report'
        f'{"s" if c != 1 else ""}</span></a>' for n, c in in_news)
    ld = {"@context": "https://schema.org", "@type": "SportsTeam",
          "name": full, "url": url, "logo": logo}
    crumb = (f'  <nav class="crumbs" aria-label="Breadcrumb">'
             f'<a href="/">LineupBeat</a><span>/</span>'
             f'<b>{esc(full)}</b></nav>\n')
    crumb_ld = {"@context": "https://schema.org", "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "LineupBeat",
                     "item": base + "/"},
                    {"@type": "ListItem", "position": 2, "name": full,
                     "item": url}]}

    body = (crumb + f'  <div class="phero">\n    <img class="shot" src="{esc(logo)}" '
            f'alt="{esc(full)}" loading="lazy" width="84" height="84" '
            f'style="border-radius:0;object-fit:contain">\n'
            f'    <div>\n      <h1>{esc(full)}</h1>\n'
            f'      <p class="who">{count} beat reports across '
            f'{len(in_news)} players</p>\n    </div>\n  </div>\n'
            f'  <h2>Players in the news</h2>\n'
            f'  <div class="grid">\n{cards}\n  </div>')
    return _render(PAGE.format(
        fonts=PAGE_FONTS,
        title=trim(esc(f"{full} beat reports and player news | LineupBeat"), 60),
        # Was 90 characters, which leaves half the snippet empty. Naming
        # what a reader actually gets is both longer and more useful.
        # Was 90 characters, which leaves half a search snippet empty.
        # Naming what a reader gets is both longer and more useful, and it
        # has to stay under 158 or the end is cut off anyway.
        description=esc(f"Local beat reporting on the {full}. Injuries, "
                        f"first-team reps and depth chart moves matched to "
                        f"the players affected, newest first."),
        canonical=esc(url), og_type="website",
        og_image=f'<meta property="og:image" content="{esc(logo)}">',
        structured=(f'<script type="application/ld+json">{json.dumps(ld)}</script>'
                    f'<script type="application/ld+json">'
                    f'{json.dumps(crumb_ld)}</script>'),
        body=body), accent, c2)


# Filter the table as you type. Two hundred rows is too many to scan for one
# player, and this page is read while somebody is on the clock.
FIND_JS = """
document.addEventListener('DOMContentLoaded', function () {
  var q = document.getElementById('pfind');
  if (!q) return;
  var rows = [].slice.call(document.querySelectorAll('.dtab tbody tr'));
  q.addEventListener('input', function () {
    var v = q.value.trim().toLowerCase();
    rows.forEach(function (r) {
      r.classList.toggle('hide', v && r.textContent.toLowerCase().indexOf(v) < 0);
    });
  });
});
"""


DATA_PAGE_CSS = """

:root {
  --lb-green: #c6f53c;
  --lb-bg: #050708;
  --lb-panel: #0e1213;
  --lb-panel-hover: #101516;
  --lb-text: #f2f2ed;
  --lb-muted: #a2a8a4;
  --lb-dim: #767d78;
  --lb-line: rgba(255,255,255,.12);
  --lb-line-strong: rgba(255,255,255,.20);
  --lb-red: #ef6a60;
  --lb-yellow: #e4cf58;
  --lb-max: 1240px;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; background: var(--lb-bg); }
body {
  margin: 0;
  background: var(--lb-bg);
  color: var(--lb-text);
  font-family: "Barlow Condensed", Arial, sans-serif;
}
a { color: inherit; }
svg { display: block; }

.lb-data-page {
  position: relative;
  overflow: hidden;
  min-height: 100vh;
  background:
    radial-gradient(circle at 22% 8%, rgba(50,61,63,.22) 0%, rgba(5,7,8,0) 34%),
    radial-gradient(circle at 84% 28%, rgba(198,245,60,.035) 0%, rgba(5,7,8,0) 30%),
    var(--lb-bg);
}

.lb-data-page::before {
  content: "";
  pointer-events: none;
  position: absolute;
  inset: 0;
  opacity: .62;
  background-image:
    linear-gradient(rgba(255,255,255,.018) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.018) 1px, transparent 1px);
  background-size: 72px 72px;
  mask-image: linear-gradient(to bottom, black 0, rgba(0,0,0,.18) 46%, transparent 100%);
}

.lb-container {
  width: min(var(--lb-max), calc(100% - 48px));
  margin: 0 auto;
  position: relative;
  z-index: 2;
}

.lb-eyebrow,
.lb-section-kicker,
.lb-card-kicker,
.lb-status-pill,
.lb-future-timing {
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .11em;
}

.lb-eyebrow,
.lb-section-kicker,
.lb-card-kicker { color: var(--lb-green); }

/* HERO */
.lb-data-hero {
  padding: 92px 0 70px;
  border-bottom: 1px solid var(--lb-line);
}

.lb-data-hero-grid {
  /* One column: the second held the proof row, and with that gone it
     reserved 340px of nothing beside the copy. */
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 80px;
  align-items: end;
}

.lb-eyebrow { font-size: 15px; line-height: 1; }

.lb-data-page .lb-data-title, .lb-data-title {
  /* .ppage h1 also uppercases, and the homepage headline is
     sentence case. Stated rather than inherited. */
  text-transform: none;
  margin: 20px 0 24px;
  max-width: 1000px;
  font-family: var(--text);
  font-size: clamp(58px, 6.1vw, 86px);
  font-weight: 400;
  line-height: .95;
  letter-spacing: -.035em;
}

.lb-data-title .accent { color: var(--lb-green); }

.lb-data-intro {
  max-width: 860px;
  margin: 0;
  color: #b7bcb8;
  font-family: Georgia, serif;
  font-size: 20px;
  line-height: 1.65;
}

.lb-data-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 34px;
}

.lb-button {
  min-height: 58px;
  padding: 0 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 18px;
  border-radius: 7px;
  border: 1px solid var(--lb-line-strong);
  text-decoration: none;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: .045em;
  text-transform: uppercase;
  transition: transform .18s ease, border-color .18s ease, background .18s ease;
}

.lb-button:hover { transform: translateY(-2px); }
.lb-button-primary {
  color: #070907;
  background: var(--lb-green);
  border-color: var(--lb-green);
}
.lb-button-primary:hover { background: #d4ff50; }
.lb-button-secondary:hover { border-color: var(--lb-green); }

.lb-arrow {
  width: 20px;
  height: 20px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
}

.lb-hero-proof {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  align-self: stretch;
  border: 1px solid var(--lb-line);
  border-radius: 14px;
  background: rgba(13,17,18,.72);
  backdrop-filter: blur(10px);
  overflow: hidden;
}

.lb-proof-stat {
  min-height: 148px;
  padding: 26px 22px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}

.lb-proof-stat + .lb-proof-stat { border-left: 1px solid var(--lb-line); }

.lb-proof-stat strong {
  font-size: 36px;
  line-height: .95;
  font-weight: 700;
}

.lb-proof-stat span {
  margin-top: 10px;
  color: var(--lb-muted);
  font-size: 12px;
  line-height: 1.25;
  letter-spacing: .09em;
  text-transform: uppercase;
}

/* SECTIONS */
.lb-section {
  padding: 78px 0;
  border-bottom: 1px solid var(--lb-line);
}

.lb-section-heading {
  margin-bottom: 32px;
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 40px;
}

.lb-section-kicker { font-size: 14px; line-height: 1; }

.lb-section-heading h2,
.lb-philosophy h2,
.lb-wire-strip h2 {
  margin: 9px 0 0;
  font-family: var(--text);
  font-weight: 400;
  line-height: 1;
  letter-spacing: -.025em;
}

.lb-section-heading h2 { font-size: clamp(38px, 4vw, 54px); }

.lb-section-heading p {
  max-width: 540px;
  margin: 0 0 3px;
  color: var(--lb-muted);
  font-family: Georgia, serif;
  font-size: 16px;
  line-height: 1.55;
}

/* CARDS */
.lb-feature-grid,
.lb-tool-grid,
.lb-future-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0,1fr));
  gap: 20px;
}

.lb-feature-card,
.lb-tool-card,
.lb-future-card {
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  color: inherit;
  border: 1px solid var(--lb-line);
  border-radius: 15px;
  background:
    linear-gradient(145deg, rgba(255,255,255,.024), rgba(255,255,255,0) 43%),
    var(--lb-panel);
}

a.lb-feature-card,
a.lb-tool-card { text-decoration: none; }

a.lb-feature-card,
a.lb-tool-card {
  transition: border-color .2s ease, transform .2s ease, background .2s ease;
}

a.lb-feature-card:hover,
a.lb-tool-card:hover {
  transform: translateY(-3px);
  border-color: rgba(198,245,60,.42);
  background:
    linear-gradient(145deg, rgba(198,245,60,.035), rgba(255,255,255,0) 43%),
    var(--lb-panel-hover);
}

.lb-feature-card {
  min-height: 500px;
  padding: 34px;
}

.lb-feature-number {
  position: absolute;
  right: 26px;
  top: 18px;
  color: rgba(255,255,255,.055);
  font-size: 74px;
  font-weight: 800;
  line-height: 1;
}

.lb-card-kicker { font-size: 13px; }

.lb-feature-card h3,
.lb-tool-card h3,
.lb-future-card h3 {
  margin: 13px 0 12px;
  font-family: var(--text);
  font-weight: 400;
  letter-spacing: -.02em;
}

.lb-feature-card h3 { font-size: 38px; }
.lb-tool-card h3,
.lb-future-card h3 { font-size: 30px; }

.lb-card-deck {
  max-width: 540px;
  margin: 0;
  color: #aeb4b0;
  font-family: Georgia, serif;
  font-size: 16px;
  line-height: 1.55;
}

.lb-preview {
  margin: 31px 0 28px;
  border: 1px solid var(--lb-line);
  border-radius: 10px;
  background: #080b0c;
  overflow: hidden;
}

.lb-preview-head {
  min-height: 42px;
  padding: 0 15px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #828a85;
  border-bottom: 1px solid var(--lb-line);
  font-size: 11px;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.lb-preview-badge {
  padding: 4px 8px;
  color: var(--lb-green);
  border: 1px solid rgba(198,245,60,.24);
  border-radius: 999px;
  font-size: 10px;
  line-height: 1;
}

.lb-preview-table { padding: 8px 14px 10px; }

.lb-preview-row {
  display: grid;
  grid-template-columns: 1fr 78px;
  min-height: 42px;
  align-items: center;
  border-bottom: 1px solid rgba(255,255,255,.06);
  font-size: 14px;
}

.lb-preview-row:last-child { border-bottom: 0; }
.lb-preview-row strong { font-size: 15px; font-weight: 600; }
.lb-preview-row > :last-child {
  text-align: right;
  color: var(--lb-green);
  font-weight: 700;
}

.lb-value-preview .lb-preview-row {
  grid-template-columns: 1fr 56px 62px 58px;
  gap: 8px;
  font-size: 13px;
}
.lb-value-preview .lb-preview-row span:not(:first-child) { text-align: right; }
.lb-value-positive { color: var(--lb-green) !important; }
.lb-value-negative { color: var(--lb-red) !important; }

.lb-card-footer {
  margin-top: auto;
  padding-top: 22px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--lb-line);
  color: var(--lb-green);
  font-size: 15px;
  font-weight: 700;
  letter-spacing: .06em;
  text-transform: uppercase;
}
.lb-card-footer .lb-arrow { width: 18px; height: 18px; }

/* LIVE TOOL CARDS */
.lb-tool-card {
  min-height: 340px;
  padding: 29px;
}

.lb-tool-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.lb-tool-icon {
  width: 43px;
  height: 43px;
  color: var(--lb-green);
  fill: none;
  stroke: currentColor;
  stroke-width: 1.55;
}

.lb-tool-tag {
  color: var(--lb-dim);
  font-size: 11px;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.lb-tool-visual {
  min-height: 92px;
  margin: 20px 0 23px;
  padding: 16px;
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 9px;
  background: #090c0d;
}

/* DURABILITY */
.lb-season-line {
  display: grid;
  grid-template-columns: 42px 1fr 28px;
  gap: 10px;
  align-items: center;
  margin: 7px 0;
  color: #89908b;
  font-size: 12px;
}
.lb-season-track { height: 7px; overflow: hidden; background: rgba(255,255,255,.06); }
.lb-season-track i { display: block; width: var(--width); height: 100%; background: var(--lb-green); }

/* SOS */
.lb-schedule-row {
  display: grid;
  grid-template-columns: 36px 1fr 68px;
  align-items: center;
  min-height: 27px;
  border-bottom: 1px solid rgba(255,255,255,.05);
  color: #9ea49f;
  font-size: 12px;
}
.lb-schedule-row:last-child { border-bottom: 0; }
.lb-difficulty { text-align: right; font-weight: 700; }
.easy { color: var(--lb-green); }
.hard { color: var(--lb-red); }
.avg { color: var(--lb-yellow); }

/* COACHING */
.lb-tendency-head {
  display: flex;
  justify-content: space-between;
  color: #929994;
  font-size: 11px;
  letter-spacing: .06em;
  text-transform: uppercase;
}
.lb-tendency-row {
  display: grid;
  grid-template-columns: 55px 1fr 34px;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
  font-size: 12px;
  color: #9da39f;
}
.lb-tendency-bar { height: 7px; background: rgba(255,255,255,.06); }
.lb-tendency-bar i { display: block; width: var(--width); height: 100%; background: var(--lb-green); }

/* OL + RB */
.lb-ol-grid {
  display: grid;
  grid-template-columns: repeat(2,1fr);
  height: 100%;
  gap: 10px;
}
.lb-ol-stat {
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  border: 1px solid rgba(255,255,255,.06);
}
.lb-ol-stat small {
  color: #7e8680;
  font-size: 10px;
  letter-spacing: .07em;
  text-transform: uppercase;
}
.lb-ol-stat strong { margin-top: 5px; font-size: 21px; }
.lb-ol-stat strong.good { color: var(--lb-green); }

/* FUTURE TOOLS */
.lb-future-section {
  position: relative;
  background:
    linear-gradient(180deg, rgba(198,245,60,.018), transparent 32%),
    #070a0b;
}

.lb-future-card {
  min-height: 310px;
  padding: 28px;
}

.lb-future-card::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(125deg, rgba(255,255,255,.015), transparent 40%);
}

.lb-future-card > * { position: relative; z-index: 1; }

.lb-future-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.lb-status-pill {
  padding: 6px 9px;
  border: 1px solid rgba(198,245,60,.26);
  border-radius: 999px;
  color: var(--lb-green);
  font-size: 10px;
  line-height: 1;
}

.lb-future-timing {
  color: #767e79;
  font-size: 10px;
}

.lb-future-visual {
  margin: 22px 0 0;
  padding: 14px;
  border: 1px solid rgba(255,255,255,.07);
  border-radius: 9px;
  background: rgba(5,7,8,.68);
}

.lb-future-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 16px;
  min-height: 31px;
  align-items: center;
  border-bottom: 1px solid rgba(255,255,255,.05);
  color: #939a95;
  font-size: 12px;
}
.lb-future-row:last-child { border-bottom: 0; }
.lb-future-row b { color: var(--lb-green); font-weight: 700; }

.lb-future-note {
  margin-top: auto;
  padding-top: 20px;
  border-top: 1px solid var(--lb-line);
  color: #7f8782;
  font-size: 12px;
  letter-spacing: .04em;
  text-transform: uppercase;
}

/* PHILOSOPHY */
.lb-philosophy {
  padding: 84px 0;
  background:
    linear-gradient(90deg, rgba(198,245,60,.025), transparent 35%, transparent 65%, rgba(198,245,60,.018));
}
.lb-philosophy-grid {
  display: grid;
  grid-template-columns: .85fr 1.15fr;
  gap: 80px;
  align-items: center;
}
.lb-philosophy h2,
.lb-wire-strip h2 { font-size: clamp(40px, 4.5vw, 60px); }
.lb-philosophy-copy {
  color: #adb3ae;
  font-family: Georgia, serif;
  font-size: 17px;
  line-height: 1.7;
}
.lb-philosophy-copy p { margin: 0 0 17px; }
.lb-method-list {
  margin-top: 25px;
  display: grid;
  grid-template-columns: repeat(3,1fr);
  gap: 12px;
}
.lb-method-pill {
  min-height: 64px;
  padding: 12px 13px;
  display: flex;
  align-items: center;
  gap: 9px;
  border: 1px solid var(--lb-line);
  border-radius: 8px;
  color: #959c97;
  font-size: 11px;
  line-height: 1.25;
  letter-spacing: .05em;
  text-transform: uppercase;
}
.lb-method-pill::before {
  content: "";
  width: 7px;
  height: 7px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--lb-green);
}

/* WIRE CTA */
.lb-wire-strip {
  padding: 76px 0;
  border-top: 1px solid var(--lb-line);
  border-bottom: 1px solid var(--lb-line);
  background:
    radial-gradient(circle at 76% 50%, rgba(198,245,60,.06), transparent 28%),
    #080b0c;
}
.lb-wire-strip-grid {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 60px;
}
.lb-wire-strip p {
  max-width: 730px;
  margin: 18px 0 0;
  color: #aeb4af;
  font-family: Georgia, serif;
  font-size: 17px;
  line-height: 1.65;
}

/* RESPONSIVE */
@media (max-width: 1040px) {
  .lb-data-hero-grid,
  .lb-philosophy-grid {
    grid-template-columns: 1fr;
    gap: 44px;
  }
  .lb-hero-proof { max-width: 700px; }
  .lb-wire-strip-grid { grid-template-columns: 1fr; gap: 30px; }
}

@media (max-width: 800px) {
  .lb-feature-grid,
  .lb-tool-grid,
  .lb-future-grid { grid-template-columns: 1fr; }

  .lb-section-heading {
    align-items: start;
    flex-direction: column;
    gap: 14px;
  }
  .lb-feature-card { min-height: 450px; }
}

@media (max-width: 620px) {
  .lb-container { width: calc(100% - 30px); }
  .lb-data-hero { padding: 62px 0 52px; }
  .lb-data-title { font-size: clamp(50px, 16vw, 68px); }
  .lb-data-intro { font-size: 17px; }
  .lb-data-actions { flex-direction: column; }
  .lb-button { width: 100%; }
  .lb-hero-proof { grid-template-columns: 1fr; }
  .lb-proof-stat { min-height: 100px; justify-content: center; }
  .lb-proof-stat + .lb-proof-stat {
    border-left: 0;
    border-top: 1px solid var(--lb-line);
  }
  .lb-section { padding: 58px 0; }
  .lb-feature-card,
  .lb-tool-card,
  .lb-future-card { padding: 23px; }
  .lb-feature-card h3 { font-size: 33px; }
  .lb-value-preview .lb-preview-row {
    grid-template-columns: 1fr 48px 52px 50px;
    gap: 4px;
    font-size: 11px;
  }
  .lb-method-list { grid-template-columns: 1fr; }
  .lb-philosophy,
  .lb-wire-strip { padding: 58px 0; }
}
"""

DATA_PAGE_HTML = """<main class="lb-data-page">

  <section class="lb-data-hero">
    <div class="lb-container">
      <div class="lb-data-hero-grid">

        <div>
          <div class="lb-eyebrow">FANTASY DATA</div>

          <h1 class="lb-data-title">
            NFL Fantasy Football <span class="accent">Data</span>
          </h1>

          <p class="lb-data-hook">
            The numbers behind the decision.
          </p>

          <p class="lb-data-intro">
            Projections, market value, schedule, durability and team context,
            built to help you decide who to draft, start and avoid.
          </p>

          <div class="lb-data-actions">
            <a class="lb-button lb-button-primary" href="/nfl/projections/">
              <span>VIEW PROJECTIONS</span>
              <svg class="lb-arrow" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 12h13M13 6l6 6-6 6"/>
              </svg>
            </a>

            <a class="lb-button lb-button-secondary" href="#live-tools">
              Explore All Tools
            </a>
          </div>
        </div>

      </div>
    </div>
  </section>


  <!-- START HERE -->
  <section class="lb-section" id="live-tools">
    <div class="lb-container">

      <div class="lb-section-heading">
        <div>
          <div class="lb-section-kicker">START HERE</div>
          <h2>Our view, then the market.</h2>
        </div>

        <p>
          Start with LineupBeat's projected stat line, then compare it with
          where the market is actually drafting the player.
        </p>
      </div>

      <div class="lb-feature-grid">

        <a class="lb-feature-card" href="/nfl/projections/">
          <span class="lb-feature-number">01</span>

          <div class="lb-card-kicker">YEARLY PROJECTIONS</div>
          <h3>Start with our view of the player.</h3>

          <p class="lb-card-deck">
            Full season projections for QB, RB, WR and TE, with the complete
            stat line behind every fantasy point.
          </p>

          <div class="lb-preview" aria-hidden="true">
            <div class="lb-preview-head">
              <span>PROJECTION PREVIEW</span>
              <span class="lb-preview-badge">PPR</span>
            </div>

            <div class="lb-preview-table">{PROJ_ROWS}</div>
          </div>

          <div class="lb-card-footer">
            <span>VIEW PROJECTIONS</span>
            <svg class="lb-arrow" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg>
          </div>
        </a>


        <a class="lb-feature-card" href="/nfl/draft-value/">
          <span class="lb-feature-number">02</span>

          <div class="lb-card-kicker">ADP &amp; DRAFT VALUE</div>
          <h3>Then compare our view with the market.</h3>

          <p class="lb-card-deck">
            See where a player is being drafted versus where LineupBeat
            projects him, and find the biggest values and reaches.
          </p>

          <div class="lb-preview lb-value-preview" aria-hidden="true">
            <div class="lb-preview-head">
              <span>VALUE PREVIEW</span>
              <span class="lb-preview-badge">ADP vs LB</span>
            </div>

            <div class="lb-preview-table">{VALUE_ROWS}</div>
          </div>

          <div class="lb-card-footer">
            <span>FIND DRAFT VALUE</span>
            <svg class="lb-arrow" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg>
          </div>
        </a>

      </div>
    </div>
  </section>


  <!-- LIVE CONTEXT TOOLS -->
  <section class="lb-section">
    <div class="lb-container">

      <div class="lb-section-heading">
        <div>
          <div class="lb-section-kicker">GO DEEPER</div>
          <h2>Context changes the evaluation.</h2>
        </div>

        <p>
          Use schedule, availability, play calling and offensive line context
          to understand what sits underneath a player's fantasy projection.
        </p>
      </div>


      <div class="lb-tool-grid">

        <a class="lb-tool-card" href="/nfl/durability/">
          <div class="lb-tool-top">
            <svg class="lb-tool-icon" viewBox="0 0 48 48" aria-hidden="true">
              <path d="M24 5v38M5 24h38"/>
              <path d="M10 13h8v8h-8zM30 27h8v8h-8z"/>
            </svg>
            <span class="lb-tool-tag">Availability history</span>
          </div>

          <h3>Who actually stays available?</h3>

          <p class="lb-card-deck">
            Games played, injuries, IR, suspensions and availability history
            compared with current draft cost.
          </p>

          <div class="lb-tool-visual" aria-hidden="true">
            <div class="lb-season-line">
              <span>2023</span><div class="lb-season-track"><i style="--width:100%"></i></div><span>17</span>
            </div>
            <div class="lb-season-line">
              <span>2024</span><div class="lb-season-track"><i style="--width:82%"></i></div><span>14</span>
            </div>
            <div class="lb-season-line">
              <span>2025</span><div class="lb-season-track"><i style="--width:94%"></i></div><span>16</span>
            </div>
          </div>

          <div class="lb-card-footer">
            <span>CHECK DURABILITY</span>
            <svg class="lb-arrow" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg>
          </div>
        </a>


        <a class="lb-tool-card" href="/nfl/strength-of-schedule/">
          <div class="lb-tool-top">
            <svg class="lb-tool-icon" viewBox="0 0 48 48" aria-hidden="true">
              <rect x="7" y="10" width="34" height="31" rx="3"/>
              <path d="M15 5v10M33 5v10M7 20h34"/>
            </svg>
            <span class="lb-tool-tag">Weekly context</span>
          </div>

          <h3>Who has the better road ahead?</h3>

          <p class="lb-card-deck">
            Opponent difficulty by actual fantasy points allowed to RBs, WRs
            and TEs, reweighted as the season changes.
          </p>

          <div class="lb-tool-visual" aria-hidden="true">
            {SCHED_ROWS}
          </div>

          <div class="lb-card-footer">
            <span>VIEW SCHEDULES</span>
            <svg class="lb-arrow" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg>
          </div>
        </a>


        <a class="lb-tool-card" href="/nfl/coaching/">
          <div class="lb-tool-top">
            <svg class="lb-tool-icon" viewBox="0 0 48 48" aria-hidden="true">
              <path d="M7 37c8-1 12-7 16-15 3-6 7-9 18-11"/>
              <circle cx="9" cy="37" r="3"/>
              <circle cx="23" cy="22" r="3"/>
              <circle cx="41" cy="11" r="3"/>
            </svg>
            <span class="lb-tool-tag">All 32 offenses</span>
          </div>

          <h3>Who is actually calling the plays?</h3>

          <p class="lb-card-deck">
            Play callers, offensive tendencies and positional impact across
            all 32 NFL teams.
          </p>

          <div class="lb-tool-visual" aria-hidden="true">
            <div class="lb-tendency-head"><span>OFFENSE PROFILE</span><span>NEW CALLER</span></div>
            <div class="lb-tendency-row"><span>PASS</span><div class="lb-tendency-bar"><i style="--width:74%"></i></div><span>↑</span></div>
            <div class="lb-tendency-row"><span>RUN</span><div class="lb-tendency-bar"><i style="--width:51%"></i></div><span>→</span></div>
            <div class="lb-tendency-row"><span>PACE</span><div class="lb-tendency-bar"><i style="--width:64%"></i></div><span>↑</span></div>
          </div>

          <div class="lb-card-footer">
            <span>EXPLORE OFFENSES</span>
            <svg class="lb-arrow" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg>
          </div>
        </a>


        <a class="lb-tool-card" href="/nfl/offensive-line-rb-performance/">
          <div class="lb-tool-top">
            <svg class="lb-tool-icon" viewBox="0 0 48 48" aria-hidden="true">
              <path d="M8 34h8V20H8zM20 34h8V12h-8zM32 34h8V7h-8z"/>
              <path d="M7 40h35"/>
            </svg>
            <span class="lb-tool-tag">Historical performance</span>
          </div>

          <h3>Was it the RB or the line?</h3>

          <p class="lb-card-deck">
            Compare run blocking with how much each back gained above or below
            expectation.
          </p>

          <div class="lb-tool-visual" aria-hidden="true">
            <div class="lb-ol-grid">
              <div class="lb-ol-stat">
                <small>Run blocking</small>
                <strong>#30</strong>
              </div>
              <div class="lb-ol-stat">
                <small>RYOE / ATT</small>
                <strong class="good">+0.97</strong>
              </div>
            </div>
          </div>

          <div class="lb-card-footer">
            <span>COMPARE RB PERFORMANCE</span>
            <svg class="lb-arrow" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg>
          </div>
        </a>

      </div>
    </div>
  </section>


  <!-- FUTURE TOOL ROADMAP -->
  <section class="lb-section lb-future-section">
    <div class="lb-container">

      <div class="lb-section-heading">
        <div>
          <div class="lb-section-kicker">NEXT ON LINEUPBEAT</div>
          <h2>Tools built around what changed.</h2>
        </div>

        <p>
          These cards are intentionally non-clickable until the underlying tools
          exist. They show the recommended next build order without pretending
          the functionality is already live.
        </p>
      </div>

      <div class="lb-future-grid">

        <article class="lb-future-card">
          <div class="lb-future-top">
            <span class="lb-status-pill">NEXT BUILD</span>
            <span class="lb-future-timing">PRESEASON + IN SEASON</span>
          </div>
          <h3>Opportunity &amp; Usage Tracker</h3>
          <p class="lb-card-deck">
            Snaps, routes, targets, target share, carries, rushing share,
            red zone opportunities and goal line work, with week over week movement.
          </p>
          <div class="lb-future-visual" aria-hidden="true">
            <div class="lb-future-row"><span>Target share</span><b>14% → 26% ↑</b></div>
            <div class="lb-future-row"><span>Route participation</span><b>61% → 84% ↑</b></div>
            <div class="lb-future-row"><span>Red zone looks</span><b>2 → 5 ↑</b></div>
          </div>
          <div class="lb-future-note">Suggested route: /nfl/usage/</div>
        </article>


        <article class="lb-future-card">
          <div class="lb-future-top">
            <span class="lb-status-pill">BUILD NOW</span>
            <span class="lb-future-timing">DRAFT SEASON</span>
          </div>
          <h3>ADP Movers</h3>
          <p class="lb-card-deck">
            Track where the market is moving over 24 hours, 7 days and 30 days,
            then compare the move with LineupBeat's projection.
          </p>
          <div class="lb-future-visual" aria-hidden="true">
            <div class="lb-future-row"><span>Current ADP</span><b>52.1</b></div>
            <div class="lb-future-row"><span>7 day ADP</span><b>61.4</b></div>
            <div class="lb-future-row"><span>Market move</span><b>+9.3 ↑</b></div>
          </div>
          <div class="lb-future-note">Suggested route: /nfl/adp-movers/</div>
        </article>


        <article class="lb-future-card">
          <div class="lb-future-top">
            <span class="lb-status-pill">WEEK 1+</span>
            <span class="lb-future-timing">IN SEASON</span>
          </div>
          <h3>Weekly Opportunity Movers</h3>
          <p class="lb-card-deck">
            Surface the biggest role increases and decreases each week,
            including new goal line work, route leaders and committee changes.
          </p>
          <div class="lb-future-visual" aria-hidden="true">
            <div class="lb-future-row"><span>Player A</span><b>ADD</b></div>
            <div class="lb-future-row"><span>Player B</span><b>ROLE ↑</b></div>
            <div class="lb-future-row"><span>Player C</span><b>DOWNGRADE</b></div>
          </div>
          <div class="lb-future-note">Suggested route: /nfl/opportunity-movers/</div>
        </article>


        <article class="lb-future-card">
          <div class="lb-future-top">
            <span class="lb-status-pill">WEEK 1+</span>
            <span class="lb-future-timing">IN SEASON</span>
          </div>
          <h3>Rest of Season Projections</h3>
          <p class="lb-card-deck">
            Recalculate the season ahead using games remaining, current role,
            updated team environment and the same raw stat line first methodology.
          </p>
          <div class="lb-future-visual" aria-hidden="true">
            <div class="lb-future-row"><span>ROS rank</span><b>RB18</b></div>
            <div class="lb-future-row"><span>Projected touches</span><b>184.5</b></div>
            <div class="lb-future-row"><span>Projected PPR</span><b>161.8</b></div>
          </div>
          <div class="lb-future-note">Suggested route: /nfl/rest-of-season/</div>
        </article>


        <article class="lb-future-card">
          <div class="lb-future-top">
            <span class="lb-status-pill">LATER</span>
            <span class="lb-future-timing">IN SEASON</span>
          </div>
          <h3>Who Should I Start?</h3>
          <p class="lb-card-deck">
            Compare two to four players using projected points, opportunity,
            matchup, role stability and current Wire reporting.
          </p>
          <div class="lb-future-visual" aria-hidden="true">
            <div class="lb-future-row"><span>Player A</span><b>18.4 FP</b></div>
            <div class="lb-future-row"><span>Player B</span><b>15.9 FP</b></div>
            <div class="lb-future-row"><span>Recommendation</span><b>START A</b></div>
          </div>
          <div class="lb-future-note">Suggested route: /nfl/start-sit/</div>
        </article>


        <article class="lb-future-card">
          <div class="lb-future-top">
            <span class="lb-status-pill">LATER</span>
            <span class="lb-future-timing">ROLE CONTEXT</span>
          </div>
          <h3>Backfield Usage Map</h3>
          <p class="lb-card-deck">
            Show all 32 backfields by early down work, passing downs, goal line
            role, carry share and target share.
          </p>
          <div class="lb-future-visual" aria-hidden="true">
            <div class="lb-future-row"><span>Early downs</span><b>RB1 72%</b></div>
            <div class="lb-future-row"><span>Passing downs</span><b>RB2 58%</b></div>
            <div class="lb-future-row"><span>Backfield type</span><b>LEAD + COMP.</b></div>
          </div>
          <div class="lb-future-note">Suggested route: /nfl/backfield-usage/</div>
        </article>

      </div>
    </div>
  </section>


  <!-- PHILOSOPHY -->
  <section class="lb-philosophy">
    <div class="lb-container">
      <div class="lb-philosophy-grid">
        <div>
          <div class="lb-section-kicker">DATA, NOT NOISE</div>
          <h2>What the record says, separate from what changed today.</h2>
        </div>

        <div class="lb-philosophy-copy">
          <p>
            Every tool is built to answer a different fantasy question.
            Projections estimate the season ahead. Draft Value compares that
            view with the market. Schedule, durability, coaching and historical
            performance provide the context around it.
          </p>

          <p>
            The goal is not to force every signal into one score. It is to make
            the underlying evidence easier to inspect before you make the decision.
          </p>

          <div class="lb-method-list">
            <div class="lb-method-pill">Published data sources</div>
            <div class="lb-method-pill">Transparent stat lines</div>
            <div class="lb-method-pill">Updated by tool cadence</div>
          </div>
        </div>
      </div>
    </div>
  </section>


  <!-- WIRE CONNECTION -->
  <section class="lb-wire-strip">
    <div class="lb-container">
      <div class="lb-wire-strip-grid">
        <div>
          <div class="lb-section-kicker">THE OTHER HALF OF LINEUPBEAT</div>
          <h2>Data tells you what happened.<br>The Wire tells you what changed.</h2>

          <p>
            Pair the numbers with reporting from an average of 3 beat reporters
            per NFL team, connected directly to the players it affects.
          </p>
        </div>

        <a class="lb-button lb-button-primary" href="/nfl/wire/">
          <span>OPEN THE WIRE</span>
          <svg class="lb-arrow" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 12h13M13 6l6 6-6 6"/>
          </svg>
        </a>
      </div>
    </div>
  </section>

</main>"""


def _preview_rows():
    """The four preview blocks, from real data.

    The supplied markup carries example rows and warns not to publish them
    as factual player data. Rather than dropping them, they read the same
    boards the tools below them link to -- a preview of the projections
    that disagrees with the projections would be worse than no preview.
    """
    import json, sqlite3

    # Top projection per position.
    best = {}
    for name, pr in PROJECTIONS.items():
        pos = (pr.get("pos") or "").upper()
        ppr = pr.get("ppr")
        if pos not in ("QB", "RB", "WR", "TE") or not ppr:
            continue
        if pos not in best or ppr > best[pos][1]:
            best[pos] = (pr.get("name") or pr.get("player")
                         or name.replace("-", " ").title(), ppr)
    proj = "".join(
        f'<div class="lb-preview-row"><strong>{esc(pos)} &middot; '
        f'{esc(nm)}</strong><span>{v:.1f}</span></div>'
        for pos, (nm, v) in sorted(best.items(), key=lambda x: -x[1][1])[:3])

    # Value: where our rank and the market's disagree most. Positive is a
    # player we like better than his ADP, which is the number a reader is
    # actually shopping for.
    # Read the roster here rather than threading it through: this runs
    # once per build, from the same file the player pages use, so a second
    # read cannot disagree with the first.
    gaps = []
    rp = ROOT / "rosters" / f"{SPORT}.csv"
    if rp.exists():
        for r in csv.DictReader(rp.open()):
            adp = (r.get("adp") or "").strip()
            pr = PROJECTIONS.get(slug(r.get("name") or ""))
            if not adp or not pr or not pr.get("rank"):
                continue
            try:
                adp = float(adp)
            except (TypeError, ValueError):
                continue
            gaps.append((r.get("name"), int(round(adp)), int(pr["rank"]),
                         int(round(adp)) - int(pr["rank"])))
    gaps.sort(key=lambda x: -abs(x[3]))
    value = "".join(
        f'<div class="lb-preview-row"><span>{esc(nm)}</span>'
        f'<span>{a}</span><span>{r}</span>'
        f'<span class="lb-value-{"positive" if d > 0 else "negative"}">'
        f'{"+" if d > 0 else "\u2212"}{abs(d)}</span></div>'
        for nm, a, r, d in gaps[:3])

    # Schedule: the Giants' opening four, with difficulty from the same
    # opponent win percentage the strength of schedule page uses.
    sched = ""
    try:
        sj = json.loads((SITE / "data" / "sos.json").read_text())
        row = next((r for r in sj.get("rows", []) if r.get("team") == "NYG"), None)
        if row:
            out = []
            for g in (row.get("sched") or [])[:4]:
                wp = g.get("wp") or 0
                band = "hard" if wp >= .55 else "easy" if wp <= .45 else "avg"
                out.append(
                    f'<div class="lb-schedule-row"><span>W{g.get("w")}</span>'
                    f'<span>{esc(g.get("o") or "")}</span>'
                    f'<span class="lb-difficulty {band}">{band.upper()}</span>'
                    f'</div>')
            sched = "".join(out)
    except Exception as exc:
        print(f"  schedule preview unavailable: {exc}")

    return proj, value, sched


def data_hub_page(base):
    """The supplied design, with the previews reading live data."""
    proj_rows, value_rows, sched_rows = _preview_rows()
    body = DATA_PAGE_HTML.replace("{PROJ_ROWS}", proj_rows)
    body = body.replace("{VALUE_ROWS}", value_rows)
    body = body.replace("{SCHED_ROWS}", sched_rows)

    # Dataset alongside the breadcrumbs. The citation fields are the ones
    # the AI crawlers read, and this is the page they were added for: a
    # hub that describes six boards should say what they contain and who
    # made them, or a model quoting the numbers has nothing to attribute.
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "Dataset",
         "name": "LineupBeat NFL fantasy data",
         "description": ("Season projections, ADP and draft value, "
                         "durability, strength of schedule, offensive "
                         "coaching and offensive line and running back "
                         "performance for the NFL."),
         "url": f"{base}/{SPORT}/data/",
         "keywords": ["fantasy football", "NFL projections", "ADP",
                      "strength of schedule", "durability"],
         "variableMeasured": ["Projected fantasy points", "ADP",
                              "Draft value", "Games missed",
                              "Opponent win percentage",
                              "Run block win rate"],
         **seo.dataset_extras(temporal="2026"),
        },
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "LineupBeat",
             "item": base},
            {"@type": "ListItem", "position": 2, "name": "Fantasy data",
             "item": f"{base}/{SPORT}/data/"}]}]}

    return _render(PAGE.format(
        fonts=PAGE_FONTS,
        title="NFL Fantasy Data: Projections, ADP and Schedule",
        description=("Projections, market value, strength of schedule, "
                     "durability and team context for every drafted NFL "
                     "player. Free, and updated daily."),
        canonical=f"{base}/{SPORT}/data/",
        og_type="website",
        og_image=f'<meta property="og:image" content="{base}/og.png">',
        structured=(f'<script type="application/ld+json">{json.dumps(ld)}</script>'
                    f'<style>{DATA_PAGE_CSS}</style>'),
        body=body), "#C6F24E", "#C6F24E", section="data")



def durability_page(conn, base):
    """The whole draft board, with what each player has actually played.

    Two curated lists were less useful than the board itself. A reader is
    about to make a pick; he wants the durability of the man in front of him,
    not a table of the ten worst. So: everybody with an ADP, in draft order,
    with the record beside the price.
    """
    import subprocess
    import json as _json
    f = Path("/tmp/dur.json")
    if f.exists():
        f.unlink()          # never build from a previous run's file
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "durability.py"),
         "--max-adp", "300", "--top", "40", "--json", str(f)],
        capture_output=True, text=True, timeout=300)
    if not f.exists():
        # Say WHY. This was swallowing the error and printing "no data",
        # which told nobody anything: in CI the page skipped for a week and
        # the log gave no clue whether it was a missing table, an empty ADP
        # column or a crash.
        why = (r.stderr or r.stdout or "").strip().splitlines()
        print(f"  durability skipped: {why[-1] if why else 'no output'}")
        return None
    d = _json.loads(f.read_text())
    board = d.get("board") or []
    if not board:
        return None

    # When the ADP was drawn, and from how many drafts. A number without a
    # date is a number a reader has to trust; with one he can judge it.
    adp_note = ""
    mp = ROOT / "rosters" / "adp_meta.json"
    if mp.exists():
        try:
            mm = json.loads(mp.read_text())
            if mm.get("end"):
                def short(s):
                    y, m_, d_ = s.split("-")
                    return f"{int(m_)}/{int(d_)}"
                # Format matters: a reader scanning a table wants 7/31 - 8/7,
                # not "31 July to 7 August", and wants to know the league
                # shape because an ADP from a ten-team league is a different
                # number.
                shape = ""
                if mm.get("teams"):
                    shape = f" &middot; {mm['teams']} teams, 15 rounds"
                adp_note = (
                    f'<p class="adpwhen">ADP from '
                    f'<b>{mm.get("drafts", 0):,}</b> drafts, '
                    f'{short(mm["start"])} &ndash; {short(mm["end"])}'
                    f'{shape} &middot; updated daily</p>')
        except Exception:
            pass

    # Every name links to that player's page: 200 links into the deep pages
    # that need them, and a reader who wants the reporting behind a number is
    # one click away.
    have_page = set()
    d_ = SITE / SPORT
    if d_.exists():
        for x in d_.iterdir():
            if x.is_dir() and (x / "index.html").exists():
                have_page.add(x.name)

    def plink(display):
        s = slug(display)
        return (f'<a href="/{SPORT}/{s}/">{esc(display)}</a>'
                if s in have_page else esc(display))

    def name_of(k):
        return " ".join(w.capitalize() for w in k.split())

    def bar(missed):
        kept = max(1, min(17, round(17 - missed)))
        gone = 17 - kept
        return ('<span class="bar">'
                f'<i class="on" style="flex:{kept}"></i>'
                + (f'<i class="off" style="flex:{gone}"></i>' if gone else "")
                + '</span>')

    def dbar(missed):
        """One bar, filled by the share of a season he typically gives.

        Per-season blocks were tried and were noise: seven small shapes a
        row, and a reader scanning a hundred players cannot compare them.
        One bar per man compares down the column at a glance, which is the
        only comparison that matters here.
        """
        pct = max(0, min(100, round((17 - missed) / 17 * 100)))
        cls = "hi" if missed >= 3 else "mid" if missed >= 1 else "lo"
        return (f'<span class="dbar" title="{17 - missed:.1f} of 17">'
                f'<i class="{cls}" style="width:{pct}%"></i></span>')

    rows = []
    for r in board:
        # A player with no NFL seasons behind him gets a row that says so.
        # Hiding him would leave a reader wondering where he went; the honest
        # answer is that there is nothing to report yet.
        if r["missed_avg"] is None:
            rows.append(
                f'<tr class="r-none">'
                f'<td class="adp dim">{r["adp"]:.1f}</td>'
                f'<td class="nm">{plink(name_of(r["name"]))}</td>'
                f'<td class="dim">{esc(r["pos"])}</td>'
                # Sits under Missed/yr, where a reader is already looking
                # for the number. Spanning from ADP left it floating in the
                # middle of nowhere.
                f'<td class="norec" colspan="5">No injury history</td>'
                f'</tr>')
            continue
        risk = ("high" if r["missed_avg"] >= 3 else
                "some" if r["missed_avg"] >= 1 else "low")
        # Suspensions get a column rather than a note trailing the season
        # list. A week missed to a suspension is a week missed, and somebody
        # drafting wants it in the same shape as everything else -- not as
        # an aside he has to read to notice.
        named = [w for w in (r.get("why") or []) if w]
        susp = r.get("noninj", 0) if named else 0
        g = "&ndash;".join(str(x) for x in r["seasons"])
        rows.append(
            f'<tr class="r-{risk}">'
            f'<td class="adp dim">{r["adp"]:.1f}</td>'
            f'<td class="nm">{plink(name_of(r["name"]))}</td>'
            f'<td class="dim">{esc(r["pos"])}</td>'
            f'<td class="n gpy">{r["missed_avg"]:.1f}</td>'
            f'<td class="bw">{dbar(r["missed_avg"])}</td>'
            f'<td class="n">{r.get("ir", 0) or ""}</td>'
            f'<td class="n">{r.get("inactive", 0) or ""}</td>'
            f'<td class="n sus">{susp or ""}</td>'
            f'<td class="w">{g}</td></tr>')

    rated = [r for r in board if r["missed_avg"] is not None]
    clean = sum(1 for r in rated if r["missed_avg"] < 1)
    top36 = [r for r in rated if r["adp"] <= 36]
    top_clean = sum(1 for r in top36 if r["missed_avg"] < 1)
    import statistics as _st
    med = _st.median(r["missed_avg"] for r in rated) if rated else 0

    body = (
        '  <nav class="crumbs" aria-label="Breadcrumb">'
        '<a href="/">LineupBeat</a><span>/</span>'
        f'<a href="/{SPORT}/data/">Fantasy data</a><span>/</span>'
        '<b>Durability</b></nav>\n'
        '  <h1 class="dh1">2026 NFL Player Durability &amp; Injury History</h1>\n'
        '  <p class="dhook">Who actually plays?</p>\n'
        '  <p class="dlede">We looked at every injury report and roster '
        'transaction since 2018 to work out how durable each player has '
        'actually been. Here is the latest ADP with durability and '
        'availability alongside it, to help you make the calls that matter '
        'on draft day. Injured reserve, healthy scratches and suspensions '
        'are counted separately, because they are different facts about a '
        'player.</p>\n'

        f'  {adp_note}\n'
        '  <section class="dmethod">\n'
        '    <h2>How this is counted</h2>\n'
        '    <div class="dmgrid">\n'
        '      <div><b>Every report since 2018</b>'
        '<p>Injury reports and roster transactions, as the league published '
        'them. A missing box score row says a player did not play. It does '
        'not say why, and the difference is the whole point.</p></div>\n'
        '      <div><b>Covid does not count</b>'
        '<p>Weeks on the covid list and the 2020 opt-out are given back. A '
        'positive test is not a fact about a player, and it should not '
        'follow anybody through a career.</p></div>\n'
        '      <div><b>Suspensions are named</b>'
        '<p>Kept out of the injury count and shown in a column of their own. '
        'A season spent on a practice squad is excluded rather than counted '
        'as seventeen missed games.</p></div>\n'
        '      <div><b>Nothing is projected</b>'
        '<p>Every number here is a transaction that was filed. No model, no '
        'estimate, no opinion about who will hold up.</p></div>\n'
        '    </div>\n'
        '  </section>\n'
        '  <h2 class="dsub">What each column means</h2>\n'
        '  <dl class="dkey">\n'
        '    <div><dt>Missed/yr</dt><dd>Games below seventeen, averaged over '
        'the seasons he was on a roster</dd></div>\n'
        '    <div><dt>Availability</dt><dd>The share of a season he typically '
        'gives; a full bar is a player who is always available</dd></div>\n'
        '    <div><dt>On IR</dt><dd>Weeks on injured reserve</dd></div>\n'
        '    <div><dt>Inactive</dt><dd>On the roster, not dressed for the '
        'game</dd></div>\n'
        '    <div><dt>Susp</dt><dd>Weeks missed to a suspension, not counted '
        'as an injury</dd></div>\n'
        '    <div><dt>Games by season</dt><dd>Played each year, oldest '
        'first</dd></div>\n'
        '  </dl>\n'
        '  <h2 class="dsub" id="board">Every drafted player, in ADP order</h2>\n'
        '  <div class="dtabwrap">\n'
        '  <table class="dtab">\n'
        '    <thead><tr><th class="adp">ADP</th><th>Player</th><th>Pos</th>'
        '<th class="ar">Missed/yr</th><th>Availability</th><th class="ar">On IR</th>'
        '<th class="ar">Inactive</th><th class="ar">Susp</th><th>Games by season</th>'
        '</tr></thead>\n'
        f'    <tbody>{"".join(rows)}</tbody>\n'
        '  </table>\n'
        '  </div>\n'

        '')


    css = """
.ppage{max-width:56rem}
.dhook{font-family:var(--agate);text-transform:uppercase;letter-spacing:.09em;font-size:.8rem;color:var(--quiet);margin:.3rem 0 0}
.lb-data-hook{font-family:var(--agate);text-transform:uppercase;letter-spacing:.09em;font-size:.85rem;color:var(--quiet);margin:.4rem 0 0}
.dh1{font-family:var(--agate);text-transform:uppercase;font-size:2.6rem;
  line-height:.95;margin:0 0 .8rem;letter-spacing:-.01em}
.dlede{color:var(--quiet);font-size:1.02rem;line-height:1.6;max-width:42rem;
  margin:0 0 1.4rem}
.dstat{display:flex;gap:2.4rem;flex-wrap:wrap;margin:0 0 2.4rem;
  padding:1.1rem 0;border-top:1px solid var(--rule);
  border-bottom:1px solid var(--rule)}
.dstat .of{font-size:.9rem;color:var(--quiet);opacity:.6}
.dstat b{display:block;font-family:var(--agate);font-size:1.9rem;
  line-height:1;color:var(--signal);font-weight:600}
.dstat span{font-family:var(--agate);font-size:.6rem;letter-spacing:.09em;
  text-transform:uppercase;color:var(--quiet)}
.dtab{min-width:38rem}
}
.dtab{width:100%;border-collapse:collapse}
.dtab th{font-family:var(--agate);text-transform:uppercase;font-size:.58rem;
  letter-spacing:.1em;color:var(--quiet);text-align:left;font-weight:600;
  padding:0 .6rem .6rem;border-bottom:1px solid var(--rule)}
.dtab th.ar{text-align:right}
/* One bar a player, filled by the share of a season he gives. */
.bw{width:6rem}
.dbar{display:block;height:.42rem;border-radius:2px;
  background:rgba(255,255,255,.07);overflow:hidden}
.dbar i{display:block;height:100%;border-radius:2px}
.dbar .lo{background:var(--signal);opacity:.85}
.dbar .mid{background:var(--signal);opacity:.45}
.dbar .hi{background:var(--alert,#ff6b6b);opacity:.7}
.dtab .w{color:var(--quiet);font-size:.8rem;
  font-family:var(--data,ui-monospace),monospace}
.dtab .sus{color:var(--signal);opacity:.75}
.dtab td{padding:.62rem .6rem;border-bottom:1px solid rgba(255,255,255,.04);
  vertical-align:middle}
.dtab .nm{font-family:var(--agate);text-transform:uppercase;font-weight:600;
  font-size:.92rem;letter-spacing:.01em}
/* A table of two hundred underlined names is unreadable. The link is there,
   it just does not announce itself until the cursor is on it. */
.dtab .nm a{color:inherit;text-decoration:none;border-bottom:1px solid transparent}
.dtab .nm a:hover{color:var(--signal);border-bottom-color:currentColor}
.dtab .pos{color:var(--quiet);font-size:.62rem;letter-spacing:.08em;
  margin-left:.5rem;font-weight:400}
.dtab .why{display:block;font-family:var(--agate);font-size:.56rem;
  letter-spacing:.08em;text-transform:uppercase;color:var(--signal);
  opacity:.65;margin-top:.15rem}
.dtab .dim{color:var(--quiet)}
/* ADP is a draft slot, not a measurement. Right-aligned it sat in a row of
   numbers that all mean "how much football did he miss", and read as one
   more of them. */
.dtab .adp{text-align:left;font-family:var(--data,ui-monospace),monospace;
  font-size:.78rem;width:3.6rem}
.dtab .n{text-align:right;font-family:var(--data,ui-monospace),monospace;
  font-size:.78rem}
/* Not .big: that is the app featured card, and it carries a
   min-height that made every row 233px tall. */
.dtab .gpy{font-size:1.02rem;color:var(--ink)}
.dtab tr:hover td{background:rgba(255,255,255,.03)}
.dtab .norec{color:var(--quiet);opacity:.55;font-size:.78rem;
  font-family:var(--agate);letter-spacing:.05em;text-transform:uppercase;
  text-align:right;padding-right:1.2rem}
.r-high .gpy{color:var(--alert,#ff6b6b)}
.r-low .gpy{color:var(--signal)}
.dsub{font-family:var(--agate);text-transform:uppercase;font-size:.68rem;
  letter-spacing:.1em;color:var(--quiet);font-weight:600;margin:0 0 .8rem}
.dkey{display:grid;grid-template-columns:repeat(3,1fr);gap:.9rem 1.6rem;
  margin:0 0 1.8rem;padding:1.2rem 0;
  border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.dkey dt{font-family:var(--agate);font-size:.62rem;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink);font-weight:600}
.dkey dd{margin:.2rem 0 0;color:var(--quiet);font-size:.78rem;line-height:1.5}
@media(max-width:820px){.dkey{grid-template-columns:repeat(2,1fr)}}
@media(max-width:520px){.dkey{grid-template-columns:1fr}}
.adpwhen{font-family:var(--agate);font-size:.66rem;letter-spacing:.07em;
  text-transform:uppercase;color:var(--quiet);margin:0 0 1.8rem}
.adpwhen b{color:var(--signal);font-weight:600}
.dmethod{margin:0 0 2rem}
.dmethod h2{margin:0 0 1.1rem}
.dmgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:.8rem}
/* Flat panels with a lime rule, not gradients.
   The gradient version borrowed blue, amber and violet from the team card
   palette, where a colour means a team. Here it meant nothing, and it pulled
   the eye toward a methodology note when the table is the thing worth
   looking at. */
.dmgrid div{background:var(--panel);border:1px solid var(--rule);
  border-top:2px solid var(--signal);border-radius:0 0 8px 8px;
  padding:1rem .95rem 1.1rem}
.dmgrid b{display:block;font-family:var(--agate);font-size:.66rem;
  letter-spacing:.08em;text-transform:uppercase;color:var(--signal);
  margin-bottom:.45rem}
.dmgrid p{margin:0;color:var(--quiet);font-size:.8rem;line-height:1.55}
@media(max-width:900px){.dmgrid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:540px){.dmgrid{grid-template-columns:1fr}}
/* Nine columns of real data will not fit a phone at any font size, so the
   table scrolls sideways inside its own box rather than off the page. The
   shadow on the right edge is the only thing telling a reader there is
   more; without it people simply do not know to swipe. */
@media (max-width:760px){
  .dtabwrap{overflow-x:auto; -webkit-overflow-scrolling:touch;
    background:linear-gradient(90deg, var(--paper) 30%, transparent),
      linear-gradient(90deg, transparent, var(--paper) 70%) 100% 0,
      radial-gradient(farthest-side at 0 50%, rgba(0,0,0,.5), transparent),
      radial-gradient(farthest-side at 100% 50%, rgba(0,0,0,.5), transparent)
      100% 0;
    background-repeat:no-repeat; background-size:36px 100%,36px 100%,
      14px 100%,14px 100%; background-attachment:local,local,scroll,scroll}
  .dtab{min-width:38rem}
}
.dtab{width:100%;border-collapse:collapse}
.dtab th{font-family:var(--agate);text-transform:uppercase;font-size:.58rem;
  letter-spacing:.1em;color:var(--quiet);text-align:left;font-weight:600;
  padding:0 .6rem .6rem;border-bottom:1px solid var(--rule)}
.dtab th.ar{text-align:right}
/* One bar a player, filled by the share of a season he gives. */
.bw{width:6rem}
.dbar{display:block;height:.42rem;border-radius:2px;
  background:rgba(255,255,255,.07);overflow:hidden}
.dbar i{display:block;height:100%;border-radius:2px}
.dbar .lo{background:var(--signal);opacity:.85}
.dbar .mid{background:var(--signal);opacity:.45}
.dbar .hi{background:var(--alert,#ff6b6b);opacity:.7}
.dtab .w{color:var(--quiet);font-size:.8rem;
  font-family:var(--data,ui-monospace),monospace}
.dtab .sus{color:var(--signal);opacity:.75}
.dtab td{padding:.62rem .6rem;border-bottom:1px solid rgba(255,255,255,.04);
  vertical-align:middle}
.dtab .nm{font-family:var(--agate);text-transform:uppercase;font-weight:600;
  font-size:.92rem;letter-spacing:.01em}
/* A table of two hundred underlined names is unreadable. The link is there,
   it just does not announce itself until the cursor is on it. */
.dtab .nm a{color:inherit;text-decoration:none;border-bottom:1px solid transparent}
.dtab .nm a:hover{color:var(--signal);border-bottom-color:currentColor}
.dtab .pos{color:var(--quiet);font-size:.62rem;letter-spacing:.08em;
  margin-left:.5rem;font-weight:400}
.dtab .why{display:block;font-family:var(--agate);font-size:.56rem;
  letter-spacing:.08em;text-transform:uppercase;color:var(--signal);
  opacity:.65;margin-top:.15rem}
.dtab .dim{color:var(--quiet)}
/* ADP is a draft slot, not a measurement. Right-aligned it sat in a row of
   numbers that all mean "how much football did he miss", and read as one
   more of them. */
.dtab .adp{text-align:left;font-family:var(--data,ui-monospace),monospace;
  font-size:.78rem;width:3.6rem}
.dtab .n{text-align:right;font-family:var(--data,ui-monospace),monospace;
  font-size:.78rem}
/* Not .big: that is the app featured card, and it carries a
   min-height that made every row 233px tall. */
.dtab .gpy{font-size:1.02rem;color:var(--ink)}
.dtab tr:hover td{background:rgba(255,255,255,.03)}
.dtab .norec{color:var(--quiet);opacity:.55;font-size:.78rem;
  font-family:var(--agate);letter-spacing:.05em;text-transform:uppercase;
  text-align:right;padding-right:1.2rem}
.r-high .gpy{color:var(--alert,#ff6b6b)}
.r-low .gpy{color:var(--signal)}
.dsub{font-family:var(--agate);text-transform:uppercase;font-size:.68rem;
  letter-spacing:.1em;color:var(--quiet);font-weight:600;margin:0 0 .8rem}
.dkey{display:grid;grid-template-columns:repeat(3,1fr);gap:.9rem 1.6rem;
  margin:0 0 1.8rem;padding:1.2rem 0;
  border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.dkey dt{font-family:var(--agate);font-size:.62rem;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink);font-weight:600}
.dkey dd{margin:.2rem 0 0;color:var(--quiet);font-size:.78rem;line-height:1.5}
@media(max-width:820px){.dkey{grid-template-columns:repeat(2,1fr)}}
@media(max-width:520px){.dkey{grid-template-columns:1fr}}
.adpwhen{font-family:var(--agate);font-size:.66rem;letter-spacing:.07em;
  text-transform:uppercase;color:var(--quiet);margin:0 0 1.8rem}
.adpwhen b{color:var(--signal);font-weight:600}
.dmethod{margin:0 0 2.6rem}
.dmethod h2{margin:0 0 1.1rem}
.dmgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:.8rem}
/* Same construction as a player card: a colour gradient with a second
   colour thrown in from one corner, and a dark wash over the lower half so
   the type stays readable. Four cards, four colours, so the page reads as
   part of the site rather than a spreadsheet somebody left out. */
.dmgrid div{position:relative;overflow:hidden;isolation:isolate;
  border-radius:12px;padding:1.1rem 1rem 1.2rem;
  border:1px solid rgba(255,255,255,.09);min-height:11rem}
.dmgrid div::before{content:"";position:absolute;inset:0;z-index:-2;
  opacity:.9}
.dmgrid div::after{content:"";position:absolute;inset:0;z-index:-1;
  background:linear-gradient(180deg,transparent 30%,rgba(8,10,7,.82) 100%)}
.dmgrid .c1::before{background:
  radial-gradient(88% 70% at 92% 6%, #C6F24E 0%, transparent 58%),
  linear-gradient(155deg,#1B4D3E 0%,#123028 42%,#0B0D0F 92%)}
.dmgrid .c2::before{background:
  radial-gradient(88% 70% at 92% 6%, #7FB2FF 0%, transparent 58%),
  linear-gradient(155deg,#123A5E 0%,#0E2740 42%,#0B0D0F 92%)}
.dmgrid .c3::before{background:
  radial-gradient(88% 70% at 92% 6%, #FFB86B 0%, transparent 58%),
  linear-gradient(155deg,#5A3312 0%,#3A210C 42%,#0B0D0F 92%)}
.dmgrid .c4::before{background:
  radial-gradient(88% 70% at 92% 6%, #D9A7FF 0%, transparent 58%),
  linear-gradient(155deg,#3E2357 0%,#2A1739 42%,#0B0D0F 92%)}


@media(max-width:900px){.dmgrid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:540px){.dmgrid{grid-template-columns:1fr}
  .dmgrid div{min-height:0}}
.dtab tr.hide{display:none}
@media(max-width:860px){.dmgrid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:520px){.dmgrid{grid-template-columns:1fr;gap:1rem}}
@media(max-width:700px){
  .dh1{font-size:1.9rem}
  .dtab .nm{font-size:.82rem}
  .bw{width:3.5rem}
  .dstat{gap:1.4rem}
  .dstat b{font-size:1.5rem}
}
"""
    # Four schemas, each doing a different job.
    #
    # Dataset says this page IS the data rather than writing about it, which
    # is what it competes on. The version this replaces named nothing
    # measured, no span and no publisher -- everything that makes a dataset
    # findable.
    #
    # FAQPage is the one worth having. The methodology notes are already
    # question-shaped, and a page that answers "do covid absences count" can
    # take the rich result for it. Every answer below appears on the page,
    # which is the rule Google actually enforces.
    #
    # BreadcrumbList makes the listing read as a path rather than a bare URL,
    # which lifts click-through on a deep page.
    from datetime import date as _date
    ld = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Dataset",
            "publisher": {"@id": "https://lineupbeat.com/#org"},
            "isAccessibleForFree": True,
            "creditText": "LineupBeat",
            "license": "https://creativecommons.org/licenses/by/4.0/",
            "spatialCoverage": "United States",
            "temporalCoverage": "2018/2026",
            "inLanguage": "en-US",
             "@id": f"{base}/{SPORT}/durability/#dataset",
             "name": "NFL player durability and availability by draft "
                     "position, 2026",
             "description":
                 "Games missed per season for every drafted NFL player, "
                 "compiled from official injury reports and weekly roster "
                 "transactions published since 2018, set against average "
                 "draft position. Injured reserve, healthy scratches and "
                 "suspensions are counted separately. Covid list weeks and "
                 "the 2020 opt-out are excluded.",
             "url": f"{base}/{SPORT}/durability/",
             "keywords": ["NFL", "injury history", "durability",
                          "games missed", "average draft position",
                          "fantasy football", "injured reserve"],
             "temporalCoverage": "2018/2026",
             "dateModified": _date.today().isoformat(),
             "isAccessibleForFree": True,
             "variableMeasured": [
                 {"@type": "PropertyValue", "name": "Average draft position",
                  "description": "Where the player is being drafted, from a "
                                 "rolling week of real drafts."},
                 {"@type": "PropertyValue", "name": "Games missed per year",
                  "description": "Games below seventeen, averaged across the "
                                 "seasons he was on a roster."},
                 {"@type": "PropertyValue", "name": "Weeks on injured reserve"},
                 {"@type": "PropertyValue", "name": "Weeks inactive",
                  "description": "On the roster but not dressed."},
                 {"@type": "PropertyValue", "name": "Weeks suspended"}],
             "creator": {"@id": f"{base}/#org"},
             "publisher": {"@id": f"{base}/#org"}},
            {"@type": "Organization", "@id": f"{base}/#org",
             "name": "LineupBeat", "url": base + "/",
             "logo": f"{base}/og.png"},
            {"@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "LineupBeat",
                 "item": base + "/"},
                {"@type": "ListItem", "position": 2, "name": "Fantasy data",
                 "item": f"{base}/{SPORT}/data/"},
                {"@type": "ListItem", "position": 3,
                 "name": "Durability and availability",
                 "item": f"{base}/{SPORT}/durability/"}]},
            {"@type": "FAQPage", "mainEntity": [
                {"@type": "Question",
                 "name": "How is NFL player durability measured here?",
                 "acceptedAnswer": {"@type": "Answer", "text":
                     "From injury reports and roster transactions the league "
                     "has published since 2018. A missing box score row says "
                     "a player did not play; the weekly roster says why, "
                     "recording whether he was active, on injured reserve, "
                     "inactive, or not on the team at all."}},
                {"@type": "Question",
                 "name": "Do covid absences count against a player?",
                 "acceptedAnswer": {"@type": "Answer", "text":
                     "No. Weeks on the covid list and the 2020 opt-out are "
                     "given back. A positive test is not a fact about a "
                     "player and should not follow anybody through a career."}},
                {"@type": "Question",
                 "name": "Are suspensions counted as injuries?",
                 "acceptedAnswer": {"@type": "Answer", "text":
                     "No. Suspensions are shown in a column of their own and "
                     "kept out of the injury count. A season spent on a "
                     "practice squad is excluded rather than counted as "
                     "seventeen missed games."}},
                {"@type": "Question",
                 "name": "Are these numbers projections?",
                 "acceptedAnswer": {"@type": "Answer", "text":
                     "No. Every number is a transaction that was filed. There "
                     "is no model, no estimate and no opinion about who will "
                     "hold up."}},
                {"@type": "Question",
                 "name": "How current is the average draft position?",
                 "acceptedAnswer": {"@type": "Answer", "text":
                     "It is drawn from a rolling week of real drafts and "
                     "refreshed daily, so it reflects where players are going "
                     "now rather than earlier in the offseason."}}]}]}
    return _render(PAGE.format(
        fonts=PAGE_FONTS,
        # "Who Actually Plays" is a headline, not a query. Nobody types it.
        # People search for injury history, games missed, durability, and
        # they search it against a draft board -- so the title leads with
        # those words and the year, and the brand goes last where it will
        # survive truncation.
        title="NFL Injury History and Durability by ADP, 2026 | LineupBeat",
        # 155 characters, leads with the answer, names the span, and gives a
        # reason to click that a competitor cannot copy.
        description=("How many games every drafted NFL player has actually "
                     "missed, from eight seasons of injury reports and roster "
                     "moves. Set against live ADP. Updated daily."),
        canonical=f"{base}/nfl/durability/",
        og_type="article",
        og_image=f'<meta property="og:image" content="{base}/og.png">',
        structured=(f'<script type="application/ld+json">{json.dumps(ld)}</script>'
                    f'<style>{css}{seo.CRUMB_CSS}</style>'
                    f'<script>{FIND_JS}</script>'),
        body=body), "#C6F24E", "#C6F24E", section="data")



ABOUT_CSS = """
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

.abwrap{max-width:1080px; margin:0 auto; padding:0 1rem 4rem}
.abhead h1{font-size:1.7rem; margin:1.6rem 0 0; letter-spacing:-.01em;
  font-family:var(--lb-about-ink)}
.ablede{font-size:1rem; line-height:1.65; color:var(--ink); max-width:70ch;
  margin:.8rem 0 0}
.abwrap h2{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.07em; font-size:.8rem; color:var(--quiet);
  margin:2.2rem 0 .6rem}
.abwrap p{font-size:.92rem; line-height:1.7; color:var(--quiet);
  max-width:70ch; margin:0 0 .9rem}
.abwrap p b{color:var(--ink)}
.abwrap p a{color:var(--quiet); text-decoration:underline}
.abwrap p a:hover{color:var(--signal)}
.abgrid{display:grid; grid-template-columns:repeat(2, 1fr); gap:.7rem;
  margin:.4rem 0 0}
@media (max-width:760px){ .abgrid{grid-template-columns:1fr} }
.abcard{background:var(--card); border:1px solid var(--rule);
  border-radius:8px; padding:.85rem 1rem}
.abcard h3{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.05em; font-size:.7rem; color:var(--signal); margin:0}
.abcard p{margin:.35rem 0 0; font-size:.84rem; line-height:1.55}
.abwho{display:flex; gap:1rem; align-items:baseline; flex-wrap:wrap;
  background:var(--card); border:1px solid var(--rule); border-radius:8px;
  padding:.9rem 1rem; margin:.4rem 0 0}
.abwho dt{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.06em; font-size:.62rem; color:var(--quiet)}
.abwho dd{margin:0 1.4rem 0 .3rem; font-size:.86rem; color:var(--ink)}
"""


def about_page(base, built):
    """Who is behind this, and how to check it.

    The single page that most affects whether a search engine treats a site
    as trustworthy, and the one thing LineupBeat did not have. Everything
    on it is either verifiable on the site itself or a plain statement of
    process -- no reviewer who does not review, no badge for a check nobody
    runs.
    """
    # The supplied design, prefixed lb-about- so nothing here can
    # reach the rest of the site. The stylesheet rides with it
    # rather than joining the global sheet, for the same reason.
    body = """<style>:root{
  --green:#c6f53c;
  --bg:#050708;
  --panel:#0e1213;
  --lb-about-ink:#f2f2ed;
  --muted:#a6aca7;
  --line:rgba(255,255,255,.12);
  --line2:rgba(255,255,255,.20);
  --max:1240px;
}
.lb-about-page,
.lb-about-page *{box-sizing:border-box}
.lb-about-page{color:var(--lb-about-ink);font-family:"Barlow Condensed",Arial,sans-serif}
.lb-about-page a{color:inherit}
.lb-about-page{
  position:relative;overflow:hidden;min-height:100vh;
  background:
    radial-gradient(circle at 24% 8%,rgba(45,57,59,.25),transparent 32%),
    radial-gradient(circle at 82% 26%,rgba(198,245,60,.035),transparent 30%),
    var(--bg);
}
.lb-about-page:before{
  content:"";position:absolute;inset:0;pointer-events:none;opacity:.58;
  background-image:
    linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px);
  background-size:72px 72px;
  mask-image:linear-gradient(to bottom,#000 0,rgba(0,0,0,.15) 48%,transparent 100%);
}
.lb-about-wrap{position:relative;z-index:2;width:min(var(--max),calc(100% - 48px));margin:0 auto}
.lb-about-kicker,.lb-about-card-kicker,.lb-about-step-number{color:var(--green);font-weight:700;letter-spacing:.11em;text-transform:uppercase}
.lb-about-kicker{font-size:14px}
.lb-about-hero{padding:100px 0 88px;border-bottom:1px solid var(--line)}
.lb-about-hero-grid{display:grid;grid-template-columns:minmax(0,1.08fr) minmax(360px,.92fr);gap:86px;align-items:start}
.lb-about-hero h1{max-width:none;margin:20px 0 27px;text-transform:none;font-family:"Source Serif 4",Georgia,serif;font-size:clamp(42px,4.6vw,78px);line-height:.95;font-weight:400;letter-spacing:-.036em}
.lb-about-hero h1 span{color:var(--green)}
.lb-about-lead{max-width:700px;margin:0;color:#b8bdb9;font-family:Georgia,serif;font-size:20px;line-height:1.7}
.lb-about-actions{display:flex;flex-wrap:wrap;gap:14px;margin-top:35px}
.lb-about-btn{min-height:58px;padding:0 27px;display:inline-flex;align-items:center;justify-content:center;gap:18px;border-radius:7px;border:1px solid var(--line2);text-decoration:none;font-size:17px;font-weight:700;letter-spacing:.045em;text-transform:uppercase;transition:.18s}
.lb-about-btn:hover{transform:translateY(-2px)}
a.lb-about-btn-primary, .lb-about-btn-primary{color:#070907 !important;
  background:var(--green);border-color:var(--green)}
.lb-about-btn-primary *{color:inherit}
.lb-about-btn-primary:hover{background:#d4ff50}
.lb-about-btn-secondary:hover{border-color:var(--green)}
.lb-about-arrow{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.8}

/* Measured: the card sat 82px below the headline, because the kicker
   above it in the left column has no counterpart on this side. */
.lb-about-wire{margin-top:-82px;
  position:relative;padding:25px;border:1px solid var(--line2);border-radius:18px;
  background:linear-gradient(145deg,rgba(255,255,255,.025),transparent 45%),rgba(14,18,19,.94);
  box-shadow:0 35px 90px rgba(0,0,0,.34)
}
.lb-about-wire-head{min-height:42px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line)}
.lb-about-live{display:flex;align-items:center;gap:10px;color:var(--green);font-size:15px;font-weight:700;letter-spacing:.065em;text-transform:uppercase}
.lb-about-dot{width:9px;height:9px;border-radius:50%;background:var(--green);box-shadow:0 0 13px rgba(198,245,60,.34)}
.lb-about-wire-head>span{color:#777f7a;font-size:11px;letter-spacing:.06em;text-transform:uppercase}
.lb-about-wire-body{position:relative;padding:8px 0}
.lb-about-wire-body:before{content:"";position:absolute;left:18px;top:37px;bottom:37px;width:1px;background:rgba(255,255,255,.11)}
.lb-about-wire-item{position:relative;display:grid;grid-template-columns:48px 1fr;gap:15px;min-height:116px;padding:19px 0;border-bottom:1px solid rgba(255,255,255,.07)}
.lb-about-wire-item:last-child{border-bottom:0}
.lb-about-marker{position:relative;z-index:2;width:38px;height:38px;display:grid;place-items:center;border-radius:50%;border:1px solid rgba(255,255,255,.16);background:#151a1b;color:var(--green);font-size:12px;font-weight:700}
.lb-about-marker:after{content:"";position:absolute;left:14px;bottom:-36px;width:7px;height:7px;border-radius:50%;background:var(--green)}
.lb-about-wire-item:last-child .lb-about-marker:after{display:none}
.lb-about-wire-title{display:flex;justify-content:space-between;gap:20px;align-items:baseline}
.lb-about-wire-title strong{font-size:19px}.lb-about-wire-title time{color:var(--green);font-size:12px}
.lb-about-wire-item p{margin:7px 0 12px;color:#e0e3df;font-family:Georgia,serif;font-size:15px;line-height:1.42}
.lb-about-source{color:#838b86;font-size:12px}

.lb-about-proof-bar{border-bottom:1px solid var(--line);background:rgba(8,11,12,.72)}
.lb-about-proof-grid{display:grid;grid-template-columns:repeat(3,1fr)}
.lb-about-proof{min-height:125px;padding:26px 34px;display:flex;align-items:center;gap:17px}
.lb-about-proof+.lb-about-proof{border-left:1px solid var(--line)}
.lb-about-proof svg{width:42px;height:42px;flex:0 0 auto;fill:none;stroke:var(--green);stroke-width:1.6}
.lb-about-proof strong{display:block;font-size:34px;line-height:.95}
.lb-about-proof span{display:block;margin-top:8px;color:var(--muted);font-size:11px;letter-spacing:.08em;text-transform:uppercase}

.lb-about-section{padding:82px 0;border-bottom:1px solid var(--line)}
.lb-about-section-head{max-width:820px;margin-bottom:40px}
.lb-about-section h2,.lb-about-split h2,.lb-about-final h2{margin:12px 0 0;font-family:var(--lb-about-ink);font-weight:400;line-height:.98;letter-spacing:-.027em}
.lb-about-section-head h2{font-size:clamp(40px,4.7vw,62px)}
.lb-about-section-head p{max-width:720px;margin:18px 0 0;color:var(--muted);font-family:Georgia,serif;font-size:17px;line-height:1.65}

.lb-about-do-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
.lb-about-do-card{min-height:330px;padding:29px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(145deg,rgba(255,255,255,.022),transparent 42%),var(--panel)}
.lb-about-do-card svg{width:46px;height:46px;fill:none;stroke:var(--green);stroke-width:1.55}
.lb-about-card-kicker{margin-top:28px;font-size:11px}
.lb-about-do-card h3{margin:10px 0 13px;font-family:var(--lb-about-ink);font-size:29px;font-weight:400;line-height:1.05}
.lb-about-do-card p{margin:0;color:#a7ada8;font-family:Georgia,serif;font-size:15px;line-height:1.62}

.lb-about-split{display:grid;grid-template-columns:.78fr 1.22fr;gap:85px;align-items:start}
.lb-about-split h2{font-size:clamp(40px,4.5vw,60px)}
.lb-about-split-copy{margin-top:20px;color:var(--muted);font-family:Georgia,serif;font-size:17px;line-height:1.68}
.lb-about-process{border-top:1px solid var(--line2)}
.lb-about-step{display:grid;grid-template-columns:70px 1fr;gap:20px;padding:28px 0;border-bottom:1px solid var(--line)}
.lb-about-step-number{font-size:13px}
.lb-about-step h3{margin:0 0 8px;font-size:23px;line-height:1}
.lb-about-step p{margin:0;color:#9da49f;font-family:Georgia,serif;font-size:15px;line-height:1.55}

.lb-about-principles{background:linear-gradient(180deg,rgba(198,245,60,.016),transparent 30%),#070a0b}
.lb-about-principle-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}
.lb-about-principle{min-height:245px;padding:28px 30px;border:1px solid var(--line);border-radius:13px;background:rgba(14,18,19,.72)}
.lb-about-principle strong{display:inline-block;color:var(--green);font-size:12px;letter-spacing:.09em;text-transform:uppercase}
.lb-about-principle h3{margin:15px 0 11px;font-family:var(--lb-about-ink);font-size:30px;font-weight:400}
.lb-about-principle p{margin:0;color:#a6aca7;font-family:Georgia,serif;font-size:15px;line-height:1.6}

.lb-about-belief{padding:92px 0;border-bottom:1px solid var(--line);background:radial-gradient(circle at 50% 50%,rgba(198,245,60,.035),transparent 34%)}
.lb-about-belief blockquote{max-width:1000px;margin:0 auto;text-align:center;font-family:var(--lb-about-ink);font-size:clamp(43px,5.1vw,72px);font-weight:400;line-height:1.03;letter-spacing:-.028em}
.lb-about-belief span{color:var(--green)}

.lb-about-source-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.lb-about-source-panel{padding:31px;border:1px solid var(--line);border-radius:14px;background:var(--panel)}
.lb-about-source-panel h3{margin:0 0 16px;font-family:var(--lb-about-ink);font-size:29px;font-weight:400}
.lb-about-source-panel p{margin:0;color:#a5aca7;font-family:Georgia,serif;font-size:15px;line-height:1.65}
.lb-about-source-list{margin:22px 0 0;padding:0;list-style:none}
.lb-about-source-list li{position:relative;padding:10px 0 10px 21px;border-top:1px solid rgba(255,255,255,.06);color:#929994;font-size:13px}
.lb-about-source-list li:before{content:"";position:absolute;left:0;top:16px;width:7px;height:7px;border-radius:50%;background:var(--green)}

.lb-about-final{padding:84px 0;background:radial-gradient(circle at 78% 50%,rgba(198,245,60,.065),transparent 27%),#080b0c}
.lb-about-final-grid{display:grid;grid-template-columns:1fr auto;gap:60px;align-items:center}
.lb-about-final h2{font-size:clamp(42px,4.6vw,62px)}
.lb-about-final p{max-width:680px;margin:18px 0 0;color:#aeb4af;font-family:Georgia,serif;font-size:17px;line-height:1.65}
.lb-about-final-actions{display:flex;flex-direction:column;gap:12px}

@media(max-width:1050px){
  .lb-about-hero-grid,.lb-about-split{grid-template-columns:1fr;gap:50px}
  .lb-about-wire{max-width:720px}
  .lb-about-do-grid{grid-template-columns:1fr}
  .lb-about-do-card{min-height:0}
  .lb-about-final-grid{grid-template-columns:1fr;gap:32px}
  .lb-about-final-actions{flex-direction:row;flex-wrap:wrap}
}
@media(max-width:760px){
  .lb-about-proof-grid{grid-template-columns:1fr}
  .lb-about-proof+.lb-about-proof{border-left:0;border-top:1px solid var(--line)}
  .lb-about-principle-grid,.lb-about-source-grid{grid-template-columns:1fr}
  .lb-about-section{padding:64px 0}
}
@media(max-width:620px){
  .lb-about-wrap{width:calc(100% - 30px)}
  .lb-about-hero{padding:62px 0 55px}
  .lb-about-hero h1{font-size:clamp(50px,16vw,68px)}
  .lb-about-lead{font-size:17px}
  .lb-about-actions,.lb-about-final-actions{flex-direction:column}
  .lb-about-btn{width:100%}
  .lb-about-wire{padding:18px}
  .lb-about-wire-title{flex-direction:column;gap:2px}
  .lb-about-step{grid-template-columns:45px 1fr;gap:12px}
  .lb-about-belief{padding:70px 0}
}

/* =========================================================
   MOBILE / TABLET HARDENING
   ========================================================= */

/* Prevent accidental horizontal scrolling from long text or SVGs */
.lb-about-page {
  max-width: 100%;
  overflow-x: hidden;
}

.lb-about-page img,
.lb-about-page svg {
  max-width: 100%;
}

.lb-about-page,
.lb-about-wrap,
.lb-about-hero-grid,
.lb-about-proof-grid,
.lb-about-do-grid,
.lb-about-split,
.lb-about-principle-grid,
.lb-about-source-grid,
.lb-about-final-grid {
  min-width: 0;
}

.lb-about-hero-grid > *,
.lb-about-split > *,
.lb-about-final-grid > *,
.lb-about-do-grid > *,
.lb-about-principle-grid > *,
.lb-about-source-grid > * {
  min-width: 0;
}

/* Make touch targets comfortably tappable */
.lb-about-btn {
  min-height: 52px;
  touch-action: manipulation;
  -webkit-tap-highlight-color: transparent;
}

/* Better tablet behavior */
@media (max-width: 900px) {
  /* Nothing to align to once the columns stack,
     and the offset pulls the card into the stats. */
  .lb-about-wire{margin-top:0}
  .lb-about-hero {
    padding-top: 72px;
    padding-bottom: 64px;
  }

  .lb-about-hero-grid {
    gap: 42px;
  }

  .lb-about-hero h1 {
    max-width: 720px;
  }

  .lb-about-wire {
    width: 100%;
    max-width: none;
  }

  .lb-about-proof {
    padding: 24px;
  }

  .lb-about-split {
    gap: 42px;
  }
}

/* Main mobile layout */
@media (max-width: 620px) {
  .lb-about-wrap {
    width: calc(100% - 28px);
  }

  .lb-about-hero {
    padding: 48px 0 42px;
  }

  .lb-about-hero-grid {
    gap: 34px;
  }

  .lb-about-hero h1 {
    margin-top: 16px;
    margin-bottom: 20px;
    font-size: clamp(44px, 13.5vw, 58px);
    line-height: .98;
    letter-spacing: -.03em;
  }

  .lb-about-lead {
    font-size: 17px;
    line-height: 1.6;
  }

  .lb-about-actions {
    gap: 10px;
    margin-top: 26px;
  }

  .lb-about-btn {
    width: 100%;
    min-height: 54px;
    padding: 0 18px;
    font-size: 16px;
  }

  /* Wire preview becomes compact and easy to scan */
  .lb-about-wire {
    padding: 16px;
    border-radius: 14px;
  }

  .lb-about-wire-head {
    min-height: 38px;
    gap: 10px;
  }

  .lb-about-live {
    font-size: 13px;
  }

  .lb-about-wire-head > span {
    font-size: 9px;
  }

  .lb-about-wire-item {
    grid-template-columns: 42px minmax(0, 1fr);
    gap: 12px;
    min-height: 0;
    padding: 16px 0;
  }

  .lb-about-marker {
    width: 34px;
    height: 34px;
    font-size: 11px;
  }

  .lb-about-wire-body::before {
    left: 16px;
    top: 31px;
    bottom: 31px;
  }

  .lb-about-marker::after {
    left: 13px;
    bottom: -31px;
    width: 6px;
    height: 6px;
  }

  .lb-about-wire-title {
    display: block;
  }

  .lb-about-wire-title strong {
    display: block;
    font-size: 18px;
  }

  .lb-about-wire-title time {
    display: block;
    margin-top: 2px;
    font-size: 11px;
  }

  .lb-about-wire-item p {
    margin-top: 7px;
    margin-bottom: 10px;
    font-size: 14px;
    line-height: 1.45;
  }

  .lb-about-source {
    font-size: 11px;
  }

  /* Proof bar stacks cleanly */
  .lb-about-proof-grid {
    grid-template-columns: 1fr;
  }

  .lb-about-proof {
    min-height: 92px;
    padding: 19px 18px;
  }

  .lb-about-proof + .lb-about-proof {
    border-left: 0;
    border-top: 1px solid var(--line);
  }

  .lb-about-proof svg {
    width: 36px;
    height: 36px;
  }

  .lb-about-proof strong {
    font-size: 30px;
  }

  /* Sections */
  .lb-about-section {
    padding: 52px 0;
  }

  .lb-about-section-head {
    margin-bottom: 28px;
  }

  .lb-about-section-head h2,
  .lb-about-split h2,
  .lb-about-final h2 {
    font-size: clamp(36px, 11vw, 48px);
    line-height: 1;
  }

  .lb-about-section-head p,
  .lb-about-split-copy,
  .lb-about-final p {
    font-size: 16px;
    line-height: 1.6;
  }

  /* Cards: one column, tighter but still premium */
  .lb-about-do-grid,
  .lb-about-principle-grid,
  .lb-about-source-grid {
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .lb-about-do-card,
  .lb-about-principle,
  .lb-about-source-panel {
    min-height: 0;
    padding: 22px;
    border-radius: 12px;
  }

  .lb-about-do-card h3,
  .lb-about-principle h3,
  .lb-about-source-panel h3 {
    font-size: 27px;
  }

  /* How the Wire works */
  .lb-about-split {
    grid-template-columns: 1fr;
    gap: 30px;
  }

  .lb-about-step {
    grid-template-columns: 36px minmax(0, 1fr);
    gap: 10px;
    padding: 21px 0;
  }

  .lb-about-step h3 {
    font-size: 21px;
  }

  .lb-about-step p {
    font-size: 14px;
    line-height: 1.55;
  }

  /* Big belief quote */
  .lb-about-belief {
    padding: 56px 0;
  }

  .lb-about-belief blockquote {
    font-size: clamp(38px, 12vw, 52px);
    line-height: 1.04;
  }

  /* Final CTA */
  .lb-about-final {
    padding: 54px 0;
  }

  .lb-about-final-grid {
    grid-template-columns: 1fr;
    gap: 28px;
  }

  .lb-about-final-actions {
    width: 100%;
    gap: 10px;
  }
}

/* Very small phones */
@media (max-width: 390px) {
  .lb-about-wrap {
    width: calc(100% - 22px);
  }

  .lb-about-hero h1 {
    font-size: 42px;
  }

  .lb-about-kicker {
    font-size: 12px;
  }

  .lb-about-wire {
    padding: 14px;
  }

  .lb-about-proof {
    padding-left: 15px;
    padding-right: 15px;
  }

  .lb-about-do-card,
  .lb-about-principle,
  .lb-about-source-panel {
    padding: 19px;
  }
}

/* Respect users who prefer less motion */
@media (prefers-reduced-motion: reduce) {
  .lb-about-btn {
    transition: none;
  }

  .lb-about-btn:hover {
    transform: none;
  }
}</style>
<main class="lb-about-page">

  <section class="lb-about-hero">
    <div class="lb-about-wrap">
      <div class="lb-about-hero-grid">
        <div>
          <div class="lb-about-kicker">ABOUT LINEUPBEAT</div>
          <h1>Fantasy decisions start with <span>better information.</span></h1>
          <p class="lb-about-lead">
            LineupBeat follows an average of 3 beat reporters for every NFL team,
            connects their reporting to the players it affects, and pairs it with
            fantasy data built to help you make better decisions.
          </p>

          <div class="lb-about-actions">
            <a class="lb-about-btn lb-about-btn-primary" href="/nfl/wire/">
              OPEN THE WIRE
              <svg class="lb-about-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M13 6l6 6-6 6"/></svg>
            </a>
            <a class="lb-about-btn lb-about-btn-secondary" href="/nfl/data/">Explore Fantasy Data</a>
          </div>
        </div>

        <aside class="lb-about-wire" aria-label="How the LineupBeat Wire works">
          <div class="lb-about-wire-head">
            <div class="lb-about-live"><span class="lb-about-dot"></span>LIVE ON THE WIRE</div>
            <span>ALL 32 NFL TEAMS</span>
          </div>

          <div class="lb-about-wire-body">
            <div class="lb-about-wire-item">
              <div class="lb-about-marker">NFL</div>
              <div>
                <div class="lb-about-wire-title"><strong>Player role changes</strong><time>minutes ago</time></div>
                <p>Local reporting surfaces a meaningful shift in first team work, health or opportunity.</p>
                <div class="lb-about-source">Original reporter credited</div>
              </div>
            </div>

            <div class="lb-about-wire-item">
              <div class="lb-about-marker">→</div>
              <div>
                <div class="lb-about-wire-title"><strong>Matched to the player</strong><time>then</time></div>
                <p>The report is connected directly to the fantasy relevant player so you do not have to hunt across dozens of feeds.</p>
                <div class="lb-about-source">Reporting stays separate from model opinion</div>
              </div>
            </div>

            <div class="lb-about-wire-item">
              <div class="lb-about-marker">FP</div>
              <div>
                <div class="lb-about-wire-title"><strong>Put in fantasy context</strong><time>when warranted</time></div>
                <p>The Wire and the data work together without turning every headline into an automatic projection change.</p>
                <div class="lb-about-source">Evidence first</div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  </section>

  <section class="lb-about-proof-bar">
    <div class="lb-about-wrap">
      <div class="lb-about-proof-grid">
        <div class="lb-about-proof">
          <svg viewBox="0 0 48 48"><path d="M7 34c6-13 13-20 20-20 5 0 9 2 14 6"/><circle cx="10" cy="34" r="3"/><circle cx="28" cy="14" r="3"/><circle cx="41" cy="20" r="3"/></svg>
          <div><strong>32</strong><span>NFL teams covered</span></div>
        </div>
        <div class="lb-about-proof">
          <svg viewBox="0 0 48 48"><circle cx="16" cy="15" r="7"/><circle cx="32" cy="16" r="6"/><path d="M5 39c1-9 5-14 12-14s11 5 12 14"/><path d="M26 39c1-7 4-11 10-11 3 0 6 1 8 4"/></svg>
          <div><strong>3</strong><span>Beat reporters per team, avg.</span></div>
        </div>
        <div class="lb-about-proof">
          <svg viewBox="0 0 48 48"><path d="M28 4 9 28h13l-3 16 20-26H26z"/></svg>
          <div><strong>FREE</strong><span>Fantasy tools and reporting</span></div>
        </div>
      </div>
    </div>
  </section>

  <section class="lb-about-section">
    <div class="lb-about-wrap">
      <div class="lb-about-section-head">
        <div class="lb-about-kicker">WHAT LINEUPBEAT DOES</div>
        <h2>Reporting first. Fantasy context second.</h2>
        <p>
          The goal is simple: make it easier to see the information that matters
          without blending verified reporting, projections and opinion into one thing.
        </p>
      </div>

      <div class="lb-about-do-grid">
        <article class="lb-about-do-card">
          <svg viewBox="0 0 48 48"><path d="M8 10h32v24H20l-9 8v-8H8z"/><path d="M14 17h20M14 23h16M14 29h11"/></svg>
          <div class="lb-about-card-kicker">01 · THE WIRE</div>
          <h3>Follow the people closest to the teams.</h3>
          <p>We follow local beat reporting across every NFL team, surface the fantasy relevant updates, and credit the original reporter and source.</p>
        </article>

        <article class="lb-about-do-card">
          <svg viewBox="0 0 48 48"><path d="M8 38h7V24H8zM20 38h7V15h-7zM32 38h7V8h-7z"/><path d="M6 42h36"/></svg>
          <div class="lb-about-card-kicker">02 · FANTASY DATA</div>
          <h3>Show the numbers behind the decision.</h3>
          <p>Projections, draft value, schedule, durability, coaching context and historical performance are built as separate tools so the underlying evidence remains visible.</p>
        </article>

        <article class="lb-about-do-card">
          <svg viewBox="0 0 48 48"><path d="M24 5v9M24 34v9M5 24h9M34 24h9"/><circle cx="24" cy="24" r="9"/><path d="m20 24 3 3 6-7"/></svg>
          <div class="lb-about-card-kicker">03 · THE CONNECTION</div>
          <h3>Separate what changed from what we think it means.</h3>
          <p>A report can matter without automatically changing a projection. Evidence changes the view when it is strong enough, and the distinction stays visible.</p>
        </article>
      </div>
    </div>
  </section>

  <section class="lb-about-section">
    <div class="lb-about-wrap">
      <div class="lb-about-split">
        <div>
          <div class="lb-about-kicker">HOW THE WIRE WORKS</div>
          <h2>Dozens of local feeds, one fantasy view.</h2>
          <p class="lb-about-split-copy">
            NFL news rarely arrives in one clean place. It shows up in practice observations,
            press conferences, local reporting, injury updates and depth chart changes.
            The Wire is built to organize that reporting around the player it affects.
          </p>
        </div>

        <div class="lb-about-process">
          <div class="lb-about-step">
            <div class="lb-about-step-number">01</div>
            <div><h3>Follow the source</h3><p>Track reporting from local NFL beat writers and established sources covering each team.</p></div>
          </div>
          <div class="lb-about-step">
            <div class="lb-about-step-number">02</div>
            <div><h3>Identify the fantasy relevance</h3><p>Surface the updates that can affect roles, health, opportunity, usage or the team environment.</p></div>
          </div>
          <div class="lb-about-step">
            <div class="lb-about-step-number">03</div>
            <div><h3>Connect it to the player</h3><p>Match the report to the relevant player or team so the context is immediately usable.</p></div>
          </div>
          <div class="lb-about-step">
            <div class="lb-about-step-number">04</div>
            <div><h3>Keep the source attached</h3><p>Credit the original reporter so users can see where the information came from and read the underlying reporting.</p></div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <section class="lb-about-section lb-about-principles">
    <div class="lb-about-wrap">
      <div class="lb-about-section-head">
        <div class="lb-about-kicker">HOW WE THINK ABOUT FANTASY INFORMATION</div>
        <h2>Facts, forecasts and uncertainty should look different.</h2>
      </div>

      <div class="lb-about-principle-grid">
        <article class="lb-about-principle">
          <strong>FACT</strong>
          <h3>A report is a report.</h3>
          <p>If a beat reporter says a player missed practice or worked with the first team, that is reporting. Present it accurately and preserve the source.</p>
        </article>

        <article class="lb-about-principle">
          <strong>FORECAST</strong>
          <h3>A projection is our estimate.</h3>
          <p>Projected carries, targets, yards and fantasy points are model outputs. They are not facts, and they should not be presented as if they are.</p>
        </article>

        <article class="lb-about-principle">
          <strong>UNCERTAINTY</strong>
          <h3>Could happen is not will happen.</h3>
          <p>Injury concern, possible discipline, camp competition and uncertain roles should stay uncertain until stronger evidence changes the probability.</p>
        </article>

        <article class="lb-about-principle">
          <strong>ACCOUNTABILITY</strong>
          <h3>The stat line should explain the rank.</h3>
          <p>Rankings should come from the underlying projected opportunity and efficiency, not the other way around.</p>
        </article>
      </div>
    </div>
  </section>

  <section class="lb-about-belief">
    <div class="lb-about-wrap">
      <blockquote>Forecasts will be wrong.<br><span>Facts should not be.</span></blockquote>
    </div>
  </section>

  <section class="lb-about-section">
    <div class="lb-about-wrap">
      <div class="lb-about-section-head">
        <div class="lb-about-kicker">SOURCES &amp; TRANSPARENCY</div>
        <h2>Show where the information comes from.</h2>
        <p>LineupBeat is most useful when users can distinguish original reporting, measured data and our own projections.</p>
      </div>

      <div class="lb-about-source-grid">
        <article class="lb-about-source-panel">
          <h3>Reporting</h3>
          <p>Wire items should preserve attribution to the original reporter or publication rather than making the reporting look like it originated with LineupBeat.</p>
          <ul class="lb-about-source-list">
            <li>Original reporter credited</li>
            <li>Publication or source shown</li>
            <li>Player or team connection visible</li>
            <li>Reporting kept separate from projection opinion</li>
          </ul>
        </article>

        <article class="lb-about-source-panel">
          <h3>Fantasy data</h3>
          <p>Data tools should explain what they measure, use published sources where applicable, and expose enough of the underlying numbers that users can understand the result.</p>
          <ul class="lb-about-source-list">
            <li>Raw stat lines behind projections</li>
            <li>Tool specific methodology</li>
            <li>Historical data kept separate from forecasts</li>
            <li>Updates handled on the appropriate cadence</li>
          </ul>
        </article>
      </div>
    </div>
  </section>

  <section class="lb-about-final">
    <div class="lb-about-wrap">
      <div class="lb-about-final-grid">
        <div>
          <div class="lb-about-kicker">SEE IT IN ACTION</div>
          <h2>Start with what changed.<br>Then look at the numbers.</h2>
          <p>Open the Wire for the latest reporting, or explore the fantasy data tools for projections, draft value, schedule and context.</p>
        </div>

        <div class="lb-about-final-actions">
          <a class="lb-about-btn lb-about-btn-primary" href="/nfl/wire/">
            OPEN THE WIRE
            <svg class="lb-about-arrow" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg>
          </a>
          <a class="lb-about-btn lb-about-btn-secondary" href="/nfl/data/">EXPLORE FANTASY DATA</a>
        </div>
      </div>
    </div>
  </section>

</main>"""

    title = "About LineupBeat | How It Works and Where the Data Comes From"
    desc = ("Why we built LineupBeat, how we follow beat reporters in all "
            "32 NFL markets, where our projections and data come from, and "
            "how we correct factual errors.")
    schema = {
        "@type": "AboutPage",
        "name": title,
        "description": desc,
        "url": f"{base}/about/",
        "dateModified": built.strftime("%Y-%m-%d"),
        "publisher": {"@id": f"{base}/#org"},
        "mainEntity": {
            "@type": "Person",
            "name": seo.AUTHOR,
            "description": seo.AUTHOR_ROLE,
            "email": "hello@lineupbeat.com",
            "worksFor": {"@id": f"{base}/#org"},
        },
    }
    crumbs = seo.breadcrumbs([("LineupBeat", "/"), ("About", "/about/")])
    return body, title, desc, seo.graph(schema, crumbs, seo.ORGANISATION)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--base", default="https://lineupbeat.com")
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--max-reports", type=int, default=60,
                    help="cap per page; a page is an archive, not the whole log")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Read the DATABASE, not site/data/feed.json.
    #
    # The feed is capped by the export limit, so Breece Hall's page showed one
    # report -- "had a couple of long runs in practice" -- because that was
    # all of him that survived the cut. A page like that is worse than none:
    # somebody searches his name, lands on a single line and leaves, which is
    # the signal that teaches a search engine not to rank you.
    #
    # The feed governs what the live wire shows. A player page is an archive.
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""SELECT * FROM nuggets WHERE sport=?
                               AND player_id IS NOT NULL
                               ORDER BY published_at DESC""",
                            (args.sport,)).fetchall()
    except sqlite3.OperationalError:
        sys.exit("  no nuggets table — run the pipeline first")
    if not rows:
        sys.exit("  no nuggets")

    roster = {}
    rp = ROOT / "rosters" / f"{args.sport}.csv"
    if rp.exists():
        for r in csv.DictReader(rp.open()):
            roster[r["id"]] = r

    by_player = defaultdict(list)
    for r in rows:
        by_player[r["player_id"]].append(dict(r))
    players = {pid: {"id": pid,
                     "name": roster.get(pid, {}).get("name") or v[0]["player_name"],
                     "team": (roster.get(pid, {}).get("team")
                              or v[0]["team"] or "").upper(),
                     "pos": (roster.get(pid, {}).get("position") or "").upper(),
                     "meta": roster.get(pid, {})}
               for pid, v in by_player.items()}

    global PROJECTIONS
    PROJECTIONS = load_projections()
    if PROJECTIONS:
        print(f"  {len(PROJECTIONS)} projections available for player pages")

    base = args.base.rstrip("/")
    written, urls = 0, []
    now = eastern_now().strftime("%Y-%m-%d")

    # A projected player gets a page even with no beat reports yet.
    #
    # The rule was "no reports, no page", which is right when the page would
    # be a name and nothing else. A projection is not nothing: it is a stat
    # line, a positional rank and three scoring formats, which is more than
    # some pages carry with two reports on them. And a board that links to
    # 614 players while 153 of them have nowhere to go is a board with
    # broken promises in it.
    for pid, r in roster.items():
        if pid in by_player:
            continue
        s = slug(r.get("name", ""))
        if s and s in PROJECTIONS:
            by_player[pid] = []
            players[pid] = {"id": pid, "name": r.get("name", ""),
                            "team": (r.get("team") or "").upper(),
                            "pos": (r.get("position") or "").upper(),
                            "meta": r}

    # Skill positions only.
    #
    # The wire already refuses to publish a nugget about a punter, so a
    # punter's page is guaranteed to hold nothing the site is for. Nine
    # hundred and forty-one directories were guards, corners and kickers,
    # and Google crawled them, found a fantasy football site full of
    # offensive linemen, and declined to index a hundred and ninety-one.
    #
    # An empty page for a skill player is a page waiting for news. An empty
    # page for a long snapper is waiting for nothing.
    skipped_pos = 0
    kept_slugs = set()
    for pid, ns in by_player.items():
        p = players.get(pid)
        if not p or not p["name"]:
            continue
        if (p.get("pos") or "").upper() not in PUBLISHED_POSITIONS:
            skipped_pos += 1
            continue
        kept_slugs.add(slug(p["name"]))
        ns = ns[:args.max_reports]
        path = SITE / args.sport / slug(p["name"]) / "index.html"
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(seo.check_page(player_page(p, ns, base), str(path)))
        # A page with no reports still changes when the board does, so it
        # gets today's date and a lower priority rather than being left out.
        urls.append((f"{base}/{args.sport}/{slug(p['name'])}/",
                     ns[0]["published_at"][:10] if ns else now,
                     "daily" if ns else "weekly",
                     "0.8" if ns else "0.5"))
        written += 1

    # Slugs that actually have a page, for the client-side linker. It
    # cannot infer this: a name is not evidence that a page was built.
    (SITE / "data").mkdir(parents=True, exist_ok=True)
    (SITE / "data" / "pages.json").write_text(json.dumps({
        "sport": args.sport,
        "generated": now,
        "slugs": sorted({u[0].rstrip("/").rsplit("/", 1)[-1] for u in urls}),
    }, separators=(",", ":")))

    by_team = defaultdict(list)
    for pid, ns in by_player.items():
        p = players.get(pid)
        if p and p.get("team") in TEAM_NAMES:
            by_team[p["team"]].append((p["name"], len(ns)))
    teams_written = 0
    for team, plist in by_team.items():
        plist.sort(key=lambda x: -x[1])
        total = sum(c for _, c in plist)
        path = SITE / args.sport / "team" / slug(team) / "index.html"
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(seo.check_page(
                team_page(team, plist, total, base), str(path)))
        urls.append((f"{base}/{args.sport}/team/{slug(team)}/", now, "daily", "0.7"))
        teams_written += 1

    # Every data page, listed here rather than appended by each builder.
    #
    # This script rewrites the sitemap from scratch and runs last, so
    # anything the other builders inserted was silently wiped: eight pages
    # including all four position boards were live and unlisted. One place
    # owns the file, and it is the one that writes it.
    for path, freq, prio in (
            (f"/{args.sport}/data/", "weekly", "0.8"),
            (f"/{args.sport}/projections/", "daily", "0.9"),
            (f"/{args.sport}/projections/qb/", "daily", "0.8"),
            (f"/{args.sport}/projections/rb/", "daily", "0.8"),
            (f"/{args.sport}/projections/wr/", "daily", "0.8"),
            (f"/{args.sport}/projections/te/", "daily", "0.8"),
            (f"/{args.sport}/draft-value/", "daily", "0.9"),
            (f"/{args.sport}/strength-of-schedule/", "weekly", "0.8"),
            (f"/{args.sport}/coaching/", "monthly", "0.7"),
            (f"/{args.sport}/durability/", "weekly", "0.8"),
            (f"/{args.sport}/projections/changes/", "weekly", "0.7"),
            (f"/{args.sport}/offensive-line-rb-performance/", "weekly", "0.7"),
            # College. Sport-neutral paths, so they are not built from
            # args.sport: the projections are college whatever sport this
            # run is generating pages for.
            ("/college-fantasy-football/projections/", "weekly", "0.9"),
            ("/college-fantasy-football/projections/qb/", "weekly", "0.8"),
            ("/college-fantasy-football/projections/rb/", "weekly", "0.8"),
            ("/college-fantasy-football/projections/wr/", "weekly", "0.8"),
            ("/college-fantasy-football/projections/te/", "weekly", "0.8"),
            ("/about/", "monthly", "0.6")):
        if (SITE / path.lstrip("/") / "index.html").exists():
            urls.append((f"{base}{path}", now, freq, prio))

    urls.insert(0, (f"{base}/", now, "hourly", "1.0"))



    # One entry per URL.
    #
    # The hub and durability sections used to insert into the finished
    # sitemap as well, after this list had been turned into XML, so both
    # appeared twice. The list above is the only place URLs are declared; a
    # duplicate is not an error a crawler reports, it is one it quietly
    # discounts.
    seen, deduped = set(), []
    for u in urls:
        if u[0] in seen:
            continue
        seen.add(u[0])
        deduped.append(u)
    urls = deduped

    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, freq, prio in urls:
        sitemap.append(f"  <url><loc>{loc}</loc><lastmod>{lastmod}</lastmod>"
                       f"<changefreq>{freq}</changefreq>"
                       f"<priority>{prio}</priority></url>")
    sitemap.append("</urlset>")

    # Do NOT disallow /data/. The site loads feed.json from there, and
    # blocking it does not stop indexing -- it stops Google rendering the
    # page, so the crawler sees an empty shell where a reader sees the wire.
    # Named AI crawlers, allowed explicitly.
    #
    # "User-agent: *" already permits them, but these agents are checked by
    # name and many sites block them, so being silent is ambiguous where
    # being explicit is not. The bet: a projection cited in an answer is
    # worth more than the pageview it replaces, because the citation is the
    # thing a competitor cannot copy.
    #
    # Note the split. OAI-SearchBot and ChatGPT-User serve answers and can
    # cite the site; GPTBot collects training data and returns nothing. All
    # three are allowed here, but they are listed separately so that
    # changing one's mind about training is a one-line edit.
    ai_agents = [
        ("OAI-SearchBot", "ChatGPT search, can cite us"),
        ("ChatGPT-User", "fetches a page when somebody asks about it"),
        ("GPTBot", "OpenAI training data"),
        ("Google-Extended", "Gemini and AI Overviews"),
        ("PerplexityBot", "Perplexity search"),
        ("Perplexity-User", "Perplexity, user-initiated fetch"),
        ("ClaudeBot", "Anthropic"),
        ("Claude-SearchBot", "Claude search"),
        ("anthropic-ai", "Anthropic, legacy agent name"),
        ("Applebot-Extended", "Apple Intelligence"),
        ("CCBot", "Common Crawl, which many models are built from"),
        ("Amazonbot", "Amazon"),
        ("Bytespider", "ByteDance"),
        ("meta-externalagent", "Meta"),
        ("cohere-ai", "Cohere"),
        ("Diffbot", "Diffbot"),
        ("Timpibot", "Timpi"),
        ("Omgilibot", "Webz.io"),
    ]
    robots = "User-agent: *\nAllow: /\n\n"
    robots += ("# Answer engines and AI crawlers, allowed by name.\n"
               "# Our data is meant to be checkable, which means being "
               "readable.\n\n")
    for agent, why in ai_agents:
        robots += f"# {why}\nUser-agent: {agent}\nAllow: /\n\n"
    robots += f"Sitemap: {base}/sitemap.xml\n"

    if not args.dry_run:
        # The durability page goes FIRST, because the sitemap is built from
        # `urls` and appending to it after the file is written puts the page
        # nowhere. It shipped unlisted, which for a page whose whole point is
        # being found is the one mistake that matters.
        #
        # It also shells out to the projection scripts, so it is the slow one.
        # The hub first: it is cheap, and the durability page's breadcrumb
        # points at it.
        try:
            hub = data_hub_page(base)
            hd = SITE / args.sport / "data"
            hd.mkdir(parents=True, exist_ok=True)
            (hd / "index.html").write_text(seo.check_page(hub, "hub"))
            print(f"  data hub written")
        except Exception as exc:
            print(f"  data hub skipped: {str(exc)[:70]}")

        # Who is behind this. The page a search engine looks for and the
        # one the site did not have.
        try:
            body, title, desc, ld = about_page(base, eastern_now())
            page = PAGE.format(
        fonts=PAGE_FONTS,
                title=esc(title), description=esc(desc),
                canonical=esc(f"{base}/about/"), og_type="website",
                og_image="",
                structured=(f'<script type="application/ld+json">{ld}</script>'
                            f'<style>{ABOUT_CSS}</style>'),
                body=body)
            a = SITE / "about"
            a.mkdir(parents=True, exist_ok=True)
            (a / "index.html").write_text(
                _render(page, "#C6F24E", "#C6F24E", section="about"))
            urls.append((f"{base}/about/", now, "monthly", "0.6"))
            print(f"  about page written")
        except Exception as exc:
            print(f"  about page skipped: {str(exc)[:80]}")

        try:
            html = durability_page(conn, base)
            if html:
                d = SITE / args.sport / "durability"
                d.mkdir(parents=True, exist_ok=True)
                (d / "index.html").write_text(html)
                print(f"  durability page written")
        except Exception as exc:
            print(f"  durability page skipped: {str(exc)[:70]}")

        (SITE / "sitemap.xml").write_text("\n".join(sitemap))
        (SITE / "robots.txt").write_text(robots)

    # Directories from earlier runs, removed.
    #
    # Nothing deleted a page when a player stopped qualifying, so every
    # roster change and every filter change left its pages behind: 1,739
    # directories for 720 players. They stayed in the deploy, stayed
    # crawlable, and were most of what Google saw.
    #
    # Only ever the player directory for this sport, and only when a real
    # build just ran -- a dry run or a failed pipeline must not empty the
    # site.
    removed = 0
    # Fifty is a floor, not a target: it means a real build ran. A pipeline
    # that failed and produced three pages must never be allowed to delete
    # seven hundred.
    if not args.dry_run and written > 50:
        import shutil
        protected = {"team", "data", "projections", "draft-value",
                     "durability", "coaching", "strength-of-schedule",
                     "offensive-line-rb-performance"}
        for d in (SITE / args.sport).glob("*"):
            if not d.is_dir() or d.name in protected:
                continue
            if d.name in kept_slugs:
                continue
            shutil.rmtree(d)
            removed += 1

    print(f"  player pages   {written}")
    if removed:
        print(f"  {removed} stale director{'y' if removed == 1 else 'ies'} "
              f"removed")
    if skipped_pos:
        print(f"  {skipped_pos} non-skill players got no page "
              f"(linemen, defence, specialists)")
    print(f"  team pages     {teams_written}")
    print(f"  sitemap URLs   {len(urls)}")
    if args.dry_run:
        print("\n  --dry-run, nothing written")
    else:
        print(f"\n  wrote {SITE}/sitemap.xml and robots.txt")
    print(f"\n  {len(players) - written} players have no reports and got no page.")
    print("  That is deliberate: thin pages are a liability, not coverage.")


if __name__ == "__main__":
    main()
