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

import json
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


def homepage_payload(text):
    """The DATA object the homepage ships, read out of the artifact."""
    i = text.find("const DATA = ")
    if i < 0:
        return None
    j = text.find("\n", i)
    try:
        return json.loads(text[i + len("const DATA = "):j].rstrip(";"))
    except ValueError:
        return None


def check_homepage(root):
    """The homepage sections the Wire replaced *around*.

    The Wire replaces one renderer -- All reports -- and nothing else. It
    once emptied DATA.sports[*].nuggets on the way past, on the assumption
    that the retired renderer was the only reader. Recent News and Moving Now
    read the same collection, so both shipped blank. These checks exist so
    that cannot happen silently again: they assert the section is populated
    whenever its source data is not empty, which is the condition the earlier
    local checks never tested.
    """
    home = root / "index.html"
    if not home.is_file():
        check("the homepage is in the artifact", False, str(home))
        return
    text = home.read_text()

    data = homepage_payload(text)
    check("the homepage payload parses", data is not None)
    if data is None:
        return

    nuggets = [n for sp in data.get("sports", {}).values()
               for n in (sp.get("nuggets") or [])]
    feed = Path("data/rollback/feed.before-replacement.json")
    expected = 0
    if feed.is_file():
        f = json.loads(feed.read_text())
        expected = sum(len(sp.get("nuggets") or [])
                       for sp in f.get("sports", {}).values())

    # The source is not empty, so the payload must not be either.
    check("the feed records survive into the deployed payload",
          len(nuggets) >= expected if expected else bool(nuggets),
          f"{len(nuggets)} report(s) in the artifact"
          + (f", {expected} in the source feed" if expected else ""))

    # Recent News renders resolved reports; Moving Now ranks the same
    # collection. Each needs its mount point and enough data to fill it.
    resolved = [n for n in nuggets if n.get("resolved")]
    check("Recent News has its mount point", 'id="livelist"' in text)
    check("Recent News has items to render", bool(resolved),
          f"{len(resolved)} resolved report(s)")
    check("Moving Now has its mount point", 'id="trending"' in text)
    check("Moving Now has items to rank",
          len({n.get("player_id") for n in resolved if n.get("player_id")}) > 1,
          f"{len({n.get('player_id') for n in resolved if n.get('player_id')})} player(s)")

    # Roster rows carry the photos, ADP and search index the rest of the page
    # uses; the Wire must not have taken them either.
    check("roster rows survive into the deployed payload",
          len(data.get("players") or []) > 100,
          f"{len(data.get('players') or [])} row(s)")

    # And the replaced section itself: every approved card exactly once.
    if "<!-- LB WIRE REPLACEMENT START" in text:
        sec = text[text.index("<!-- LB WIRE REPLACEMENT START"):
                   text.index("<!-- LB WIRE REPLACEMENT END")]
        names = [re.sub(r"<[^>]+>", "", m)
                 for m in re.findall(r"<h4>(.*?)</h4>", sec, re.S)]
        pubs = Path("data/wire_publications.json")
        approved = []
        if pubs.is_file():
            approved = [p["player_name"]
                        for p in json.loads(pubs.read_text())["publications"]]
        dupes = sorted({n for n in names if names.count(n) > 1})
        check("no Wire report is duplicated in the replaced section",
              not dupes, "; ".join(dupes[:3]))
        for who in approved:
            check(f"the homepage carries {who} exactly once",
                  names.count(who) == 1, f"{names.count(who)} card(s)")
        check("the old All reports renderer is disabled",
              "__LB_WIRE_REPLACEMENT__" in text)

        # The design regression: placeholders instead of art.
        cards = re.findall(r'<article class="tile wire".*?</article>', sec, re.S)
        check("every Wire card is a homepage tile",
              len(cards) == len(names), f"{len(cards)} tile(s)")
        no_photo = [n for c, n in zip(cards, names) if 'class="shot"' not in c]
        check("every Wire card carries a real player photo",
              not no_photo, "; ".join(no_photo[:3]))
        no_logo = [n for c, n in zip(cards, names)
                   if "teamlogos/nfl/500" not in c]
        check("every Wire card carries a real team logo",
              not no_logo, "; ".join(no_logo[:3]))
        check("no card falls back to initials by default",
              'class="wpic"' not in sec and 'class="wlogo"' not in sec)
        check("every Wire card carries its team colour",
              len(re.findall(r"--c1:#", sec)) == len(cards),
              f"{len(re.findall(chr(45)+chr(45)+'c1:#', sec))} of {len(cards)}")
        check("the Wire grid is two columns, never three",
              "repeat(2,minmax(0,1fr))" in text and "repeat(3" not in
              text[text.index("#lbwire .tiles{"):
                   text.index("#lbwire .tiles{") + 120])


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

    # Whoever is published right now, read from the file the page is built
    # from. Naming players here made a retraction fail the deploy: Anthony
    # Richardson was rejected as not fantasy relevant and this check kept
    # demanding him.
    pubs_file = Path("data/wire_publications.json")
    published, retracted = [], []
    if pubs_file.is_file():
        payload = json.loads(pubs_file.read_text())
        published = [p["player_name"] for p in payload.get("publications", [])]
    check("the artifact page matches the published file",
          bool(published), f"{len(published)} published")
    for who in published:
        check(f"it contains {who}", who in html)
    check("every card on the page is in the published file",
          len(re.findall(r'<article class="wcard"', html)) == len(published),
          f"{len(re.findall(chr(60) + 'article class=' + chr(34) + 'wcard' + chr(34), html))} cards, "
          f"{len(published)} published")
    check("the reporter block and our commentary are separate elements",
          'class="wrep"' in html and 'class="wimp"' in html)

    check_homepage(root)

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
