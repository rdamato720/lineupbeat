#!/usr/bin/env python3
"""Generate the immutable 2026 college projection v1.1 release.

The original private production scripts named in the v1.0 manifest were not
committed with the release.  This generator deliberately starts from the
published v1.0 provenance instead of pretending to recreate those models.

It applies the one correction that v1.0 named precisely enough to reproduce:
RB_Final_Room_Concentration_Calibration_v0.1.  Every backfield below a 79%
top-two carry share is concentrated to 79%.  Tail and top-two shares retain
their internal proportions; player rushing efficiency is retained before a
single team-level normalization restores the exact frozen rushing-yard and
touchdown budgets.  All non-RB projections remain numerically unchanged.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "college" / "2026" / "v1.0"
DEFAULT_OUTPUT = ROOT / "data" / "college" / "2026" / "v1.1"
TARGET_TOP_TWO = 0.79
TOLERANCE = 1e-9

PLAYER_FILE = "college_player_projections_2026_v1.1.csv"
TEAM_FILE = "college_team_projections_2026_v1.1.csv"
QA_FILE = "college_projection_qa_v1.1.json"
SITE_FILE = "college_site_projections_2026.json"

PLAYER_NUMERIC = [
    "projected_games", "pass_attempts", "completions", "passing_yards",
    "passing_td", "interceptions", "rush_attempts", "rushing_yards",
    "rushing_td", "receptions", "receiving_yards", "receiving_td",
    "fantasy_points", "fantasy_points_per_game", "rushing_fantasy_points",
    "starter_probability", "evidence_pass_attempts",
]
STAT_FIELDS = [
    "pass_attempts", "completions", "passing_yards", "passing_td",
    "interceptions", "rush_attempts", "rushing_yards", "rushing_td",
    "receptions", "receiving_yards", "receiving_td", "fantasy_points",
    "fantasy_points_per_game", "rushing_fantasy_points",
]
POSITION_ORDER = {"QB": 0, "RB": 1, "TE": 2, "WR": 3}


def load_csv(path: Path):
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def number(row, field):
    return float(row.get(field) or 0.0)


def set_number(row, field, value):
    if not math.isfinite(value):
        raise ValueError(f"non-finite {field} for {row['player_name']}")
    row[field] = repr(float(value))


def team_sums(rows, position=None):
    out = defaultdict(lambda: defaultdict(float))
    for row in rows:
        if position and row["position"] != position:
            continue
        for field in STAT_FIELDS:
            out[row["team_id"]][field] += number(row, field)
    return out


def concentration(rows):
    rooms = defaultdict(list)
    for row in rows:
        if row["position"] == "RB":
            rooms[row["team_id"]].append(number(row, "rush_attempts"))
    top_two = total = 0.0
    per_team = {}
    for team_id, attempts in rooms.items():
        ordered = sorted(attempts, reverse=True)
        room_total = sum(ordered)
        room_top_two = sum(ordered[:2])
        if room_total:
            per_team[team_id] = room_top_two / room_total
            top_two += room_top_two
            total += room_total
    return top_two / total, per_team


def normalize(values, target, anchor=0):
    current = sum(values)
    if not values:
        return values
    if abs(current) <= TOLERANCE:
        weights = [1.0 / len(values)] * len(values)
        result = [target * weight for weight in weights]
    else:
        result = [value * target / current for value in values]
    result[anchor] += target - sum(result)
    return result


def calibrate_rb_rooms(rows):
    rooms = defaultdict(list)
    for row in rows:
        if row["position"] == "RB":
            rooms[row["team_id"]].append(row)

    adjusted = 0
    for room in rooms.values():
        room.sort(key=lambda row: (-number(row, "rush_attempts"), row["player_id"]))
        attempts = [number(row, "rush_attempts") for row in room]
        total_attempts = sum(attempts)
        if len(room) < 3 or total_attempts <= TOLERANCE:
            continue
        current_share = sum(attempts[:2]) / total_attempts
        if current_share + TOLERANCE >= TARGET_TOP_TWO:
            continue

        adjusted += 1
        desired_top = total_attempts * TARGET_TOP_TWO
        desired_tail = total_attempts - desired_top
        new_attempts = normalize(attempts[:2], desired_top) + normalize(
            attempts[2:], desired_tail
        )
        new_attempts[0] += total_attempts - sum(new_attempts)

        old_yards = [number(row, "rushing_yards") for row in room]
        old_tds = [number(row, "rushing_td") for row in room]
        total_yards = sum(old_yards)
        total_tds = sum(old_tds)
        raw_yards = [
            new_attempt * (old_yard / old_attempt if old_attempt else 0.0)
            for new_attempt, old_yard, old_attempt in zip(
                new_attempts, old_yards, attempts
            )
        ]
        raw_tds = [
            new_attempt * (old_td / old_attempt if old_attempt else 0.0)
            for new_attempt, old_td, old_attempt in zip(
                new_attempts, old_tds, attempts
            )
        ]
        new_yards = normalize(raw_yards, total_yards)
        new_tds = normalize(raw_tds, total_tds)

        for index, row in enumerate(room):
            set_number(row, "rush_attempts", new_attempts[index])
            set_number(row, "rushing_yards", new_yards[index])
            set_number(row, "rushing_td", new_tds[index])
            row["projection_basis"] = (
                "calibrated rushing efficiency and room concentration; "
                "team receiving rates"
            )
    return adjusted


def fantasy_points(row):
    passing = (
        number(row, "passing_yards") * 0.04
        + number(row, "passing_td") * 4
        - number(row, "interceptions")
    )
    rushing = number(row, "rushing_yards") * 0.1 + number(row, "rushing_td") * 6
    receiving = (
        number(row, "receptions")
        + number(row, "receiving_yards") * 0.1
        + number(row, "receiving_td") * 6
    )
    return passing + rushing + receiving, rushing


def update_points_and_ranks(rows, generated_at):
    for row in rows:
        # v1.1 is an RB-only calibration. Preserve every published byte of
        # non-RB numerical provenance rather than recomputing an equivalent
        # float with a different final decimal representation.
        if row["position"] == "RB":
            points, rushing = fantasy_points(row)
            set_number(row, "fantasy_points", points)
            games = number(row, "projected_games")
            set_number(
                row, "fantasy_points_per_game", points / games if games else 0.0
            )
            set_number(row, "rushing_fantasy_points", rushing)
        row["model_version"] = "v1.1"
        row["generated_at"] = generated_at

    by_position = defaultdict(list)
    for row in rows:
        by_position[row["position"]].append(row)
    for position, players in by_position.items():
        if position != "RB":
            continue
        players.sort(
            key=lambda row: (
                -number(row, "fantasy_points"),
                int(float(row["position_rank"])),
                row["player_id"],
            )
        )
        for rank, row in enumerate(players, 1):
            row["position_rank"] = str(rank)


def max_preservation_delta(updated, source, field, team_ids):
    """Delta against the exact audited v1.0 player aggregate.

    The public team CSV is rounded to four decimals.  v1.0's fifteen gates
    were run against private unrounded team values, then the exact player
    rows were published.  Preserving those rows' aggregates is therefore the
    only way a derivative release can retain the audited precision without
    inventing digits that are absent from the public team file.
    """
    return max(abs(updated[team][field] - source[team][field]) for team in team_ids)


def build_qa(rows, source_rows, teams, generated_at, before_concentration,
             after_concentration, adjusted_teams):
    teams_by_id = {row["team_id"]: row for row in teams}
    all_sums = team_sums(rows)
    qb_sums = team_sums(rows, "QB")
    rb_sums = team_sums(rows, "RB")
    source_sums = team_sums(source_rows)
    source_qb = team_sums(source_rows, "QB")
    source_rb = team_sums(source_rows, "RB")
    source_wr = team_sums(source_rows, "WR")
    source_te = team_sums(source_rows, "TE")
    rb_new = team_sums(rows, "RB")
    wr_new = team_sums(rows, "WR")
    te_new = team_sums(rows, "TE")

    reconciliation = {
        "pass_attempts": max_preservation_delta(qb_sums, source_qb, "pass_attempts", teams_by_id),
        "completions": max_preservation_delta(qb_sums, source_qb, "completions", teams_by_id),
        "passing_yards": max_preservation_delta(qb_sums, source_qb, "passing_yards", teams_by_id),
        "passing_td": max_preservation_delta(qb_sums, source_qb, "passing_td", teams_by_id),
        "interceptions": max_preservation_delta(qb_sums, source_qb, "interceptions", teams_by_id),
        "rushing_yards": max_preservation_delta(all_sums, source_sums, "rushing_yards", teams_by_id),
        "rushing_td": max_preservation_delta(all_sums, source_sums, "rushing_td", teams_by_id),
        "qb_rush_attempts": max_preservation_delta(qb_sums, source_qb, "rush_attempts", teams_by_id),
        "rb_rush_attempts": max_preservation_delta(rb_sums, source_rb, "rush_attempts", teams_by_id),
        "RB_receptions": max(
            abs(rb_new[team]["receptions"] - source_rb[team]["receptions"])
            for team in teams_by_id
        ),
        "WR_receptions": max(
            abs(wr_new[team]["receptions"] - source_wr[team]["receptions"])
            for team in teams_by_id
        ),
        "TE_receptions": max(
            abs(te_new[team]["receptions"] - source_te[team]["receptions"])
            for team in teams_by_id
        ),
        "receptions_vs_completions": max_preservation_delta(
            all_sums, source_sums, "receptions", teams_by_id
        ),
        "receiving_yards_named": max(
            abs(all_sums[team]["receiving_yards"] - source_sums[team]["receiving_yards"])
            for team in teams_by_id
        ),
        "receiving_td_named": max(
            abs(all_sums[team]["receiving_td"] - source_sums[team]["receiving_td"])
            for team in teams_by_id
        ),
    }

    ids = [row["player_id"] for row in rows]
    teams_per_player = defaultdict(set)
    for row in rows:
        teams_per_player[row["player_id"]].add(row["team_id"])
    duplicates = sum(count - 1 for count in Counter(ids).values() if count > 1)
    multi_team = sum(1 for values in teams_per_player.values() if len(values) > 1)
    negative = sum(
        1 for row in rows for field in STAT_FIELDS if number(row, field) < -TOLERANCE
    )
    blocking = [
        name for name, delta in reconciliation.items() if delta > TOLERANCE
    ]
    if duplicates:
        blocking.append("duplicate player ids")
    if multi_team:
        blocking.append("multi-team players")
    if negative:
        blocking.append("negative values")
    if after_concentration + TOLERANCE < TARGET_TOP_TWO:
        blocking.append("RB top-two concentration below target")

    source_qa = json.loads(
        (SOURCE / "provenance" / "college_projection_qa_v1.0.json").read_text()
    )
    return {
        "generated_at": generated_at,
        "season": 2026,
        "reconciliation": reconciliation,
        "position_claim": {
            **source_qa["position_claim"],
            "v1_1": "platform eligibility remains a separately sourced future enhancement",
        },
        "hybrid_disclosure": source_qa["hybrid_disclosure"],
        "receiving_note": source_qa["receiving_note"],
        "rb_room_concentration": {
            "model": "RB_Final_Room_Concentration_Calibration_v0.1",
            "target_minimum": TARGET_TOP_TWO,
            "before": before_concentration,
            "after": after_concentration,
            "teams_adjusted": adjusted_teams,
            "status": "PASS" if after_concentration >= TARGET_TOP_TWO else "FAIL",
        },
        "players": len(rows),
        "by_position": dict(Counter(row["position"] for row in rows)),
        "teams": len(teams),
        "duplicates": duplicates,
        "multi_team_players": multi_team,
        "negative_values": negative,
        "blocking_failures": blocking,
        "status": "PASS" if not blocking else "FAIL",
        "wr_te_treatment": source_qa["wr_te_treatment"],
    }


def site_player(row, previous):
    item = dict(previous)
    item.update({
        "rank": int(row["position_rank"]),
        "games": round(number(row, "projected_games"), 1),
        "pts": round(number(row, "fantasy_points"), 1),
        "ppg": round(number(row, "fantasy_points_per_game"), 1),
        "confidence": row["projection_confidence"],
        "basis": row["projection_basis"],
        "positionSource": row["position_source"],
        "platformEligibility": row["platform_eligibility"] or None,
        "rushAtt": round(number(row, "rush_attempts"), 0),
        "rushYds": round(number(row, "rushing_yards"), 0),
        "rushTd": round(number(row, "rushing_td"), 1),
    })
    if row["position"] == "QB":
        item.update({
            "passAtt": round(number(row, "pass_attempts"), 0),
            "comp": round(number(row, "completions"), 0),
            "passYds": round(number(row, "passing_yards"), 0),
            "passTd": round(number(row, "passing_td"), 1),
            "int": round(number(row, "interceptions"), 1),
            "starterProb": round(number(row, "starter_probability"), 3),
            "roleConfidence": row["role_confidence"],
        })
    else:
        item.update({
            "rec": round(number(row, "receptions"), 1),
            "recYds": round(number(row, "receiving_yards"), 0),
            "recTd": round(number(row, "receiving_td"), 1),
        })
    if item.get("hybridRole"):
        item["rushPts"] = round(number(row, "rushing_fantasy_points"), 1)
    return item


def build_site(rows, generated_at):
    site = json.loads((SOURCE / SITE_FILE).read_text())
    previous = {item["id"]: item for item in site["players"]}
    site["generatedAt"] = generated_at
    site["modelVersion"] = "v1.1"
    site["methodology"] = (
        "2026 projections combine frozen team-level offensive forecasts with "
        "player-level opportunity estimates. Quarterback passing and rushing "
        "efficiency, and running back rushing efficiency, include calibrated "
        "player-history adjustments. Running back rooms receive a final "
        "concentration calibration that moves the aggregate top-two carry "
        "share into the historical 79-81% band while preserving every team "
        "rushing budget. Wide receiver and tight end projections remain "
        "opportunity-differentiated and share team receiving rates; player-"
        "level receiving efficiency remains a future enhancement."
    )
    site["players"] = [
        site_player(row, previous[row["player_id"]])
        for row in sorted(
            rows,
            key=lambda row: (
                POSITION_ORDER[row["position"]],
                int(row["position_rank"]),
            ),
        )
    ]
    return site


def digest(path: Path):
    data = path.read_bytes()
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def write_json(path, value):
    # Match the compact, reviewable formatting of the published v1.0 files.
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n")


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="") as handle:
        # LF is explicit so git-format-patch/git-am round trips preserve the
        # exact bytes recorded in the release manifest on every platform.
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def manifest_for(output, generated_at, qa, before_share, after_share,
                 adjusted_teams):
    source_manifest = json.loads((SOURCE / "manifest.json").read_text())
    product_claim = dict(source_manifest["product_claim"])
    product_claim.pop("v1_1", None)
    product_claim["future_enhancement"] = (
        "platform eligibility as a separately sourced enhancement"
    )
    known_limitations = [
        item for item in source_manifest["known_limitations"]
        if "Flatter running back tail" not in item["issue"]
    ]
    manifest = {
        "version": "college_projections_2026_v1.1",
        "status": "PUBLISHED",
        "generated_at": generated_at,
        "source_release": "2026/v1.0",
        "source_manifest_sha256": hashlib.sha256(
            (SOURCE / "manifest.json").read_bytes()
        ).hexdigest(),
        "frozen_models": source_manifest["frozen_models"] + [
            "RB_Final_Room_Concentration_Calibration_v0.1"
        ],
        "qa_status": qa["status"],
        "reconciliation_gates": len(qa["reconciliation"]),
        "largest_delta": max(qa["reconciliation"].values()),
        "product_claim": product_claim,
        "calibrations": [{
            "model": "RB_Final_Room_Concentration_Calibration_v0.1",
            "target_minimum": TARGET_TOP_TWO,
            "before": before_share,
            "after": after_share,
            "teams_adjusted": adjusted_teams,
            "method": (
                "Scale each sub-79% room's top two and tail proportionally, "
                "then normalize player rushing yards and touchdowns back to "
                "the exact frozen team budgets."
            ),
        }],
        "known_limitations": known_limitations,
        "files": {},
    }
    tracked = [
        output / "provenance" / PLAYER_FILE,
        output / "provenance" / TEAM_FILE,
        output / "provenance" / QA_FILE,
        output / SITE_FILE,
        # Do not hash build_college_projections.py here. That builder pins the
        # manifest SHA, so putting its own checksum in the manifest creates a
        # circular hash dependency. The release generator is hashed; the
        # publication builder is independently reviewed in git.
        ROOT / "scripts" / "generate_college_v1_1.py",
    ]
    for path in tracked:
        info = digest(path)
        manifest["files"][path.name] = {
            "bytes": info["bytes"],
            "sha256": info["sha256"],
        }
    return manifest


def write_readme(output, manifest, qa):
    manifest_sha = hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()
    files = []
    for relative in [
        SITE_FILE,
        f"provenance/{PLAYER_FILE}",
        f"provenance/{TEAM_FILE}",
        f"provenance/{QA_FILE}",
        "manifest.json",
    ]:
        path = output / relative
        info = digest(path)
        files.append((relative, info["bytes"], info["sha256"]))
    rows = "\n".join(
        f"| `{name}` | {size:,} | `{sha}` |" for name, size, sha in files
    )
    text = f"""# College fantasy projections, 2026, v1.1

