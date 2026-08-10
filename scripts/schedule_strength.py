#!/usr/bin/env python3
"""Strength of schedule, both ways, updating as the season is played.

    python3 scripts/schedule_strength.py
    python3 scripts/schedule_strength.py --season 2026 --from-week 15

TWO NUMBERS, BECAUSE THEY ANSWER DIFFERENT QUESTIONS

Opponent win percentage is the standard. It measures whether a schedule is
hard to WIN. Every outlet publishes it and a reader expects to see it.

Points allowed by position measures whether a schedule is hard to SCORE
FANTASY POINTS against, which is the question somebody setting a lineup
actually has. The two come apart badly. A team can have a top-five defense
and a losing record -- and by opponent win percentage it reads as an easy
matchup while being one of the harder ones to throw on.

So the page carries both, and says which is which.

WHAT UPDATES WHEN

Before week 1 there is nothing to measure this season, so both numbers use
last season: last year's records, last year's defences. That is a real
limitation and the page says so rather than presenting it as current.

From week 1 the current season blends in, weighted by how much of it has
been played. By week 8 the current season carries most of the weight; by
the end it carries all of it. The blend is shown, so nobody has to guess
whether they are looking at this year or last.

WHY WEEKS MATTER MORE THAN SEASONS

A full-season number is close to useless by October: half of it describes
games already played. The default window is the rest of the regular season,
and --from-week 15 gives the fantasy playoffs, which is what people plan
around in the first place.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ["QB", "RB", "WR", "TE"]
REGULAR = ("REG",)


def blend_weight(games_played, total=17):
    """How much this season counts, against last season.

    Linear in games played, which is crude and honest: after four games a
    defence has told you something but not much, and pretending otherwise
    is how a small sample becomes a confident wrong answer.
    """
    if not games_played:
        return 0.0
    return min(1.0, games_played / max(1, total))


def team_records(conn, season):
    """Wins, losses and ties per team, from games with a result."""
    rec = defaultdict(lambda: [0, 0, 0])
    for g in conn.execute(
            """SELECT home_team, away_team, home_score, away_score
               FROM games
               WHERE season=? AND game_type='REG' AND home_score IS NOT NULL""",
            (season,)):
        h, a, hs, as_ = g
        if hs > as_:
            rec[h][0] += 1
            rec[a][1] += 1
        elif as_ > hs:
            rec[a][0] += 1
            rec[h][1] += 1
        else:
            rec[h][2] += 1
            rec[a][2] += 1
    return {t: (w, l, d) for t, (w, l, d) in rec.items()}


def win_pct(rec):
    out = {}
    for t, (w, l, d) in rec.items():
        n = w + l + d
        out[t] = (w + d * 0.5) / n if n else 0.0
    return out


# Half PPR is derivable rather than stored: PPR is standard plus one point
# per catch, so half is exactly the midpoint. No third column needed.
FORMATS = {
    "ppr": "COALESCE(fantasy_points_ppr, 0)",
    "half": "(COALESCE(fantasy_points,0) + COALESCE(fantasy_points_ppr,0)) / 2.0",
    "std": "COALESCE(fantasy_points, 0)",
}


def points_allowed(conn, season, fmt="ppr"):
    """Fantasy points per game allowed by each defence, by position.

    Points scored BY players AGAINST a team, per game that team played.
    Only the regular season, and only positions the wire covers.

    Computed per scoring format, because a defence that gives up catches
    looks very different in PPR and standard -- and a page that showed one
    number for all three would be wrong for two of them.
    """
    # Which team each team faced, per week.
    opp = {}
    played = defaultdict(int)
    for g in conn.execute(
            """SELECT week, home_team, away_team, home_score
               FROM games WHERE season=? AND game_type='REG'""", (season,)):
        wk, h, a, hs = g
        opp[(season, wk, h)] = a
        opp[(season, wk, a)] = h
        if hs is not None:
            played[h] += 1
            played[a] += 1

    allowed = defaultdict(lambda: defaultdict(float))
    expr = FORMATS.get(fmt, FORMATS["ppr"])
    for r in conn.execute(
            f"""SELECT season, week, team, position, {expr} pts
               FROM weekly_stats
               WHERE season=? AND season_type='REG'
                 AND position IN ('QB','RB','WR','TE')""", (season,)):
        d = opp.get((r[0], r[1], (r[2] or "").upper()))
        if not d:
            continue
        allowed[d][r[3]] += r[4]

    out = {}
    for t, by_pos in allowed.items():
        n = played.get(t, 0)
        if not n:
            continue
        out[t] = {p: by_pos.get(p, 0.0) / n for p in POSITIONS}
    return out


def remaining(conn, season, from_week, to_week):
    """Each team's opponents in the window, and whether they are played."""
    out = defaultdict(list)
    for g in conn.execute(
            """SELECT week, home_team, away_team, home_score
               FROM games
               WHERE season=? AND game_type='REG' AND week BETWEEN ? AND ?
               ORDER BY week""", (season, from_week, to_week)):
        wk, h, a, hs = g
        out[h].append({"week": wk, "opp": a, "home": True,
                       "played": hs is not None})
        out[a].append({"week": wk, "opp": h, "home": False,
                       "played": hs is not None})
    return out


