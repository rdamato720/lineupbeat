"""The Wire's own player registry. Identity only.

Built from the public nflverse roster release and from nothing else. It does
not read `rosters/nfl.csv` -- not to copy it, not to check against it, not
during generation -- because that file carries ADP and the Wire may not touch
fantasy data at any point in its life, including at build time.

WHAT IS IN IT

A stable id, the names a reporter might use, the current team, the position,
roster status, the season, and where it came from. That is the whole list.
There is no ranking, no projection, no ADP, no ownership and no value, and a
test walks the generated file recursively to prove it.

WHY RESOLUTION IS SO CAUTIOUS

Most of what this resolves comes from automatic captions, where names are
misheard rather than misspelled: Jarran Reed and Jayden Reed are different
people on different teams, and a fuzzy matcher trying to be helpful will pick
the more famous one. So there is no fuzzy matching here at all. An exact
stable id, or a normalised name with a matching team AND position, or
nothing -- and nothing means a human looks at it.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .capture import _get

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "sources" / "wire_players.json"

SOURCE_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
              "rosters/roster_2026.csv")
SEASON = 2026

# Positions the Wire may attach a claim to. Everything else is stored so a
# name can be recognised and set aside -- a safety who is known to be a
# safety is a cheap rejection, where an unknown name costs a review.
CANDIDATE_POSITIONS = {"QB", "RB", "WR", "TE"}
# Linemen are context: "three starting linemen are hurt" affects a fantasy
# player without being about one, so they are kept and never resolved to.
CONTEXT_POSITIONS = {"OL", "T", "G", "C", "OT", "OG"}

NFL_TEAMS = set(
    "ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LAC LAR "
    "LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS".split())

# Exactly what may be copied out of the roster. A whitelist rather than a
# blocklist, so a new upstream column cannot arrive unnoticed.
IDENTITY_FIELDS = ("gsis_id", "full_name", "football_name", "first_name",
                   "last_name", "team", "position", "status", "season")

# Anything shaped like these must never appear in the registry.
FORBIDDEN_FIELDS = ("adp", "rank", "ranking", "projection", "projected",
                    "points", "ppr", "value", "ownership", "owned", "tier",
                    "vorp", "draft_value", "fantasy_points", "fantasy_data_id",
                    "sleeper_id", "yahoo_id", "rotowire_id", "pff_id")

SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.I)

# nflverse writes Arizona as AZ and the Rams as LA; the rest of the Wire uses
# ARI and LAR. Left alone, every Arizona and Rams resolution fails on the team
# check and every one of those players lands in manual review looking like an
# unknown name. Normalised once here so the registry speaks the Wire's codes.
TEAM_ALIASES = {"AZ": "ARI", "LA": "LAR", "SD": "LAC", "OAK": "LV",
                "STL": "LAR", "WSH": "WAS"}


def team_code(raw: str) -> str:
    t = (raw or "").upper().strip()
    return TEAM_ALIASES.get(t, t)


def norm(name: str) -> str:
    """Fold a name the way a reporter's spelling varies, and no further.

    Punctuation and suffixes go, case goes, spacing collapses. Nothing else:
    this is normalisation, not matching, and it must never bring two
    different people together.
    """
    n = re.sub(r"[.'`’\-]", "", (name or "").lower())
    n = SUFFIX.sub(" ", n)
    return " ".join(n.split())


def aliases_for(row: dict) -> list[str]:
    """The forms of a name a beat writer or a caption might produce."""
    full = (row.get("full_name") or "").strip()
    first = (row.get("first_name") or "").strip()
    last = (row.get("last_name") or "").strip()
    football = (row.get("football_name") or "").strip()
    out = {full}
    if football and last:
        out.add(f"{football} {last}")
    if first and last:
        out.add(f"{first} {last}")
        out.add(f"{first[0]}. {last}")
        out.add(f"{first[0]}.{last}")
    return sorted({norm(a) for a in out if a and norm(a)})


@dataclass
class Player:
    player_id: str
    full_name: str
    display_name: str
    aliases: list[str]
    team: str
    position: str
    status: str
    season: int
    fantasy_candidate: bool
    context_only: bool = False


@dataclass
class Registry:
    players: list[Player] = field(default_factory=list)
    version: str = ""
    source_url: str = ""
    source_fetched_at: str = ""
    by_id: dict = field(default_factory=dict)
    _by_key: dict = field(default_factory=dict)

    def index(self):
        self.by_id = {p.player_id: p for p in self.players if p.player_id}
        self._by_key = {}
        for p in self.players:
            for a in p.aliases:
                self._by_key.setdefault((a, p.team, p.position), []).append(p)
        return self

    def resolve(self, name: str, team: str = "", position: str = "",
                player_id: str = "") -> tuple[list[Player], str]:
        """(matches, how). Zero or several matches is never resolved further.

        The caller is expected to treat anything but a single match as
        MANUAL_REVIEW_ONLY. Nothing here narrows a tie by popularity, recency
        or anything else.
        """
        if player_id and player_id in self.by_id:
            return [self.by_id[player_id]], "stable_id"
        key = norm(name)
        if not key:
            return [], "empty_name"
        if not team or not position:
            # A bare name is not an identity. There are two Josh Allens and
            # they play different positions for different teams.
            hits = [p for p in self.players if key in p.aliases]
            return hits, "name_only_insufficient"
        exact = self._by_key.get((key, team_code(team), position.upper()), [])
        if len(exact) == 1:
            return exact, "name_team_position"
        return exact, "ambiguous" if exact else "no_match"


def _row_to_player(row: dict) -> Player | None:
    pos = (row.get("position") or "").upper().strip()
    full = (row.get("full_name") or "").strip()
    if not full:
        return None
    team = team_code(row.get("team"))
    return Player(
        player_id=(row.get("gsis_id") or "").strip(),
        full_name=full,
        display_name=(row.get("football_name") or row.get("first_name")
                      or "").strip(),
        aliases=aliases_for(row),
        team=team,
        position=pos,
        status=(row.get("status") or "").strip(),
        season=int(row.get("season") or SEASON),
        fantasy_candidate=pos in CANDIDATE_POSITIONS,
        context_only=pos in CONTEXT_POSITIONS,
    )


def build_from_csv(text: str, source_url: str = SOURCE_URL) -> dict:
    """The registry payload, from the roster CSV. Identity fields only."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("roster download parsed to zero rows")
    missing = [c for c in ("full_name", "team", "position", "season")
               if c not in rows[0]]
    if missing:
        raise ValueError(f"roster is missing {missing}")

    players, seen = [], set()
    for r in rows:
        p = _row_to_player(r)
        if p is None:
            continue
        # A player traded mid-season appears twice. Keep the first row and
        # note nothing else: this registry describes who somebody is, not
        # where he has been.
        key = p.player_id or f"{norm(p.full_name)}|{p.team}|{p.position}"
        if key in seen:
            continue
        seen.add(key)
        players.append(p)

    digest = hashlib.sha256(text.encode()).hexdigest()
    return {
        "registry_version": digest[:16],
        "source_url": source_url,
        "source_sha256": digest,
        "source_fetched_at": datetime.now(timezone.utc)
        .replace(microsecond=0).isoformat(),
        "season": SEASON,
        "player_count": len(players),
        "identity_fields": list(IDENTITY_FIELDS),
        "players": [
            {"player_id": p.player_id, "full_name": p.full_name,
             "display_name": p.display_name, "aliases": p.aliases,
             "team": p.team, "position": p.position, "status": p.status,
             "season": p.season, "fantasy_candidate": p.fantasy_candidate,
             "context_only": p.context_only}
            for p in players],
    }


