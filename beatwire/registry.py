"""Loads the per-sport configuration.

Adding a sport should require exactly three things and no code:
  1. sources/<sport>.yaml   - who to poll
  2. rosters/<sport>.csv    - player id, name, team, position, aliases
  3. a `profile` block in sources/<sport>.yaml describing what matters

If adding a sport ever requires touching pipeline.py, the abstraction has
leaked and should be fixed there rather than special-cased here.
"""

from __future__ import annotations

import csv
from pathlib import Path

import yaml

from .models import Player, Source

ROOT = Path(__file__).resolve().parent.parent


class SportProfile:
    """Sport-specific guidance handed to the extractor at prompt time.

    This is the only place sport knowledge lives. It exists because "what
    counts as an actionable nugget" is genuinely different per sport: a goalie
    confirmation is the whole ballgame in the NHL and meaningless in golf.
    """

    def __init__(self, sport: str, raw: dict):
        self.sport = sport
        self.display = raw.get("display", sport.upper())
        self.positions: list[str] = raw.get("positions", [])
        self.high_value: list[str] = raw.get("high_value", [])
        self.low_value: list[str] = raw.get("low_value", [])
        self.notes: str = raw.get("notes", "")
        # Maps a coarse hint the extractor can infer from context ("P") onto
        # the roster positions it covers (["SP", "RP", "P"]). Used to break
        # surname ties, which matters enormously in MLB and barely at all in
        # the NFL.
        self.position_groups: dict[str, list[str]] = raw.get("position_groups", {})

    def prompt_block(self) -> str:
        lines = [f"Sport: {self.display}"]
        if self.positions:
            lines.append("Positions: " + ", ".join(self.positions))
        if self.position_groups:
            lines.append(
                "If context makes the player's role obvious, set `position_hint` "
                "to one of: " + ", ".join(self.position_groups)
            )
        if self.high_value:
            lines.append(
                "Treat these as high actionability (3):\n  - "
                + "\n  - ".join(self.high_value)
            )
        if self.low_value:
            lines.append(
                "Treat these as low actionability (0-1):\n  - "
                + "\n  - ".join(self.low_value)
            )
        if self.notes:
            lines.append("Notes: " + self.notes)
        return "\n".join(lines)


class Registry:
    def __init__(self, sport: str, root: Path = ROOT,
                 load_players: bool = True):
        self.sport = sport
        self.root = root
        cfg_path = root / "sources" / f"{sport}.yaml"
        if not cfg_path.exists():
            raise FileNotFoundError(
                f"No source registry for '{sport}'. Create {cfg_path}."
            )
        raw = yaml.safe_load(cfg_path.read_text())

        self.profile = SportProfile(sport, raw.get("profile", {}))
        self.sources = [
            Source(sport=sport, **s) for s in raw.get("sources", [])
        ]
        # Capture-only source polling is also used by the editorial Wire.
        # That boundary may read source identities but must not read the
        # fantasy roster (which carries rank, ADP and depth fields), so raw
        # capture can deliberately omit it.
        self.players = self._load_roster() if load_players else []

    def _load_roster(self) -> list[Player]:
        path = self.root / "rosters" / f"{self.sport}.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"No roster for '{self.sport}'. Create {path} with columns: "
                "id,name,team,position,aliases"
            )
        players = []
        with path.open() as fh:
            for row in csv.DictReader(fh):
                aliases = [
                    a.strip()
                    for a in (row.get("aliases") or "").split("|")
                    if a.strip()
                ]
                players.append(
                    Player(
                        id=row["id"],
                        sport=self.sport,
                        name=row["name"],
                        team=row["team"],
                        position=row.get("position", ""),
                        aliases=aliases,
                        espn_id=(row.get("espn_id") or "").strip(),
                        rank=int(row.get("rank") or 0),
                        depth_pos=(row.get("depth_pos") or "").strip(),
                        depth_order=int(row.get("depth_order") or 0),
                        injury_status=(row.get("injury_status") or "").strip(),
                        years_exp=int(row["years_exp"]) if (row.get("years_exp") or "").strip() else -1,
                        adp=float(row["adp"]) if (row.get("adp") or "").strip() else 0.0,
                    )
                )
        return players

    @property
    def enabled_sources(self) -> list[Source]:
        return [s for s in self.sources if s.enabled]

    def players_for_team(self, team: str) -> list[Player]:
        return [p for p in self.players if p.team == team]
