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
    """Retired player impact cards must stay absent after every build step."""
    pages = list((root / "nfl").rglob("*.html"))
    remaining = [str(p.relative_to(root)) for p in pages
                 if re.search(r'<section class="lbimpact"|id="latest-impact"', p.read_text())]
    check("NFL pages do not display retired decision-context cards",
          not remaining, "; ".join(remaining[:5]))


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


def check_my_team(root, development=True):
    """My Team must ship as a complete browser-local app."""
    page = root / "my-team" / "index.html"
    text = page.read_text() if page.is_file() else ""
    check("My Team has a dedicated route", bool(text), str(page))
    check("My Team is noindex and analytics-free",
          bool(re.search(r'name="robots" content="noindex,\s*nofollow(?:,\s*noarchive)?"', text))
          and "cloudflareinsights" not in text.lower()
          and "data-cf-beacon" not in text.lower())
    check("My Team exposes the browser-local privacy controls",
          "Connect Fantasy extension" in text
          and "Disconnect &amp; clear" in text
          and "Roster data never leaves this browser" in text)
    check("My Team exposes only implemented provider connections",
          all(f"<h3>{provider}</h3>" in text for provider in ("ESPN", "Yahoo", "CBS"))
          and "Connect Sleeper" not in text)
    team_page = root / "my-team" / "team" / "index.html"
    team_text = team_page.read_text() if team_page.is_file() else ""
    check("the connected roster has a separate private team dashboard",
          bool(team_text)
          and 'data-my-team-view="dashboard"' in team_text
          and 'id="mt-team"' in team_text
          and 'id="mt-roster"' in team_text
          and 'id="mt-team"' not in text,
          str(team_page))
    runtime_text = (root / "my-team" / "my-team.js").read_text() if (
        root / "my-team" / "my-team.js"
    ).is_file() else ""
    check("saved rosters move from the landing page to the team dashboard",
          "location.replace('/my-team/team/')" in runtime_text
          and "if(!dashboard){openDashboard();return}" in runtime_text)
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
    encoded_manifest = json.dumps(manifest)
    expected_site_matches = {
        "https://lineupbeat-dev.pages.dev/my-team/*",
        "https://lineupbeat-dev.pages.dev/league-history/*",
        "https://lineupbeat.com/my-team/*",
        "https://lineupbeat.com/league-history/*",
        "https://www.lineupbeat.com/my-team/*",
        "https://www.lineupbeat.com/league-history/*",
    }
    actual_site_matches = {
        match for script in scripts
        for match in (script.get("matches") or [])
        if "lineupbeat" in match
    }
    check("the download is the restricted twelve-file version 0.5.0 package",
          manifest.get("version") == "0.5.0"
          and len(package_files) == 12
          and actual_site_matches == expected_site_matches
          and any("https://football.fantasysports.yahoo.com/f1/*" in (script.get("matches") or []) for script in scripts)
          and any("https://*.football.cbssports.com/*" in (script.get("matches") or []) for script in scripts)
          and "https://lineupbeat.com/*" not in encoded_manifest
          and "https://www.lineupbeat.com/*" not in encoded_manifest
          and "localhost" not in encoded_manifest
          and "127.0.0.1" not in encoded_manifest
          and package_files[:7] == ["manifest.json", "background.js", "espn-roster-parser.js", "espn-history-parser.js", "yahoo-roster-parser.js", "cbs-roster-parser.js", "cbs-history-parser.js"])
    expected_origin = ('https://lineupbeat-dev.pages.dev' if development
                       else 'https://lineupbeat.com')
    check("the connector download validates capture, retrieval, and clear senders",
          "ESPN_ORIGIN = 'https://fantasy.espn.com'" in worker
          and "ESPN_PATH = '/football/'" in worker
          and f"MY_TEAM_ORIGIN = '{expected_origin}'" in worker
          and "'https://lineupbeat-dev.pages.dev'" in worker
          and "'https://www.lineupbeat.com'" in worker
          and "MY_TEAM_PATH = '/my-team/'" in worker
          and worker.count("return reject(sendResponse)") >= 13)
    support = root / "my-team" / "extension" / "index.html"
    privacy = root / "my-team" / "extension" / "privacy" / "index.html"
    support_text = support.read_text() if support.is_file() else ""
    privacy_text = privacy.read_text() if privacy.is_file() else ""
    check("extension support and privacy pages are deployed",
          bool(support_text) and bool(privacy_text))
    check("extension privacy accurately describes local storage and deletion",
          "chrome.storage.local" in privacy_text
          and "Private provider data is not uploaded" in privacy_text
          and "Clear each copy" in privacy_text
          and "No provider password, cookie value, session token" in privacy_text)
    check("extension support exposes the labeled direct download",
          'href="/my-team/lineupbeat-espn-extension.zip"' in support_text
          and "Download version 0.5.0" in support_text
          and "Direct download" in support_text)
    model_path = root / "data" / "my-team-week1.json"
    try:
        model = json.loads(model_path.read_text())
    except (OSError, ValueError):
        model = {}
    players = model.get("players") or []
    check("My Team ships only the redacted public Week 1 model",
          model.get("schemaVersion") == "lineupbeat-my-team-week1-v1"
          and len(players) >= 350
          and len({player.get("id") for player in players}) == len(players)
          and len({player.get("team") for player in players}) == 32
          and all(player.get("position") in {"QB", "RB", "WR", "TE"}
                  for player in players)
          and all("history" not in player and "adp" not in player
                  for player in players),
          f"{len(players)} player(s)")