def validate(payload: dict) -> list[str]:
    """Refuse a registry that is wrong before it can replace a good one."""
    bad = []
    players = payload.get("players") or []
    if len(players) < 1500:
        bad.append(f"only {len(players)} players; the roster should be "
                   f"thousands -- refusing a partial file")
    ids = [p["player_id"] for p in players if p.get("player_id")]
    if len(ids) != len(set(ids)):
        bad.append("duplicate player_id")
    if not payload.get("registry_version") or not payload.get("source_sha256"):
        bad.append("no registry version or source hash")

    # No fantasy field may exist anywhere in the structure, at any depth.
    def walk(node, path="") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if any(f in str(k).lower() for f in FORBIDDEN_FIELDS):
                    bad.append(f"forbidden field {path}{k!r}")
                walk(v, f"{path}{k}.")
        elif isinstance(node, list):
            for x in node[:50]:
                walk(x, path)
    walk(payload)

    teams = {p["team"] for p in players if p.get("team")}
    if len(teams) < 30:
        bad.append(f"only {len(teams)} teams present")
    stray = sorted(t for t in teams if t in TEAM_ALIASES)
    if stray:
        bad.append(f"un-normalised team code(s) {stray}")
    unknown = sorted(t for t in teams if t not in NFL_TEAMS)
    if unknown:
        bad.append(f"unknown team code(s) {unknown}")
    for p in players[:400]:
        if not p.get("full_name"):
            bad.append("a player with no name")
            break
        if p.get("fantasy_candidate") and p["position"] not in CANDIDATE_POSITIONS:
            bad.append(f"{p['full_name']}: candidate flag on {p['position']}")
            break
    return bad


def fetch(url: str = SOURCE_URL) -> str:
    status, body, _ = _get(url, timeout=90)
    if not (isinstance(status, int) and status == 200 and body):
        raise RuntimeError(f"roster download returned {status}")
    return body


def write_atomic(payload: dict, path: Path | None = None) -> bool:
    """Replace the registry in one move, or not at all.

    Written to a sibling, read back, validated from disk and only then moved
    over the real file. A refresh that fails leaves the last known good
    registry exactly as it was -- the Wire would rather resolve against
    yesterday's roster than against half of today's.
    """
    out = Path(path or REGISTRY)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=1) + "\n")
        reread = json.loads(tmp.read_text())
        problems = validate(reread)
        if problems:
            raise ValueError("; ".join(problems[:3]))
        os.replace(tmp, out)
        return True
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def load(path: Path | None = None) -> Registry:
    p = Path(path or REGISTRY)
    if not p.exists():
        return Registry().index()
    payload = json.loads(p.read_text())
    reg = Registry(
        players=[Player(**{k: v for k, v in row.items()}) for row in
                 payload.get("players", [])],
        version=payload.get("registry_version", ""),
        source_url=payload.get("source_url", ""),
        source_fetched_at=payload.get("source_fetched_at", ""))
    return reg.index()
