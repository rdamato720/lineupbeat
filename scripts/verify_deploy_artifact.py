#!/usr/bin/env python3
"""Check the directory the deploy actually uploads, not the build that made it.

    python3 scripts/verify_deploy_artifact.py site

`wrangler pages deploy site` uploads this directory, so this is the last
place the truth can be checked. The distinction is not academic: /nfl/wire/
was built correctly, and then deleted by a later step that prunes stale
player directories, and every local check still passed because they all read
the builder's output rather than what remained on disk at deploy time. The
homepage shipped with three links to a page that no longer existed.

So every assertion here reads files from the artifact root, and a link is
only satisfied by a file that is present in it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FAILURES = []


def check(name, ok, detail=""):
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def resolve(root: Path, href: str) -> Path | None:
    """The file the host would serve for this href, if any."""
    rel = href.strip("/")
    for candidate in (root / rel, root / rel / "index.html",
                      root / f"{rel}.html"):
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    print(f"  artifact: {root.resolve()}")
    if not root.is_dir():
        print("  the deploy directory does not exist")
        return 1

    # The exact path the host needs. Not "somewhere under site" -- this one.
    wire = root / "nfl" / "wire" / "index.html"
    check("nfl/wire/index.html exists in the artifact", wire.is_file(),
          str(wire))
    if not wire.is_file():
        # Nothing below can be meaningful without it.
        print("\n  the Wire page is not in the artifact; refusing to deploy")
        return 1

    html = wire.read_text()
    check("it contains the page heading", "The NFL Wire" in html)
    for who in ("Chris Blair", "Anthony Richardson"):
        check(f"it contains {who}", who in html)
    check("the reporter block and our commentary are separate elements",
          'class="wrep"' in html and 'class="wimp"' in html)

    # Every homepage link to the Wire must land on a file that is here.
    home = root / "index.html"
    if home.is_file():
        hrefs = set(re.findall(r'href="(/nfl/wire/[^"]*)"', home.read_text()))
        check("the homepage links to the Wire", bool(hrefs), f"{len(hrefs)} href(s)")
        for href in sorted(hrefs):
            check(f"homepage link {href} resolves in the artifact",
                  resolve(root, href) is not None)
    else:
        check("the homepage is in the artifact", False, str(home))

    # And any other page that links to it, so this cannot regress elsewhere.
    dangling = []
    for page in root.rglob("*.html"):
        for href in set(re.findall(r'href="(/nfl/wire/[^"]*)"', page.read_text())):
            if resolve(root, href) is None:
                dangling.append(f"{page.relative_to(root)} -> {href}")
    check("no page in the artifact links to a missing Wire URL",
          not dangling, "; ".join(dangling[:3]))

    print()
    if FAILURES:
        print(f"  {len(FAILURES)} artifact check(s) failed; refusing to deploy")
        return 1
    print("  artifact verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
