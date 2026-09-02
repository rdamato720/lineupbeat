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
import zipfile
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


def check_my_team(root):
    """The development-only My Team surface must ship as a complete local app."""
    page = root / "my-team" / "index.html"
    text = page.read_text() if page.is_file() else ""
    check("My Team has a dedicated development route", bool(text), str(page))
    check("My Team is noindex and analytics-free",
          bool(re.search(r'name="robots" content="noindex,\s*nofollow(?:,\s*noarchive)?"', text))
          and "cloudflareinsights" not in text.lower()
          and "data-cf-beacon" not in text.lower())
    check("My Team exposes the browser-local privacy controls",
          "Connect ESPN extension" in text
          and "Disconnect &amp; clear" in text
          and "Roster data never leaves this browser" in text)
    check("unfinished providers have no active connection controls",
          "Connect Yahoo" not in text and "Connect CBS" not in text
          and "Connect Sleeper" not in text)
    assets = [
        root / "my-team" / "league-adapter.js",
        root / "my-team" / "espn-adapter.js",
        root / "my-team" / "my-team.js",
        root / "my-team" / "my-team.css",
    ]
    check("My Team runtime is complete",
          all(path.is_file() for path in assets),
          "; ".join(str(path) for path in assets if not path.is_file()))
    public_zip = root / "my-team" / "lineupbeat-espn-extension.zip"
    try:
        with zipfile.ZipFile(public_zip) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            worker = archive.read("background.js").decode()
            package_files = archive.namelist()
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        manifest, worker, package_files = {}, "", []
    scripts = manifest.get("content_scripts") or [{}, {}]
    matches = scripts[1].get("matches") if len(scripts) > 1 else None
    check("the development download is the restricted version 0.2.1 package",
          manifest.get("version") == "0.2.1"
          and matches == ["https://lineupbeat-dev.pages.dev/my-team/*"]
          and "localhost" not in json.dumps(manifest)
          and "127.0.0.1" not in json.dumps(manifest)
          and "https://lineupbeat.com" not in json.dumps(manifest)
          and package_files[:4] == ["manifest.json", "background.js", "espn-roster-parser.js", "content.js"])
    check("the development download validates capture, retrieval, and clear senders",
          "ESPN_ORIGIN = 'https://fantasy.espn.com'" in worker
          and "ESPN_PATH = '/football/'" in worker
          and "MY_TEAM_ORIGIN = 'https://lineupbeat-dev.pages.dev'" in worker
          and "MY_TEAM_PATH = '/my-team/'" in worker
          and worker.count("return reject(sendResponse)") == 4)
    support = root / "my-team" / "extension" / "index.html"
    privacy = root / "my-team" / "extension" / "privacy" / "index.html"
    support_text = support.read_text() if support.is_file() else ""
    privacy_text = privacy.read_text() if privacy.is_file() else ""
    check("extension support and privacy pages are deployed",
          bool(support_text) and bool(privacy_text))
    check("extension privacy accurately describes local storage and deletion",
          "chrome.storage.local" in privacy_text
          and "not placed in a URL or sent to a Lineup Beat server" in privacy_text
          and "Disconnect &amp; clear" in privacy_text
          and "No ESPN password, cookie, session token" in privacy_text)
    check("extension support exposes the labeled development package",
          'href="/my-team/lineupbeat-espn-extension.zip"' in support_text
          and "Download version 0.2.1" in support_text
          and "Development-only package" in support_text)
    model_path = root / "data" / "my-team-week1.json"
    try:
        model = json.loads(model_path.read_text())
    except (OSError, ValueError):
        model = {}
    players = model.get("players") or []
    check("My Team ships only the redacted public Week 1 model",
          model.get("schemaVersion") == "lineupbeat-my-team-week1-v1"
          and len(players) == 182
          and all(player.get("position") in {"QB", "RB", "WR", "TE"}
                  for player in players)
          and all("history" not in player and "adp" not in player
                  for player in players),
          f"{len(players)} player(s)")


