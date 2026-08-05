#!/usr/bin/env python3
"""Find out whether your beat writers are actually on Bluesky, then emit config.

    python scripts/bluesky_audit.py --names writers.txt --sport nfl
    python scripts/bluesky_audit.py --names writers.txt --sport nfl --emit >> sources/nfl.yaml

Input is one writer per line: `Name, TEAM`

    Zack Rosenblatt, NYJ
    Jeff Howe, NE

Free API access is necessary but not sufficient. The question that decides
whether this adapter earns its place is coverage: what share of your target
writers post there, and how recently. This answers that empirically in a few
minutes instead of by assumption, and then converts the hits straight into
source config so the audit doubles as the registry work.

Judgement call you still have to make: `searchActors` matches on display name
and description, so a common name can return the wrong person. Anything below
the confidence bar is printed for review rather than auto-emitted. Check the
handles before you trust them; attributing a claim to the wrong reporter is
worse than missing it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://public.api.bsky.app/xrpc"
UA = {"User-Agent": "beatwire-bluesky-audit/1.0"}


def get(endpoint: str, params: dict, timeout: int = 20) -> dict:
    url = f"{API}/{endpoint}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# Words in a profile description that suggest this really is a beat reporter
# rather than a fan account using the same name.
BEAT_WORDS = [
    "beat", "reporter", "covers", "covering", "writer", "insider",
    "correspondent", "journalist", "staff writer",
]


def score_candidate(actor: dict, name: str, team: str) -> float:
    """Cheap confidence heuristic. Not clever, just enough to sort."""
    display = (actor.get("displayName") or "").lower()
    desc = (actor.get("description") or "").lower()
    handle = (actor.get("handle") or "").lower()
    n = name.lower()

    s = 0.0
    if n == display:
        s += 0.5
    elif all(part in display for part in n.split()):
        s += 0.35
    if n.replace(" ", "") in handle.replace(".", "").replace("-", ""):
        s += 0.2
    if any(w in desc for w in BEAT_WORDS):
        s += 0.25
    if team.lower() in desc:
        s += 0.15
    return min(1.0, s)


def last_post(handle: str) -> tuple[datetime | None, int]:
    """Recency is the real signal. An account that exists but has not posted
    in three months is not a source, it is a placeholder."""
    try:
        payload = get("app.bsky.feed.getAuthorFeed",
                      {"actor": handle, "limit": 20, "filter": "posts_no_replies"})
    except Exception:
        return None, 0
    feed = payload.get("feed", [])
    newest = None
    for entry in feed:
        rec = (entry.get("post") or {}).get("record") or {}
        raw = rec.get("createdAt")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if newest is None or dt > newest:
            newest = dt
    return newest, len(feed)


def audit(names: list[tuple[str, str]], threshold: float) -> list[dict]:
    out = []
    for name, team in names:
        try:
            res = get("app.bsky.actor.searchActors", {"q": name, "limit": 10})
        except Exception as exc:
            print(f"  ! {name}: search failed ({exc})", file=sys.stderr)
            out.append({"name": name, "team": team, "found": False})
            continue

        actors = res.get("actors", [])
        ranked = sorted(
            ((score_candidate(a, name, team), a) for a in actors),
            key=lambda t: t[0], reverse=True,
        )
        if not ranked or ranked[0][0] < 0.35:
            out.append({"name": name, "team": team, "found": False})
            print(f"  [ -- ] {name:<26} {team:<4} not found")
            time.sleep(0.15)
            continue

        conf, actor = ranked[0]
        handle = actor.get("handle", "")
        newest, recent_n = last_post(handle)
        days = (datetime.now(timezone.utc) - newest).days if newest else None

        rec = {
            "name": name, "team": team, "found": True, "handle": handle,
            "display": actor.get("displayName", ""), "confidence": round(conf, 2),
            "last_post_days": days, "recent_posts": recent_n,
            "description": (actor.get("description") or "").replace("\n", " ")[:90],
        }
        out.append(rec)

        flag = "ok  " if conf >= threshold else "CHECK"
        age = f"{days}d ago" if days is not None else "no posts"
        print(f"  [{flag}] {name:<26} {team:<4} @{handle:<28} "
              f"conf={conf:.2f} last={age}")
        time.sleep(0.15)
    return out


def summarize(rows: list[dict], threshold: float, stale_days: int) -> None:
    found = [r for r in rows if r.get("found")]
    confident = [r for r in found if r["confidence"] >= threshold]
    active = [r for r in confident
              if r["last_post_days"] is not None and r["last_post_days"] <= stale_days]

    total = len(rows)
    print(f"\n  searched          {total}")
    print(f"  found             {len(found)}  ({len(found)/total*100:.0f}%)" if total else "")
    print(f"  confident match   {len(confident)}")
    print(f"  active (<{stale_days}d)     {len(active)}  "
          f"({len(active)/total*100:.0f}% of your target list)" if total else "")

    print()
    if total and len(active) / total >= 0.5:
        print("  Worth building on. Over half your writers are active there.")
    elif total and len(active) / total >= 0.25:
        print("  Useful supplement, not a primary source. Wire it up, but do not")
        print("  let it displace effort on local feeds and audio.")
    else:
        print("  Thin. Coverage does not justify the integration yet. Recheck in a")
        print("  few months rather than building against it now.")


def emit_yaml(rows: list[dict], sport: str, threshold: float, stale_days: int) -> None:
    keep = [r for r in rows
            if r.get("found") and r["confidence"] >= threshold
            and r["last_post_days"] is not None and r["last_post_days"] <= stale_days]
    print("\n  # --- paste into sources/%s.yaml under `sources:` ---" % sport)
    for r in keep:
        slug = r["handle"].split(".")[0].replace("-", "")[:14]
        print(f"""
  - id: {sport}-{r['team'].lower()}-bsky-{slug}
    kind: bluesky
    handle: {r['handle']}
    name: {r['name']}
    outlet: Bluesky
    teams: [{r['team']}]""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", required=True, help="file of 'Name, TEAM' lines")
    ap.add_argument("--sport", default="nfl")
    ap.add_argument("--threshold", type=float, default=0.6,
                    help="confidence below this is printed for review, not emitted")
    ap.add_argument("--stale-days", type=int, default=30)
    ap.add_argument("--emit", action="store_true", help="print yaml for confident hits")
    ap.add_argument("--json", help="write full results here")
    args = ap.parse_args()

    names = []
    for line in Path(args.names).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, team = line.partition(",")
        names.append((name.strip(), team.strip().upper()))

    if not names:
        sys.exit("  no names parsed. Expected 'Name, TEAM' per line.")

    print(f"  auditing {len(names)} writers\n")
    rows = audit(names, args.threshold)
    summarize(rows, args.threshold, args.stale_days)

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2))
        print(f"\n  wrote {args.json}")
    if args.emit:
        emit_yaml(rows, args.sport, args.threshold, args.stale_days)


if __name__ == "__main__":
    main()