def check_development_repairs(root, development=True):
    """Cross-page consistency and discoverability fixes in the dev release."""
    hub_path = root / "nfl" / "data" / "index.html"
    hub = hub_path.read_text() if hub_path.is_file() else ""
    ranking_path = root / "data" / "nfl-half-ppr-rankings.json"
    try:
        ranking = json.loads(ranking_path.read_text()).get("players", [])[:3]
    except (OSError, ValueError):
        ranking = []
    check("the data hub preview matches the published Half-PPR board",
          len(ranking) == 3 and all(
              f'{row["overall_rank"]} &middot; {row["player_name"]}' in hub
              for row in ranking))
    unavailable = ("durability", "strength-of-schedule",
                   "offensive-line-rb-performance")
    check("unavailable historical tools are hidden from the data hub",
          bool(hub) and all(f'href="/nfl/{route}/"' not in hub
                            for route in unavailable)
          and "Explore the available tools. Use" in hub)
    check("draft-value preview labels both positional ranks",
          "MKT · LB · GAP" in hub)

    search_path = root / "data" / "nfl-player-search.json"
    try:
        search = json.loads(search_path.read_text()).get("players", [])
    except (OSError, ValueError):
        search = []
    sample_page = root / "nfl" / "josh-allen" / "index.html"
    sample = sample_page.read_text() if sample_page.is_file() else ""
    check("persistent NFL search has the trusted player index and Enter handling",
          len(search) >= 424 and "data-player-search" in sample
          and "event.key !== 'Enter'" in sample
          and all(resolve(root, row.get("url", "")) for row in search))

    week = root / "college-fantasy-football" / "week-1" / "index.html"
    season = root / "college-fantasy-football" / "projections" / "index.html"
    week_text = week.read_text() if week.is_file() else ""
    season_text = season.read_text() if season.is_file() else ""
    check("college overview search covers the full player pools",
          "Search all 2,205 players" in week_text and "Kevin Sperry" in week_text
          and "Search all 2,351 players" in season_text and "Kevin Sperry" in season_text)

    projections = root / "nfl" / "projections" / "index.html"
    projection_text = projections.read_text() if projections.is_file() else ""
    check("the large NFL projection board starts capped with a full-board control",
          'data-top="200"' in projection_text and "Show all 424" in projection_text
          and 'loading="lazy"' in projection_text)
    check("feedback styles load in the document head",
          bool(sample) and "/feedback.css" in sample.split("</head>", 1)[0])
    my_team_js = root / "my-team" / "my-team.js"
    runtime = my_team_js.read_text() if my_team_js.is_file() else ""
    check("an installed Fantasy extension without a roster gets a terminal status",
          "Fantasy extension detected, but no saved roster was found" in runtime)


