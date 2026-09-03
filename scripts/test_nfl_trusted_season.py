#!/usr/bin/env python3
"""Regression checks for the development-only trusted-current release."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import build_nfl_trusted_season as trusted


def digest(value) -> str:
    return hashlib.sha256(trusted.dump(value).encode()).hexdigest()


def main() -> None:
    model, rankings, withheld = trusted.build()
    players = model["players"]
    by_name = {p["name"]: p for p in players}

    assert len(players) == 424
    assert model["metadata"]["position_counts"] == {"QB": 37, "RB": 108, "TE": 108, "WR": 171}
    assert len(withheld["players"]) == 81
    assert len({p["gsis_id"] for p in players}) == 424
    assert all(p["status"] == "ACT" for p in players)
    assert all(p["disposition"] == "trusted_baseline_exact_current_active_match" for p in players)
    assert all(p["methodology_version"] == trusted.VERSION for p in players)
    assert model["metadata"]["external_provider_requests"] == 0
    assert model["metadata"]["recommendations_enabled"] is False
    assert model["metadata"]["production_deployment_authorized"] is False

    expected = {
        "Josh Allen": 361.1,
        "Caleb Williams": 317.1,
        "James Cook": 271.8,
        "Bhayshul Tuten": 186.2,
        "Tony Pollard": 160.9,
        "Rico Dowdle": 156.0,
    }
    for name, points in expected.items():
        assert by_name[name]["formats"]["ppr"] == points, (name, by_name[name]["formats"]["ppr"])
    assert "Josh Jacobs" not in by_name

    for fmt in ("ppr", "half_ppr", "non_ppr"):
        rows = rankings["formats"][fmt]["rows"]
        assert len(rows) == 424
        assert len({r["gsis_id"] for r in rows}) == 424
        assert [r["fantasy_points"] for r in rows] == sorted(
            (p["formats"][fmt] for p in players), reverse=True
        )

    model2, rankings2, withheld2 = trusted.build()
    assert digest(model) == digest(model2)
    assert digest(rankings) == digest(rankings2)
    assert digest(withheld) == digest(withheld2)
    print(json.dumps({
        "trusted": len(players),
        "withheld": len(withheld["players"]),
        "formats": 3,
        "deterministic": True,
        "recommendations_enabled": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
