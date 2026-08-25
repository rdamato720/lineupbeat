#!/usr/bin/env python3
"""Check the directory the deploy actually uploads, not the build that made it.

    python3 scripts/verify_deploy_artifact.py site

`wrangler pages deploy site` uploads this directory, so this is the last
place the truth can be checked. The distinction is not academic: a page was
once built correctly and then deleted by a later step that prunes stale
player directories, and every local check still passed because they all read
the builder's output rather than what remained on disk at deploy time.

The Wire has no page of its own now -- it is the homepage -- so what these
checks defend is the homepage, the approved impact reused on canonical player
pages, and the redirect that keeps the old URL alive.

So every assertion here reads files from the artifact root, and a link is
only satisfied by a file that is present in it.
"""

from __future__ import annotations

import json
import re
import sys
from html import unescape
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


def slug(value: str) -> str:
    value = re.sub(r"[^\w\s-]", "", str(value or "").lower())
    return re.sub(r"[\s_]+", "-", value).strip("-")


def check_player_page_impacts(root):
    """Prove the final artifact reused only final publication wording.

    Player pages show the newest publication per stable player id. This runs
    after every page-pruning and homepage-replacement step, so a successful
    intermediate build cannot hide a missing or stale deployed module.
    """
    publications = Path("data/wire_publications.json")
    if not publications.is_file():
        check("approved Wire publications are available for artifact checks",
              False, str(publications))
        return
    records = json.loads(publications.read_text()).get("publications", [])
    records.sort(key=lambda item: (
        str(item.get("published_date", "")),
        str(item.get("publication_id", ""))), reverse=True)
    latest = {}
    for publication in records:
        latest.setdefault(publication.get("player_id"), publication)

    for player_id, publication in latest.items():
        who = publication.get("player_name") or player_id
        page = root / "nfl" / slug(who) / "index.html"
        check(f"{who}'s canonical page is in the artifact", page.is_file(),
              str(page))
        if not page.is_file():
            continue
        text = unescape(page.read_text())
        match = re.search(
            r'<section class="lbimpact".*?</section>', text, re.S)
        module = match.group(0) if match else ""
        check(f"{who}'s approved impact module is deployed", bool(module))
        summary = str(publication.get("public_evidence_summary") or "").strip()
        impact = str(publication.get("lineupbeat_impact") or "").strip()
        evidence = str(publication.get("reporter_found") or "").strip()
        check(f"{who}'s approved summary is on the player page",
              bool(summary) and summary in module)
        check(f"{who}'s approved analysis is on the player page",
              bool(impact) and impact in module)
        check(f"{who}'s raw evidence is absent from the impact module",
              not evidence or evidence[:80] not in module)


def check_ranking_formats(root):
    """The supported ranking URLs must survive every later pruning step."""
    paths = [
        "nfl/rankings/ppr/index.html",
        *[f"nfl/rankings/ppr/{p}/index.html" for p in ("qb", "rb", "wr", "te")],
        "nfl/rankings/non-ppr/index.html",
        *[f"nfl/rankings/non-ppr/{p}/index.html" for p in ("qb", "rb", "wr", "te")],
        "nfl/rankings/top-200-ppr/index.html",
        "nfl/rankings/top-200-non-ppr/index.html",
        "nfl/rankings/top-200-superflex/index.html",
        "nfl/rankings/dynasty/index.html",
        *[f"nfl/rankings/dynasty/{p}/index.html" for p in ("qb", "rb", "wr", "te")],
    ]
    missing = [path for path in paths if not (root / path).is_file()]
    check("all supported ranking-format pages are in the artifact",
          not missing, "; ".join(missing[:3]))
    hub = root / "nfl" / "rankings" / "index.html"
    text = hub.read_text() if hub.is_file() else ""
    for label in ("Preseason Rankings (PPR)", "Preseason Rankings (NON-PPR)",
                  "Top 200 Rankings (PPR)", "Top 200 Rankings (NON-PPR)",
                  "Top 200 Rankings (Superflex)", "Dynasty Rankings"):
        check(f"the rankings menu includes {label}", label in text)
    check("IDP is omitted and Dynasty is linked as a live page",
          '/nfl/rankings/idp/' not in text
          and '/nfl/rankings/dynasty/' in text)
    sitemap = root / "sitemap.xml"
    sm = sitemap.read_text() if sitemap.is_file() else ""
    absent = [path for path in paths
              if f"/{path.removesuffix('index.html')}" not in sm]
    check("every supported ranking-format page is in the sitemap",
          not absent, "; ".join(absent[:3]))


