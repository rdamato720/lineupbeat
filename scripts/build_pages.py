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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
    header = (
        '<header class="topbar">\n'
        '  <div class="wrap tbrow">\n'
        '    <a class="logo" href="/">Lineup<em>Beat</em></a>\n'
        '    <nav class="views">'
        '<a class="vbtn" href="/">The Wire</a>'
        # My Roster is an in-app view, so from a static page it can only be a
        # link into the app that opens it. The hash is what the wire reads on
        # load, so the section is showing by the time anybody sees the page.
        '<a class="vbtn" href="/#v=roster">My Roster</a>'
        f'<a class="vbtn" href="/{SPORT}/data/"'
        + (' aria-current="page"' if section == "data" else "")
        + '>Fantasy Data</a>'
        '</nav>\n'
        '    <div class="finder">\n'
        '      <input id="pfind" type="search" placeholder="Find a player"\n'
        '             autocomplete="off" aria-label="Find a player">\n'
        '    </div>\n'
        '  </div>\n'
        '</header>'
    )

    return (css.group(1) if css else ""), header, (foot.group(0) if foot else "")


APP_CSS, APP_HEADER, APP_FOOTER = site_chrome()
# The same bar with Fantasy Data marked, for the pages that live under it.
# A player page is wire content and gets the plain one; the hub and the
# boards get the marker, so the highlight means where you are rather than
# which script built the page.
_, DATA_HEADER, _ = site_chrome("data")

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
        d = (datetime.now(timezone.utc)
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
.crumbs{display:flex;flex-wrap:wrap;align-items:center;gap:.45rem;
  margin:0 0 1.1rem;font:.68rem/1 var(--agate,system-ui),sans-serif;
  letter-spacing:.08em;text-transform:uppercase}
.crumbs a{color:var(--quiet);text-decoration:none}
/* A breadcrumb link at eleven pixels is not tappable. The text stays small
   because it is a breadcrumb; the target does not. */
@media(max-width:760px){
  .crumbs{gap:.2rem}
  .crumbs a{display:inline-flex;align-items:center;min-height:44px;
    padding:0 .3rem}
  .crumbs b{display:inline-flex;align-items:center;min-height:44px}
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
  .chips{justify-content:center}
}
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
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
                     DATA_HEADER if section == "data" else APP_HEADER)
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
                        "pos": pos}
    return out


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

    # The projection, first, because it is the number somebody came for.
    #
    # Only where the board has one. A page for a long snapper should not
    # carry an empty PPR chip explaining that nobody projected him.
    pr = PROJECTIONS.get(slug(name))
    if pr:
        chips.insert(0, f'<span class="chip">PPR <b>{pr["ppr"]:.1f}</b>'
                        f'</span>')
        if pr.get("rank"):
            chips.insert(1, f'<span class="chip">{esc(pos or "")}'
                            f'<b>{pr["rank"]}</b></span>')

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
            + (f'  <h2>{len(nuggets)} beat report'
               f'{"s" if len(nuggets) != 1 else ""}, newest first</h2>\n'
               if nuggets else
               '  <h2>No beat reports yet</h2>\n'
               '  <p class="dlede">Nothing has been filed about this player '
               'since the wire started watching. The projection above is '
               'what the board has him down for.</p>\n')
            + "\n".join(arts))

    return _render(PAGE.format(
        title=esc(f"{name} news, beat reports and updates | LineupBeat"),
        description=esc(page_description(name, who, nuggets)),
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
    cards = "\n".join(
        f'    <a href="/{SPORT}/{slug(n)}/">{esc(n)}<span>{c} report'
        f'{"s" if c != 1 else ""}</span></a>' for n, c in players)
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
            f'{len(players)} players</p>\n    </div>\n  </div>\n'
            f'  <h2>Players in the news</h2>\n'
            f'  <div class="grid">\n{cards}\n  </div>')
    return _render(PAGE.format(
        title=esc(f"{full} beat reports and player news | LineupBeat"),
        description=esc(f"Local beat reporting on the {full}, matched to "
                        f"players and updated through the day."),
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


def data_hub_page(base):
    """The index for everything derived from the data rather than the wire.

    Two entries now. Projections had briefly become its own nav item, which
    put two data pages at different levels of the site for no reason a
    reader could see -- one behind Fantasy Data and one beside it.
    """
    cards = [
        {
            "href": f"/{SPORT}/projections/",
            "kicker": "Every relevant player",
            "title": "Yearly projections",
            "blurb": "Full-season point projections in PPR, half PPR and "
                     "standard scoring, with the stat line behind each "
                     "number. Ranked within position, and the board "
                     "reorders when you change the scoring.",
            "meta": "Updated through the preseason",
        },
        {
            "href": f"/{SPORT}/coaching/",
            "kicker": "All 32 offences",
            "title": "Offensive coaching",
            "blurb": "Who actually calls each offence, which seventeen teams "
                     "changed callers, and which positions that favours. A "
                     "tiebreaker between players at comparable ADP, not a "
                     "reason to reach.",
            "meta": "Verified August 9",
        },
        {
            "href": f"/{SPORT}/strength-of-schedule/",
            "kicker": "Every team, week by week",
            "title": "Strength of schedule",
            "blurb": "Which teams have the easiest run left, by opponent "
                     "record and by the fantasy points each opponent "
                     "actually allows to backs, receivers and tight ends. "
                     "Reweights itself as the season is played.",
            "meta": "Updated weekly in season",
        },
        {
            "href": f"/{SPORT}/durability/",
            "kicker": "Every drafted player",
            "title": "Durability and availability",
            "blurb": "How many games each player has actually given, from "
                     "every injury report and roster transaction since 2018, "
                     "set against live ADP. Injured reserve, healthy "
                     "scratches and suspensions counted separately.",
            "meta": "Updated daily",
        },
    ]
    items = []
    for c in cards:
        items.append(
            f'<a class="hubcard" href="{c["href"]}">'
            f'<span class="hk">{esc(c["kicker"])}</span>'
            f'<h2>{esc(c["title"])}</h2>'
            f'<p>{esc(c["blurb"])}</p>'
            f'<span class="hm">{esc(c["meta"])}</span></a>')

    body = (
        '  <nav class="crumbs" aria-label="Breadcrumb">'
        '<a href="/">LineupBeat</a><span>/</span><b>Fantasy data</b></nav>\n'
        '  <h1 class="dh1">Fantasy data</h1>\n'
        '  <p class="dlede">What the record says, separate from what the beat '
        'is saying today. Everything here is built from published data and '
        'refreshed on its own schedule.</p>\n'
        f'  <div class="hubgrid">{"".join(items)}</div>\n')

    css = """
.ppage{max-width:56rem}
.dh1{font-family:var(--agate);text-transform:uppercase;font-size:2.6rem;
  line-height:.95;margin:0 0 .8rem;letter-spacing:-.01em}
.dlede{color:var(--quiet);font-size:1.02rem;line-height:1.6;max-width:42rem;
  margin:0 0 2.2rem}
.hubgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:1rem}
.hubcard{display:block;background:var(--panel);border:1px solid var(--rule);
  border-top:2px solid var(--signal);border-radius:0 0 10px 10px;
  padding:1.2rem 1.15rem 1.3rem;text-decoration:none;color:inherit;
  transition:border-color .12s, background .12s}
.hubcard:hover{background:rgba(255,255,255,.03);border-color:var(--quiet);
  border-top-color:var(--signal)}
.hubcard .hk{font-family:var(--agate);font-size:.58rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--signal);display:block;
  margin-bottom:.5rem}
/* The app styles h2 on these pages as a small grey kicker, which is right
   for a section label and wrong for a card title. Fifth class collision
   after .hero, .big, .how and .dmgrid. */
.hubcard h2{font-family:var(--agate)!important;text-transform:uppercase;
  font-size:1.15rem!important;letter-spacing:.01em;margin:0 0 .5rem!important;
  color:var(--ink)!important;font-weight:600}
.hubcard p{margin:0 0 .9rem;color:var(--quiet);font-size:.86rem;
  line-height:1.55}
.hubcard .hm{font-family:var(--data,ui-monospace),monospace;font-size:.66rem;
  color:var(--quiet);opacity:.7}
@media(max-width:640px){.hubgrid{grid-template-columns:1fr}
  .dh1{font-size:1.9rem}}
"""
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "CollectionPage",
         "name": "NFL fantasy data",
         "description": "Durability, availability and draft data for the NFL, "
                        "built from published records.",
         "url": f"{base}/{SPORT}/data/"},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "LineupBeat",
             "item": base + "/"},
            {"@type": "ListItem", "position": 2, "name": "Fantasy data",
             "item": f"{base}/{SPORT}/data/"}]}]}

    return _render(PAGE.format(
        title="NFL Fantasy Data: Durability, Availability and ADP",
        description=("Durability and availability for every drafted NFL "
                     "player, built from injury reports and roster "
                     "transactions since 2018. Free, and updated daily."),
        canonical=f"{base}/{SPORT}/data/",
        og_type="website",
        og_image=f'<meta property="og:image" content="{base}/og.png">',
        structured=(f'<script type="application/ld+json">{json.dumps(ld)}</script>'
                    f'<style>{css}</style>'),
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
        '  <h1 class="dh1">Who actually plays</h1>\n'
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
        '  <table class="dtab">\n'
        '    <thead><tr><th class="adp">ADP</th><th>Player</th><th>Pos</th>'
        '<th class="ar">Missed/yr</th><th>Availability</th><th class="ar">On IR</th>'
        '<th class="ar">Inactive</th><th class="ar">Susp</th><th>Games by season</th>'
        '</tr></thead>\n'
        f'    <tbody>{"".join(rows)}</tbody>\n'
        '  </table>\n'

        '')


    css = """
.ppage{max-width:56rem}
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
                    f'<style>{css}</style>'
                    f'<script>{FIND_JS}</script>'),
        body=body), "#C6F24E", "#C6F24E", section="data")


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
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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

    for pid, ns in by_player.items():
        p = players.get(pid)
        if not p or not p["name"]:
            continue
        ns = ns[:args.max_reports]
        path = SITE / args.sport / slug(p["name"]) / "index.html"
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(player_page(p, ns, base))
        # A page with no reports still changes when the board does, so it
        # gets today's date and a lower priority rather than being left out.
        urls.append((f"{base}/{args.sport}/{slug(p['name'])}/",
                     ns[0]["published_at"][:10] if ns else now,
                     "daily" if ns else "weekly",
                     "0.8" if ns else "0.5"))
        written += 1

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
            path.write_text(team_page(team, plist, total, base))
        urls.append((f"{base}/{args.sport}/team/{slug(team)}/", now, "daily", "0.7"))
        teams_written += 1

    urls.insert(0, (f"{base}/", now, "hourly", "1.0"))

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
    robots = (f"User-agent: *\n"
              f"Allow: /\n"
              f"\n"
              f"Sitemap: {base}/sitemap.xml\n")

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
            (hd / "index.html").write_text(hub)
            sitemap.insert(len(sitemap) - 1,
                           f"  <url><loc>{base}/{args.sport}/data/</loc>"
                           f"<lastmod>{now}</lastmod>"
                           f"<changefreq>weekly</changefreq>"
                           f"<priority>0.8</priority></url>")
            print(f"  data hub written")
        except Exception as exc:
            print(f"  data hub skipped: {str(exc)[:70]}")

        try:
            html = durability_page(conn, base)
            if html:
                d = SITE / args.sport / "durability"
                d.mkdir(parents=True, exist_ok=True)
                (d / "index.html").write_text(html)
                sitemap.insert(
                    len(sitemap) - 1,
                    f"  <url><loc>{base}/{args.sport}/durability/</loc>"
                    f"<lastmod>{now}</lastmod>"
                    f"<changefreq>daily</changefreq>"
                    f"<priority>0.9</priority></url>")
                print(f"  durability page written")
        except Exception as exc:
            print(f"  durability page skipped: {str(exc)[:70]}")

        (SITE / "sitemap.xml").write_text("\n".join(sitemap))
        (SITE / "robots.txt").write_text(robots)

    print(f"  player pages   {written}")
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
