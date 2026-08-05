#!/usr/bin/env python3
"""Generate sources/<sport>.yaml for all 32 NFL and 30 MLB teams.

    python scripts/build_registry.py nfl mlb
    python -m beatwire.cli doctor --sport nfl      # team codes must line up
    python -m beatwire.cli verify --sport nfl --fix

Two feed families are generated from verified URL patterns:

  SB Nation   https://www.<site>.com/rss/index.xml
  MLB.com     https://www.mlb.com/<slug>/feeds/news/rss.xml

A third slot per team is emitted disabled, with the team name in a TODO. That
is the local daily or beat podcast, which has no pattern and has to be found
by hand. It is deliberately left as structured, obvious, delegatable work:
hand this file to someone, tell them to fill in the TODOs and get `verify`
to pass, and the acceptance test is objective.

Team codes below MUST match the codes your roster import produces, or team
scoping silently stops working and every bare surname goes unresolved. Run
`doctor` after generating. It exists because that failure is invisible.

Concentration warning: SB Nation is one company, and Vox Media has been
reported to be exploring a sale of it. Do not let it become your only source
family. The third slot per team is how you avoid that.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# (team_code, sbnation_site, display name)
NFL_TEAMS = [
    ("BUF", "buffalorumblings",      "Bills"),
    ("MIA", "thephinsider",          "Dolphins"),
    ("NE",  "patspulpit",            "Patriots"),
    ("NYJ", "ganggreennation",       "Jets"),
    ("BAL", "baltimorebeatdown",     "Ravens"),
    ("CIN", "cincyjungle",           "Bengals"),
    ("CLE", "dawgsbynature",         "Browns"),
    ("PIT", "behindthesteelcurtain", "Steelers"),
    ("HOU", "battleredblog",         "Texans"),
    ("IND", "stampedeblue",          "Colts"),
    ("JAX", "bigcatcountry",         "Jaguars"),
    ("TEN", "musiccitymiracles",     "Titans"),
    ("DEN", "milehighreport",        "Broncos"),
    ("KC",  "arrowheadpride",        "Chiefs"),
    ("LV",  "silverandblackpride",   "Raiders"),
    ("LAC", "boltsfromtheblue",      "Chargers"),
    ("DAL", "bloggingtheboys",       "Cowboys"),
    ("NYG", "bigblueview",           "Giants"),
    ("PHI", "bleedinggreennation",   "Eagles"),
    ("WAS", "hogshaven",             "Commanders"),
    ("CHI", "windycitygridiron",     "Bears"),
    ("DET", "prideofdetroit",        "Lions"),
    ("GB",  "acmepackingcompany",    "Packers"),
    ("MIN", "dailynorseman",         "Vikings"),
    ("ATL", "thefalcoholic",         "Falcons"),
    ("CAR", "catscratchreader",      "Panthers"),
    ("NO",  "canalstreetchronicles", "Saints"),
    ("TB",  "bucsnation",            "Buccaneers"),
    ("ARI", "revengeofthebirds",     "Cardinals"),
    ("LAR", "turfshowtimes",         "Rams"),
    ("SF",  "ninersnation",          "49ers"),
    ("SEA", "fieldgulls",            "Seahawks"),
]

# (team_code, sbnation_site, mlb_com_slug, display name)
# Team codes follow the MLB stats API. The awkward ones are called out in
# ALIASES below because they have changed and older sources disagree.
MLB_TEAMS = [
    ("BAL", "camdenchat",        "orioles",   "Orioles"),
    ("BOS", "overthemonster",    "redsox",    "Red Sox"),
    ("NYY", "pinstripealley",    "yankees",   "Yankees"),
    ("TB",  "draysbay",          "rays",      "Rays"),
    ("TOR", "bluebirdbanter",    "bluejays",  "Blue Jays"),
    ("CWS", "southsidesox",      "whitesox",  "White Sox"),
    ("CLE", "letsgotribe",       "guardians", "Guardians"),
    ("DET", "blessyouboys",      "tigers",    "Tigers"),
    ("KC",  "royalsreview",      "royals",    "Royals"),
    ("MIN", "twinkietown",       "twins",     "Twins"),
    ("HOU", "crawfishboxes",     "astros",    "Astros"),
    ("LAA", "halosheaven",       "angels",    "Angels"),
    ("ATH", "athleticsnation",   "athletics", "Athletics"),
    ("SEA", "lookoutlanding",    "mariners",  "Mariners"),
    ("TEX", "lonestarball",      "rangers",   "Rangers"),
    ("ATL", "batterypower",      "braves",    "Braves"),
    ("MIA", "fishstripes",       "marlins",   "Marlins"),
    ("NYM", "amazinavenue",      "mets",      "Mets"),
    ("PHI", "thegoodphight",     "phillies",  "Phillies"),
    ("WSH", "federalbaseball",   "nationals", "Nationals"),
    ("CHC", "bleedcubbieblue",   "cubs",      "Cubs"),
    ("CIN", "redreporter",       "reds",      "Reds"),
    ("MIL", "brewcrewball",      "brewers",   "Brewers"),
    ("PIT", "bucsdugout",        "pirates",   "Pirates"),
    ("STL", "vivaelbirdos",      "cardinals", "Cardinals"),
    ("AZ",  "azsnakepit",        "dbacks",    "Diamondbacks"),
    ("COL", "purplerow",         "rockies",   "Rockies"),
    ("LAD", "truebluela",        "dodgers",   "Dodgers"),
    ("SD",  "gaslampball",       "padres",    "Padres"),
    ("SF",  "mccoveychronicles", "giants",    "Giants"),
]

# Codes that differ between data sources. `doctor` uses these to tell you
# "your roster says OAK, your registry says ATH" instead of silently
# resolving nothing for that team all season.
ALIASES = {
    "mlb": {"OAK": "ATH", "CHW": "CWS", "ARI": "AZ", "WAS": "WSH", "SDP": "SD",
            "SFG": "SF", "TBR": "TB", "KCR": "KC", "CHW ": "CWS"},
    "nfl": {"WSH": "WAS", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR",
            "JAC": "JAX"},
}


def sbnation(code: str, site: str, name: str, sport: str) -> dict:
    return {
        "id": f"{sport}-{code.lower()}-sbn",
        "kind": "rss",
        "url": f"https://www.{site}.com/rss/index.xml",
        "name": f"{name} community beat",
        "outlet": "SB Nation",
        "teams": [code],
    }


def mlb_official(code: str, slug: str, name: str) -> dict:
    return {
        "id": f"mlb-{code.lower()}-official",
        "kind": "rss",
        "url": f"https://www.mlb.com/{slug}/feeds/news/rss.xml",
        "name": f"{name} team site",
        "outlet": "MLB.com",
        "teams": [code],
    }


def local_slot(code: str, name: str, sport: str) -> dict:
    """The hand-research slot. Disabled until someone fills in a real URL."""
    return {
        "id": f"{sport}-{code.lower()}-local",
        "kind": "rss",
        "url": f"TODO-find-local-beat-feed-for-{name.lower().replace(' ', '-')}",
        "name": f"TODO {name} local beat",
        "outlet": "TODO",
        "teams": [code],
        "enabled": False,
    }


def build(sport: str) -> dict:
    sources = []
    if sport == "nfl":
        for code, site, name in NFL_TEAMS:
            sources.append(sbnation(code, site, name, "nfl"))
            sources.append(local_slot(code, name, "nfl"))
    elif sport == "mlb":
        for code, site, slug, name in MLB_TEAMS:
            sources.append(sbnation(code, site, name, "mlb"))
            sources.append(mlb_official(code, slug, name))
            sources.append(local_slot(code, name, "mlb"))
    else:
        raise ValueError(sport)
    return {"sources": sources}


HEADER = """# {display} source registry - GENERATED by scripts/build_registry.py
#
# Regenerating replaces the `sources` list and preserves `profile`.
#
# URLs come from two verified patterns (SB Nation /rss/index.xml and
# MLB.com /feeds/news/rss.xml), NOT from a live check of each one. Feeds rot.
# Before trusting any of this:
#
#   python -m beatwire.cli doctor --sport {sport}
#   python -m beatwire.cli verify --sport {sport} --fix
#
# The disabled `-local` entries are the hand-research slots: one local daily
# or beat podcast per team. Filling those in is the single highest-value
# manual task in this project, and the one worth paying someone to do.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sports", nargs="+", choices=["nfl", "mlb"])
    args = ap.parse_args()

    for sport in args.sports:
        path = ROOT / "sources" / f"{sport}.yaml"

        profile = {}
        if path.exists():
            existing = yaml.safe_load(path.read_text()) or {}
            profile = existing.get("profile", {})
        if not profile:
            print(f"  ! {sport}: no existing profile block to preserve. "
                  f"Write one before generating, or the extractor loses its "
                  f"sport guidance.")
            sys.exit(1)

        doc = {"profile": profile, **build(sport)}
        body = yaml.dump(doc, sort_keys=False, allow_unicode=True, width=100)
        display = profile.get("display", sport.upper())
        path.write_text(HEADER.format(display=display, sport=sport) + "\n" + body)

        srcs = doc["sources"]
        live = [s for s in srcs if s.get("enabled", True)]
        todo = [s for s in srcs if not s.get("enabled", True)]
        teams = {t for s in srcs for t in s["teams"]}
        print(f"  {sport}: {len(teams)} teams, {len(srcs)} sources "
              f"({len(live)} generated, {len(todo)} TODO slots) -> {path}")


if __name__ == "__main__":
    main()
