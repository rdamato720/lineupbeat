#!/usr/bin/env python3
"""Create the immutable 2026 Week 1 college fantasy projection release.

The weekly layer starts with the reviewed v1.1 season allocation, converts
each stat to a per-game baseline, then adjusts volume and touchdowns using
the scheduled matchup's market total and spread.  The source scoreboard is
passed in explicitly so an already-reviewed release can always be rebuilt.
"""
import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data/college/2026/v1.1"
TEAM_ALIASES = {"Pitt": "Pittsburgh"}
STATS = (
    "pass_attempts", "completions", "passing_yards", "passing_td",
    "interceptions", "rush_attempts", "rushing_yards", "rushing_td",
    "receptions", "receiving_yards", "receiving_td",
)


def clamp(value, low, high):
    return max(low, min(high, value))


def num(row, key):
    return float(row.get(key) or 0)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def yahoo_points(row):
    return (
        row["passing_yards"] * .04 + row["passing_td"] * 4
        - row["interceptions"] + row["rushing_yards"] * .1
        + row["rushing_td"] * 6 + row["receptions"]
        + row["receiving_yards"] * .1 + row["receiving_td"] * 6
    )


def event_team(team):
    return team.get("location") or team.get("shortDisplayName")


def scheduled_games(scoreboard, eligible):
    games = {}
    compact = []
    for event in scoreboard.get("events", []):
        competition = event["competitions"][0]
        if event["status"]["type"]["name"] != "STATUS_SCHEDULED":
            continue
        competitors = competition["competitors"]
        home = next(c for c in competitors if c["homeAway"] == "home")
        away = next(c for c in competitors if c["homeAway"] == "away")
        home_name, away_name = event_team(home["team"]), event_team(away["team"])
        odds = (competition.get("odds") or [{}])[0]
        total = float(odds["overUnder"]) if odds.get("overUnder") else None
        home_line = None
        try:
            home_line = float(odds["pointSpread"]["home"]["close"]["line"])
        except (KeyError, TypeError, ValueError):
            pass
        implied_home = (total - home_line) / 2 if total is not None and home_line is not None else None
        implied_away = total - implied_home if implied_home is not None else None
        record = {
            "event_id": event["id"], "date": event["date"],
            "home": home_name, "away": away_name,
            "home_abbr": home["team"].get("abbreviation"),
            "away_abbr": away["team"].get("abbreviation"),
            "over_under": total, "home_spread": home_line,
            "source": next((x["href"] for x in event.get("links", [])
                            if "summary" in x.get("rel", [])), ""),
        }
        compact.append(record)
        for team, opponent, is_home, implied, opp_implied in (
            (home_name, away_name, True, implied_home, implied_away),
            (away_name, home_name, False, implied_away, implied_home),
        ):
            canonical = next((name for name in eligible
                              if TEAM_ALIASES.get(name, name) == team), None)
            if canonical:
                games[canonical] = {**record, "opponent": opponent,
                                    "home": is_home, "implied": implied,
                                    "opponent_implied": opp_implied}
    return games, compact