def build(conn, season, from_week, to_week):
    """Every team's remaining schedule, scored two ways."""
    prev = season - 1

    cur_rec = team_records(conn, season)
    prev_rec = team_records(conn, prev)
    cur_wp, prev_wp = win_pct(cur_rec), win_pct(prev_rec)

    cur_pa = {f: points_allowed(conn, season, f) for f in FORMATS}
    prev_pa = {f: points_allowed(conn, prev, f) for f in FORMATS}

    games_played = conn.execute(
        """SELECT COUNT(*) FROM games WHERE season=? AND game_type='REG'
           AND home_score IS NOT NULL""", (season,)).fetchone()[0]
    weeks_in = conn.execute(
        """SELECT COALESCE(MAX(week),0) FROM games WHERE season=?
           AND game_type='REG' AND home_score IS NOT NULL""",
        (season,)).fetchone()[0]
    w = blend_weight(weeks_in, 17)

    def opp_strength(t):
        """One opponent's difficulty, blended between seasons."""
        a = cur_wp.get(t)
        b = prev_wp.get(t)
        if a is None and b is None:
            return None
        if a is None:
            return b
        if b is None:
            return a
        return w * a + (1 - w) * b

    def opp_allowed(t, pos, fmt="ppr"):
        a = (cur_pa[fmt].get(t) or {}).get(pos)
        b = (prev_pa[fmt].get(t) or {}).get(pos)
        if a is None and b is None:
            return None
        if a is None:
            return b
        if b is None:
            return a
        return w * a + (1 - w) * b

    sched = remaining(conn, season, from_week, to_week)
    rows = []
    for t, games in sorted(sched.items()):
        ahead = [g for g in games if not g["played"]]
        # Before the season everything is ahead; after it, nothing is. Either
        # way the window is what has not happened yet inside it.
        window = ahead or []
        if not window:
            continue
        # Per game, not just the average.
        #
        # The page lets a reader pick a window -- rest of season, next four
        # weeks, the fantasy playoffs -- and a season-long average cannot be
        # narrowed after the fact. Shipping each opponent's difficulty lets
        # the client re-average for any window from one payload.
        games_out = []
        for g in window:
            entry = {"w": g["week"], "o": g["opp"],
                     "h": 1 if g["home"] else 0,
                     "wp": opp_strength(g["opp"])}
            for pos in POSITIONS:
                for f in FORMATS:
                    entry[f"{pos}_{f}"] = opp_allowed(g["opp"], pos, f)
                # The default format keeps its plain key, so nothing that
                # already reads entry["RB"] has to change.
                entry[pos] = entry[f"{pos}_ppr"]
            games_out.append(entry)

        wps = [x["wp"] for x in games_out if x["wp"] is not None]
        # Which weeks in the window this team is not playing. A three-game
        # four-week stretch is not the same as a four-game one, and a table
        # that shows only an average hides the difference.
        weeks_here = {g["week"] for g in window}
        span = range(from_week, to_week + 1)
        played_all = {g["week"] for g in games}
        byes = [w for w in span
                if w not in weeks_here and w not in played_all]

        row = {
            "team": t,
            "byes": byes,
            "games": len(window),
            "opp_win_pct": sum(wps) / len(wps) if wps else None,
            "sched": games_out,
            "home": sum(1 for g in window if g["home"]),
        }
        for pos in POSITIONS:
            for f in FORMATS:
                k = f"{pos}_{f}"
                vals = [x[k] for x in games_out if x[k] is not None]
                row[k] = sum(vals) / len(vals) if vals else None
            row[pos] = row[f"{pos}_ppr"]
        rows.append(row)

    # Rank each column. 1 is the easiest schedule -- the softest opponents
    # for win percentage, the most points allowed for a position, because a
    # defence that gives up points is a good matchup.
    def rank(key, reverse):
        vals = [(r[key], r["team"]) for r in rows if r.get(key) is not None]
        vals.sort(reverse=reverse)
        order = {t: i + 1 for i, (_, t) in enumerate(vals)}
        for r in rows:
            r[f"{key}_rank"] = order.get(r["team"])

    rank("opp_win_pct", reverse=False)      # lowest win pct = easiest
    for pos in POSITIONS:
        rank(pos, reverse=True)             # most points allowed = easiest

    return {
        "season": season,
        "from_week": from_week,
        "to_week": to_week,
        "weeks_played": weeks_in,
        "games_played": games_played,
        "blend": w,
        "prev_season": prev,
        "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--from-week", type=int, default=1)
    ap.add_argument("--to-week", type=int, default=18)
    ap.add_argument("--out", default="site/data/sos.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    db = ROOT / args.db
    if not db.exists():
        sys.exit(f"  no database at {db}")
    conn = sqlite3.connect(db)
    try:
        conn.execute("SELECT 1 FROM games LIMIT 1")
    except sqlite3.OperationalError:
        sys.exit("  no schedule. Run: python3 scripts/import_schedule.py")

    data = build(conn, args.season, args.from_week, args.to_week)
    if not data["rows"]:
        sys.exit(f"  no unplayed games in weeks "
                 f"{args.from_week}-{args.to_week} of {args.season}")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")))

    if args.quiet:
        print(f"  wrote {args.out}")
        return 0

    w = data["blend"]
    print(f"\n  {args.season} weeks {args.from_week}-{args.to_week}, "
          f"{len(data['rows'])} teams")
    if data["weeks_played"] == 0:
        print(f"  nothing played yet, so this is entirely "
              f"{data['prev_season']}")
    else:
        print(f"  {data['weeks_played']} weeks played: "
              f"{w:.0%} this season, {1-w:.0%} {data['prev_season']}")

    rows = [r for r in data["rows"] if r["opp_win_pct"] is not None]
    rows.sort(key=lambda r: r["opp_win_pct"])
    print(f"\n  EASIEST BY OPPONENT WIN PERCENTAGE\n")
    for r in rows[:5]:
        print(f"    {r['team']:<5}{r['opp_win_pct']:.3f}   "
              f"{r['games']} games")
    print(f"\n  HARDEST\n")
    for r in rows[-5:][::-1]:
        print(f"    {r['team']:<5}{r['opp_win_pct']:.3f}   "
              f"{r['games']} games")

    # The number the other one does not give you.
    for pos in ("RB", "WR"):
        pr = [r for r in data["rows"] if r.get(pos) is not None]
        if not pr:
            continue
        pr.sort(key=lambda r: -r[pos])
        print(f"\n  SOFTEST FOR {pos}, POINTS ALLOWED PER GAME\n")
        for r in pr[:5]:
            wr = r["opp_win_pct"]
            note = ""
            if wr is not None and r["opp_win_pct_rank"] and r[f"{pos}_rank"]:
                gap = r["opp_win_pct_rank"] - r[f"{pos}_rank"]
                if gap >= 8:
                    note = (f"   (win pct says "
                            f"{r['opp_win_pct_rank']}th easiest)")
            print(f"    {r['team']:<5}{r[pos]:>6.1f}{note}")

    print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