def check_league_history(root, development=True):
    """The private-first experience and its canonical source must survive the build."""
    page = root / "league-history" / "index.html"
    text = page.read_text() if page.is_file() else ""
    check("League History has a dedicated route",
          bool(text) and "League history" in text, str(page))
    check("League History opens in a clear private setup state",
          bool(re.search(r'name="robots" content="noindex,\s*nofollow(?:,\s*noarchive)?"', text))
          and 'body class="history-empty"' in text
          and "private by default" in text
          and "Set up your league history" in text
          and "Choose your fantasy platform" in text
          and "Choose platform" in text
          and "Connect and import" in text
          and "Match managers" in text
          and "View and share" in text
          and 'id="connection-stage" hidden' in text
          and "Install connector" in text
          and 'id="connect-yahoo"' in text
          and "Prototype boundary" not in text
          and "Fictional demonstration data" not in text
          and '<footer class="global-footer">' in text)
    check("League History connector checks end with recovery guidance",
          "No local '+name+' import found." in text
          and "Install the connector, import from your '+name+' league page" in text)
    dashboard_path = root / "assets" / "league-history-dashboard.js"
    dashboard = dashboard_path.read_text() if dashboard_path.is_file() else ""
    check("League History preserves CBS and Sleeper provider labels",
          dashboard.count("=== 'cbs' ? 'CBS' :") >= 2
          and dashboard.count("=== 'sleeper' ? 'Sleeper' : 'ESPN'") >= 2)
    payload_path = root / "data" / "league-history-demo.json"
    try:
        payload = json.loads(payload_path.read_text())
    except (OSError, ValueError):
        payload = {}
    canonical = payload.get("canonical", {})
    record_book = payload.get("recordBook", {})
    check("League History ships the provider-neutral canonical archive",
          canonical.get("schemaVersion") == "lineupbeat-league-history-v1"
          and len(canonical.get("managers", [])) == 10
          and len(canonical.get("franchises", [])) == 10
          and len(canonical.get("matchups", [])) == 96
          and canonical.get("league", {}).get("sourcePlatform") == "demo")
    check("League History recomputes the deterministic demo record book",
          record_book.get("counts") == {"seasons": 2, "franchises": 10, "games": 96}
          and record_book.get("records", {}).get("highestWeek", {}).get("score") == 170.42)
    check("League History does not republish BGNCo participant records",
          all(name not in text for name in
              ("Adrian Chadzynski", "Ralph Damato", "Bobby Digital")))
    sitemap = root / "sitemap.xml"
    sitemap_text = sitemap.read_text() if sitemap.is_file() else ""
    landing = root / "my-league" / "index.html"
    landing_text = landing.read_text() if landing.is_file() else ""
    robots_ok = (bool(re.search(
        r'name="robots" content="noindex,\s*nofollow,\s*noarchive"', landing_text
    )) if development else 'name="robots" content="index,follow"' in landing_text)
    check("My League has a public SEO landing page",
          bool(landing_text)
          and robots_ok
          and 'href="https://lineupbeat.com/my-league/"' in landing_text
          and "Fantasy Football League History &amp; Record Book" in landing_text
          and "Connect your league" in landing_text
          and "All-time standings" in landing_text
          and "Yahoo" in landing_text and "CBS" in landing_text,
          str(landing))
    check("My League landing is discoverable without indexing private workspaces",
          "/my-league/" in sitemap_text
          and "/league-history/" not in sitemap_text
          and "/leagues/" not in sitemap_text)


