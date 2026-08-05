#!/usr/bin/env python3
"""Find NFL team podcast feeds, then pull an episode's audio URL to test.

    python3 scripts/find_podcasts.py --team NYJ
    python3 scripts/find_podcasts.py --team NYJ --episodes 3
    python3 scripts/find_podcasts.py --all --add        # write them to the registry

Uses Apple's public search endpoint, which returns the real RSS `feedUrl` for
a podcast and needs no key. That is the honest way to find these: podcast RSS
is a published standard and the feed is meant to be polled, unlike scraping a
web page.

Why this matters more than the X clips: those were 8-85 second fragments where
the writer had already put the fact in the caption. A team podcast or local
radio show is twenty minutes of a beat writer talking through a practice that
nobody wrote down. If audio is worth anything, it is here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import feedparser

ROOT = Path(__file__).resolve().parent.parent
SEARCH = "https://itunes.apple.com/search"

TEAM_QUERY = {
    "ARI": "Arizona Cardinals podcast", "ATL": "Atlanta Falcons podcast",
    "BAL": "Baltimore Ravens podcast", "BUF": "Buffalo Bills podcast",
    "CAR": "Carolina Panthers podcast", "CHI": "Chicago Bears podcast",
    "CIN": "Cincinnati Bengals podcast", "CLE": "Cleveland Browns podcast",
    "DAL": "Dallas Cowboys podcast", "DEN": "Denver Broncos podcast",
    "DET": "Detroit Lions podcast", "GB": "Green Bay Packers podcast",
    "HOU": "Houston Texans podcast", "IND": "Indianapolis Colts podcast",
    "JAX": "Jacksonville Jaguars podcast", "KC": "Kansas City Chiefs podcast",
    "LV": "Las Vegas Raiders podcast", "LAC": "Los Angeles Chargers podcast",
    "LAR": "Los Angeles Rams podcast", "MIA": "Miami Dolphins podcast",
    "MIN": "Minnesota Vikings podcast", "NE": "New England Patriots podcast",
    "NO": "New Orleans Saints podcast", "NYG": "New York Giants podcast",
    "NYJ": "New York Jets podcast", "PHI": "Philadelphia Eagles podcast",
    "PIT": "Pittsburgh Steelers podcast", "SF": "San Francisco 49ers podcast",
    "SEA": "Seattle Seahawks podcast", "TB": "Tampa Bay Buccaneers podcast",
    "TEN": "Tennessee Titans podcast", "WAS": "Washington Commanders podcast",
}


def search(term: str, limit: int = 6) -> list[dict]:
    url = f"{SEARCH}?" + urllib.parse.urlencode(
        {"term": term, "entity": "podcast", "limit": limit, "country": "US"})
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())
    return [
        {"name": x.get("collectionName", ""), "feed": x.get("feedUrl", ""),
         "artist": x.get("artistName", "")}
        for x in data.get("results", []) if x.get("feedUrl")
    ]


def episodes(feed_url: str, limit: int = 3) -> list[dict]:
    feed = feedparser.parse(feed_url)
    out = []
    for e in feed.entries[:limit]:
        audio = None
        for l in getattr(e, "links", []):
            if (l.get("type") or "").startswith("audio"):
                audio = l.get("href")
                break
        if not audio:
            for enc in getattr(e, "enclosures", []):
                audio = enc.get("href") or enc.get("url")
                break
        secs = None
        dur = getattr(e, "itunes_duration", "") or ""
        if dur.isdigit():
            secs = int(dur)
        elif ":" in dur:
            parts = [int(p) for p in dur.split(":") if p.isdigit()]
            secs = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
        out.append({
            "title": getattr(e, "title", "")[:70],
            "published": getattr(e, "published", "")[:22],
            "audio": audio, "secs": secs,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", help="team code, e.g. NYJ")
    ap.add_argument("--all", action="store_true", help="search every team")
    ap.add_argument("--episodes", type=int, default=0,
                    help="also list this many recent episodes per feed")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    teams = list(TEAM_QUERY) if args.all else [args.team] if args.team else []
    if not teams:
        sys.exit("  pass --team NYJ or --all")

    for code in teams:
        q = TEAM_QUERY.get(code.upper())
        if not q:
            print(f"  unknown team {code}")
            continue
        print(f"\n=== {code.upper()} ===")
        try:
            hits = search(q, args.limit)
        except Exception as exc:
            print(f"  search failed: {exc}")
            continue
        if not hits:
            print("  nothing found")
            continue

        for i, h in enumerate(hits, 1):
            print(f"  {i}. {h['name'][:52]:<52} {h['artist'][:22]}")
            print(f"     {h['feed']}")
            if args.episodes:
                try:
                    for e in episodes(h["feed"], args.episodes):
                        mins = f"{e['secs']//60}m" if e["secs"] else "?"
                        print(f"       - {e['published']:<22} {mins:>5}  {e['title']}")
                        if e["audio"]:
                            # Never truncate: the whole point of printing this
                            # is that it gets copied into the next command.
                            print(f"         {e['audio']}")
                except Exception as exc:
                    print(f"       (feed error: {exc})")
        time.sleep(0.3)

    print("\n  To test one, copy an episode audio url and run:")
    print("    python3 scripts/try_transcribe.py --url <audio-url> --team NYJ \\")
    print("           --model small --minutes 6")
    print("\n  Cap the minutes. A 45 minute episode on CPU takes a while, and the")
    print("  first six minutes of a practice-day show is enough to tell you")
    print("  whether the content is dense or not.")


if __name__ == "__main__":
    main()
