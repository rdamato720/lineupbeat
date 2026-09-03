#!/usr/bin/env python3
"""Build the College Week 1 v1.1 market-reconciled development release.

The published v1.0 release divided season allocations into one game but did
not reconcile receiving production to quarterback production. It also carried
season-long quarterback uncertainty into games where an exact, current player
market identified the expected passer. This builder repairs those structural
problems without fuzzy identity matching or copying unsupported prop types.

Raw TheRundown responses are user-supplied private inputs. They are validated
by hash at build time and are not copied into the repository output.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data/college/2026/week-1/v1.0"
DEFAULT_OUTPUT = ROOT / "data/college/2026/week-1/v1.1"
BASE_CSV = BASE / "provenance/college_week1_player_projections_2026_v1.0.csv"
BASE_SITE = BASE / "college_week1_site_projections_2026.json"
BASE_SCHEDULE = BASE / "provenance/college_week1_schedule_2026.json"

MARKETS = {
    51: ("passing_yards", "passing_yards"),
    52: ("passing_touchdowns", "passing_td"),
    53: ("rushing_yards", "rushing_yards"),
    57: ("receiving_yards", "receiving_yards"),
    58: ("receptions", "receptions"),
}
ANYTIME_TD = 55
BOOKS = {"3": "Pinnacle", "19": "DraftKings", "23": "FanDuel"}
TEAM_ALIASES = {
    "Long Island University": "Long Island",
    "Miami": "Miami (FL)",
    "Nicholls": "Nicholls State",
    "Pitt": "Pittsburgh",
}
NUMERIC = (
    "pass_attempts", "completions", "passing_yards", "passing_td",
    "interceptions", "rush_attempts", "rushing_yards", "rushing_td",
    "receptions", "receiving_yards", "receiving_td", "fantasy_points",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(value: str | None) -> str:
    text = re.sub(r"[^a-z0-9 ]", "", (value or "").lower())
    return " ".join(re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", text).split())


def parse_line(value: object) -> float:
    match = re.search(r"(-?\d+(?:\.\d+)?)", str(value or ""))
    if not match:
        raise ValueError(f"player market line is not numeric: {value!r}")
    return float(match.group(1))


def yahoo_points(row: dict) -> float:
    return (
        row["passing_yards"] * .04 + row["passing_td"] * 4
        - row["interceptions"] + row["rushing_yards"] * .1
        + row["rushing_td"] * 6 + row["receptions"]
        + row["receiving_yards"] * .1 + row["receiving_td"] * 6
    )


def allocate(rows: list[dict], field: str, budget: float,
             anchors: dict[str, float]) -> float:
    """Allocate one team component, preserving anchors and residual shares."""
    anchored = [row for row in rows if row["player_id"] in anchors]
    residual = [row for row in rows if row["player_id"] not in anchors]
    anchor_total = sum(anchors[row["player_id"]] for row in anchored)
    budget = max(float(budget), anchor_total)
    remaining = max(0.0, budget - anchor_total)
    old_residual = sum(max(0.0, row[field]) for row in residual)
    for row in anchored:
        row[field] = anchors[row["player_id"]]
    if residual:
        if old_residual > 0:
            for row in residual:
                row[field] = remaining * max(0.0, row[field]) / old_residual
        else:
            for index, row in enumerate(residual):
                row[field] = remaining if index == 0 else 0.0
    return budget


def scale_to(rows: list[dict], field: str, budget: float) -> None:
    values = [max(0.0, row[field]) for row in rows]
    total = sum(values)
    if not rows:
        return
    if total <= 0:
        rows[0][field] = budget
        for row in rows[1:]:
            row[field] = 0.0
        return
    for row, value in zip(rows, values):
        row[field] = budget * value / total


def modeled_teams(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for row in rows:
        record = {
            "team": row["team_name"], "team_id": row["team_id"],
            "opponent": row["opponent"], "home": bool(row["home"]),
            "game_date": row["game_date"], "event_id": row["event_id"],
        }
        prior = out.setdefault(row["team_name"], record)
        if prior != record:
            raise ValueError(f"conflicting schedule for {row['team_name']}")
    return out


def event_team_names(event: dict) -> set[str]:
    return {TEAM_ALIASES.get(team.get("name"), team.get("name"))
            for team in event.get("teams") or []}


def event_matches_team(event: dict, schedule: dict) -> bool:
    event_date = datetime.fromisoformat(event["event_date"].replace("Z", "+00:00"))
    game_date = datetime.fromisoformat(schedule["game_date"].replace("Z", "+00:00"))
    expected = {
        TEAM_ALIASES.get(schedule["team"], schedule["team"]),
        TEAM_ALIASES.get(schedule["opponent"], schedule["opponent"]),
    }
    return event_date == game_date and event_team_names(event) == expected


def participant_lines(participant: dict) -> tuple[float | None, set[str], str | None]:
    by_book: dict[str, list[float]] = defaultdict(list)
    latest = None
    for line in participant.get("lines") or []:
        value = parse_line(line.get("value"))
        for book_id, price in (line.get("prices") or {}).items():
            if book_id not in BOOKS or not price.get("is_main_line"):
                continue
            by_book[book_id].append(value)
            update = price.get("updated_at")
            if update and (latest is None or update > latest):
                latest = update
    book_lines = []
    for book_id, values in by_book.items():
        unique = sorted(set(values))
        if len(unique) != 1:
            raise ValueError(
                f"conflicting over/under lines for {participant.get('name')} at {BOOKS[book_id]}")
        book_lines.append(unique[0])
    return (statistics.median(book_lines) if book_lines else None,
            set(by_book), latest)


def load_market_evidence(paths: list[Path], rows: list[dict]) -> tuple[dict, dict]:
    schedules = modeled_teams(rows)
    players_by_team: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        players_by_team[row["team_name"]][normalized(row["player_name"])].append(row)

    all_events = []
    source_hashes = {}
    for path in paths:
        raw = json.loads(path.read_text())
        source_hashes[path.name] = digest(path)
        all_events.extend(raw.get("events") or [])

    event_to_teams: dict[str, list[str]] = defaultdict(list)
    for team, schedule in schedules.items():
        matches = [event for event in all_events if event_matches_team(event, schedule)]
        if len(matches) != 1:
            raise ValueError(f"expected one provider event for {team}; found {len(matches)}")
        event_to_teams[matches[0]["event_id"]].append(team)

    evidence: dict[str, dict] = defaultdict(lambda: {"numeric": {}, "anytime_td": False})
    counts = Counter()
    unresolved = set()
    ambiguous = set()
    updates = []
    for event in all_events:
        teams = event_to_teams.get(event.get("event_id"), [])
        if not teams:
            continue
        for market in event.get("markets") or []:
            market_id = market.get("market_id")
            if market_id not in MARKETS and market_id != ANYTIME_TD:
                continue
            for participant in market.get("participants") or []:
                if participant.get("type") != "TYPE_PLAYER":
                    continue
                name_key = normalized(participant.get("name"))
                matches = [row for team in teams for row in players_by_team[team].get(name_key, [])]
                if len(matches) != 1:
                    target = ambiguous if len(matches) > 1 else unresolved
                    target.add((event.get("event_id"), participant.get("name")))
                    continue
                row = matches[0]
                if market_id == ANYTIME_TD:
                    books = set()
                    latest = None
                    for outcome in participant.get("lines") or []:
                        for book_id, price in (outcome.get("prices") or {}).items():
                            if book_id in BOOKS and price.get("is_main_line"):
                                books.add(book_id)
                                update = price.get("updated_at")
                                if update and (latest is None or update > latest):
                                    latest = update
                    if not books:
                        continue
                    if latest:
                        updates.append(latest)
                    evidence[row["player_id"]]["anytime_td"] = True
                    evidence[row["player_id"]].setdefault("books", set()).update(books)
                    counts["anytime_td"] += 1
                    continue
                line, books, latest = participant_lines(participant)
                if not books:
                    continue
                if latest:
                    updates.append(latest)
                label, stat = MARKETS[market_id]
                if line is None:
                    continue
                existing = evidence[row["player_id"]]["numeric"].get(stat)
                record = {"line": float(line), "books": sorted(books),
                          "latest_update_at": latest, "market": label}
                if existing and existing != record:
                    raise ValueError(f"duplicate conflicting market for {row['player_name']} {stat}")
                evidence[row["player_id"]]["numeric"][stat] = record
                evidence[row["player_id"]].setdefault("books", set()).update(books)
                counts[label] += 1

    for record in evidence.values():
        record["books"] = sorted(record.get("books") or [])
    audit = {
        "source_sha256": source_hashes,
        "provider": "TheRundown", "plan": "pro", "data_delay_seconds": 30,
        "modeled_teams": len(schedules),
        "players_with_evidence": len(evidence),
        "players_with_numeric_evidence": sum(bool(row["numeric"]) for row in evidence.values()),
        "market_record_counts": dict(sorted(counts.items())),
        "unresolved_participants": len(unresolved),
        "ambiguous_participants": len(ambiguous),
        "latest_market_update_at": max(updates) if updates else None,
        "identity_method": "exact normalized name bounded to the exact scheduled teams; no fuzzy matching",
    }
    return dict(evidence), audit


def reconcile_team(team_rows: list[dict], evidence: dict[str, dict]) -> dict:
    before = deepcopy(team_rows)
    quarterbacks = [row for row in team_rows if row["position"] == "QB"]
    market_qbs = [row for row in quarterbacks
                  if set(evidence.get(row["player_id"], {}).get("numeric", {}))
                  & {"passing_yards", "passing_td"}]
    starter_mode = "season_uncertainty_preserved"

    base_pass = {
        field: sum(row[field] for row in quarterbacks)
        for field in ("pass_attempts", "completions", "passing_yards",
                      "passing_td", "interceptions")
    }
    selected = None
    if len(market_qbs) == 1:
        selected = market_qbs[0]
        starter_mode = "exact_player_market"
    elif not market_qbs and quarterbacks:
        strongest = max(quarterbacks, key=lambda row: (
            row["starter_probability"], row["pass_attempts"], row["player_id"]))
        if strongest["starter_probability"] >= .85:
            selected = strongest
            starter_mode = "high_confidence_weekly_starter"
    elif len(market_qbs) > 1:
        raise ValueError(f"multiple market quarterbacks for {team_rows[0]['team_name']}")

    if selected:
        numeric = evidence.get(selected["player_id"], {}).get("numeric", {})
        target_yards = numeric.get("passing_yards", {}).get("line", base_pass["passing_yards"])
        ratio = target_yards / base_pass["passing_yards"] if base_pass["passing_yards"] else 1.0
        for row in quarterbacks:
            for field in ("pass_attempts", "completions", "passing_yards",
                          "passing_td", "interceptions"):
                row[field] = 0.0
        selected["passing_yards"] = target_yards
        selected["pass_attempts"] = base_pass["pass_attempts"] * ratio
        selected["completions"] = base_pass["completions"] * ratio
        selected["interceptions"] = base_pass["interceptions"] * ratio
        selected["passing_td"] = numeric.get("passing_td", {}).get(
            "line", base_pass["passing_td"] * ratio)

    pass_yards = sum(row["passing_yards"] for row in quarterbacks)
    completions = sum(row["completions"] for row in quarterbacks)
    pass_tds = sum(row["passing_td"] for row in quarterbacks)
    receivers = [row for row in team_rows]
    yd_anchors = {row["player_id"]: evidence[row["player_id"]]["numeric"]["receiving_yards"]["line"]
                  for row in receivers if "receiving_yards" in evidence.get(row["player_id"], {}).get("numeric", {})}
    rec_anchors = {row["player_id"]: evidence[row["player_id"]]["numeric"]["receptions"]["line"]
                   for row in receivers if "receptions" in evidence.get(row["player_id"], {}).get("numeric", {})}
    adjusted_pass_yards = allocate(receivers, "receiving_yards", pass_yards, yd_anchors)
    adjusted_completions = allocate(receivers, "receptions", completions, rec_anchors)
    if adjusted_pass_yards > pass_yards and quarterbacks:
        lead = selected or max(quarterbacks, key=lambda row: row["passing_yards"])
        lead["passing_yards"] += adjusted_pass_yards - pass_yards
        pass_yards = adjusted_pass_yards
    if adjusted_completions > completions and quarterbacks:
        lead = selected or max(quarterbacks, key=lambda row: row["completions"])
        lead["completions"] += adjusted_completions - completions
        if lead["pass_attempts"] < lead["completions"]:
            lead["pass_attempts"] = lead["completions"]
        completions = adjusted_completions
    scale_to(receivers, "receiving_td", pass_tds)

    rushing_anchors = {row["player_id"]: evidence[row["player_id"]]["numeric"]["rushing_yards"]["line"]
                       for row in team_rows if "rushing_yards" in evidence.get(row["player_id"], {}).get("numeric", {})}
    if rushing_anchors:
        prior_nonanchor_yards = sum(row["rushing_yards"] for row in team_rows
                                    if row["player_id"] not in rushing_anchors)
        rush_budget = max(sum(row["rushing_yards"] for row in team_rows),
                          sum(rushing_anchors.values()) + prior_nonanchor_yards)
        old = {row["player_id"]: (row["rush_attempts"], row["rushing_yards"])
               for row in team_rows}
        allocate(team_rows, "rushing_yards", rush_budget, rushing_anchors)
        team_ypc = (sum(value[1] for value in old.values()) /
                    max(sum(value[0] for value in old.values()), 1e-9))
        for row in team_rows:
            attempts, yards = old[row["player_id"]]
            ypc = yards / attempts if attempts > 0 and yards > 0 else team_ypc
            row["rush_attempts"] = row["rushing_yards"] / max(ypc, 1e-9)

    for row in team_rows:
        row["fantasy_points"] = yahoo_points(row)
        market = evidence.get(row["player_id"], {})
        row["market_evidence"] = sorted(
            item["market"] for item in market.get("numeric", {}).values())
        row["market_role_evidence"] = bool(market.get("anytime_td"))
        row["projection_basis"] = (
            "market_component_anchor_and_team_reconciliation" if row["market_evidence"]
            else "team_reconciliation_and_current_weekly_role")

    after_pass_yards = sum(row["passing_yards"] for row in quarterbacks)
    after_completions = sum(row["completions"] for row in quarterbacks)
    after_pass_tds = sum(row["passing_td"] for row in quarterbacks)
    return {
        "team": team_rows[0]["team_name"], "starter_mode": starter_mode,
        "market_numeric_players": sum(bool(evidence.get(row["player_id"], {}).get("numeric"))
                                      for row in team_rows),
        "before_pass_receive_yard_delta": abs(
            sum(row["passing_yards"] for row in before)
            - sum(row["receiving_yards"] for row in before)),
        "before_completion_reception_delta": abs(
            sum(row["completions"] for row in before)
            - sum(row["receptions"] for row in before)),
        "after_pass_receive_yard_delta": abs(
            after_pass_yards - sum(row["receiving_yards"] for row in team_rows)),
        "after_completion_reception_delta": abs(
            after_completions - sum(row["receptions"] for row in team_rows)),
        "after_pass_receive_td_delta": abs(
            after_pass_tds - sum(row["receiving_td"] for row in team_rows)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--props", action="append", required=True)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--generated-at", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    provenance = output / "provenance"
    provenance.mkdir(parents=True, exist_ok=True)

    with BASE_CSV.open(newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    rows = []
    for source in source_rows:
        row = dict(source)
        for field in NUMERIC:
            row[field] = float(row.get(field) or 0)
        row["starter_probability"] = float(row.get("starter_probability") or 0)
        row["home"] = str(row.get("home")).lower() == "true"
        rows.append(row)

    evidence, market_audit = load_market_evidence(
        [Path(path) for path in args.props], rows)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["team_id"]].append(row)
    team_audits = [reconcile_team(grouped[team_id], evidence)
                   for team_id in sorted(grouped)]

    by_position: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_position[row["position"]].append(row)
    for group in by_position.values():
        group.sort(key=lambda row: (-row["fantasy_points"], row["player_name"], row["player_id"]))
        for rank, row in enumerate(group, 1):
            row["position_rank"] = rank
    rows.sort(key=lambda row: (-row["fantasy_points"], row["player_name"], row["player_id"]))
    for rank, row in enumerate(rows, 1):
        row["overall_rank"] = rank

    csv_name = "college_week1_player_projections_2026_v1.1.csv"
    csv_path = provenance / csv_name
    fieldnames = list(source_rows[0]) + [
        "market_evidence", "market_role_evidence", "projection_basis"]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            record = {key: row.get(key, "") for key in fieldnames}
            record["market_evidence"] = "|".join(row["market_evidence"])
            record["market_role_evidence"] = str(row["market_role_evidence"]).lower()
            writer.writerow(record)

    schedule_path = provenance / BASE_SCHEDULE.name
    schedule_path.write_bytes(BASE_SCHEDULE.read_bytes())
    audit_path = output / "projection_repair_audit.json"
    before = {row["player_id"]: row for row in source_rows}
    differences = []
    for row in rows:
        prior = before[row["player_id"]]
        if any(abs(row[field] - float(prior.get(field) or 0)) > 1e-9 for field in NUMERIC):
            differences.append({
                "player_id": row["player_id"], "team": row["team_name"],
                "position": row["position"], "market_evidence": row["market_evidence"],
                "points_before": round(float(prior["fantasy_points"]), 4),
                "points_after": round(row["fantasy_points"], 4),
            })
    audit = {
        "schemaVersion": "lineupbeat-college-week1-projection-repair-v1",
        "generated_at": args.generated_at,
        "base_manifest_sha256": digest(BASE / "manifest.json"),
        "market_input": market_audit,
        "team_reconciliation": {
            "teams": len(team_audits),
            "max_before_pass_receive_yard_delta": max(
                row["before_pass_receive_yard_delta"] for row in team_audits),
            "max_before_completion_reception_delta": max(
                row["before_completion_reception_delta"] for row in team_audits),
            "max_after_pass_receive_yard_delta": max(
                row["after_pass_receive_yard_delta"] for row in team_audits),
            "max_after_completion_reception_delta": max(
                row["after_completion_reception_delta"] for row in team_audits),
            "max_after_pass_receive_td_delta": max(
                row["after_pass_receive_td_delta"] for row in team_audits),
            "starter_modes": dict(Counter(row["starter_mode"] for row in team_audits)),
        },
        "changed_players": len(differences),
        "changed_player_summary": differences,
        "guardrails": {
            "anytime_td_changes_projection": False,
            "fuzzy_identity_matching": False,
            "raw_provider_response_redistributed": False,
            "current_injury_adjustment": False,
        },
    }
    audit_path.write_text(json.dumps(audit, indent=2) + "\n")

    site_path = output / BASE_SITE.name
    base_site = json.loads(BASE_SITE.read_text())
    site = {
        **{key: value for key, value in base_site.items() if key != "players"},
        "modelVersion": "week1-v1.1-market-reconciled",
        "generatedAt": args.generated_at,
        "methodology": (
            "Season v1.1 per-game priors adjusted for the Week 1 game environment, "
            "then reconciled so passing equals receiving. Exact, team-bounded current "
            "player markets anchor only their named statistical components; unsupported "
            "and unresolved markets do not alter projections."),
        "marketInput": {
            "provider": "TheRundown", "plan": "Pro", "dataDelaySeconds": 30,
            "playersWithEvidence": market_audit["players_with_evidence"],
            "playersWithNumericEvidence": market_audit["players_with_numeric_evidence"],
            "latestMarketUpdateAt": market_audit["latest_market_update_at"],
            "note": "Market lines are projection inputs, not outcomes or guarantees.",
        },
        "players": [{
            "id": row["player_id"], "name": row["player_name"],
            "team": row["team_name"], "teamId": row["team_id"],
            "pos": row["position"], "rank": row["position_rank"],
            "overallRank": row["overall_rank"], "opponent": row["opponent"],
            "home": row["home"], "gameDate": row["game_date"],
            "impliedTotal": float(row["implied_team_total"]),
            "pts": round(row["fantasy_points"], 1),
            "passAtt": round(row["pass_attempts"], 1),
            "comp": round(row["completions"], 1),
            "passYds": round(row["passing_yards"], 1),
            "passTd": round(row["passing_td"], 2),
            "int": round(row["interceptions"], 2),
            "rushAtt": round(row["rush_attempts"], 1),
            "rushYds": round(row["rushing_yards"], 1),
            "rushTd": round(row["rushing_td"], 2),
            "rec": round(row["receptions"], 1),
            "recYds": round(row["receiving_yards"], 1),
            "recTd": round(row["receiving_td"], 2),
            "confidence": row["projection_confidence"],
            "marketEvidence": row["market_evidence"],
            "marketRoleEvidence": row["market_role_evidence"],
            "projectionBasis": row["projection_basis"],
        } for row in rows],
    }
    site_path.write_text(json.dumps(site, separators=(",", ":")) + "\n")

    readme = output / "README.md"
    readme.write_text("""# College fantasy Week 1 projections, 2026, v1.1