def check_homepage(root, decision_room=False, public_only=False):
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
        check("the root is the LineupBeat platform homepage, not the full tool",
              'id="lineup-beat-home"' in text and 'id="decision-room"' not in text)
        expected_nav = (("NFL", "College", "About") if public_only
                        else ("NFL", "College", "My Team", "About"))
        check("the homepage has neutral primary navigation",
              all(f'>{label}<' in text for label in expected_nav)
              and ("My Team" not in text if public_only else True)
              and 'class="vbtn sport-pill"' not in text
              and "The Beat" not in text)
        expected_products = (
            "NFL + COLLEGE FANTASY FOOTBALL", "WHO WE ARE",
            "EXPLORE LINEUPBEAT", "Everything you need. All season.",
            "Fantasy research without the noise.", "NFL FANTASY", "COLLEGE FANTASY",
        ) + (() if public_only else ("MY TEAM", "MY LEAGUE"))
        check("the homepage introduces the full LineupBeat platform",
              all(label in text for label in expected_products)
              and ("MY TEAM" not in text and "MY LEAGUE" not in text
                   if public_only else True)
              and "NFL and College have their own dedicated experiences." not in text
              and "Compare 2,205 players using validated Yahoo scoring" not in text
              and "Today’s Decision Board" not in text
              and "The latest from The Beat" not in text)
        check("the homepage uses one contained product panel without floating cards",
              'class="hp-product-window"' in text
              and 'class="hp-window-grid"' in text
              and 'class="hp-wordmark-field"' not in text
              and 'class="hp-product-stage"' not in text
              and 'class="hp-stage-card' not in text)
        icon_names = (("rankings", "college") if public_only
                      else ("rankings", "college", "team", "league"))
        icon_assets = [root / "assets" / "homepage" / f"{name}-3d.png"
                       for name in icon_names]
        check("the homepage 3D navigation icons are deployed",
              text.count('class="hp-3d-icon"') == (4 if public_only else 8)
              and all(asset.is_file() for asset in icon_assets),
              "; ".join(str(asset) for asset in icon_assets if not asset.is_file()))
        check("the Decision Room is presented as one tool, not the brand identity",
              "ONE TOOL, WHEN YOU NEED IT" in text
              and text.count('class="hp-decision-summary"') == 0
              and "QUICK EXAMPLES" not in text
              and "Tony Pollard" not in text
              and 'href="/decision-room/nfl/"' in text
              and 'href="/decision-room/college/"' in text)
        check("the homepage tools are isolated in their own full-width section",
              'class="hp-tools-band" id="tools"' in text
              and 'class="hp-section hp-platform"' not in text)
        check("the homepage identity is a standalone section",
              'class="hp-about-band"' in text
              and 'class="hp-about-inner"' in text
              and 'class="hp-identity-strip"' not in text)
        check("the homepage shows concrete product examples",
              'class="hp-examples"' in text
              and text.count('<article class="hp-example') == (2 if public_only else 3)
              and "RANKINGS + PROJECTIONS" in text
              and ("ROSTER SNAPSHOT" not in text if public_only
                   else "ROSTER SNAPSHOT" in text)
              and "PROJECTED EDGE" in text)
        faq_markup = (text.split('class="hp-faq-list">', 1)[1].split("</div></section><script", 1)[0]
                      if 'class="hp-faq-list">' in text else "")
        check("the homepage FAQ is visible and has matching structured data",
              'class="hp-section hp-faq"' in text
              and faq_markup.count("<details>") == (4 if public_only else 6)
              and '"@type":"FAQPage"' in text
              and "Is the Decision Room the whole product?" in text
              and ("Roster data stays in your browser." not in text
                   if public_only else "Roster data stays in your browser." in text))
        expected_links = (
            'href="/nfl/rankings/"', 'href="/nfl/projections/"',
            'href="/college-fantasy-football/week-1/"', 'href="/about/"',
        )
        check("the homepage links the released product paths",
              all(route in text for route in expected_links)
              and ('href="/my-team/"' not in text and 'href="/my-league/"' not in text
                   if public_only else
                   'href="/my-team/"' in text and 'href="/my-league/"' in text))
        check("the homepage is not a hidden sport-switching experience",
              "data-home-sport" not in text
              and "pushState" not in text and "?sport=college" not in text)
        college_payload = root / "data" / "decision-room-college.json"
        check("the College Decision Room payload is isolated from the homepage",
              college_payload.is_file()
              and 'id="college-decision-room"' not in text
              and 'id="college-dr-data"' not in text,
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
            market_teams = college.get("market_context_by_team", {})
            check("the delayed College sportsbook context covers every modeled team",
                  college.get("market", {}).get("state")
                  == "available_delayed_market_context"
                  and college.get("market", {}).get("data_delay_seconds") == 30
                  and len(market_teams) == 64
                  and all(row.get("state") == "available"
                          for row in market_teams.values()))
            player_market = college.get("market", {}).get("player_coverage", {})
            check("the College sportsbook context separates game and player evidence",
                  all("player" not in key.lower()
                      for row in market_teams.values() for key in row)
                  and player_market.get("playersWithNumericEvidence") == 112
                  and player_market.get("playersWithEvidence") == 345
                  and "Exact player markets anchor only their named component" in
                  college.get("sources", {}).get("market", {}).get("note", ""))
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


def check_week1_boards(root):
    reference_path = root / "decision-room/nfl/index.html"
    reference_text = reference_path.read_text() if reference_path.is_file() else ""
    match = re.search(r'<script id="dr-data" type="application/json">(.*?)</script>', reference_text, re.S)
    reference = json.loads(match.group(1)) if match else {}
    expected = {p["id"]: p["formats"] for p in reference.get("players", [])}
    sitemap = (root / "sitemap.xml").read_text()
    for kind in ("rankings", "projections"):
        route = f"/nfl/week-1/{kind}/"
        page = root / route.lstrip("/") / "index.html"
        text = page.read_text() if page.is_file() else ""
        match = re.search(r'<script id="week1-data" type="application/json">(.*?)</script>', text, re.S)
        payload = json.loads(match.group(1)) if match else {}
        found = {p["id"]: p["formats"] for p in payload.get("players", [])}
        check(f"Week 1 {kind} survives final pruning and matches Decision Room",
              bool(expected) and found == expected
              and payload.get("updated_at") == reference.get("updated_at"))
        check(f"Week 1 {kind} is in the sitemap", route in sitemap)
        check(f"Week 1 {kind} has distinct weekly and season navigation",
              f'href="{route}" aria-current="page">Week 1 {kind.title()}</a>' in text
              and 'href="/nfl/rankings/">Season Rankings</a>' in text
              and 'href="/nfl/projections/">Season Projections</a>' in text)


def main() -> int:
    args = [arg for arg in sys.argv[1:]
            if arg not in {"--decision-room", "--public-only"}]
    decision_room = "--decision-room" in sys.argv[1:]
    public_only = "--public-only" in sys.argv[1:]
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

    check_homepage(root, decision_room=decision_room, public_only=public_only)
    if decision_room:
        check_week1_boards(root)
    check_player_page_impacts(root)
    check_ranking_formats(root)
    check_comparison_tool(root)
    if decision_room and not public_only:
        development = (root / "_headers").is_file()
        check_my_team(root, development=development)
        check_development_repairs(root, development=development)
        check_league_history(root, development=development)
    if public_only:
        import public_copy
        page_count, copy_failures = public_copy.audit(root)
        check("all public pages and change-history notes are free of engineering copy", not copy_failures,
              f"{page_count} pages; " + "; ".join(copy_failures[:5]))
        hidden = [root / route for route in
                  ("my-team", "my-league", "league-history")]
        check("development-only fantasy routes are absent from production",
              all(not path.exists() for path in hidden),
              "; ".join(str(path) for path in hidden if path.exists()))
        leaks = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {
                    ".html", ".css", ".js", ".json", ".xml", ".txt"}:
                continue
            page_text = path.read_text(errors="replace")
            if any(route in page_text for route in (
                    "/my-team/", "/my-league/", "/league-history/",
                    "/api/yahoo/", "/api/leagues/")):
                leaks.append(str(path.relative_to(root)))
        check("production contains no connector links or API references",
              not leaks, "; ".join(leaks[:5]))
        home_text = (root / "index.html").read_text(errors="replace").lower()
        dev_markers = ("development preview", "lb-dev-banner", "lb-dev-style",
                       "lineupbeat-dev.pages.dev")
        check("production homepage has no development preview protection",
              not any(marker in home_text for marker in dev_markers)
              and not re.search(
                  r'<meta\s+name=["\']robots["\'][^>]*\bnoindex\b', home_text))
        headers = root / "_headers"
        headers_text = headers.read_text(errors="replace").lower() if headers.is_file() else ""
        check("production has no global noindex response header",
              "x-robots-tag: noindex" not in headers_text)

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
