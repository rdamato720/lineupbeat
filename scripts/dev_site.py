#!/usr/bin/env python3
"""Seed and protect a static development build without touching live inputs.

The development workflow reads the already-public production feed (or the
committed rollback snapshot when explicitly supplied), builds the branch's
pages around it, then marks every HTML document as a non-indexable preview.
Nothing in this module fetches news, odds, rosters, or model output.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
from pathlib import Path


DATA_PLACEHOLDER = (
    '/*__DATA__*/ {"generated_at":new Date().toISOString(),'
    '"sports":{},"players":[]}'
)
ROBOTS_META = '<meta name="robots" content="noindex, nofollow, noarchive">'
FEEDBACK_CSS = '<link rel="stylesheet" href="/feedback.css">'
TRACKING_NEEDLES = (
    "cloudflareinsights.com/beacon.min.js",
    "data-cf-beacon",
    "redditstatic.com/ads/pixel.js",
    "rdt(",
    "static.ads-twitter.com/uwt.js",
    "twq(",
    "window.lbtrack",
    'sessionstorage.getitem("lb_pv")',
)
BANNER_STYLE = """<style id="lb-dev-style">
#lb-dev-banner{position:fixed;top:0;left:0;right:0;z-index:2147483647;
background:#facc15;color:#111827;border-bottom:2px solid #111827;
font:800 12px/30px system-ui,-apple-system,sans-serif;letter-spacing:.08em;
text-align:center;text-transform:uppercase;pointer-events:none}
body{padding-top:30px!important}
</style>"""


def load_feed(path: Path) -> dict:
    try:
        feed = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"development feed is unreadable: {path}: {exc}") from exc
    if not isinstance(feed, dict):
        raise SystemExit("development feed root must be an object")
    if not isinstance(feed.get("sports"), dict):
        raise SystemExit("development feed must contain a sports object")
    if not isinstance(feed.get("players"), list):
        raise SystemExit("development feed must contain a players list")
    for sport, payload in feed["sports"].items():
        if not isinstance(payload, dict) or not isinstance(payload.get("nuggets"), list):
            raise SystemExit(f"development feed sport {sport!r} must contain a nuggets list")
    return feed


def seed(feed_path: Path, template_path: Path, root: Path) -> None:
    feed = load_feed(feed_path)
    template = template_path.read_text()
    if DATA_PLACEHOLDER not in template:
        raise SystemExit(f"development data placeholder missing from {template_path}")

    # Prevent a public feed string from ending the script element early.
    payload = json.dumps(feed, separators=(",", ":")).replace("</", "<\\/")
    page = template.replace(DATA_PLACEHOLDER, payload, 1)
    if "<body" not in page or "</html>" not in page:
        raise SystemExit("refusing to seed malformed development homepage")

    data_path = root / "data" / "feed.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(json.dumps(feed, indent=2) + "\n")
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(page)
    reports = sum(len(value["nuggets"]) for value in feed["sports"].values())
    print(f"seeded {root}/index.html from {feed_path} ({reports} reports)")


def hydrate_db(feed_path: Path, db_path: Path) -> None:
    """Create a disposable page-builder DB from public feed fields only."""
    feed = load_feed(feed_path)
    if db_path.exists():
        db_path.unlink()
    connection = sqlite3.connect(db_path)
    connection.execute("""CREATE TABLE nuggets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedupe_key TEXT NOT NULL,
        sport TEXT NOT NULL,
        player_id TEXT,
        player_name TEXT NOT NULL,
        mention TEXT NOT NULL DEFAULT '',
        team TEXT NOT NULL,
        category TEXT NOT NULL,
        horizon TEXT NOT NULL DEFAULT 'day',
        event TEXT NOT NULL DEFAULT '',
        claim TEXT NOT NULL,
        actionability INTEGER NOT NULL,
        confidence REAL NOT NULL,
        published_at TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '[]',
        attributions TEXT NOT NULL DEFAULT '[]',
        weight REAL NOT NULL DEFAULT 1.0,
        media TEXT NOT NULL DEFAULT '[]'
    )""")
    inserted = 0
    columns = (
        "id", "dedupe_key", "sport", "player_id", "player_name", "mention",
        "team", "category", "horizon", "event", "claim", "actionability",
        "confidence", "published_at", "tags", "attributions", "weight", "media",
    )
    placeholders = ",".join("?" for _ in columns)
    statement = f"INSERT INTO nuggets ({','.join(columns)}) VALUES ({placeholders})"
    for sport, payload in feed["sports"].items():
        for row in payload["nuggets"]:
            if not isinstance(row, dict):
                raise SystemExit(f"development feed sport {sport!r} has a non-object report")
            values = (
                row.get("id"),
                str(row.get("dedupe_key") or f"dev:{sport}:{inserted}"),
                str(row.get("sport") or sport),
                row.get("player_id"),
                str(row.get("player_name") or row.get("mention") or "Unknown"),
                str(row.get("mention") or row.get("player_name") or "Unknown"),
                str(row.get("team") or ""),
                str(row.get("category") or "other"),
                str(row.get("horizon") or "day"),
                str(row.get("event") or ""),
                str(row.get("claim") or ""),
                int(row.get("actionability") or 0),
                float(row.get("confidence") or 0),
                str(row.get("published_at") or feed.get("generated_at") or ""),
                json.dumps(row.get("tags") or []),
                json.dumps(row.get("attributions") or []),
                float(row.get("weight") or 1),
                json.dumps(row.get("media") or []),
            )
            try:
                connection.execute(statement, values)
            except sqlite3.IntegrityError as exc:
                raise SystemExit(f"development feed cannot hydrate report {inserted}: {exc}") from exc
            inserted += 1
    connection.commit()
    connection.close()
    print(f"hydrated {db_path} from {inserted} public reports")


def _protect_page(path: Path, label: str) -> None:
    text = path.read_text()
    if "<head" not in text or "</head>" not in text or "<body" not in text:
        raise SystemExit(f"refusing to protect malformed HTML: {path}")

    # Analytics is a production concern. Remove each complete tracking script
    # from the finished artifact so development views cannot inflate pageview,
    # conversion, search, filter, or engagement numbers. Functional scripts
    # remain byte-for-byte intact.
    def drop_tracking(match: re.Match) -> str:
        script = match.group(0)
        lowered = script.lower()
        return "" if any(needle in lowered for needle in TRACKING_NEEDLES) else script

    text = re.sub(r"<script\b[^>]*>.*?</script>", drop_tracking, text,
                  flags=re.I | re.S)

    text = re.sub(
        r'<meta\s+name=["\']robots["\'][^>]*>',
        ROBOTS_META,
        text,
        count=1,
        flags=re.I,
    )
    if ROBOTS_META not in text:
        text = text.replace("</head>", f"{ROBOTS_META}\n</head>", 1)

    # Load the feedback presentation with the document. Adding it only after
    # feedback.js executes creates a visible unstyled tab on fast navigation.
    if '/feedback.js' in text and '/feedback.css' not in text:
        text = text.replace("</head>", f"{FEEDBACK_CSS}\n</head>", 1)

    text = re.sub(
        r'<style id="lb-dev-style">.*?</style>',
        BANNER_STYLE,
        text,
        count=1,
        flags=re.S,
    )
    if 'id="lb-dev-style"' not in text:
        text = text.replace("</head>", f"{BANNER_STYLE}\n</head>", 1)

    safe_label = html.escape(label, quote=True)
    banner = (
        '<div id="lb-dev-banner" role="status">'
        f'Development preview · {safe_label} · not live</div>'
    )
    text = re.sub(
        r'<div id="lb-dev-banner"[^>]*>.*?</div>',
        banner,
        text,
        count=1,
        flags=re.S,
    )
    if 'id="lb-dev-banner"' not in text:
        body = re.search(r"<body[^>]*>", text, flags=re.I)
        if body is None:
            raise SystemExit(f"body opening tag missing from {path}")
        text = text[: body.end()] + "\n" + banner + text[body.end() :]
    path.write_text(text)


def protect(root: Path, label: str) -> None:
    pages = sorted(root.rglob("*.html"))
    if not pages:
        raise SystemExit(f"no HTML pages found in development artifact: {root}")
    for path in pages:
        _protect_page(path, label)

    (root / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (root / "_headers").write_text(
        "/*\n"
        "  X-Robots-Tag: noindex, nofollow, noarchive\n"
        "  Cache-Control: no-store\n"
    )
    print(f"protected {len(pages)} development HTML pages")


def verify(root: Path) -> None:
    failures: list[str] = []
    pages = sorted(root.rglob("*.html"))
    if not pages:
        failures.append("no HTML pages")
    for path in pages:
        text = path.read_text()
        if text.count('id="lb-dev-banner"') != 1:
            failures.append(f"{path}: development banner count is not one")
        if text.count('id="lb-dev-style"') != 1:
            failures.append(f"{path}: development style count is not one")
        if ROBOTS_META not in text:
            failures.append(f"{path}: robots meta is missing")
        lowered = text.lower()
        remaining = [needle for needle in TRACKING_NEEDLES if needle in lowered]
        if remaining:
            failures.append(f"{path}: analytics remains ({', '.join(remaining)})")
    headers = root / "_headers"
    if not headers.is_file() or "X-Robots-Tag: noindex" not in headers.read_text():
        failures.append("Cloudflare noindex header is missing")
    robots = root / "robots.txt"
    if not robots.is_file() or "Disallow: /" not in robots.read_text():
        failures.append("robots.txt does not block crawling")
    if failures:
        raise SystemExit("development artifact is unsafe:\n  " + "\n  ".join(failures))
    print(f"verified {len(pages)} protected development HTML pages")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    seed_parser = sub.add_parser("seed")
    seed_parser.add_argument("--feed", type=Path, required=True)
    seed_parser.add_argument("--template", type=Path, default=Path("site/template.html"))
    seed_parser.add_argument("--root", type=Path, default=Path("site"))
    hydrate_parser = sub.add_parser("hydrate-db")
    hydrate_parser.add_argument("--feed", type=Path, required=True)
    hydrate_parser.add_argument("--db", type=Path, required=True)
    protect_parser = sub.add_parser("protect")
    protect_parser.add_argument("--root", type=Path, default=Path("site"))
    protect_parser.add_argument("--label", default="development")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--root", type=Path, default=Path("site"))
    args = parser.parse_args()

    if args.command == "seed":
        seed(args.feed, args.template, args.root)
    elif args.command == "hydrate-db":
        hydrate_db(args.feed, args.db)
    elif args.command == "protect":
        protect(args.root, args.label)
    else:
        verify(args.root)


if __name__ == "__main__":
    main()
