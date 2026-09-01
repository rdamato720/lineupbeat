#!/usr/bin/env python3
"""Deterministic season-mode decisions from validated Lineup Beat data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


FORMATS = ("ppr", "half_ppr", "non_ppr")
FORMAT_LABELS = {"ppr": "PPR", "half_ppr": "Half-PPR", "non_ppr": "Non-PPR"}
FORMAT_LABELS["yahoo"] = "Yahoo"
MIN_HISTORY_GAMES = 8
THRESHOLDS = {
    # A full-season difference must clear both the published-point noise floor
    # and one percent of the higher projection before it becomes a call.
    "season": {"toss_up_abs": 2.0, "toss_up_pct": 1.0,
               "lean_pct": 3.0, "edge_pct": 7.0},
    # Weekly projections are smaller and noisier, so the honest no-call band
    # is wider as a percentage of the higher displayed projection.
    "weekly": {"toss_up_abs": 0.5, "toss_up_pct": 3.0,
               "lean_pct": 7.0, "edge_pct": 15.0},
}


@dataclass(frozen=True)
class DecisionContext:
    mode: str
    season: int
    scoring_format: str
    week: int | None = None

    def __post_init__(self) -> None:
        if self.mode not in ("season", "weekly"):
            raise ValueError("unsupported projection mode")
        if self.mode == "season" and self.week is not None:
            raise ValueError("season mode cannot carry a week")
        if self.mode == "weekly" and self.week is None:
            raise ValueError("weekly mode requires a week")
        if self.scoring_format not in FORMAT_LABELS:
            raise ValueError("unsupported scoring format")


def confidence(gap: float, reference_points: float = 200.0,
               mode: str = "season") -> str:
    """Classify a displayed edge without implying win probability.

    The public inputs are rounded to one decimal.  Absolute floors keep tiny
    differences from becoming calls, while percentage bands make the same
    framework honest for full-season NFL and weekly College projections.
    """
    if mode not in THRESHOLDS:
        raise ValueError("unsupported threshold mode")
    gap = round(abs(float(gap)), 1)
    reference = max(abs(float(reference_points)), 0.1)
    pct = round(gap / reference * 100, 6)
    threshold = THRESHOLDS[mode]
    if gap <= threshold["toss_up_abs"] or pct <= threshold["toss_up_pct"]:
        return "Toss-Up"
    if pct <= threshold["lean_pct"]:
        return "Lean"
    if pct <= threshold["edge_pct"]:
        return "Edge"
    return "Strong Edge"


def gap_percent(gap: float, reference_points: float) -> float:
    return round(abs(float(gap)) / max(abs(float(reference_points)), 0.1) * 100, 1)


def meaningful_gap_to_call(reference_points: float, mode: str) -> float:
    threshold = THRESHOLDS[mode]
    pct_floor = abs(float(reference_points)) * threshold["toss_up_pct"] / 100
    return round(max(threshold["toss_up_abs"], pct_floor) + 0.1, 1)


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
    projection_leader = None if ap == bp else (a if ap > bp else b)
    projection_runner_up = None if projection_leader is None else (
        b if projection_leader["id"] == a["id"] else a)
    gap = round(abs(ap - bp), 1)
    reference = max(ap, bp)
    classification = confidence(gap, reference, context.mode)
    no_clear_edge = classification == "Toss-Up"
    winner = None if no_clear_edge else projection_leader
    runner_up = None if no_clear_edge else projection_runner_up
    wf = _format(projection_leader, context.scoring_format) if projection_leader else af
    rf = _format(projection_runner_up, context.scoring_format) if projection_runner_up else bf
    # Inputs are published to one decimal. A tenth beyond equality is the
    # smallest honest threshold that actually changes the recommendation.
    flip = round(gap + 0.1, 1)
    changes = []
    for fmt in sorted(set(a.get("formats", {})) & set(b.get("formats", {}))):
        if fmt == context.scoring_format:
            continue
        try:
            other = compare_shallow(a, b, fmt, context.mode)
        except ValueError:
            continue
        leader_id = winner["id"] if winner else None
        if other["winner_id"] != leader_id:
            changes.append(FORMAT_LABELS[fmt])
    market = "not_applicable" if no_clear_edge else "unavailable"
    if winner and runner_up:
        wa, ra = winner.get("adp"), runner_up.get("adp")
        if wa is not None and ra is not None:
            market = "disagrees" if float(wa) > float(ra) else "agrees"
    return {
        "winner": winner,
        "runner_up": runner_up,
        "projection_leader": projection_leader,
        "projection_runner_up": projection_runner_up,
        "player_a": a, "player_b": b,
        "player_a_format": af, "player_b_format": bf,
        "winner_format": wf,
        "runner_up_format": rf,
        "gap": gap,
        "gap_percent": gap_percent(gap, reference),
        "confidence": classification,
        "call": "No clear edge" if no_clear_edge else winner["name"],
        "recommendation": None if no_clear_edge else winner["id"],
        "no_clear_edge": no_clear_edge,
        "meaningful_gap_to_call": meaningful_gap_to_call(reference, context.mode),
        "runner_up_gain_to_flip": flip,
        "winner_decline_to_flip": flip,
        "format_flips": changes,
        "market_alignment": market,
        "context": context,
        "is_tie": ap == bp,
    }


def compare_shallow(a: dict, b: dict, scoring_format: str,
                    mode: str = "season") -> dict:
    af, bf = _format(a, scoring_format), _format(b, scoring_format)
    ap = round(float(af["projected_points"]), 1)
    bp = round(float(bf["projected_points"]), 1)
    gap = round(abs(ap - bp), 1)
    classification = confidence(gap, max(ap, bp), mode)
    winner = None if classification == "Toss-Up" else (a if ap > bp else b)
    return {"winner_id": winner["id"] if winner else None,
            "gap": gap, "gap_percent": gap_percent(gap, max(ap, bp)),
            "confidence": classification}


def editorial_for_pair(a: dict, b: dict, opinions: list[dict]) -> dict | None:
    pair = {a.get("id"), b.get("id")}
    for opinion in opinions:
        if {opinion.get("subject_id"), opinion.get("preferred_over_id")} == pair:
            return opinion
    return None


def _history(player: dict, scoring_format: str) -> dict | None:
    history = (player.get("history") or {}).get(scoring_format)
    if history and int(history.get("games") or 0) >= MIN_HISTORY_GAMES:
        return history
    return None


def evidence_stack(a: dict, b: dict, context: DecisionContext,
                   opinions: list[dict] | None = None,
                   sources: dict | None = None) -> dict:
    """Build factual comparison evidence without narrative inference."""
    result = compare(a, b, context)
    af, bf = result["player_a_format"], result["player_b_format"]
    common_formats = sorted(set(a.get("formats", {})) & set(b.get("formats", {})))
    ah, bh = _history(a, context.scoring_format), _history(b, context.scoring_format)
    editorial = editorial_for_pair(a, b, opinions or [])
    opponent_context = bool(a.get("opponent") and b.get("opponent"))
    categories = {
        "projections": "present",
        "ranks": "present",
        "adp": "present" if a.get("adp") is not None and b.get("adp") is not None else "unavailable",
        "scoring_formats": "present" if len(common_formats) > 1 else "unavailable",
        "history": "present" if ah and bh else "unavailable",
        "editorial": "present" if editorial else "not_documented",
        "schedule_sos": "context_only" if opponent_context else "unavailable",
    }
    present = sum(value in {"present", "context_only"} for value in categories.values())
    quality = "High" if present >= 6 else "Moderate" if present >= 4 else "Limited"
    return {
        "result": result,
        "categories": categories,
        "data_quality": quality,
        "categories_present": present,
        "categories_total": len(categories),
        "history_a": ah, "history_b": bh,
        "editorial": editorial,
        "editorial_stale": bool(editorial and _is_older(
            editorial.get("evidence_date"), (sources or {}).get("projections", {}).get("updated_at"))),
        "common_formats": common_formats,
        "rank_gap": abs(int(af["overall_rank"]) - int(bf["overall_rank"])),
    }


def _is_older(evidence_date: str | None, current_date: str | None) -> bool:
    if not evidence_date or not current_date:
        return False
    try:
        old = date.fromisoformat(evidence_date[:10])
        new = datetime.fromisoformat(current_date).date()
    except ValueError:
        return False
    return old < new


def closest_calls(players: list[dict], scoring_format: str, limit: int = 6,
                  context: DecisionContext | None = None) -> list[dict]:
    calls = []
    caps = {"QB": 24, "RB": 48, "WR": 60, "TE": 30}
    for position, cap in caps.items():
        pool = [p for p in players if p.get("position") == position
                and p.get("formats", {}).get(scoring_format)
                and p["formats"][scoring_format].get("position_rank", 10_000) <= cap]
        pool.sort(key=lambda p: (-p["formats"][scoring_format]["projected_points"], p["name"]))
        for a, b in zip(pool, pool[1:]):
            result = compare(a, b, context or
                             DecisionContext("season", 2026, scoring_format))
            calls.append(result)
    calls.sort(key=lambda r: (r["gap"],
                              (r["winner"] or r["player_a"])["position"],
                              (r["winner"] or r["player_a"])["name"]))
    return calls[:limit]


def strongest_projection_edges(players: list[dict], scoring_format: str,
                                limit: int = 6,
                                context: DecisionContext | None = None) -> list[dict]:
    """Largest adjacent same-position edges among decision-relevant players."""
    rows = closest_calls(players, scoring_format, limit=len(players), context=context)
    rows.sort(key=lambda r: (-r["gap"],
                             (r["winner"] or r["player_a"])["position"],
                             (r["winner"] or r["player_a"])["name"]))
    return rows[:limit]


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
