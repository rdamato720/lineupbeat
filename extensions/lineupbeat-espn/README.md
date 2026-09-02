# Lineup Beat ESPN My Team BETA

This unpacked Manifest V3 extension reads only the roster rows visible on an
ESPN Fantasy Football team page. It does not request cookie access, read or
store passwords or session tokens, collect manager identities, or send a
roster to a Lineup Beat server.

1. Download the submission artifact from the successful development workflow,
   or download product-polish version 0.2.6 from the development support page, and unzip the
   store package.
2. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**,
   and select the unzipped directory.
3. Open the ESPN team roster page, select its reception scoring, and choose
   **Save roster locally for My Team**. The extension does not guess league scoring.
4. After the save completes, My Team opens automatically. If the browser
   prevents that, choose the prominent **Open My Team** action.
5. Use **Disconnect & clear** on My Team to delete the extension-local copy.

Version 0.2.6 preserves the proven row-first parser and local handoff, improves
unambiguous visible league/team label capture, and adds Week 1 roster outlook,
projection, opponent, opportunity, status, and actionable lineup evidence.
Installed-extension QA remains blocked until Ralph verifies a live save. If
the empty-roster error appears, choose **Copy safe diagnostics** and paste the
JSON into Codex. The diagnostic is created only by that click, copied only to
the local clipboard, excludes roster and identity content, and makes no
network request.

The extension is host-limited to ESPN Fantasy Football, the isolated Lineup
Beat development My Team route. It cannot run on other development routes,
localhost, loopback addresses, or production.

Privacy policy:
`https://lineupbeat-dev.pages.dev/my-team/extension/privacy/`
