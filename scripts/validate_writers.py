#!/usr/bin/env python3
"""Validate a seed list of beat writer handles, then emit source config.

    python scripts/validate_writers.py --file writers.nfl.txt --check-bluesky
    python scripts/validate_writers.py --file writers.nfl.txt --check-x
    python scripts/validate_writers.py --file writers.nfl.txt --check-x --emit x

A published beat-writer list goes stale fast: outlets fold, people change
beats, accounts go quiet. A two-year-old list is a set of leads, not a roster.
This turns leads into verified sources, or tells you which ones to drop.

Bluesky checks are free. X checks cost money, so they are priced up front and
you have to pass --yes to spend anything:

    68 handles, profile lookup only   ~$0.68   (User: Read at $0.010)
    68 handles, plus recency check    ~$7.50   (adds Posts: Read at $0.005)

That is a rounding error against getting the list wrong, which costs you a
whole team's coverage for a season.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BSKY = "https://public.api.bsky.app/xrpc"
X_API = "https://api.x.com/2"
X_USER_READ = 0.010
X_POST_READ = 0.005


def parse_list(path: Path) -> list[tuple[str, str]]:
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        handle, _, team = line.partition(",")
        handle = handle.strip().lstrip("@")
        team = team.strip().upper()
        if handle and team:
            out.append((handle, team))
    return out


def _get(url: str, headers: dict, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "lineupbeat/1.0", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------------------
# Bluesky: free, so check everything
# ---------------------------------------------------------------------------

def bluesky_lookup(handle: str, team: str) -> dict:
    """Journalists commonly reuse their X handle stem on Bluesky, so try the
    obvious constructions first, then fall back to search."""
    candidates = [f"{handle}.bsky.social", handle.replace("_", "") + ".bsky.social"]
    for actor in candidates:
        try:
            prof = _get(f"{BSKY}/app.bsky.actor.getProfile?"
                        + urllib.parse.urlencode({"actor": actor}), {})
            return _bsky_result(prof, handle, team, "direct")
        except urllib.error.HTTPError:
            continue
        except Exception:
            continue

    try:
        res = _get(f"{BSKY}/app.bsky.actor.searchActors?"
                   + urllib.parse.urlencode({"q": handle, "limit": 5}), {})
    except Exception:
        return {"handle": handle, "team": team, "bsky": None}
    for actor in res.get("actors", []):
        h = (actor.get("handle") or "").lower()
        stem = handle.lower().replace("_", "")
        if stem and stem in h.replace(".", "").replace("-", ""):
            return _bsky_result(actor, handle, team, "search")
    return {"handle": handle, "team": team, "bsky": None}


def _bsky_result(prof: dict, handle: str, team: str, how: str) -> dict:
    bh = prof.get("handle", "")
    newest, n = _bsky_recency(bh)
    days = (datetime.now(timezone.utc) - newest).days if newest else None
    return {
        "handle": handle, "team": team, "bsky": bh, "via": how,
        "display": prof.get("displayName", ""),
        "last_post_days": days, "recent": n,
    }


def _bsky_recency(actor: str) -> tuple[datetime | None, int]:
    try:
        payload = _get(f"{BSKY}/app.bsky.feed.getAuthorFeed?"
                       + urllib.parse.urlencode(
                           {"actor": actor, "limit": 20, "filter": "posts_no_replies"}), {})
    except Exception:
        return None, 0
    newest = None
    for entry in payload.get("feed", []):
        raw = ((entry.get("post") or {}).get("record") or {}).get("createdAt")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if newest is None or dt > newest:
            newest = dt
    return newest, len(payload.get("feed", []))


# ---------------------------------------------------------------------------
# X: metered, so ask before spending
# ---------------------------------------------------------------------------

def x_lookup(handle: str, team: str, token: str, recency: bool) -> dict:
    hdr = {"Authorization": f"Bearer {token}"}
    rec = {"handle": handle, "team": team, "x_id": None, "cost": 0.0}
    try:
        prof = _get(f"{X_API}/users/by/username/{handle}?user.fields=username", hdr)
        rec["cost"] += X_USER_READ
    except urllib.error.HTTPError as e:
        rec["error"] = f"HTTP {e.code}"      # 404 means the account is gone
        rec["cost"] += X_USER_READ
        return rec
    except Exception as e:
        rec["error"] = str(e)[:40]
        return rec

    data = prof.get("data") or {}
    rec["x_id"] = data.get("id")
    rec["x_handle"] = data.get("username", handle)
    if not (recency and rec["x_id"]):
        return rec

    try:
        tl = _get(f"{X_API}/users/{rec['x_id']}/tweets?"
                  + urllib.parse.urlencode(
                      {"max_results": 5, "tweet.fields": "created_at",
                       "exclude": "retweets,replies"}), hdr)
        posts = tl.get("data") or []
        rec["cost"] += len(posts) * X_POST_READ
        if posts:
            newest = max(
                datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
                for p in posts if p.get("created_at")
            )
            rec["last_post_days"] = (datetime.now(timezone.utc) - newest).days
        else:
            rec["last_post_days"] = None
    except Exception as e:
        rec["error"] = str(e)[:40]
    return rec


# ---------------------------------------------------------------------------

def emit(rows: list[dict], platform: str, sport: str, stale_days: int) -> None:
    print(f"\n  # --- paste into sources/{sport}.yaml under `sources:` ---")
    for r in rows:
        fresh = r.get("last_post_days")
        if fresh is None or fresh > stale_days:
            continue
        if platform == "bluesky" and r.get("bsky"):
            slug = r["handle"].lower().replace("_", "")[:14]
            print(f"""
  - id: {sport}-{r['team'].lower()}-bsky-{slug}
    kind: bluesky
    handle: {r['bsky']}
    name: {r.get('display') or r['handle']}
    outlet: Bluesky
    teams: [{r['team']}]""")
        elif platform == "x" and r.get("x_id"):
            slug = r["handle"].lower().replace("_", "")[:14]
            print(f"""
  - id: {sport}-{r['team'].lower()}-x-{slug}
    kind: x
    handle: {r.get('x_handle', r['handle'])}
    x_user_id: "{r['x_id']}"
    name: {r['handle']}
    outlet: X
    teams: [{r['team']}]""")


def summarize(rows: list[dict], key: str, stale_days: int, total: int) -> None:
    live = [r for r in rows if r.get(key)]
    active = [r for r in live
              if r.get("last_post_days") is not None
              and r["last_post_days"] <= stale_days]
    teams_covered = {r["team"] for r in active}

    print(f"\n  checked           {total}")
    print(f"  account exists    {len(live)}")
    print(f"  active (<{stale_days}d)     {len(active)}")
    print(f"  teams covered     {len(teams_covered)} / 32")

    thin = sorted({r["team"] for r in rows} - teams_covered)
    if thin:
        print(f"\n  NO active writer found for: {', '.join(thin)}")
        print("  These are holes your users will notice. Backfill them by hand.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--check-bluesky", action="store_true")
    ap.add_argument("--check-x", action="store_true")
    ap.add_argument("--recency", action="store_true", default=True)
    ap.add_argument("--stale-days", type=int, default=45)
    ap.add_argument("--emit", choices=["bluesky", "x"])
    ap.add_argument("--json")
    ap.add_argument("--yes", action="store_true", help="approve X spend")
    args = ap.parse_args()

    writers = parse_list(Path(args.file))
    if not writers:
        sys.exit("  no handles parsed")
    print(f"  {len(writers)} handles across {len({t for _, t in writers})} teams")

    rows = []

    if args.check_bluesky:
        print("\n  checking Bluesky (free)\n")
        for h, team in writers:
            r = bluesky_lookup(h, team)
            rows.append(r)
            if r.get("bsky"):
                age = (f"{r['last_post_days']}d ago"
                       if r.get("last_post_days") is not None else "no posts")
                print(f"  [ ok ] {h:<22} {team:<4} -> @{r['bsky']:<26} last={age}")
            else:
                print(f"  [ -- ] {h:<22} {team:<4} not on Bluesky")
            time.sleep(0.12)
        summarize(rows, "bsky", args.stale_days, len(writers))

    if args.check_x:
        token = os.environ.get("X_BEARER_TOKEN")
        if not token:
            sys.exit("  X_BEARER_TOKEN not set")
        est = len(writers) * X_USER_READ + (
            len(writers) * 5 * X_POST_READ if args.recency else 0)
        print(f"\n  X check will cost about ${est:.2f}")
        if not args.yes:
            sys.exit("  rerun with --yes to approve that spend")

        print()
        spent = 0.0
        for h, team in writers:
            r = x_lookup(h, team, token, args.recency)
            spent += r.get("cost", 0)
            rows.append(r)
            if r.get("x_id"):
                age = (f"{r['last_post_days']}d ago"
                       if r.get("last_post_days") is not None else "no posts")
                print(f"  [ ok ] {h:<22} {team:<4} id={r['x_id']:<20} last={age}")
            else:
                print(f"  [GONE] {h:<22} {team:<4} {r.get('error','')}")
            time.sleep(0.2)
        print(f"\n  spent ${spent:.2f}")
        summarize(rows, "x_id", args.stale_days, len(writers))

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2))
        print(f"\n  wrote {args.json}")
    if args.emit:
        emit(rows, args.emit, args.sport, args.stale_days)


if __name__ == "__main__":
    main()
