#!/usr/bin/env python3
"""Build the development-only My Team page and redacted public Week 1 model."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
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
<meta name="description" content="Connect an ESPN fantasy roster locally and inspect Week 1 starter and bench decisions without uploading roster data.">
<title>My Team · NFL Week 1 | Lineup Beat Development</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600;700;800&amp;family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&amp;display=swap" rel="stylesheet">
<link rel="stylesheet" href="/my-team/my-team.css"></head><body>
{nav}
<main class="mt-shell"><section class="mt-hero"><div class="mt-kicker">NFL · My Team · Development preview</div>
<h1>Your roster stays in your browser.</h1>
<p>Connect the Lineup Beat ESPN extension to compare supported starters and bench players against the public Week 1 model. Roster matching and every lineup calculation happen locally on this page.</p>
<div class="mt-actions"><button class="mt-button" id="mt-connect" type="button">Connect ESPN extension</button><button class="mt-button secondary" id="mt-disconnect" type="button" hidden>Disconnect &amp; clear</button><button class="mt-button secondary" id="mt-demo" type="button" hidden>Load reviewer demo roster</button><a class="mt-button secondary" href="/my-team/extension/">Extension support</a></div>
<p class="mt-status" id="mt-status" role="status" aria-live="polite">Loading the public Week 1 model…</p></section>
<section class="mt-privacy" aria-label="Privacy model"><article><small>What stays local</small><h2>Roster data never leaves this browser</h2><p>The extension keeps its captured roster in extension-local storage. The My Team page holds the normalized roster in memory only. It is never placed in a URL or sent to a Lineup Beat server.</p></article><article><small>What is never collected</small><h2>No ESPN secrets or manager identity</h2><ul><li>No password, cookie, session token, or manager identity.</li><li>No roster analytics or server persistence.</li><li>Disconnect &amp; clear removes the extension-local roster copy.</li></ul></article></section>
<section class="mt-section"><div class="mt-section-head"><div><small>Connection 01</small><h2>ESPN now. Neutral architecture underneath.</h2></div><p>Only the working ESPN browser-local connection is active. Every future provider must produce the same validated league, team, slots, roster, identity, and unresolved-status structure.</p></div>
<div class="mt-provider-grid"><article class="mt-provider active"><small>Available in this development release</small><h3>ESPN browser-local extension</h3><p>Visible roster capture, local matching, and local Week 1 decisions. The extension cannot connect to production.</p><strong>Active connection above</strong></article><article class="mt-provider mt-provider-planned"><small>Planned · unavailable</small><h3>Sleeper</h3><p>Read-only API adapter only after licensing and production-use requirements are reviewed.</p></article><article class="mt-provider mt-provider-planned"><small>Planned · unavailable</small><h3>Yahoo</h3><p>Requires approved OAuth and secure server-side token handling.</p></article><article class="mt-provider mt-provider-planned"><small>Planned · unavailable</small><h3>CBS</h3><p>Only after a current supported access method is validated. Lineup Beat will never request or store a CBS password.</p></article><article class="mt-provider mt-provider-planned"><small>Planned · unavailable</small><h3>MFL</h3><p>Requires a validated API or league-URL import method.</p></article><article class="mt-provider mt-provider-planned"><small>Planned · unavailable</small><h3>RT Sports</h3><p>Requires a validated API or league-URL import method.</p></article></div>
<section class="mt-team-card" id="mt-team" hidden><div class="mt-team-title"><div><small>Connected league</small><h3 id="mt-team-name"></h3><p id="mt-league-name"></p></div><p id="mt-league-meta"></p></div><div id="mt-roster"></div></section></section>
<section class="mt-section"><div class="mt-section-head"><div><small>Decision stack 02</small><h2>Starter and bench decisions</h2></div><p>Projection gap, expected opportunity, scoring format, and labeled 2025 opponent context. Current injuries and sportsbook evidence remain unavailable.</p></div><div class="mt-decisions" id="mt-decisions"><article class="mt-empty"><h3>Connect a roster to begin.</h3><p>Only QB, RB, WR and TE receive comparisons. D/ST and every unresolved player remain visible with an explicit reason.</p></article></div></section>
<section class="mt-section"><div class="mt-section-head"><div><small>Guardrails 03</small><h2>What this release does not claim</h2></div></div><div class="mt-proof"><article><h3>No predictive-lift claim</h3><p>This development release has not established improvement over a validated baseline.</p></article><article><h3>No independent corroboration</h3><p>Projection, opportunity and matchup context come from the same Lineup Beat Week 1 model.</p></article><article><h3>No invented availability</h3><p>Current injury and sportsbook evidence are unavailable. D/ST is unsupported rather than guessed.</p></article></div></section></main>
{seo.site_footer()}
<script src="/my-team/league-adapter.js"></script><script src="/my-team/espn-adapter.js"></script><script src="/my-team/my-team.js"></script>
</body></html>'''


def render_extension_guide() -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><title>ESPN extension support | Lineup Beat Development</title><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600;700;800&amp;family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&amp;display=swap" rel="stylesheet"><link rel="stylesheet" href="/my-team/my-team.css"></head><body>{seo.site_nav("my_team", "nfl")}<main class="mt-shell"><section class="mt-hero"><div class="mt-kicker">Unlisted beta support</div><h1>Connect ESPN without sharing credentials.</h1><p>The beta extension reads only visible roster rows after an explicit save. It has no cookie permission, collects no password, token, or manager identity, and is restricted to ESPN Fantasy Football plus the isolated development My Team route.</p><div class="mt-actions"><a class="mt-button" href="/my-team/lineupbeat-espn-extension.zip" download>Download version 0.2.0</a><a class="mt-button secondary" href="/my-team/">Open My Team</a><a class="mt-button secondary" href="/my-team/extension/privacy/">Extension privacy</a></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Use the beta</small><h2>Capture, open, clear</h2></div></div><div class="mt-proof"><article><h3>1 · Install</h3><p>Unzip the download, open chrome://extensions, enable Developer mode, choose Load unpacked, and select the unzipped folder.</p></article><article><h3>2 · Open ESPN</h3><p>Open the ESPN Fantasy Football team roster that is already visible in your signed-in browser.</p></article><article><h3>3 · Choose scoring</h3><p>Select PPR, Half-PPR or Non-PPR. The extension does not guess league scoring.</p></article><article><h3>4 · Save locally</h3><p>Choose Save roster locally for My Team after reading the in-extension disclosure.</p></article><article><h3>5 · Continue</h3><p>My Team opens automatically. If it does not, choose Open My Team in the extension panel.</p></article><article><h3>6 · Clear</h3><p>Use Disconnect &amp; clear to delete the chrome.storage.local roster copy.</p></article></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Distribution</small><h2>Development-only package</h2></div><p>This download is byte-identical to the validated version 0.2.0 Chrome Web Store submission artifact. It is provided for Ralph's manual installed-extension QA and is not a Chrome Web Store publication.</p></div></section></main>{seo.site_footer()}</body></html>'''


def render_extension_privacy() -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><meta name="description" content="Privacy policy for the Lineup Beat ESPN My Team beta extension."><title>ESPN My Team extension privacy | Lineup Beat Development</title><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600;700;800&amp;family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&amp;display=swap" rel="stylesheet"><link rel="stylesheet" href="/my-team/my-team.css"></head><body>{seo.site_nav("my_team", "nfl")}<main class="mt-shell"><section class="mt-hero"><div class="mt-kicker">Extension privacy · Effective September 1, 2026</div><h1>Your ESPN roster stays local.</h1><p>This policy applies only to Lineup Beat ESPN My Team BETA. Its single purpose is to let you save the fantasy roster already visible on an ESPN Fantasy Football page and use it locally on the Lineup Beat development My Team page.</p><div class="mt-actions"><a class="mt-button" href="/my-team/">Open My Team</a><a class="mt-button secondary" href="/my-team/extension/">Extension support</a></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Data handled</small><h2>What the extension reads</h2></div></div><div class="mt-proof"><article><h3>Visible roster data</h3><p>Player names, ESPN player IDs, NFL teams, positions and starting, bench or reserve lineup slots that are visible on the open roster page.</p></article><article><h3>League and team labels</h3><p>League ID, league name, season, the reception-scoring choice you make, team ID and team name. Manager identities are not collected.</p></article><article><h3>Never read or collected</h3><p>No ESPN password, cookie, session token, authentication token, manager identity, browsing history or data from another site.</p></article></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Storage and use</small><h2>No roster upload</h2></div></div><div class="mt-proof"><article><h3>Browser-local storage</h3><p>After you explicitly choose Save roster locally for My Team, the captured roster is stored in chrome.storage.local in that Chrome profile. It remains there until you clear it or uninstall the extension.</p></article><article><h3>Local processing</h3><p>The development My Team page receives the roster through the local extension bridge and holds its normalized copy in page memory. The roster is not placed in a URL or sent to a Lineup Beat server.</p></article><article><h3>Public model only</h3><p>My Team separately downloads the public Week 1 model over HTTPS. Roster data is not included in that request. The extension contains no analytics or advertising code.</p></article></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Access boundary</small><h2>Why each permission exists</h2></div></div><div class="mt-proof"><article><h3>storage</h3><p>Required only to retain the roster locally between the ESPN roster page and My Team, and to delete it when you choose Disconnect &amp; clear.</p></article><article><h3>fantasy.espn.com/football</h3><p>Required only to show the explicit save panel and read the visible ESPN Fantasy Football roster after you choose to save it.</p></article><article><h3>lineupbeat-dev.pages.dev/my-team</h3><p>Required only to pass the locally stored roster to the development My Team page and honor its clear request. The extension has no production, localhost or broad host access.</p></article></div></section><section class="mt-section"><div class="mt-section-head"><div><small>Control and limited use</small><h2>Delete, sharing and contact</h2></div></div><div class="mt-proof"><article><h3>Delete your data</h3><p>Choose Disconnect &amp; clear on My Team to remove the extension-local roster immediately. Uninstalling the extension also removes its local storage.</p></article><article><h3>No sharing or secondary use</h3><p>Roster and league data is not sold, shared, transferred, used for advertising, used for creditworthiness or lending, or made available for human review. It is handled only for the extension's single user-facing purpose.</p></article><article><h3>Contact</h3><p>Questions about this policy or the beta may be sent to <a href="mailto:hello@lineupbeat.com">hello@lineupbeat.com</a>. Do not send passwords, cookies, tokens or private roster exports.</p></article></div></section></main>{seo.site_footer()}</body></html>'''


def append_sitemap(site: Path) -> None:
    path = site / "sitemap.xml"
    if not path.exists():
        return
    text = path.read_text()
    url = "https://lineupbeat-dev.pages.dev/my-team/"
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
