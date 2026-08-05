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


def when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%B %-d, %Y")
    except (TypeError, ValueError):
        return ""


CSS = """<style>
:root{
  color-scheme:dark;
  --ink:#E8E6E1; --quiet:#8A8F85; --rule:#23261F;
  --bg:#0A0C08; --panel:#0F1310; --signal:#C6F24E;
  --accent:__ACCENT__;
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);margin:0;
  font:16px/1.65 Georgia,"Times New Roman",serif}
.wrap{max-width:46rem;margin-inline:auto;padding:1.5rem 1.25rem 4rem}
a{color:var(--signal);text-decoration:none}
a:hover{text-decoration:underline}
.top{padding:.9rem 0;border-bottom:1px solid var(--rule);margin-bottom:2rem}
.brand{font:600 1.05rem/1 system-ui,sans-serif;letter-spacing:.02em;
  text-transform:uppercase;color:var(--ink)}
.brand em{font-style:normal;color:var(--signal)}
.hero{display:flex;gap:1.15rem;align-items:center;padding:1.25rem;
  border-radius:10px;background:var(--panel);
  border-left:4px solid var(--accent)}
.shot{width:84px;height:84px;border-radius:50%;object-fit:cover;
  background:#1B2024;flex:none}
h1{font-size:2rem;line-height:1.1;margin:0 0 .3rem;letter-spacing:-.015em}
.who{color:var(--quiet);font:.9rem/1.4 system-ui,sans-serif;margin:0}
.chips{display:flex;flex-wrap:wrap;gap:.4rem;margin:1rem 0 0}
.chip{font:.72rem/1 system-ui,sans-serif;letter-spacing:.04em;
  text-transform:uppercase;color:var(--quiet);border:1px solid var(--rule);
  border-radius:999px;padding:.4rem .7rem}
.chip b{color:var(--ink);font-weight:600}
h2{font:.75rem/1 system-ui,sans-serif;letter-spacing:.1em;
  text-transform:uppercase;color:var(--quiet);margin:2.25rem 0 .5rem}
article{border-top:1px solid var(--rule);padding:1.1rem 0}
.claim{margin:0 0 .45rem;font-size:1.02rem}
.meta{color:var(--quiet);font:.8rem/1.4 system-ui,sans-serif;margin:0}
.meta a{color:var(--quiet);text-decoration:underline}
.meta a:hover{color:var(--signal)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(13rem,1fr));
  gap:.5rem}
.grid a{display:block;padding:.7rem .85rem;border:1px solid var(--rule);
  border-radius:8px;color:var(--ink)}
.grid a:hover{border-color:var(--signal);text-decoration:none}
.grid span{display:block;color:var(--quiet);
  font:.75rem/1.3 system-ui,sans-serif;margin-top:.15rem}
footer{margin-top:3rem;padding-top:1.25rem;border-top:1px solid var(--rule);
  color:var(--quiet);font:.82rem/1.6 system-ui,sans-serif}
footer a{color:var(--quiet);text-decoration:underline}
@media(max-width:34rem){
  .hero{flex-direction:column;text-align:center}
  h1{font-size:1.6rem}
  .chips{justify-content:center}
}
</style>"""

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
<meta name="twitter:card" content="summary">
<meta name="theme-color" content="#0A0C08">
{structured}
__CSS__
</head>
<body>
<div class="wrap">
  <div class="top"><a class="brand" href="/">Lineup<em>Beat</em></a></div>
{body}
  <footer>
  <p>Every claim is paraphrased in our own words and linked back to the
  reporter who filed it. We never reproduce their copy.</p>
  <p><a href="/">LineupBeat</a> &mdash; local beat reporting from every NFL
     market, matched to players.</p>
  </footer>
</div>
</body>
</html>
"""


def _render(page, accent):
    """CSS lives outside .format() because a stylesheet is full of braces and
    every one of them has to be doubled otherwise -- which is unreadable and
    breaks the moment somebody edits the CSS without knowing why."""
    return page.replace("__CSS__", CSS.replace("__ACCENT__", accent))


def player_page(p, nuggets, base):
    name, team = p["name"], p["team"]
    pos, meta = p["pos"], p.get("meta") or {}
    url = f"{base}/{SPORT}/{slug(name)}/"
    accent = TEAM_COLORS.get(team, "#C6F24E")
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
        arts.append(f'  <article>\n    <p class="claim">{esc(n["claim"])}</p>\n'
                    f'    <p class="meta">{esc(when(n["published_at"]))} &middot; '
                    f'{cite}{esc(extra)}</p>\n  </article>')

    ld = {"@context": "https://schema.org", "@type": "Person", "name": name,
          "url": url, "image": shot, "jobTitle": who}
    if team:
        ld["memberOf"] = {"@type": "SportsTeam",
                          "name": TEAM_NAMES.get(team, team)}

    body = (f'  <div class="hero">\n    <img class="shot" src="{esc(shot)}" '
            f'alt="{esc(name)}" loading="lazy" width="84" height="84">\n'
            f'    <div>\n      <h1>{esc(name)}</h1>\n'
            f'      <p class="who">{esc(who)}</p>\n    </div>\n  </div>\n'
            + (f'  <div class="chips">{"".join(chips)}</div>\n' if chips else "")
            + f'  <h2>{len(nuggets)} beat report'
              f'{"s" if len(nuggets) != 1 else ""}, newest first</h2>\n'
            + "\n".join(arts)
            + (f'\n  <p style="margin-top:2rem"><a href="/{SPORT}/team/{slug(team)}/">'
               f'More {esc(TEAM_NAMES.get(team, team))} reports</a></p>'
               if team else ""))

    return _render(PAGE.format(
        title=esc(f"{name} news, beat reports and updates | LineupBeat"),
        description=esc((nuggets[0]["claim"] or "")[:150]),
        canonical=esc(url), og_type="profile",
        og_image=f'<meta property="og:image" content="{esc(shot)}">',
        structured=f'<script type="application/ld+json">{json.dumps(ld)}</script>',
        body=body), accent)


def team_page(team, players, count, base):
    full = TEAM_NAMES.get(team, team)
    url = f"{base}/{SPORT}/team/{slug(team)}/"
    accent = TEAM_COLORS.get(team, "#C6F24E")
    logo = f"https://a.espncdn.com/i/teamlogos/nfl/500/{team.lower()}.png"
    cards = "\n".join(
        f'    <a href="/{SPORT}/{slug(n)}/">{esc(n)}<span>{c} report'
        f'{"s" if c != 1 else ""}</span></a>' for n, c in players)
    ld = {"@context": "https://schema.org", "@type": "SportsTeam",
          "name": full, "url": url, "logo": logo}
    body = (f'  <div class="hero">\n    <img class="shot" src="{esc(logo)}" '
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
        structured=f'<script type="application/ld+json">{json.dumps(ld)}</script>',
        body=body), accent)


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

    robots = (f"User-agent: *\n"
              f"Allow: /\n"
              f"Disallow: /data/\n\n"
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