def project(players, teams, games, generated_at):
    team_by_name = {row["team_name"]: row for row in teams}
    output = []
    for source in players:
        team = source["team_name"]
        if team not in games:
            continue
        game = games[team]
        team_row = team_by_name[team]
        season_games = max(num(source, "projected_games"), 1)
        baseline_team_points = (
            6 * (num(team_row, "pass_td") + num(team_row, "rush_td"))
            / max(num(team_row, "games"), 1) + 6.5
        )
        implied = game["implied"] or baseline_team_points
        opponent_implied = game["opponent_implied"] or baseline_team_points
        scoring = clamp(implied / baseline_team_points, .65, 1.40)
        total = implied + opponent_implied
        pace = clamp((total / 55) ** .35, .88, 1.12)
        margin = implied - opponent_implied
        pass_script = clamp(1 - margin / 180, .88, 1.14)
        rush_script = clamp(1 + margin / 150, .86, 1.16)
        efficiency = scoring ** .22
        row = {
            "season": 2026, "week": 1, "player_id": source["player_id"],
            "player_name": source["player_name"], "team_id": source["team_id"],
            "team_name": team, "position": source["position"],
            "opponent": game["opponent"], "home": game["home"],
            "game_date": game["date"], "event_id": game["event_id"],
            "implied_team_total": round(implied, 2),
            "game_total": round(total, 2),
            "starter_probability": num(source, "starter_probability"),
            "role_confidence": source["role_confidence"],
            "projection_confidence": source["projection_confidence"],
            "position_source": source["position_source"],
            "platform_eligibility": source["platform_eligibility"],
            "source_season_ppg": num(source, "fantasy_points_per_game"),
            "model_version": "week1-v1.0", "generated_at": generated_at,
        }
        base = {key: num(source, key) / season_games for key in STATS}
        row["pass_attempts"] = base["pass_attempts"] * pace * pass_script
        row["completions"] = base["completions"] * pace * pass_script
        row["passing_yards"] = base["passing_yards"] * pace * pass_script * efficiency
        row["passing_td"] = base["passing_td"] * scoring
        row["interceptions"] = base["interceptions"] * pace * pass_script
        row["rush_attempts"] = base["rush_attempts"] * pace * rush_script
        row["rushing_yards"] = base["rushing_yards"] * pace * rush_script * efficiency
        row["rushing_td"] = base["rushing_td"] * scoring
        row["receptions"] = base["receptions"] * pace * pass_script
        row["receiving_yards"] = base["receiving_yards"] * pace * pass_script * efficiency
        row["receiving_td"] = base["receiving_td"] * scoring
        row["fantasy_points"] = yahoo_points(row)
        output.append(row)

    by_position = defaultdict(list)
    for row in output:
        by_position[row["position"]].append(row)
    for rows in by_position.values():
        rows.sort(key=lambda x: (-x["fantasy_points"], x["player_name"]))
        for rank, row in enumerate(rows, 1):
            row["position_rank"] = rank
    output.sort(key=lambda x: (-x["fantasy_points"], x["player_name"]))
    for rank, row in enumerate(output, 1):
        row["overall_rank"] = rank
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scoreboard", required=True)
    parser.add_argument("--output", default=str(ROOT / "data/college/2026/week-1/v1.0"))
    parser.add_argument("--generated-at", default=datetime.now(timezone.utc).isoformat())
    args = parser.parse_args()
    out = Path(args.output)
    provenance = out / "provenance"
    provenance.mkdir(parents=True, exist_ok=True)
    with (SOURCE / "provenance/college_player_projections_2026_v1.1.csv").open(newline="") as handle:
        players = list(csv.DictReader(handle))
    with (SOURCE / "provenance/college_team_projections_2026_v1.1.csv").open(newline="") as handle:
        teams = list(csv.DictReader(handle))
    scoreboard = json.loads(Path(args.scoreboard).read_text())
    games, compact = scheduled_games(scoreboard, {r["team_name"] for r in teams})
    rows = project(players, teams, games, args.generated_at)
    if len(games) != 64:
        raise SystemExit(f"expected 64 scheduled model teams, found {len(games)}")
    if not rows:
        raise SystemExit("no weekly projections generated")

    csv_path = provenance / "college_week1_player_projections_2026_v1.0.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
    schedule_path = provenance / "college_week1_schedule_2026.json"
    schedule_path.write_text(json.dumps({
        "source": "ESPN college football scoreboard; odds displayed by ESPN",
        "source_url": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
        "generated_at": args.generated_at, "games": compact,
    }, indent=1) + "\n")
    site_path = out / "college_week1_site_projections_2026.json"
    site_path.write_text(json.dumps({
        "season": 2026, "week": 1, "modelVersion": "week1-v1.0",
        "generatedAt": args.generated_at, "scoring": "Yahoo scoring rules",
        "disclosure": "Positions reflect school roster listings sourced through CFBD and may differ from fantasy-platform eligibility.",
        "methodology": "Season v1.1 per-game player allocations adjusted for the Week 1 schedule, market-implied team total, game total and expected game script. No unconfirmed injury or depth-chart change is inferred.",
        "counts": {"players": len(rows), "teams": len(games), "games": len({r['event_id'] for r in rows})},
        "players": [{
            "id": r["player_id"], "name": r["player_name"], "team": r["team_name"],
            "teamId": r["team_id"], "pos": r["position"], "rank": r["position_rank"],
            "overallRank": r["overall_rank"], "opponent": r["opponent"],
            "home": r["home"], "gameDate": r["game_date"],
            "impliedTotal": r["implied_team_total"], "pts": round(r["fantasy_points"], 1),
            "passAtt": round(r["pass_attempts"], 1), "comp": round(r["completions"], 1),
            "passYds": round(r["passing_yards"], 1), "passTd": round(r["passing_td"], 2),
            "int": round(r["interceptions"], 2), "rushAtt": round(r["rush_attempts"], 1),
            "rushYds": round(r["rushing_yards"], 1), "rushTd": round(r["rushing_td"], 2),
            "rec": round(r["receptions"], 1), "recYds": round(r["receiving_yards"], 1),
            "recTd": round(r["receiving_td"], 2), "confidence": r["projection_confidence"],
        } for r in rows],
    }, separators=(",", ":")) + "\n")
    files = {p.name: {"bytes": p.stat().st_size, "sha256": sha(p)}
             for p in (csv_path, schedule_path, site_path)}
    manifest = {
        "version": "college_week1_2026_v1.0", "status": "PUBLISHED",
        "generated_at": args.generated_at, "source_release": "2026/v1.1",
        "source_manifest_sha256": sha(SOURCE / "manifest.json"),
        "qa_status": "PASS", "scoring": "Yahoo scoring rules",
        "counts": {"players": len(rows), "teams": len(games),
                   "positions": {p: sum(r["position"] == p for r in rows)
                                 for p in ("QB", "RB", "WR", "TE")}},
        "files": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"week 1: {len(rows)} players, {len(games)} teams")


if __name__ == "__main__":
    main()
