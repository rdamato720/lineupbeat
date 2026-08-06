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


def site_chrome():
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
    header = (
        '<header class="topbar">\n'
        '  <div class="wrap tbrow">\n'
        '    <a class="logo" href="/">Lineup<em>Beat</em></a>\n'
        '  </div>\n'
        '</header>'
    )
    return (css.group(1) if css else ""), header, (foot.group(0) if foot else "")


APP_CSS, APP_HEADER, APP_FOOTER = site_chrome()

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
.crumbs a:hover{color:var(--signal)}
.crumbs span{color:var(--rule)}
.crumbs b{color:var(--ink);font-weight:600}
/* The header links are anchors here, not the app's buttons, so they pick up
   the default underline. Match the app's chrome instead. */
.topbar .logo,.topbar .vbtn{text-decoration:none}
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


def _render(page, accent, c2="#C6F24E"):
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
            .replace("__HEADER__", APP_HEADER)
            .replace("__FOOTER__", APP_FOOTER))


def page_description(name, who, nuggets):
    """A description that reads as a sentence in a search result.

    The newest claim alone came out at 61 characters -- "Brown was limited
    with what the team called general soreness" -- which reads as a fragment
    torn out of context and tells a searcher nothing about what the page is.
    Google also tends to write its own when the tag is too thin.
    """
    n = len(nuggets)
    lead = (nuggets[0]["claim"] or "").rstrip(".")
    tail = (f"{n} beat reports on {name}, newest first, each linked to the "
            f"reporter who filed it.")
    body = f"{who}. {lead}. {tail}"
    return body[:158].rsplit(" ", 1)[0] if len(body) > 160 else body


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
            + f'  <h2>{len(nuggets)} beat report'
              f'{"s" if len(nuggets) != 1 else ""}, newest first</h2>\n'
            + "\n".join(arts)
            + (f'\n  <p style="margin-top:2rem"><a href="/{SPORT}/team/{slug(team)}/">'
               f'More {esc(TEAM_NAMES.get(team, team))} reports</a></p>'
               if team else ""))

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

    base = args.base.rstrip("/")
    written, urls = 0, []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for pid, ns in by_player.items():
        p = players.get(pid)
        if not p or not p["name"]:
            continue
        ns = ns[:args.max_reports]
        path = SITE / args.sport / slug(p["name"]) / "index.html"
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(player_page(p, ns, base))
        urls.append((f"{base}/{args.sport}/{slug(p['name'])}/",
                     ns[0]["published_at"][:10], "daily", "0.8"))
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
