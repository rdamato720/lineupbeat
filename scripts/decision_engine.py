#!/usr/bin/env python3
"""Deterministic season-mode decisions from validated Lineup Beat data."""

from __future__ import annotations

from dataclasses import dataclass


FORMATS = ("ppr", "half_ppr", "non_ppr")
FORMAT_LABELS = {"ppr": "PPR", "half_ppr": "Half-PPR", "non_ppr": "Non-PPR"}


@dataclass(frozen=True)
class DecisionContext:
    mode: str
    season: int
    scoring_format: str
    week: int | None = None

    def __post_init__(self) -> None:
        if self.mode != "season":
            raise ValueError("only validated season mode is available")
        if self.week is not None:
            raise ValueError("season mode cannot carry a week")
        if self.scoring_format not in FORMATS:
            raise ValueError("unsupported scoring format")


def confidence(gap: float) -> str:
    """Classify a full-season point edge without implying probability."""
    if round(abs(gap), 1) == 0:
        return "True Toss-Up"
    if gap <= 2.0:
        return "Toss-Up"
    if gap < 12.0:
        return "Lean"
    return "Clear Edge"


def _format(player: dict, scoring_format: str) -> dict:
    value = player.get("formats", {}).get(scoring_format)
    if not value or value.get("projected_points") is None:
        raise ValueError(f"{player.get('name', 'player')} has no {scoring_format} projection")
    return value


def compare(a: dict, b: dict, context: DecisionContext) -> dict:
    if a.get("id") == b.get("id"):
        raise ValueError("select two different players")
    af, bf = _format(a, context.scoring_format), _format(b, context.scoring_format)
    ap = round(float(af["projected_points"]), 1)
    bp = round(float(bf["projected_points"]), 1)
    if ap == bp:
        changes = []
        for fmt in FORMATS:
            if fmt == context.scoring_format:
                continue
            other = compare_shallow(a, b, fmt)
            if other["winner_id"] is not None:
                changes.append(FORMAT_LABELS[fmt])
        return {
            "winner": None, "runner_up": None,
            "player_a": a, "player_b": b,
            "player_a_format": af, "player_b_format": bf,
            "gap": 0.0, "confidence": "True Toss-Up", "is_tie": True,
            "runner_up_gain_to_flip": 0.1,
            "winner_decline_to_flip": 0.1,
            "format_flips": changes, "market_alignment": "not_applicable",
            "context": context,
        }
    winner, runner_up = (a, b) if ap > bp else (b, a)
    wf, rf = _format(winner, context.scoring_format), _format(runner_up, context.scoring_format)
    gap = round(float(wf["projected_points"]) - float(rf["projected_points"]), 1)
    # Inputs are published to one decimal. A tenth beyond equality is the
    # smallest honest threshold that actually changes the recommendation.
    flip = round(gap + 0.1, 1)
    changes = []
    for fmt in FORMATS:
        if fmt == context.scoring_format:
            continue
        try:
            other = compare_shallow(a, b, fmt)
        except ValueError:
            continue
        if other["winner_id"] != winner["id"]:
            changes.append(FORMAT_LABELS[fmt])
    wa, ra = winner.get("adp"), runner_up.get("adp")
    market = "unavailable"
    if wa is not None and ra is not None:
        market = "disagrees" if float(wa) > float(ra) else "agrees"
    return {
        "winner": winner,
        "runner_up": runner_up,
        "winner_format": wf,
        "runner_up_format": rf,
        "gap": gap,
        "confidence": confidence(gap),
        "runner_up_gain_to_flip": flip,
        "winner_decline_to_flip": flip,
        "format_flips": changes,
        "market_alignment": market,
        "context": context,
        "is_tie": False,
    }


def compare_shallow(a: dict, b: dict, scoring_format: str) -> dict:
    af, bf = _format(a, scoring_format), _format(b, scoring_format)
    ap = round(float(af["projected_points"]), 1)
    bp = round(float(bf["projected_points"]), 1)
    if ap == bp:
        winner = None
    else:
        winner = a if ap > bp else b
    return {"winner_id": winner["id"] if winner else None,
            "gap": round(abs(ap - bp), 1),
            "confidence": confidence(abs(ap - bp))}


def closest_calls(players: list[dict], scoring_format: str, limit: int = 6) -> list[dict]:
    calls = []
    caps = {"QB": 24, "RB": 48, "WR": 60, "TE": 30}
    for position, cap in caps.items():
        pool = [p for p in players if p.get("position") == position
                and p.get("formats", {}).get(scoring_format)
                and p["formats"][scoring_format].get("position_rank", 10_000) <= cap]
        pool.sort(key=lambda p: (-p["formats"][scoring_format]["projected_points"], p["name"]))
        for a, b in zip(pool, pool[1:]):
            result = compare(a, b, DecisionContext("season", 2026, scoring_format))
            calls.append(result)
    calls.sort(key=lambda r: (r["gap"],
                              (r["winner"] or r["player_a"])["position"],
                              (r["winner"] or r["player_a"])["name"]))
    return calls[:limit]


def convictions(players: list[dict], scoring_format: str, limit: int = 6) -> list[dict]:
    rows = []
    for player in players:
        fmt = player.get("formats", {}).get(scoring_format)
        adp = player.get("adp")
        if not fmt or adp is None or fmt.get("overall_rank") is None:
            continue
        delta = round(float(adp) - float(fmt["overall_rank"]), 1)
        if abs(delta) < 12:
            continue
        rows.append({"player": player, "format": fmt, "rank_adp_delta": delta,
                     "stance": "ahead" if delta > 0 else "behind"})
    rows.sort(key=lambda r: (-abs(r["rank_adp_delta"]), r["player"]["name"]))
    return rows[:limit]


def value_signals(players: list[dict], scoring_format: str,
                  limit: int = 3) -> tuple[list[dict], list[dict]]:
    """Return projection-vs-ADP gaps as separate values and fades."""
    rows = convictions(players, scoring_format, limit=len(players))
    values = [row for row in rows if row["rank_adp_delta"] > 0][:limit]
    fades = [row for row in rows if row["rank_adp_delta"] < 0][:limit]
    return values, fades


def eligible_opponents(players: list[dict], selected_id: str,
                       cross_position: bool = False) -> list[dict]:
    """The accessible selector's deterministic player-two candidate set."""
    selected = next((p for p in players if p.get("id") == selected_id), None)
    if selected is None:
        return []
    return [p for p in players if p.get("id") != selected_id and
            (cross_position or p.get("position") == selected.get("position"))]


def scoring_movers(players: list[dict], limit: int = 6) -> list[dict]:
    rows = []
    for player in players:
        if any(fmt not in player.get("formats", {}) for fmt in FORMATS):
            continue
        ranks = {fmt: player["formats"][fmt]["position_rank"] for fmt in FORMATS}
        spread = max(ranks.values()) - min(ranks.values())
        if spread < 5:
            continue
        best = min(ranks, key=ranks.get)
        worst = max(ranks, key=ranks.get)
        rows.append({"player": player, "ranks": ranks, "spread": spread,
                     "best_format": best, "worst_format": worst})
    rows.sort(key=lambda r: (-r["spread"], r["player"]["name"]))
    return rows[:limit]
