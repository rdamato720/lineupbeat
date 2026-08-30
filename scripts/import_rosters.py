#!/usr/bin/env python3
"""Build rosters/<sport>.csv from public, unauthenticated APIs.

    python scripts/import_rosters.py nfl
    python scripts/import_rosters.py mlb
    python scripts/import_rosters.py nfl mlb --check

Neither source needs a key or a scraper.

  NFL  Sleeper's player dump. One large JSON object keyed by player id.
  MLB  MLB's stats API. One call for teams, then one roster call per team.

I could not reach either host from where this was written, so the response
shapes below are from documentation and prior use rather than a live call.
Run with --check first: it prints a sample of what it parsed so you can eyeball
the mapping before it writes anything. If a field name has drifted, the fix is
in the two `_parse_*` functions and nowhere else.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
import json
import re
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "beatwire-roster-import/1.0"}

SLEEPER_PLAYERS = "https://api.sleeper.app/v1/players/nfl"
MLB_TEAMS = "https://statsapi.mlb.com/api/v1/teams?sportId=1"
MLB_ROSTER = "https://statsapi.mlb.com/api/v1/teams/{id}/roster?rosterType=40Man"


def get_json(url: str, timeout: int = 90):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

SUFFIX_RE = re.compile(r"\s+(?:Jr\.?|Sr\.?|I{2,3}|IV|V)$", re.I)


def strip_accents(s: str) -> str:
    n = unicodedata.normalize("NFKD", s)
    return "".join(c for c in n if not unicodedata.combining(c))


def aliases_for(name: str) -> list[str]:
    """Generate the spellings a beat writer might actually use.

    This is not cosmetic. Accented surnames are everywhere in MLB and are
    inconsistently accented across outlets, initials get written both ways,
    and generational suffixes appear and vanish depending on the writer.
    Every one of these is a resolution failure if it is not covered.
    """
    out = set()
    plain = strip_accents(name)
    if plain != name:
        out.add(plain)

    for base in {name, plain}:
        # A.J. Brown <-> AJ Brown
        if re.search(r"\b[A-Z]\.[A-Z]\.", base):
            out.add(re.sub(r"\b([A-Z])\.([A-Z])\.", r"\1\2", base))
        elif re.match(r"^[A-Z]{2}\s", base):
            out.add(re.sub(r"^([A-Z])([A-Z])\s", r"\1.\2. ", base))

        # with and without the generational suffix
        no_suffix = SUFFIX_RE.sub("", base)
        if no_suffix != base:
            out.add(no_suffix)

    out.discard(name)
    return sorted(a for a in out if a.strip())


# ---------------------------------------------------------------------------
# NFL
# ---------------------------------------------------------------------------

SKILL = {"QB", "RB", "WR", "TE", "K", "FB"}

# Sleeper lists team defenses as players named "Kansas City Chiefs". Their
# surname is the team nickname, so any sentence containing "Chiefs" resolves
# to a player and floods the feed with team-level noise. Fantasy DST is a real
# concept, but it does not belong in a beat-mention roster.
EXCLUDE_POSITIONS = {"DEF", "DST"}

# Legacy franchise codes Sleeper still emits. Map them forward or team scoping
# breaks for whichever team is split across two codes.
TEAM_ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LAR", "WSH": "WAS", "JAC": "JAX"}


def _stale(p: dict) -> bool:
    """Sleeper never marks retirees. `active` and `status` both say Active for
    players who left years ago (Le'Veon Bell, listed on TB, still says
    Active). What does give it away is that `age` freezes when the record
    stops being maintained, so it drifts from birth_date by exactly the number
    of years since the player left.

    The separation is clean rather than fuzzy: on a real dump, 2,766 players
    show zero drift and the next cluster sits at four and five years. Nothing
    legitimate lands in between, so a one-year tolerance is safe and covers
    ordinary birthday timing.
    """
    bd, age = p.get("birth_date"), p.get("age")
    if not bd or not isinstance(age, int):
        return False                      # no evidence either way, keep it
    try:
        y, m, d = map(int, bd.split("-"))
    except ValueError:
        return False
    today = date.today()
    real = today.year - y - ((today.month, today.day) < (m, d))
    return abs(real - age) > 1


def _parse_nfl(blob: dict, skill_only: bool) -> list[dict]:
    rows = []
    for pid, p in blob.items():
        if not isinstance(p, dict):
            continue
        team = p.get("team")
        pos = p.get("position")
        name = p.get("full_name") or " ".join(
            x for x in [p.get("first_name"), p.get("last_name")] if x
        )
        if not (team and pos and name):
            continue                      # free agents and practice bodies
        # Every retired player left in is a false candidate the resolver has
        # to beat, which directly costs resolution accuracy.
        if _stale(p):
            continue
        # NO status whitelist. Sleeper sets status="Inactive" for players on
        # IR, and those are the single most newsworthy group there is: Ricky
        # Pearsall went on season-ending IR and silently vanished from the
        # roster, so eight beat reports about him could not resolve. Filtering
        # on status drops precisely the players this product exists to cover.
        # Free agents are already excluded by the team check above, and
        # retirees by the age-drift check, so status adds nothing but harm.
        team = TEAM_ALIASES.get(team.strip().upper(), team.strip().upper())
        if pos.strip().upper() in EXCLUDE_POSITIONS:
            continue
        if skill_only and pos not in SKILL:
            continue
        rows.append({
            "id": f"nfl-{pid}",
            "name": name.strip(),
            "team": team,
            "position": pos.strip().upper(),
            "aliases": "|".join(aliases_for(name.strip())),
            # Kept only as a headshot fallback. Sleeper's own CDN is keyed on
            # the id above, so this is the second try when that 404s.
            "espn_id": str(p.get("espn_id") or ""),
            # Sleeper's fantasy relevance ordering: lower is more notable.
            # Used to decide which stories get played big, so a backup tight
            # end does not lead over George Kittle.
            "rank": str(p.get("search_rank") or ""),
            # Official depth chart. Two uses: label a card "RB2" for free
            # context, and cross-check our own extraction. When a writer says
            # a player took first-team reps and the depth chart still has him
            # third, that gap is the story rather than a contradiction.
            "depth_pos": str(p.get("depth_chart_position") or ""),
            "depth_order": str(p.get("depth_chart_order") or ""),
            # Sleeper's own injury designation. Not for republishing -- that is
            # the commodity layer everyone already has -- but a coverage check:
            # a player Sleeper lists as out with nothing in our feed is a hole.
            "injury_status": str(p.get("injury_status") or ""),
            "years_exp": str(p.get("years_exp") if p.get("years_exp") is not None else ""),
            # Age was being dropped, which silently disabled the age curve --
            # a thirty-year-old back with 450 touches was projected like a
            # twenty-four-year-old. Sleeper carries it directly.
            "age": str(p.get("age") or ""),
        })
    return rows


def fetch_nfl(skill_only: bool = False) -> list[dict]:
    print("  fetching Sleeper player dump (large, be patient)")
    return _parse_nfl(get_json(SLEEPER_PLAYERS), skill_only)


# ---------------------------------------------------------------------------
# MLB
# ---------------------------------------------------------------------------

def _parse_mlb_roster(payload: dict, abbrev: str) -> list[dict]:
    rows = []
    for entry in payload.get("roster", []):
        person = entry.get("person", {}) or {}
        name = person.get("fullName")
        pid = person.get("id")
        pos = (entry.get("position", {}) or {}).get("abbreviation", "")
        if not (name and pid):
            continue
        rows.append({
            "id": f"mlb-{pid}",
            "name": name.strip(),
            "team": abbrev,
            "position": pos.strip().upper(),
            "aliases": "|".join(aliases_for(name.strip())),
        })
    return rows


def fetch_mlb() -> list[dict]:
    teams = get_json(MLB_TEAMS).get("teams", [])
    teams = [t for t in teams if t.get("active", True) and t.get("abbreviation")]
    print(f"  {len(teams)} teams")
    rows = []
    for t in teams:
        abbrev = t["abbreviation"].upper()
        try:
            payload = get_json(MLB_ROSTER.format(id=t["id"]))
        except Exception as exc:
            print(f"  ! {abbrev}: {exc}")
            continue
        got = _parse_mlb_roster(payload, abbrev)
        rows.extend(got)
        print(f"  {abbrev:<4} {len(got):>3} players")
        time.sleep(0.25)          # be a good citizen, it is a free API
    return rows


# ---------------------------------------------------------------------------

FETCHERS = {"nfl": fetch_nfl, "mlb": fetch_mlb}


def report(sport: str, rows: list[dict]) -> None:
    """Surface the things that will bite the resolver later."""
    from collections import Counter

    teams = sorted(set(r["team"] for r in rows))
    print(f"\n  {len(rows)} players, {len(teams)} teams")
    print(f"  team codes: {' '.join(teams)}")
    if len(teams) != 32 and sport == "nfl":
        print(f"  ! expected 32 NFL teams. Odd codes here are usually free "
              f"agents or a placeholder, and should be dropped.")
    print(f"  positions: {sorted(set(r['position'] for r in rows))}")

    surnames = Counter(
        (r["team"], strip_accents(r["name"]).split()[-1].lower()) for r in rows
    )
    dupes = [(k, v) for k, v in surnames.items() if v > 1]
    print(f"  same-surname collisions within a team: {len(dupes)}")
    for (team, sn), n in sorted(dupes, key=lambda x: -x[1])[:8]:
        who = [r["name"] for r in rows
               if r["team"] == team and strip_accents(r["name"]).split()[-1].lower() == sn]
        print(f"    {team} {sn}: {', '.join(who)}")
    if dupes:
        print("  ^ these are the mentions team scoping alone cannot resolve.")
        print("    Position hints handle most; the rest should go to review.")

    print("\n  sample:")
    for r in rows[:5]:
        print(f"    {r['id']:<14} {r['name']:<24} {r['team']:<4} "
              f"{r['position']:<4} {r['aliases']}")


def write(sport: str, rows: list[dict]) -> Path:
    for r in rows:
        for k in ("espn_id", "rank", "depth_pos", "depth_order",
                  "injury_status", "years_exp", "age"):
            r.setdefault(k, "")
    rows.sort(key=lambda r: (r["team"], r["name"]))
    path = ROOT / "rosters" / f"{sport}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["id", "name", "team", "position", "aliases",
                            "espn_id", "rank", "depth_pos", "depth_order",
                            "injury_status", "years_exp", "age"],
            extrasaction="ignore", lineterminator="\n",
        )
        w.writeheader()
        w.writerows(rows)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sports", nargs="+", choices=sorted(FETCHERS))
    ap.add_argument("--check", action="store_true",
                    help="parse and report, write nothing")
    ap.add_argument("--skill-only", action="store_true",
                    help="NFL: keep QB/RB/WR/TE/K only")
    args = ap.parse_args()

    for sport in args.sports:
        print(f"\n=== {sport.upper()} ===")
        try:
            rows = (FETCHERS[sport](args.skill_only)
                    if sport == "nfl" else FETCHERS[sport]())
        except Exception as exc:
            print(f"  failed: {exc}")
            print("  If this is a KeyError the response shape moved. "
                  f"Fix _parse_{sport}() and rerun.")
            sys.exit(1)

        if not rows:
            print("  parsed zero players. Check the mapping before writing.")
            sys.exit(1)

        report(sport, rows)
        if args.check:
            print("\n  --check, nothing written")
        else:
            print(f"\n  wrote {write(sport, rows)}")


if __name__ == "__main__":
    main()
