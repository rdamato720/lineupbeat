#!/usr/bin/env python3
"""Resolver regression tests.

Run before every deploy: `python scripts/test_resolve.py`

The resolver is the only component whose failures are invisible. A dead feed
looks like a quiet news day; a bad extraction looks like a boring nugget; but
a misresolved player looks completely normal and is quietly wrong. These
cases exist so that stops being true.

Every case here came from a real ambiguity class, not an invented one.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatwire.registry import Registry
from beatwire.resolve import Resolver

# (sport, mention, team, position hint, expected name or None for "must refuse")
CASES = [
    # --- team scoping: the same surname on different teams -----------------
    ("nfl", "Allen",   "NYJ", None, "Braelon Allen"),
    ("nfl", "Allen",   "BUF", None, "Josh Allen"),
    # He plays as Josh Hines-Allen now. The resolver was right and the
    # expectation was stale, which is what a fixture does when a real
    # person changes his name.
    ("nfl", "Allen",   "JAX", None, "Josh Hines-Allen"),
    ("nfl", "Allen",   None,  None, None),   # league-wide: genuinely ambiguous

    # --- aliases: punctuation, accents, suffixes ---------------------------
    ("nfl", "AJ Brown",   "PHI", None, "A.J. Brown"),
    ("nfl", "A.J. Brown", "PHI", None, "A.J. Brown"),
    ("mlb", "Acuna",      "ATL", None, "Ronald Acuna Jr."),
    ("mlb", "Acuña",      "ATL", None, "Ronald Acuna Jr."),
    ("mlb", "Alvarado",   "PHI", "P",  "Jose Alvarado"),

    # --- MLB: same surname, same team. Position hint is the only signal ----
    ("mlb", "Naylor", "ATL", None, None),          # must refuse
    ("mlb", "Naylor", "ATL", "C",  "Bo Naylor"),
    ("mlb", "Naylor", "ATL", "IF", "Josh Naylor"),
    ("mlb", "Naylor", "ATL", "P",  None),          # neither is a pitcher

    # --- a name whose alias normalizes identically must not self-collide ---
    ("nfl", "CeeDee Lamb",  "DAL", None, "CeeDee Lamb"),
    ("nfl", "Ceedee Lamb",  "DAL", None, "CeeDee Lamb"),
    ("nfl", "DeVonta Smith", "PHI", None, "DeVonta Smith"),
    ("mlb", "Ronald Acuna Jr.", "ATL", None, "Ronald Acuna Jr."),

    # --- unambiguous surnames still resolve --------------------------------
    ("mlb", "Judge",  "NYY", None, "Aaron Judge"),
    ("mlb", "Cole",   "NYY", "P",  "Gerrit Cole"),
    ("mlb", "Ohtani", None,  None, "Shohei Ohtani"),

    # --- a lone candidate is not rejected for a position mismatch ----------
    ("mlb", "Betts",  "LAD", "OF", "Mookie Betts"),

    # --- garbage in, nothing out -------------------------------------------
    ("nfl", "",             "NYJ", None, None),
    ("nfl", "the offense",  "NYJ", None, None),
    ("nfl", "Zzzzz",        "NYJ", None, None),
("nfl", "Harrison Bryant", "SEA", None, None),
]


def main() -> int:
    resolvers = {}
    for sport in {c[0] for c in CASES}:
        reg = Registry(sport)
        resolvers[sport] = Resolver(reg.players, reg.profile.position_groups)

    failures = []
    for sport, mention, team, pos, expected in CASES:
        player, conf = resolvers[sport].resolve(mention, team, pos)
        got = player.name if player else None
        ok = got == expected
        if not ok:
            failures.append((sport, mention, team, pos, expected, got))
        mark = "  ok " if ok else "FAIL"
        print(f"[{mark}] {sport} {mention!r:<16} team={str(team):<5} "
              f"pos={str(pos):<5} -> {str(got):<20} conf={conf:.2f}")

    print()
    if failures:
        print(f"{len(failures)} of {len(CASES)} failed:")
        for sport, m, t, p, exp, got in failures:
            print(f"  {sport} {m!r} team={t} pos={p}: expected {exp}, got {got}")
        return 1
    print(f"all {len(CASES)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
