#!/usr/bin/env python3
"""Write the shared header into site/template.html.

    python3 scripts/sync_home_nav.py
    python3 scripts/sync_home_nav.py --check     # exits 1 if stale

WHY THIS EXISTS

Eight builders take their header from seo.site_nav(). The homepage does
not: it is the app, its nav is built by JavaScript from an array with live
counts and a Medical Tent entry the static pages have no equivalent for,
and it is a hand-authored file rather than a generated one.

So the ninth surface had its own copy of everything. The teams menu was
already duplicated -- the same 32 links, the same divisions, the same
markup, pasted into a <template> tag -- and the CSS and the open/close
listener with it. Anything fixed in seo.py stopped at the eight.

This closes that by generating the parts that can be shared into marked
regions of the template. The regions are written, not merged: whatever is
between the markers is replaced, so the only way to change it is to change
seo.py, which is the point.

What stays hand-written on the homepage is the part that is genuinely
different -- renderViews() filling both the desktop bar and the drawer,
because only the app knows the counts.
"""

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import seo

TEMPLATE = pathlib.Path(__file__).resolve().parent.parent / "site" / "template.html"

# (name, opening marker, closing marker, generator)
REGIONS = [
    # The teams panel and the mobile header are one region: the static
    # builders pull TEAMS_CSS and NAV_CSS separately, but the homepage has
    # one stylesheet and both belong to the same component.
    ("nav css",
     "/* __NAV_CSS__ */", "/* __END_NAV_CSS__ */",
     lambda: seo.TEAMS_CSS + seo.NAV_CSS),
    ("teams menu",
     "<!--__TEAMS_MENU__-->", "<!--__END_TEAMS_MENU__-->",
     lambda: seo.teams_menu("nfl")),
    # Both listeners, for the same reason the CSS is one region. The
    # homepage carried its own copy of the teams one, which is how it came
    # to differ from the eight in the first place.
    ("nav js",
     "<!--__NAV_JS__-->", "<!--__END_NAV_JS__-->",
     lambda: seo.TEAMS_JS + seo.NAV_JS),
]


def apply(src):
    """Return the template with every region rewritten, and what changed."""
    changed = []
    for name, start, end, gen in REGIONS:
        pattern = re.compile(
            re.escape(start) + r".*?" + re.escape(end), re.S)
        if not pattern.search(src):
            sys.exit(f"marker missing in template.html: {start}\n"
                     f"Add {start} ... {end} where the {name} belongs.")
        want = start + "\n" + gen().strip() + "\n" + end
        new = pattern.sub(lambda _: want, src, count=1)
        if new != src:
            changed.append(name)
        src = new
    return src, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit non-zero, writing nothing")
    args = ap.parse_args()

    src = TEMPLATE.read_text()
    out, changed = apply(src)

    if args.check:
        if changed:
            print("  template.html is stale: " + ", ".join(changed))
            print("  run: python3 scripts/sync_home_nav.py")
            sys.exit(1)
        print("  template.html header matches seo.py")
        return

    if not changed:
        print("  template.html header already current")
        return
    TEMPLATE.write_text(out)
    print("  template.html updated: " + ", ".join(changed))


if __name__ == "__main__":
    main()
