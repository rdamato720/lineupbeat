#!/usr/bin/env python3
"""Audit every rendered development route and every rendered internal link."""

from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse


DEV_HOST = "lineupbeat-dev.pages.dev"
PROD_HOSTS = {"lineupbeat.com", "www.lineupbeat.com"}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[str] = []
        self.images: list[str] = []
        self.h1 = 0
        self.headers = 0
        self.footers = 0
        self.in_header = 0
        self.in_footer = 0
        self.shell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.anchors.append(values["href"] or "")
        elif tag == "img" and values.get("src"):
            self.images.append(values["src"] or "")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "header" and "topbar" in (values.get("class") or "").split():
            self.headers += 1
            self.in_header += 1
        elif tag == "footer" and "global-footer" in (values.get("class") or "").split():
            self.footers += 1
            self.in_footer += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "header" and self.in_header:
            self.in_header -= 1
        elif tag == "footer" and self.in_footer:
            self.in_footer -= 1

    def handle_data(self, data: str) -> None:
        if self.in_header or self.in_footer:
            self.shell_text.append(data)


def local_target(root: Path, path: str) -> Path:
    clean = unquote(path).lstrip("/")
    target = root / clean
    if path.endswith("/") or not target.suffix:
        target = target / "index.html"
    return target


def sitemap_paths(root: Path) -> list[str]:
    text = (root / "sitemap.xml").read_text()
    return [urlparse(value).path for value in re.findall(r"<loc>(.*?)</loc>", text)]


def audit(root: Path) -> tuple[int, int, int]:
    errors: list[str] = []
    routes = sitemap_paths(root)
    required = {"/", "/nfl/data/", "/decision-room/nfl/",
                "/decision-room/college/"}
    missing = sorted(required - set(routes))
    if missing:
        errors.append("sitemap missing: " + ", ".join(missing))
    if "/decision-room/reviewed-wire/" in routes:
        errors.append("unlisted reviewed Wire archive appears in sitemap")

    route_files: list[Path] = []
    for route in routes:
        target = local_target(root, route)
        if not target.is_file():
            errors.append(f"sitemap route has no artifact: {route}")
        else:
            route_files.append(target)

    archive = root / "decision-room/reviewed-wire/index.html"
    if not archive.is_file():
        errors.append("reviewed Wire archive is missing")
    elif archive.read_text().count('class="tile wire"') != 86:
        errors.append("reviewed Wire archive does not render exactly 86 cards")

    # Audit every canonical rendered route plus the intentional unlisted
    # archive and the platform 404. Source templates are build inputs, not
    # routes, and must not be mistaken for rendered pages.
    extras = [path for path in (archive, root / "404.html") if path.is_file()]
    pages = sorted(set(route_files + extras))
    parsed: dict[Path, PageParser] = {}
    links_checked = 0
    for page in pages:
        parser = PageParser()
        parser.feed(page.read_text(errors="replace"))
        parsed[page] = parser
        rel = "/" + page.relative_to(root).as_posix()
        if parser.headers != 1:
            errors.append(f"{rel}: expected one global header, found {parser.headers}")
        if parser.h1 != 1:
            errors.append(f"{rel}: expected one semantic h1, found {parser.h1}")
        if parser.footers != 1:
            errors.append(f"{rel}: expected one global footer, found {parser.footers}")
        shell = " ".join(parser.shell_text).lower()
        for legacy in ("the wire", "my roster", "fantasy data",
                       "every team on the beat"):
            if legacy in shell:
                errors.append(f"{rel}: legacy shell text {legacy!r}")
        text = page.read_text(errors="replace")
        if 'class="dr-sports"' in text:
            errors.append(f"{rel}: redundant Decision Room sport navigation")
        if '<meta name="viewport"' not in text:
            errors.append(f"{rel}: missing mobile viewport")
        if page == root / "index.html":
            scripts = " ".join(re.findall(r"<script\b[^>]*>(.*?)</script>", text,
                                           flags=re.I | re.S)).lower()
            for legacy in ("the wire", "my roster", "fantasy data"):
                if legacy in scripts:
                    errors.append(f"{rel}: hidden legacy homepage script {legacy!r}")
            for legacy_id in ('id="roshero"', 'id="medhero"'):
                if legacy_id in text:
                    errors.append(f"{rel}: hidden legacy homepage DOM {legacy_id!r}")

    for page, parser in parsed.items():
        route = "/" + page.relative_to(root).as_posix()
        if route.endswith("index.html"):
            route = route[:-10]
        for href in parser.anchors:
            if not href or href.startswith(("#", "mailto:", "tel:")):
                continue
            if "${" in href or "{{" in href or "}" in href:
                continue
            resolved = urlparse(urljoin(f"https://{DEV_HOST}{route}", href))
            if resolved.scheme not in ("http", "https"):
                continue
            if resolved.hostname in PROD_HOSTS:
                errors.append(f"{route}: clickable internal link targets production: {href}")
                continue
            if resolved.hostname != DEV_HOST:
                continue
            links_checked += 1
            target = local_target(root, resolved.path)
            if not target.is_file():
                errors.append(f"{route}: broken internal link {href} -> {resolved.path}")
        for src in parser.images:
            parsed_src = urlparse(src)
            if parsed_src.scheme or src.startswith("//") or src.startswith("data:"):
                continue
            target = local_target(root, urljoin(route, src))
            if not target.is_file():
                errors.append(f"{route}: missing local image {src}")

    scope_checks = {
        root / "decision-room/nfl/index.html": ("177", "2026"),
        root / "nfl/who-should-i-draft/index.html": ("216-player", "2025 weekly"),
        root / "nfl/data/index.html": ("177-player", "615-player", "216-player"),
        root / "decision-room/college/index.html": ("2,205", "64 teams", "Yahoo"),
    }
    for page, needles in scope_checks.items():
        text = page.read_text() if page.is_file() else ""
        for needle in needles:
            if needle not in text:
                errors.append(f"/{page.relative_to(root)}: missing scope label {needle!r}")

    if errors:
        print("site unification audit failed:", file=sys.stderr)
        for error in errors[:200]:
            print(f"  - {error}", file=sys.stderr)
        if len(errors) > 200:
            print(f"  - ... {len(errors) - 200} more", file=sys.stderr)
        raise SystemExit(1)
    return len(routes), len(pages), links_checked


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("site"))
    args = parser.parse_args()
    routes, pages, links = audit(args.root)
    print(f"site unification audit passed: {routes} sitemap routes, "
          f"{pages} rendered HTML files, {links} internal links checked")


if __name__ == "__main__":
    main()