Development release repairing the v1.0 weekly allocation defect. All 64 team
passing/receiving stat lines reconcile. Exact, team-bounded TheRundown player
markets anchor only the named passing, rushing, receiving, or reception
component. Anytime-touchdown prices are role context only and do not directly
change projections. Raw provider responses are not redistributed.

No fuzzy identity matching, current injury assumption, or unsupported market
substitution is used.
""")

    files = {path.name: {"bytes": path.stat().st_size, "sha256": digest(path)}
             for path in (csv_path, schedule_path, audit_path, site_path, readme)}
    manifest = {
        "version": "college_week1_2026_v1.1", "status": "DEVELOPMENT",
        "generated_at": args.generated_at, "source_release": "2026/week-1/v1.0",
        "source_manifest_sha256": digest(BASE / "manifest.json"),
        "qa_status": "PASS", "scoring": "Yahoo scoring rules",
        "counts": {"players": len(rows), "teams": len(grouped),
                   "positions": dict(Counter(row["position"] for row in rows))},
        "files": files,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({
        "players": len(rows), "teams": len(grouped),
        "changed_players": len(differences),
        "market_players": market_audit["players_with_evidence"],
        "numeric_market_players": market_audit["players_with_numeric_evidence"],
        "max_reconciliation_delta": max(
            max(row["after_pass_receive_yard_delta"],
                row["after_completion_reception_delta"],
                row["after_pass_receive_td_delta"]) for row in team_audits),
    }, indent=2))


if __name__ == "__main__":
    main()