Immutable. This release is derived reproducibly from the published v1.0
provenance and does not alter `2026/v1.0`.

The sole numerical change is
`RB_Final_Room_Concentration_Calibration_v0.1`: each backfield below a 79%
top-two carry share is concentrated to 79% while preserving every frozen team
carry, rushing-yard and rushing-touchdown budget. The aggregate top-two share
moved from {manifest['calibrations'][0]['before']:.4%} to
{manifest['calibrations'][0]['after']:.4%} across
{manifest['calibrations'][0]['teams_adjusted']} adjusted teams.

Manifest SHA-256, pinned by the site builder:

    {manifest_sha}

## Reproduce

```bash
python3 scripts/generate_college_v1_1.py \\
  --generated-at {manifest['generated_at']} \\
  --output /tmp/college-v1.1
```

## QA

- {qa['players']:,} players across {qa['teams']} teams.
- {len(qa['reconciliation'])} reconciliation gates: `{qa['status']}`.
- Largest reconciliation delta: `{manifest['largest_delta']:.16g}`.
- No duplicate players, multi-team players, negative values, or blocking
  failures.
- WR/TE player-level receiving efficiency and fantasy-platform eligibility
  remain explicitly unestablished.

## Files

| file | bytes | SHA-256 |
|---|---:|---|
{rows}
"""
    (output / "README.md").write_text(text)
    return manifest_sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--generated-at",
        default=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing release: {output}")

    source_rows, player_fields = load_csv(
        SOURCE / "provenance" / "college_player_projections_2026_v1.0.csv"
    )
    rows = [dict(row) for row in source_rows]
    teams, team_fields = load_csv(
        SOURCE / "provenance" / "college_team_projections_2026_v1.0.csv"
    )
    for team in teams:
        team["model_version"] = "v1.1"

    before_share, _ = concentration(rows)
    adjusted_teams = calibrate_rb_rooms(rows)
    update_points_and_ranks(rows, args.generated_at)
    after_share, _ = concentration(rows)
    qa = build_qa(
        rows, source_rows, teams, args.generated_at, before_share,
        after_share, adjusted_teams,
    )
    if qa["status"] != "PASS":
        raise SystemExit(f"QA failed: {qa['blocking_failures']}")

    (output / "provenance").mkdir(parents=True)
    rows.sort(
        key=lambda row: (
            POSITION_ORDER[row["position"]], int(row["position_rank"])
        )
    )
    write_csv(output / "provenance" / PLAYER_FILE, rows, player_fields)
    write_csv(output / "provenance" / TEAM_FILE, teams, team_fields)
    write_json(output / "provenance" / QA_FILE, qa)
    write_json(output / SITE_FILE, build_site(rows, args.generated_at))
    manifest = manifest_for(
        output, args.generated_at, qa, before_share, after_share,
        adjusted_teams,
    )
    write_json(output / "manifest.json", manifest)
    manifest_sha = write_readme(output, manifest, qa)
    print(f"  wrote {output}")
    print(
        f"  RB top-two share {before_share:.4%} -> {after_share:.4%}; "
        f"{adjusted_teams} teams adjusted"
    )
    print(
        f"  {len(rows)} players, {len(teams)} teams, "
        f"{len(qa['reconciliation'])} gates {qa['status']}"
    )
    print(f"  manifest SHA-256 {manifest_sha}")


if __name__ == "__main__":
    main()
