#!/usr/bin/env python3
"""Remove development-only fantasy connectors from a production artifact."""

from __future__ import annotations

import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


HIDDEN_ROUTES = ("my-team", "my-league", "league-history")
HIDDEN_ASSETS = (
    "assets/homepage/team-3d.png",
    "assets/homepage/league-3d.png",
    "assets/league-history-dashboard.js",
    "assets/sleeper-history-client.js",
    "assets/sleeper-history.js",
    "assets/yahoo-history.js",
    "data/league-history-demo.json",
    "data/my-team-week1.json",
)
FORBIDDEN_REFERENCES = (
    "/my-team/",
    "/my-league/",
    "/league-history/",
    "/api/yahoo/",
    "/api/leagues/",
)
TEXT_SUFFIXES = {".html", ".css", ".js", ".json", ".xml", ".txt"}


def scrub_connector_navigation(text: str) -> str:
    """Remove connector links from retained pages built with older chrome."""
    routes = "|".join(re.escape(f"/{route}/") for route in HIDDEN_ROUTES)
    text = re.sub(
        rf'<a\b[^>]*\bhref=["\'](?:{routes})["\'][^>]*>.*?</a>(?:<br\s*/?>)?',
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Older pages group these links under a My Fantasy disclosure. Once its
    # only links are removed, do not leave an empty menu in production.
    for class_name in ("navgroup", "navsection"):
        pattern = rf'<details\b[^>]*class=["\'][^"\']*\b{class_name}\b[^"\']*["\'][^>]*>.*?</details>'
        text = re.sub(
            pattern,
            lambda match: "" if "<a " not in match.group(0).lower() else match.group(0),
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return text


def prepare(root: Path) -> None:
    if os.environ.get("LINEUPBEAT_RELEASE_TARGET") != "production":
        raise RuntimeError("public release pruning requires the production target")
    root = root.resolve()
    if not root.is_dir() or not (root / "index.html").is_file():
        raise RuntimeError(f"invalid deployment artifact: {root}")

    for route in HIDDEN_ROUTES:
        target = root / route
        if target.exists():
            shutil.rmtree(target)
    for relative in HIDDEN_ASSETS:
        (root / relative).unlink(missing_ok=True)

    sitemap = root / "sitemap.xml"
    if sitemap.is_file():
        tree = ET.parse(sitemap)
        document = tree.getroot()
        namespace = document.tag.removesuffix("urlset").strip("{}")
        if namespace:
            ET.register_namespace("", namespace)
        location = f"{{{namespace}}}loc" if namespace else "loc"
        for entry in list(document):
            loc = entry.find(location)
            if loc is not None and any(
                    f"/{route}/" in (loc.text or "") for route in HIDDEN_ROUTES):
                document.remove(entry)
        tree.write(sitemap, encoding="unicode", xml_declaration=True)

    # Some evergreen pages are retained rather than rebuilt on every release.
    # Their chrome may predate the production-only navigation switch.
    for path in root.rglob("*.html"):
        text = path.read_text(errors="replace")
        scrubbed = scrub_connector_navigation(text)
        if scrubbed != text:
            path.write_text(scrubbed)

    leaks: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(errors="replace")
        found = [value for value in FORBIDDEN_REFERENCES if value in text]
        if found:
            leaks.append(f"{path.relative_to(root)}: {', '.join(found)}")
    if leaks:
        raise RuntimeError(
            "development-only connector references reached production:\n"
            + "\n".join(leaks[:20])
        )
    print("production artifact excludes My Team, My League, League History, and connector APIs")


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    prepare(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
