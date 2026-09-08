#!/usr/bin/env python3
"""Remove development-only fantasy connectors from a production artifact."""

from __future__ import annotations

import os
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