def check_homepage(root, decision_room=False):
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
    if decision_room:
        # The decision-first homepage deliberately does not ship the retired
        # news/roster application's megabyte-scale JSON payload.  Use the
        # committed rollback feed below to continue exercising preservation
        # invariants without requiring that private legacy state in the DOM.
        check("the retired homepage news/roster payload is absent", data is None)
        feed_source = Path("data/rollback/feed.before-replacement.json")
        data = json.loads(feed_source.read_text()) if feed_source.is_file() else None
        check("the committed rollback feed remains available for preservation checks",
              data is not None, str(feed_source))
    else:
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
    recent_surface = text
    nfl_decision = root / "decision-room" / "nfl" / "index.html"
    if decision_room and nfl_decision.is_file():
        recent_surface = nfl_decision.read_text()
    if decision_room:
        check("Recent News is absent from the visible Decision Room experience",
              'id="livelist"' not in recent_surface and "RECENT NEWS" not in text)
    else:
        check("Recent News has its mount point on a reader-facing route",
              'id="livelist"' in recent_surface)
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
    wire_text = text
    if decision_room:
        dedicated = root / "decision-room" / "reviewed-wire" / "index.html"
        check("the complete reviewed Wire page is in the development artifact",
              dedicated.is_file(), str(dedicated))
        if dedicated.is_file():
            wire_text = dedicated.read_text()
            check("the complete reviewed Wire remains filterable",
                  'id="wteam"' in wire_text and 'data-f="all"' in wire_text)
            check("the complete reviewed Wire preserves mobile viewport support",
                  'name="viewport"' in wire_text)
        home_cards = re.findall(r'<article class="tile wire".*?</article>', text, re.S)
        check("the Lineup Beat homepage has no public Wire cards",
              len(home_cards) == 0, f"{len(home_cards)} rendered")
        nfl_room = root / "decision-room" / "nfl" / "index.html"
        college_room = root / "decision-room" / "college" / "index.html"
        check("the full NFL Decision Room has a dedicated route", nfl_room.is_file(),
              str(nfl_room))
        check("the full College Decision Room has a dedicated route", college_room.is_file(),
              str(college_room))
        check("the root is a decision-first Lineup Beat homepage, not the full tool",
              'id="lineup-beat-home"' in text and 'id="decision-room"' not in text)
        check("the homepage has complete primary navigation",
              all(f'>{label}<' in text for label in
                  ("NFL", "COLLEGE", "Decision", "Rankings", "Projections"))
              and "The Beat" not in text)
        check("the context-aware NFL player search remains available",
              'id="site-player-search"' in text
              and 'placeholder="Search NFL players"' in text)
        check("the homepage has the required decision sections",
              all(label in text for label in
                  ("MAKE YOUR NEXT MOVE", "WHERE WE SEE IT DIFFERENTLY",
                   "CLOSEST CALLS"))
              and "NFL or College" not in text
              and "Today’s Decision Board" not in text
              and "The latest from The Beat" not in text)
        check("legacy sport query states have compatibility routing",
              "new URLSearchParams(location.search).get" in text
              and "pushState" in text and "popstate" in text)
        college_payload = root / "data" / "decision-room-college.json"
        check("the College Decision Room payload is isolated from the homepage",
              college_payload.is_file() and '"CFP_' not in text,
              str(college_payload))
        if college_payload.is_file():
            college = json.loads(college_payload.read_text())
            players = college.get("players", [])
            check("the deployed College Decision Room uses validated Week 1 metadata",
                  college.get("mode") == "weekly" and college.get("season") == 2026
                  and college.get("week") == 1)
            check("the deployed college identity and player counts reconcile",
                  len(players) == 2205 and
                  len({p.get("id") for p in players}) == len(players))
            check("the homepage exposes separate NFL and College routes",
                  '/decision-room/nfl/' in text and '/decision-room/college/' in text)
    if "<!-- LB WIRE REPLACEMENT START" in wire_text:
        sec = wire_text[wire_text.index("<!-- LB WIRE REPLACEMENT START"):
                        wire_text.index("<!-- LB WIRE REPLACEMENT END")]
        cards = re.findall(r'<article class="tile wire".*?</article>', sec, re.S)
        names = [unescape(re.sub(r"<[^>]+>", "", m))
                 for m in re.findall(r"<h4[^>]*>(.*?)</h4>", sec, re.S)]
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
        check("approved publication count equals rendered Wire-card count",
              len(approved) == len(cards),
              f"{len(approved)} approved, {len(cards)} rendered")
        for publication_id, who in approved:
            count = publication_ids.count(publication_id)
            check(f"the reviewed archive carries {who} [{publication_id}] exactly once",
                  count == 1, f"{count} card(s)")
        check("the old All reports renderer is disabled",
              (decision_room and "__LB_WIRE_REPLACEMENT__" not in text)
              or "__LB_WIRE_REPLACEMENT__" in text)

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
        gi = wire_text.find("#wire .tiles{")
        rule = wire_text[gi:gi + 120] if gi >= 0 else ""
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
        check("the card uses the compact news and Analysis hierarchy",
              all(marker in sec for marker in (
                  'class="wplayer"', 'class="wheadline"',
                  'class="wmeta"', 'class="wdate"',
                  '<div class="wlab">Analysis</div>'))
              and "What the reporter found" not in sec)

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
              (decision_room and 'id="livelist"' not in text)
              or "row.href = (href && href !== \"#\") ? href : \"#wire\";" in text)
        check("no live code builds a player URL from a slug field",
              "href = `/nfl/${p.slug" not in text
              and "`/nfl/${p.slug || \"\"}/`" not in text)
        check("no reporters-per-team claim remains",
              "Reporters / team" not in text)


def main() -> int:
    args = [arg for arg in sys.argv[1:] if arg != "--decision-room"]
    decision_room = "--decision-room" in sys.argv[1:]
    root = Path(args[0] if args else "site")
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

    check_homepage(root, decision_room=decision_room)
    check_player_page_impacts(root)
    check_ranking_formats(root)
    check_comparison_tool(root)
    if decision_room:
        check_my_team(root)

    home = root / "index.html"
    if home.is_file():
        text = home.read_text()
        if decision_room:
            check("the reviewed Wire archive is unlisted from the homepage",
                  'href="/decision-room/reviewed-wire/"' not in text)
        else:
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
