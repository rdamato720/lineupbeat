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
import html
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

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
<style>
  :root{{color-scheme:dark}}
  body{{background:#0A0C08;color:#E8E6E1;font:16px/1.6 Georgia,serif;
       margin:0;padding:2rem 1.25rem;max-width:44rem;margin-inline:auto}}
  a{{color:#C6F24E}}
  h1{{font-size:1.9rem;margin:0 0 .25rem;letter-spacing:-.01em}}
  .sub{{color:#8A8F85;font-size:.95rem;margin:0 0 2rem}}
  article{{border-top:1px solid #23261F;padding:1.1rem 0}}
  .claim{{margin:0 0 .4rem}}
  .meta{{color:#8A8F85;font-size:.82rem;font-family:system-ui,sans-serif}}
  nav{{margin-top:2.5rem;padding-top:1.25rem;border-top:1px solid #23261F;
      font-size:.9rem}}
  footer{{margin-top:2rem;color:#8A8F85;font-size:.8rem}}
</style>
</head>
<body>
<h1>{heading}</h1>
<p class="sub">{subhead}</p>
{body}
<nav>{nav}</nav>
<footer>
  <p>Every claim is paraphrased in our own words and linked back to the
  reporter who filed it. We never reproduce their copy.</p>
  <p><a href="/">LineupBeat</a> &mdash; local beat reporting from every NFL market.</p>
</footer>
</body>
</html>
"""


def player_page(p, nuggets, base):
    name = p["name"]
    team = p.get("team") or ""
    pos = p.get("pos") or ""
    url = f"{base}/player/{slug(name)}/"
    latest = nuggets[0]
    desc = (latest["claim"] or "")[:150]
    who = f"{pos} for the {TEAM_NAMES.get(team, team)}" if team else pos

    arts = []
    for n in nuggets[:30]:
        srcs = n.get("attributions") or []
        src = srcs[0] if srcs else {}
        link = src.get("url") or ""
        credit = src.get("source_name") or src.get("outlet") or "beat report"
        more = ""
        if len(srcs) > 1:
            more = f" and {len(srcs) - 1} other" + ("s" if len(srcs) > 2 else "")
        arts.append(
            f'<article>\n'
            f'  <p class="claim">{esc(n["claim"])}</p>\n'
            f'  <p class="meta">{esc(when(n["published_at"]))} &middot; '
            f'{f"<a href={chr(34)}{esc(link)}{chr(34)} rel={chr(34)}nofollow noopener{chr(34)}>" if link else ""}'
            f'{esc(credit)}{"</a>" if link else ""}{esc(more)}</p>\n'
            f'</article>'
        )

    ld = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": name,
        "url": url,
        "jobTitle": "Professional football player",
        **({"affiliation": {"@type": "SportsTeam",
                            "name": TEAM_NAMES.get(team, team)}} if team else {}),
    }
    nav = ""
    if team:
        nav = (f'<a href="/team/{slug(team)}/">More {TEAM_NAMES.get(team, team)} '
               f'beat reports</a>')

    return PAGE.format(
        title=esc(f"{name} news and beat reports | LineupBeat"),
        description=esc(desc),
        canonical=esc(url),
        og_type="profile",
        structured=f'<script type="application/ld+json">{json.dumps(ld)}</script>',
        heading=esc(name),
        subhead=esc(f"{who}. {len(nuggets)} beat report"
                    f"{'s' if len(nuggets) != 1 else ''}, most recent first."),
        body="\n".join(arts),
        nav=nav,
    )


def team_page(team, players, count, base):
    full = TEAM_NAMES.get(team, team)
    url = f"{base}/team/{slug(team)}/"
    links = "\n".join(
        f'<article><p class="claim"><a href="/player/{slug(n)}/">{esc(n)}</a>'
        f'</p><p class="meta">{c} report{"s" if c != 1 else ""}</p></article>'
        for n, c in players)
    ld = {"@context": "https://schema.org", "@type": "SportsTeam",
          "name": full, "url": url}
    return PAGE.format(
        title=esc(f"{full} beat reports and player news | LineupBeat"),
        description=esc(f"Local beat reporting on the {full}, matched to players "
                        f"and updated through the day."),
        canonical=esc(url),
        og_type="website",
        structured=f'<script type="application/ld+json">{json.dumps(ld)}</script>',
        heading=esc(full),
        subhead=esc(f"{count} beat reports across {len(players)} players."),
        body=links,
        nav='<a href="/">All 32 teams on the wire</a>',
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="site/data/feed.json")
    ap.add_argument("--base", default="https://lineupbeat.com")
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--min-reports", type=int, default=1,
                    help="skip players below this; thin pages help nobody")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    feed = Path(args.feed)
    if not feed.exists():
        sys.exit(f"  no {feed} — run the export first")
    data = json.loads(feed.read_text())
    nuggets = data["sports"][args.sport]["nuggets"]
    players = {p["id"]: p for p in data["players"] if p.get("sport") == args.sport}

    by_player = defaultdict(list)
    for n in nuggets:
        if n.get("player_id") and n.get("resolved"):
            by_player[n["player_id"]].append(n)
    for v in by_player.values():
        v.sort(key=lambda n: n["published_at"], reverse=True)

    base = args.base.rstrip("/")
    written, urls = 0, []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for pid, ns in by_player.items():
        p = players.get(pid)
        if not p or len(ns) < args.min_reports:
            continue
        path = SITE / "player" / slug(p["name"]) / "index.html"
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(player_page(p, ns, base))
        urls.append((f"{base}/player/{slug(p['name'])}/",
                     ns[0]["published_at"][:10], "daily", "0.8"))
        written += 1

    by_team = defaultdict(list)
    for pid, ns in by_player.items():
        p = players.get(pid)
        if p and p.get("team"):
            by_team[p["team"]].append((p["name"], len(ns)))
    teams_written = 0
    for team, plist in by_team.items():
        plist.sort(key=lambda x: -x[1])
        total = sum(c for _, c in plist)
        path = SITE / "team" / slug(team) / "index.html"
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(team_page(team, plist, total, base))
        urls.append((f"{base}/team/{slug(team)}/", now, "daily", "0.7"))
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
