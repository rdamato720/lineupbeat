#!/usr/bin/env python3
"""Build the My Team page and redacted public Week 1 model."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
from pathlib import Path

import decision_data
import build_chrome_store_bundle
import seo

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "my-team"
DISPLAY = ROOT / "data" / "wire_display_fantasy.json"
DEFAULT_SITE = ROOT / "site"


def public_model() -> dict:
    source = decision_data.load_weekly()
    display = json.loads(DISPLAY.read_text())["players"]
    players = []
    for player in source["players"]:
        show = display.get(player["id"], {})
        players.append({
            "id": player["id"],
            "name": player["name"],
            "team": player["team"],
            "position": player["position"],
            "providerIds": {"espn": str(show["espn"])} if show.get("espn") else {},
            "formats": {
                key: {
                    "projectedPoints": value["projected_points"],
                    "overallRank": value["overall_rank"],
                    "positionRank": value["position_rank"],
                }
                for key, value in player["formats"].items()
            },
            "expectedOpportunity": {
                "passAttempts": player["expected_opportunity"].get("pass_attempts", 0),
                "carries": player["expected_opportunity"].get("carries", 0),
                "targets": player["expected_opportunity"].get("targets", 0),
            },
            "opponent": player["opponent"],
            "home": player["home"],
            "matchupFactor": player["matchup"].get("projection_factor", 1),
            "matchupLabel": "2025 prior-season context",
            "availability": "Current injury report unavailable",
            "photo": player.get("photo"),
            "teamLogo": player["team_logo"],
        })
    return {
        "schemaVersion": "lineupbeat-my-team-week1-v1",
        "season": source["season"],
        "week": source["week"],
        "updatedAt": source["updated_at"],
        "identityMethod": source["identity_method"],
        "supportedPositions": ["QB", "RB", "WR", "TE"],
        "players": players,
        "limitations": {
            "sportsbookEvidence": "unavailable; zero provider requests",
            "currentInjuryReport": "unavailable",
            "dstModel": "unsupported; no projection is guessed",
            "matchupContext": "2025 prior-season context",
            "predictiveLiftClaim": False,
            "independentCorroboration": False,
        },
        "sources": {
            "model": source["sources"]["model"],
            "matchup": source["sources"]["matchup"],
            "seasonPrior": source["sources"]["season_prior"],
        },
    }


def render_page(model: dict) -> str:
    nav = seo.site_nav("my_team", "nfl")
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="description" content="Connect an ESPN, Yahoo, or CBS fantasy roster locally and inspect Week 1 starter and bench decisions without uploading roster data.">
<title>My Team · NFL Week 1 | Lineup Beat</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600;700;800&amp;family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&amp;display=swap" rel="stylesheet">
<link rel="stylesheet" href="/my-team/my-team.css"></head><body>
{nav}
<main class="mt-shell"><section class="mt-hero"><div class="mt-kicker">NFL · My Team</div>
<h1>Your roster stays in your browser.</h1>
<p>Connect the Lineup Beat Fantasy extension to compare supported starters and bench players from ESPN, Yahoo, or CBS against the public Week 1 model. Roster matching and every lineup calculation happen locally on this page.</p>
<div class="mt-actions"><button class="mt-button" id="mt-connect" type="button">Connect Fantasy extension</button><button class="mt-button secondary" id="mt-disconnect" type="button" hidden>Disconnect &amp; clear</button><button class="mt-button secondary" id="mt-demo" type="button" hidden>Load reviewer demo roster</button><a class="mt-button secondary" href="/my-team/extension/">Extension support</a></div>
<p class="mt-status" id="mt-status" role="status" aria-live="polite">Loading the public Week 1 model…</p></section>
<section class="mt-privacy" aria-label="Privacy model"><article><small>What stays local</small><h2>Roster data never leaves this browser</h2><p>The extension keeps its captured roster in extension-local storage. The My Team page holds the normalized roster in memory only. It is never placed in a URL or sent to a Lineup Beat server.</p></article><article><small>What is never collected</small><h2>No provider secrets or manager identity</h2><ul><li>No password, cookie, session token, or manager identity.</li><li>No roster analytics or server persistence.</li><li>Disconnect &amp; clear removes the extension-local roster copy.</li></ul></article></section>
<section class="mt-section"><div class="mt-section-head"><div><small>Connection 01</small><h2>Choose your fantasy platform.</h2></div><p>Open your roster on the provider site and use the same browser-local extension. Every provider produces the same validated roster structure before Lineup Beat makes a comparison.</p></div>
<section class="mt-team-card" id="mt-team" hidden><div class="mt-team-title"><div><small>Connected league</small><h3 id="mt-team-name"></h3><p id="mt-league-name"></p></div><p id="mt-league-meta"></p></div><div id="mt-outlook"></div><div class="mt-team-block"><div class="mt-team-block-head"><small>Lineup decisions</small><h3>Changes that clear the bar</h3></div><div class="mt-decisions" id="mt-decisions"></div></div><div class="mt-team-block"><div class="mt-team-block-head"><small>Roster intelligence</small><h3>Week 1 evidence by player</h3></div><div id="mt-roster"></div></div></section>
<div class="mt-provider-grid"><article class="mt-provider active"><small>Supported connection</small><h3>ESPN</h3><p>Roster capture plus multi-season league-history import.</p><strong>Browser-local</strong></article><article class="mt-provider active"><small>Supported connection</small><h3>Yahoo</h3><p>Visible team-roster capture for local Week 1 decisions.</p><strong>Browser-local</strong></article><article class="mt-provider active"><small>Supported connection</small><h3>CBS</h3><p>Visible My Team roster capture with fail-closed diagnostics.</p><strong>Browser-local</strong></article></div>
</section>
<section class="mt-section"><div class="mt-section-head"><div><small>Guardrails 02</small><h2>What this model does not claim</h2></div></div><div class="mt-proof"><article><h3>No predictive-lift claim</h3><p>This model has not established improvement over a validated baseline.</p></article><article><h3>No independent corroboration</h3><p>Projection, opportunity and matchup context come from the same Lineup Beat Week 1 model.</p></article><article><h3>No invented availability</h3><p>Current injury and sportsbook evidence are unavailable. D/ST is unsupported rather than guessed.</p></article></div></section></main>
{seo.site_footer()}
<script src="/my-team/league-adapter.js"></script><script src="/my-team/espn-adapter.js"></script><script src="/my-team/my-team.js"></script>
</body></html>'''


def render_extension_guide() -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><title>Fantasy connector support | Lineup Beat</title><link rel="stylesheet" href="/my-team/my-team.css"></head><body>{seo.site_nav("my_team", "nfl")}<main class="mt-shell"><section class="mt-hero"><div class="mt-kicker">Fantasy connector</div><h1>Connect without sharing credentials.</h1><p>Version 0.4.0 supports My Team roster capture from ESPN, Yahoo, and CBS. ESPN also supports private league-history import and commissioner identity review. Data stays in extension-local storage unless you explicitly publish a view-only league page.</p><div class="mt-actions"><a class="mt-button" href="/my-team/lineupbeat-espn-extension.zip" download>Download version 0.4.0</a><a class="mt-button secondary" href="/league-history/">Open League History</a><a class="mt-button secondary" href="/my-team/extension/privacy/">Privacy</a></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Manual installation</small><h2>Install once, choose a provider</h2></div></div><div class="mt-proof"><article><h3>1 · Install</h3><p>Unzip the download. In chrome://extensions, enable Developer mode and choose Load unpacked.</p></article><article><h3>2 · Open your roster</h3><p>Sign in to ESPN, Yahoo, or CBS, open the team roster page, and refresh after loading version 0.4.0.</p></article><article><h3>3 · Choose scoring</h3><p>Select PPR, Half-PPR, or Non-PPR, then save the visible roster locally.</p></article><article><h3>4 · Open My Team</h3><p>The extension opens My Team after a successful capture. Matching and comparisons run locally.</p></article><article><h3>5 · ESPN history</h3><p>On ESPN, Import league history remains available for commissioner review and publishing.</p></article><article><h3>6 · Clear</h3><p>Each destination page can delete its own extension-local data.</p></article></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Distribution</small><h2>Direct download</h2></div><p>The connector is currently installed manually from this versioned package. Chrome Web Store distribution can replace this step without changing either feature.</p></div></section></main>{seo.site_footer()}</body></html>'''


def render_extension_privacy() -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><meta name="description" content="Privacy policy for the Lineup Beat Fantasy connector."><title>Fantasy connector privacy | Lineup Beat</title><link rel="stylesheet" href="/my-team/my-team.css"></head><body>{seo.site_nav("my_team", "nfl")}<main class="mt-shell"><section class="mt-hero"><div class="mt-kicker">Extension privacy · Effective September 4, 2026</div><h1>Your fantasy data stays local.</h1><p>The connector supports ESPN, Yahoo, and CBS roster capture for My Team. ESPN also supports league-history capture for commissioner review. Neither flow uploads private provider data to Lineup Beat.</p><div class="mt-actions"><a class="mt-button" href="/league-history/">Open League History</a><a class="mt-button secondary" href="/my-team/extension/">Support</a></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Data handled</small><h2>Only what each flow needs</h2></div></div><div class="mt-proof"><article><h3>My Team roster</h3><p>Visible player names and provider IDs, NFL teams, positions, lineup slots, league/team labels, season and your scoring choice.</p></article><article><h3>ESPN league history</h3><p>League/team names, seasons, manager labels and IDs, standings, team records, matchup weeks and scores for up to 25 available seasons.</p></article><article><h3>Never stored</h3><p>No provider password, cookie value, session token, authentication token, general browsing history or data from unrelated sites.</p></article></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Storage and use</small><h2>No private-data upload</h2></div></div><div class="mt-proof"><article><h3>Browser-local storage</h3><p>Data is written to chrome.storage.local only after you choose Save roster or Import league history.</p></article><article><h3>Local review</h3><p>My Team and League History receive their local copies through the extension bridge. Commissioner identity edits are stored locally too.</p></article><article><h3>Your control</h3><p>Clear each copy from its destination page, or uninstall the extension to remove all extension-local data.</p></article></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Permissions</small><h2>Narrow access</h2></div></div><div class="mt-proof"><article><h3>Provider roster pages</h3><p>Controls appear only on ESPN, Yahoo Fantasy Football, and CBS Fantasy Football pages. CBS and Yahoo captures read the visible roster only.</p></article><article><h3>ESPN history host</h3><p>lm-api-reads.fantasy.espn.com returns explicitly requested league history using the active ESPN session; the extension never reads that session value.</p></article><article><h3>Lineup Beat pages</h3><p>Only the exact My Team and League History routes on Lineup Beat production and development can request their local dataset. Localhost and unrelated routes are excluded.</p></article></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Limited use</small><h2>No selling or secondary use</h2></div></div><div class="mt-proof"><article><h3>No sharing</h3><p>Private roster and history data is not sold, shared, used for advertising, creditworthiness or lending, or made available for human review.</p></article><article><h3>No analytics</h3><p>The extension contains no advertising or analytics code.</p></article><article><h3>Contact</h3><p>Email <a href="mailto:hello@lineupbeat.com">hello@lineupbeat.com</a>. Do not send passwords, cookies, tokens or private exports.</p></article></div></section></main>{seo.site_footer()}</body></html>'''


def append_sitemap(site: Path) -> None:
    path = site / "sitemap.xml"
    if not path.exists():
        return
    text = path.read_text()
    origin_match = re.search(r"<loc>(https://[^/<]+)", text)
    origin = origin_match.group(1) if origin_match else "https://lineupbeat.com"
    url = origin + "/my-team/"
    if url not in text:
        text = text.replace("</urlset>", f"<url><loc>{html.escape(url)}</loc></url></urlset>")
        path.write_text(text)


def build(site: Path) -> None:
    model = public_model()
    target = site / "my-team"
    target.mkdir(parents=True, exist_ok=True)
    build_chrome_store_bundle.write_package(
        target / "lineupbeat-espn-extension.zip"
    )
    (target / "index.html").write_text(render_page(model))
    guide = target / "extension"
    guide.mkdir(exist_ok=True)
    (guide / "index.html").write_text(render_extension_guide())
    privacy = guide / "privacy"
    privacy.mkdir(exist_ok=True)
    (privacy / "index.html").write_text(render_extension_privacy())
    for name in ("league-adapter.js", "espn-adapter.js", "my-team.js", "my-team.css"):
        shutil.copyfile(SOURCE / name, target / name)
    data = site / "data"
    data.mkdir(exist_ok=True)
    serialized = json.dumps(model, sort_keys=True, separators=(",", ":")) + "\n"
    (data / "my-team-week1.json").write_text(serialized)
    append_sitemap(site)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    print(f"Built My Team: {len(model['players'])} players, model sha256 {digest}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE)
    args = parser.parse_args()
    build(args.site)


if __name__ == "__main__":
    main()
