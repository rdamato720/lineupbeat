#!/usr/bin/env python3
"""Collect private sportsbook consensus inputs for projection work.

The public site never reads these tables.  The collector writes only to the
ignored runtime database and exposes small read helpers for projection jobs.

Examples:

    python scripts/odds_inputs.py --sports nfl,ncaaf
    python scripts/odds_inputs.py --sports nfl --include-props --force
    python scripts/odds_inputs.py --report

The API key must be supplied as ``THE_ODDS_API_KEY``.  It is never written to
SQLite or included in an error message.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
API_ROOT = "https://api.the-odds-api.com/v4"
SPORT_KEYS = {
    "nfl": "americanfootball_nfl",
    "ncaaf": "americanfootball_ncaaf",
}
FEATURED_MARKETS = ("h2h", "spreads", "totals")
PROP_MARKETS = (
    "player_pass_yds",
    "player_pass_tds",
    "player_rush_yds",
    "player_receptions",
    "player_reception_yds",
    "player_anytime_td",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS odds_fetch_runs (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sport_key TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    include_props INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    prop_event_count INTEGER NOT NULL DEFAULT 0,
    credits_used INTEGER,
    credits_remaining INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_odds_runs_sport_time
    ON odds_fetch_runs(sport_key, fetched_at DESC);

CREATE TABLE IF NOT EXISTS odds_events (
    snapshot_id INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    sport_key TEXT NOT NULL,
    commence_time TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    game_total REAL,
    home_spread REAL,
    home_win_probability REAL,
    home_implied_total REAL,
    away_implied_total REAL,
    total_book_count INTEGER NOT NULL DEFAULT 0,
    spread_book_count INTEGER NOT NULL DEFAULT 0,
    moneyline_book_count INTEGER NOT NULL DEFAULT 0,
    quality TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, event_id)
);

CREATE TABLE IF NOT EXISTS odds_quotes (
    snapshot_id INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    bookmaker_key TEXT NOT NULL,
    bookmaker_updated_at TEXT,
    market_key TEXT NOT NULL,
    participant TEXT NOT NULL DEFAULT '',
    outcome_name TEXT NOT NULL,
    line REAL,
    american_price REAL,
    PRIMARY KEY (
        snapshot_id, event_id, bookmaker_key, market_key,
        participant, outcome_name, line
    )
);

CREATE TABLE IF NOT EXISTS odds_player_props (
    snapshot_id INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    market_key TEXT NOT NULL,
    player_name TEXT NOT NULL,
    consensus_line REAL,
    fair_over_probability REAL,
    book_count INTEGER NOT NULL,
    line_dispersion REAL,
    quality TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, event_id, market_key, player_name)
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).astimezone(timezone.utc).isoformat()


def median(values):
    values = [float(value) for value in values if value is not None]
    return statistics.median(values) if values else None


def american_probability(price):
    if price is None:
        return None
    price = float(price)
    if price == 0:
        return None
    return 100.0 / (price + 100.0) if price > 0 else -price / (-price + 100.0)


def devig_probability(over_price, under_price):
    over = american_probability(over_price)
    under = american_probability(under_price)
    if over is None or under is None or over + under <= 0:
        return None
    return over / (over + under)


def quality(book_count: int, dispersion: float | None = None) -> str:
    if book_count >= 3 and (dispersion is None or dispersion <= 2.0):
        return "HIGH"
    if book_count >= 2:
        return "MEDIUM"
    return "LOW"


def iter_quotes(event: dict):
    """Yield normalized quotes without retaining the provider response."""
    for bookmaker in event.get("bookmakers") or []:
        book = str(bookmaker.get("key") or "")
        updated = bookmaker.get("last_update")
        for market in bookmaker.get("markets") or []:
            market_key = str(market.get("key") or "")
            for outcome in market.get("outcomes") or []:
                # Player-prop outcomes use description for the player and
                # name for Over/Under. Featured markets have no description.
                participant = str(outcome.get("description") or "")
                if (market_key == "player_anytime_td" and not participant
                        and str(outcome.get("name") or "").lower() not in {"yes", "no"}):
                    participant = str(outcome.get("name") or "")
                yield {
                    "event_id": str(event.get("id") or ""),
                    "bookmaker_key": book,
                    "bookmaker_updated_at": updated,
                    "market_key": market_key,
                    "participant": participant,
                    "outcome_name": str(outcome.get("name") or ""),
                    "line": outcome.get("point"),
                    "american_price": outcome.get("price"),
                }


def event_consensus(event: dict) -> dict:
    quotes = list(iter_quotes(event))
    home = str(event.get("home_team") or "")
    away = str(event.get("away_team") or "")

    totals = [q["line"] for q in quotes
              if q["market_key"] == "totals"
              and q["outcome_name"].lower() == "over"]
    spreads = [q["line"] for q in quotes
               if q["market_key"] == "spreads" and q["outcome_name"] == home]

    total_books = {q["bookmaker_key"] for q in quotes
                   if q["market_key"] == "totals" and q["line"] is not None}
    spread_books = {q["bookmaker_key"] for q in quotes
                    if q["market_key"] == "spreads" and q["line"] is not None}

    h2h = defaultdict(dict)
    for quote in quotes:
        if quote["market_key"] == "h2h" and quote["outcome_name"] in {home, away}:
            h2h[quote["bookmaker_key"]][quote["outcome_name"]] = quote["american_price"]
    win_probabilities = []
    for book in h2h.values():
        hp = american_probability(book.get(home))
        ap = american_probability(book.get(away))
        if hp is not None and ap is not None and hp + ap > 0:
            win_probabilities.append(hp / (hp + ap))

    game_total = median(totals)
    home_spread = median(spreads)
    home_implied = away_implied = None
    if game_total is not None and home_spread is not None:
        # Provider spread convention: a favorite is negative.
        home_implied = game_total / 2.0 - home_spread / 2.0
        away_implied = game_total - home_implied

    counts = (len(total_books), len(spread_books), len(win_probabilities))
    # Implied team totals depend on the total and spread. Moneyline coverage
    # is useful context but should not lower the quality of those two inputs.
    available_counts = [count for count in counts[:2] if count]
    consensus_quality = quality(min(available_counts) if available_counts else 0)
    return {
        "event_id": str(event.get("id") or ""),
        "sport_key": str(event.get("sport_key") or ""),
        "commence_time": str(event.get("commence_time") or ""),
        "home_team": home,
        "away_team": away,
        "game_total": game_total,
        "home_spread": home_spread,
        "home_win_probability": median(win_probabilities),
        "home_implied_total": home_implied,
        "away_implied_total": away_implied,
        "total_book_count": counts[0],
        "spread_book_count": counts[1],
        "moneyline_book_count": counts[2],
        "quality": consensus_quality,
    }


def props_consensus(event: dict) -> list[dict]:
    grouped = defaultdict(lambda: defaultdict(dict))
    for quote in iter_quotes(event):
        if not quote["market_key"].startswith("player_") or not quote["participant"]:
            continue
        grouped[(quote["market_key"], quote["participant"])][
            quote["bookmaker_key"]
        ][quote["outcome_name"].lower()] = quote

    rows = []
    for (market_key, player_name), books in sorted(grouped.items()):
        lines = []
        probabilities = []
        usable_books = set()
        for book_key, outcomes in books.items():
            over = outcomes.get("over")
            under = outcomes.get("under")
            # Anytime touchdown is a one-sided Yes/No-style market. Its
            # participant is still the player, while the quote price alone
            # carries the probability and cannot be de-vigged safely.
            if market_key == "player_anytime_td":
                candidates = list(outcomes.values())
                if candidates:
                    probability = american_probability(candidates[0]["american_price"])
                    if probability is not None:
                        probabilities.append(probability)
                        usable_books.add(book_key)
                continue
            if not over or not under or over["line"] is None or under["line"] is None:
                continue
            lines.append((float(over["line"]) + float(under["line"])) / 2.0)
            probability = devig_probability(over["american_price"], under["american_price"])
            if probability is not None:
                probabilities.append(probability)
            usable_books.add(book_key)

        consensus_line = median(lines)
        dispersion = statistics.pstdev(lines) if len(lines) > 1 else (0.0 if lines else None)
        book_count = len(usable_books)
        if not book_count:
            continue
        rows.append({
            "event_id": str(event.get("id") or ""),
            "market_key": market_key,
            "player_name": player_name,
            "consensus_line": consensus_line,
            "fair_over_probability": median(probabilities),
            "book_count": book_count,
            "line_dispersion": dispersion,
            "quality": quality(book_count, dispersion),
        })
    return rows


class OddsClient:
    def __init__(self, api_key: str, timeout: int = 30):
        if not api_key:
            raise ValueError("THE_ODDS_API_KEY is not configured")
        self.api_key = api_key
        self.timeout = timeout
        self.credits_used = None
        self.credits_remaining = None

    def _safe_error(self, value) -> str:
        text = str(value).replace(self.api_key, "[redacted]")
        return text[:500]

    def get(self, path: str, **params):
        query = urllib.parse.urlencode({**params, "apiKey": self.api_key})
        url = f"{API_ROOT}/{path.lstrip('/')}?{query}"
        request = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/odds-inputs-v1"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self.credits_used = _int_header(response.headers, "x-requests-used")
                self.credits_remaining = _int_header(response.headers, "x-requests-remaining")
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(1024).decode("utf-8", "replace")
            raise RuntimeError(self._safe_error(f"odds API HTTP {exc.code}: {detail}")) from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(self._safe_error(f"odds API request failed: {exc}")) from None


def _int_header(headers, name):
    try:
        return int(headers.get(name))
    except (TypeError, ValueError):
        return None


def connect(path: Path | str):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def is_fresh(conn, sport_key: str, include_props: bool, max_age_hours: float) -> bool:
    after = iso(utcnow() - timedelta(hours=max_age_hours))
    prop_clause = "AND include_props=1" if include_props else ""
    row = conn.execute(
        f"""SELECT 1 FROM odds_fetch_runs
             WHERE sport_key=? AND status='complete' AND fetched_at>=? {prop_clause}
             ORDER BY fetched_at DESC LIMIT 1""",
        (sport_key, after),
    ).fetchone()
    return row is not None


def store_snapshot(conn, sport_key: str, events: list[dict], prop_events: list[dict],
                   client: OddsClient, error: str | None = None,
                   include_props: bool | None = None) -> int:
    fetched_at = iso()
    status = "failed" if error else "complete"
    with conn:
        cursor = conn.execute(
            """INSERT INTO odds_fetch_runs
               (sport_key, fetched_at, include_props, status, event_count,
                prop_event_count, credits_used, credits_remaining, error)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (sport_key, fetched_at,
             bool(prop_events) if include_props is None else include_props,
             status, len(events),
             len(prop_events), client.credits_used, client.credits_remaining, error),
        )
        snapshot_id = int(cursor.lastrowid)
        for event in events:
            row = event_consensus(event)
            conn.execute(
                """INSERT INTO odds_events VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (snapshot_id, row["event_id"], sport_key, row["commence_time"],
                 row["home_team"], row["away_team"], row["game_total"],
                 row["home_spread"], row["home_win_probability"],
                 row["home_implied_total"], row["away_implied_total"],
                 row["total_book_count"], row["spread_book_count"],
                 row["moneyline_book_count"], row["quality"]),
            )
            _store_quotes(conn, snapshot_id, event)
        for event in prop_events:
            _store_quotes(conn, snapshot_id, event)
            for row in props_consensus(event):
                conn.execute(
                    """INSERT INTO odds_player_props VALUES (?,?,?,?,?,?,?,?,?)""",
                    (snapshot_id, row["event_id"], row["market_key"],
                     row["player_name"], row["consensus_line"],
                     row["fair_over_probability"], row["book_count"],
                     row["line_dispersion"], row["quality"]),
                )
    return snapshot_id


def _store_quotes(conn, snapshot_id: int, event: dict):
    for quote in iter_quotes(event):
        conn.execute(
            """INSERT OR REPLACE INTO odds_quotes VALUES (?,?,?,?,?,?,?,?,?)""",
            (snapshot_id, quote["event_id"], quote["bookmaker_key"],
             quote["bookmaker_updated_at"], quote["market_key"],
             quote["participant"], quote["outcome_name"], quote["line"],
             quote["american_price"]),
        )


def latest_team_inputs(conn, sport_key: str) -> list[dict]:
    """Private, projection-ready game environment from the latest snapshot."""
    row = conn.execute(
        """SELECT snapshot_id FROM odds_fetch_runs
           WHERE sport_key=? AND status='complete'
           ORDER BY fetched_at DESC LIMIT 1""",
        (sport_key,),
    ).fetchone()
    if not row:
        return []
    return [dict(value) for value in conn.execute(
        """SELECT event_id, commence_time, home_team, away_team, game_total,
                  home_spread, home_win_probability, home_implied_total,
                  away_implied_total, quality
           FROM odds_events WHERE snapshot_id=? ORDER BY commence_time""",
        (row["snapshot_id"],),
    )]


def latest_snapshot_info(conn, sport_key: str, require_props: bool = False) -> dict | None:
    """Return audit metadata without exposing quotes or provider payloads."""
    prop_clause = "AND r.include_props=1" if require_props else ""
    row = conn.execute(
        f"""SELECT r.snapshot_id, r.sport_key, r.fetched_at, r.include_props,
                   r.event_count, r.prop_event_count, r.credits_used,
                   r.credits_remaining,
                   COUNT(DISTINCT p.player_name) AS player_count,
                   COUNT(p.market_key) AS prop_count
              FROM odds_fetch_runs r
              LEFT JOIN odds_player_props p USING (snapshot_id)
             WHERE r.sport_key=? AND r.status='complete' {prop_clause}
             GROUP BY r.snapshot_id
             ORDER BY r.fetched_at DESC LIMIT 1""",
        (sport_key,),
    ).fetchone()
    return dict(row) if row else None


def latest_player_inputs(conn, sport_key: str) -> list[dict]:
    """Private player-prop consensus from the latest prop-bearing snapshot."""
    row = conn.execute(
        """SELECT snapshot_id FROM odds_fetch_runs
           WHERE sport_key=? AND status='complete' AND include_props=1
           ORDER BY fetched_at DESC LIMIT 1""",
        (sport_key,),
    ).fetchone()
    if not row:
        return []
    return [dict(value) for value in conn.execute(
        """SELECT p.event_id, e.commence_time, e.home_team, e.away_team,
                  p.market_key, p.player_name, p.consensus_line,
                  p.fair_over_probability, p.book_count, p.line_dispersion,
                  p.quality
           FROM odds_player_props p
           JOIN odds_events e USING (snapshot_id, event_id)
           WHERE p.snapshot_id=?
           ORDER BY e.commence_time, p.player_name, p.market_key""",
        (row["snapshot_id"],),
    )]


def fetch_sport(conn, client: OddsClient, sport: str, include_props: bool,
                max_prop_events: int, credit_reserve: int):
    sport_key = SPORT_KEYS[sport]
    events = client.get(
        f"sports/{sport_key}/odds",
        regions="us",
        markets=",".join(FEATURED_MARKETS),
        oddsFormat="american",
        dateFormat="iso",
    )
    if not isinstance(events, list):
        raise RuntimeError(f"odds API returned a non-list event payload for {sport}")

    prop_events = []
    prop_attempts = 0
    if include_props:
        # More heavily covered games are much more likely to carry player
        # markets. Chronological selection burned the NCAAF cap on small early
        # games whose event payloads were valid but contained zero props.
        upcoming = sorted(
            events,
            key=lambda event: (
                -len(event.get("bookmakers") or []),
                event.get("commence_time") or "",
            ),
        )
        max_attempts = min(len(upcoming), max_prop_events * 2)
        for event in upcoming:
            if len(prop_events) >= max_prop_events or prop_attempts >= max_attempts:
                break
            if (client.credits_remaining is not None
                    and client.credits_remaining - credit_reserve < len(PROP_MARKETS)):
                break
            payload = client.get(
                f"sports/{sport_key}/events/{event['id']}/odds",
                regions="us",
                markets=",".join(PROP_MARKETS),
                oddsFormat="american",
                dateFormat="iso",
            )
            prop_attempts += 1
            if isinstance(payload, dict) and props_consensus(payload):
                # The event endpoint may omit sport_key; keep the join stable.
                payload.setdefault("sport_key", sport_key)
                prop_events.append(payload)

    snapshot_id = store_snapshot(
        conn, sport_key, events, prop_events, client,
        include_props=include_props,
    )
    return snapshot_id, len(events), len(prop_events), prop_attempts


def report(conn):
    rows = conn.execute(
        """SELECT r.snapshot_id, r.sport_key, r.fetched_at, r.include_props,
                  r.status, r.event_count, r.prop_event_count,
                  r.credits_used, r.credits_remaining,
                  COUNT(DISTINCT p.player_name) player_count,
                  COUNT(p.market_key) prop_count
           FROM odds_fetch_runs r
           LEFT JOIN odds_player_props p USING (snapshot_id)
           GROUP BY r.snapshot_id
           ORDER BY r.snapshot_id DESC LIMIT 10"""
    ).fetchall()
    if not rows:
        print("  no private odds snapshots")
        return
    print("  private odds snapshots (not published)")
    for row in rows:
        print(
            f"  {row['snapshot_id']:>4} {row['sport_key']:<24} "
            f"events={row['event_count']:<3} prop_events={row['prop_event_count']:<3} "
            f"players={row['player_count']:<4} props={row['prop_count']:<5} "
            f"remaining={row['credits_remaining']} "
            f"{row['fetched_at']}"
        )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "beatwire.db"))
    parser.add_argument("--sports", default="nfl,ncaaf")
    parser.add_argument("--include-props", action="store_true")
    parser.add_argument("--max-prop-events-per-sport", type=int, default=16)
    parser.add_argument("--credit-reserve", type=int, default=75)
    parser.add_argument("--max-age-hours", type=float, default=20)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    if args.report:
        report(conn)
        return 0

    sports = [value.strip().lower() for value in args.sports.split(",") if value.strip()]
    bad = sorted(set(sports) - set(SPORT_KEYS))
    if bad:
        parser.error(f"unsupported sports: {', '.join(bad)}")
    if args.max_prop_events_per_sport < 0 or args.credit_reserve < 0:
        parser.error("credit limits cannot be negative")

    key = os.environ.get("THE_ODDS_API_KEY", "")
    if not key:
        print("  THE_ODDS_API_KEY unavailable; private odds refresh skipped")
        return 0
    client = OddsClient(key)
    failures = []
    for sport in sports:
        sport_key = SPORT_KEYS[sport]
        if not args.force and is_fresh(conn, sport_key, args.include_props, args.max_age_hours):
            print(f"  {sport}: private odds snapshot is fresh; 0 API credits used")
            continue
        try:
            snapshot_id, events, prop_events, prop_attempts = fetch_sport(
                conn, client, sport, args.include_props,
                args.max_prop_events_per_sport, args.credit_reserve,
            )
            print(
                f"  {sport}: snapshot {snapshot_id}, {events} games, "
                f"{prop_events} prop games from {prop_attempts} attempts, "
                f"credits remaining={client.credits_remaining}"
            )
        except Exception as exc:
            safe = client._safe_error(exc)
            failures.append(f"{sport}: {safe}")
            store_snapshot(
                conn, sport_key, [], [], client, error=safe,
                include_props=args.include_props,
            )
            print(f"  {sport}: private odds refresh failed: {safe}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