def check_comparison_tool(root):
    hub = root / "nfl" / "who-should-i-draft" / "index.html"
    text = hub.read_text() if hub.is_file() else ""
    check("Who Should I Draft tool is in the artifact",
          bool(text) and "Who Should I Draft?" in text)
    check("comparison tool includes weekly consistency metrics",
          "Weekly floor" in text and "Consistency score" in text)
    pair_pages = list((hub.parent if hub.parent.exists() else root).glob("*-vs-*/index.html"))
    check("indexable comparison pages are in the artifact",
          len(pair_pages) >= 100, str(len(pair_pages)))
    sitemap = (root / "sitemap.xml").read_text()
    check("comparison hub is in the sitemap",
          "/nfl/who-should-i-draft/</loc>" in sitemap)


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

    # Recent News renders resolved reports. Moving Now used to rank the same
    # collection and has been removed from the homepage, so what is checked
    # now is that it is gone -- and that Recent News, which still reads the
    # feed, did not go with it.
    resolved = [n for n in nuggets if n.get("resolved")]
    check("Recent News has its mount point", 'id="livelist"' in text)
    check("Recent News has items to render", bool(resolved),
          f"{len(resolved)} resolved report(s)")
    check("Moving Now is gone from the homepage",
          'id="trending"' not in text and 'id="mn-title"' not in text)
    check("the Fantasy Data section is gone from the homepage",
          'id="fdata"' not in text and "<h2>Fantasy data</h2>" not in text)
    # The feed is still the reason Recent News works, so it must survive the
    # removal of the two sections that also read it.
    check("the feed still backs Recent News after both removals",
          len(resolved) > 0, f"{len(resolved)} resolved report(s)")

    # Roster rows carry the photos, ADP and search index the rest of the page
    # uses; the Wire must not have taken them either.
    check("roster rows survive into the deployed payload",
          len(data.get("players") or []) > 100,
          f"{len(data.get('players') or [])} row(s)")

    # And the replaced section itself: every approved card exactly once.
    if "<!-- LB WIRE REPLACEMENT START" in text:
        sec = text[text.index("<!-- LB WIRE REPLACEMENT START"):
                   text.index("<!-- LB WIRE REPLACEMENT END")]
        cards = re.findall(r'<article class="tile wire".*?</article>', sec, re.S)
        names = [unescape(re.sub(r"<[^>]+>", "", m))
                 for m in re.findall(r"<h4>(.*?)</h4>", sec, re.S)]
        publication_ids = [unescape(m) for c in cards for m in
                           re.findall(r'data-publication-id="([^"]+)"', c)]
        pubs = Path("data/wire_publications.json")
        approved = []
        if pubs.is_file():
            approved = [(p["publication_id"], p["player_name"])
                        for p in json.loads(pubs.read_text())["publications"]]
        dupes = sorted({pid for pid in publication_ids
                        if publication_ids.count(pid) > 1})
        check("no Wire report is duplicated in the replaced section",
              not dupes, "; ".join(dupes[:3]))
        for publication_id, who in approved:
            count = publication_ids.count(publication_id)
            check(f"the homepage carries {who} [{publication_id}] exactly once",
                  count == 1, f"{count} card(s)")
        check("the old All reports renderer is disabled",
              "__LB_WIRE_REPLACEMENT__" in text)

        # The design regression: placeholders instead of art.
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
        # One card per row at every width. A grid rule here would put the
        # reporting, the attribution and our analysis into a half-width
        # measure, which is the layout this replaced.
        gi = text.find("#wire .tiles{")
        rule = text[gi:gi + 120] if gi >= 0 else ""
        check("the Wire is one card per row",
              "display:block" in rule and "repeat(" not in rule, rule[:60])

        # The public sentence, and the evidence it must not have replaced.
        pubs_f = Path("data/wire_publications.json")
        records = (json.loads(pubs_f.read_text())["publications"]
                   if pubs_f.is_file() else [])
        for r in records:
            who = r["player_name"]
            summary = (r.get("public_evidence_summary") or "").strip()
            check(f"{who} has an approved one-sentence summary",
                  bool(summary) and bool(r.get("public_evidence_summary_approved_by"))
                  and len(summary) <= 180)
            check(f"{who}'s summary is what the card shows",
                  summary and summary.replace("'", "&#x27;") in sec
                  or summary in sec)
            check(f"{who}'s full evidence is retained internally",
                  bool((r.get("reporter_found") or "").strip()))
            check(f"{who}'s passage is not on the page",
                  (r.get("reporter_found") or "x" * 9)[:80] not in sec)
        check("the card labels the summary 'What changed'",
              "What changed" in sec and "What the reporter found" not in sec)

        # Retired sections, gone from the markup rather than hidden.
        check("there is no League News section",
              'class="league"' not in text and "<h2>League news</h2>" not in text)
        check("there is no video section",
              "<h2>Video from the beat</h2>" not in text
              and 'class="vgrid"' not in text)
        # The panel must route through playerHref, which checks the slug
        # against the pages that were actually written. Comments mentioning
        # the old shape are not code, so the check reads the assignment.
        check("Recent News routes its links through playerHref",
              "row.href = (href && href !== \"#\") ? href : \"#wire\";" in text)
        check("no live code builds a player URL from a slug field",
              "href = `/nfl/${p.slug" not in text
              and "`/nfl/${p.slug || \"\"}/`" not in text)
        check("no reporters-per-team claim remains",
              "Reporters / team" not in text)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    print(f"  artifact: {root.resolve()}")
    if not root.is_dir():
        print("  the deploy directory does not exist")
        return 1

    # There must be no separate destination, and the old one must still
    # answer. Cloudflare Pages reads _redirects from the artifact root.
    wire_dir = root / "nfl" / "wire"
    check("there is no separate /nfl/wire/ page in the artifact",
          not wire_dir.exists(), str(wire_dir))

    rules = (root / "_redirects")
    text = rules.read_text() if rules.is_file() else ""
    check("_redirects is in the artifact", bool(text))
    # Both forms. The bare path is not covered by the splat and 404'd in
    # production while the trailing-slash form redirected.
    for form, pat in (("/nfl/wire/", r"^/nfl/wire/\*?\s+/#wire\s+30[12]\s*$"),
                      ("/nfl/wire", r"^/nfl/wire\s+/#wire\s+30[12]\s*$")):
        check(f"{form} redirects to the homepage Wire",
              bool(re.search(pat, text, re.M)))

    # No page may still send a reader to the retired destination.
    dangling = []
    for page in root.rglob("*.html"):
        for href in set(re.findall(r'href="([^"]*?/nfl/wire/[^"]*)"',
                                   page.read_text())):
            dangling.append(f"{page.relative_to(root)} -> {href}")
    check("no page in the artifact links to /nfl/wire/",
          not dangling, "; ".join(dangling[:3]))

    sm = root / "sitemap.xml"
    if sm.is_file():
        check("the sitemap does not list /nfl/wire/",
              "/nfl/wire/" not in sm.read_text())

    check_homepage(root)
    check_player_page_impacts(root)
    check_ranking_formats(root)
    check_comparison_tool(root)

    home = root / "index.html"
    if home.is_file():
        text = home.read_text()
        check("the homepage carries the Wire anchor", 'id="wire"' in text)
        check("the calls to action point at the homepage Wire",
              text.count('href="#wire"') >= 2,
              f"{text.count(chr(34) + chr(35) + 'wire' + chr(34))} anchor link(s)")

    print()
    if FAILURES:
        print(f"  {len(FAILURES)} artifact check(s) failed; refusing to deploy")
        return 1
    print("  artifact verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
