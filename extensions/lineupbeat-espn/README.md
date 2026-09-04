# Lineup Beat ESPN Connector

This unpacked Manifest V3 extension supports two explicit, browser-local ESPN
flows: saving the roster visible on an ESPN Fantasy Football team page for My
Team, and importing available league seasons for commissioner review in League
History. It does not request cookie permission, read or store passwords,
cookies, or session tokens, or upload either dataset to a Lineup Beat server.

1. Download the submission artifact from the successful development workflow,
   or download version 0.3.0 from the support page, and unzip the
   store package.
2. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**,
   and select the unzipped directory.
3. Open the ESPN team roster page, select its reception scoring, and choose
   **Save roster locally for My Team**. The extension does not guess league scoring.
4. After the save completes, My Team opens automatically. If the browser
   prevents that, choose the prominent **Open My Team** action.
5. Use **Disconnect & clear** on My Team to delete the extension-local copy.
6. To test history, choose **Import league history** on an ESPN league page.
   The extension requests that league's available seasons directly from ESPN
   using the ESPN session already active in the browser, then opens the private
   League History review. Approve manager names and possible identity merges or
   clear the local snapshot there.

Version 0.3.0 preserves the roster flow and adds a bounded ESPN history import,
local snapshot storage, incomplete-season reporting, and commissioner identity
review. The history importer handles up to 25 seasons and captures league/team
labels, manager labels, standings, and matchup scores. If the empty-roster
error appears, choose **Copy safe diagnostics** and paste the JSON into Codex.
The diagnostic is created only by that click, copied only to the local
clipboard, excludes roster and identity content, and makes no network request.

The extension is host-limited to ESPN Fantasy Football, ESPN's fantasy read API,
and the exact Lineup Beat production and development My Team and League History
routes. It cannot run on other Lineup Beat routes, localhost, or loopback addresses.

Privacy policy:
`https://lineupbeat.com/my-team/extension/privacy/`
