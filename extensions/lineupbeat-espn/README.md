# Lineup Beat Fantasy Connector

This unpacked Manifest V3 extension saves visible ESPN, Yahoo, and CBS Fantasy
Football rosters locally for My Team. ESPN imports available league seasons and
CBS adds the visible History season for commissioner review in League History. It does not request
cookie permission, read or store passwords,
cookies, or session tokens, or upload either dataset to a Lineup Beat server.

1. Download the submission artifact from the successful development workflow,
   or download version 0.5.0 from the support page, and unzip the
   store package.
2. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**,
   and select the unzipped directory.
3. Open the ESPN, Yahoo, or CBS team roster page, select its reception scoring, and choose
   **Save roster locally for My Team**. The extension does not guess league scoring.
4. After the save completes, My Team opens automatically. If the browser
   prevents that, choose the prominent **Open My Team** action.
5. Use **Disconnect & clear** on My Team to delete the extension-local copy.
6. To test ESPN history, choose **Import league history** on an ESPN league page.
   The extension requests that league's available seasons directly from ESPN
   using the ESPN session already active in the browser, then opens the private
   League History review. Approve manager names and possible identity merges or
   clear the local snapshot there.
7. To capture CBS history, open My League, History, then a completed season
   scoreboard or schedule. Choose **Add this history season** and repeat for
   each season. The extension refuses to save incomplete or ambiguous tables.

Version 0.5.0 adds fail-closed CBS season-history capture while preserving Yahoo
and CBS roster capture, bounded ESPN history import, local snapshot storage,
incomplete-season reporting, and commissioner identity review. The history archive handles up to 25 seasons and captures league/team
labels, manager labels, standings, and matchup scores. If the empty-roster
error appears, choose **Copy safe diagnostics** and paste the JSON into Codex.
The diagnostic is created only by that click, copied only to the local
clipboard, excludes roster and identity content, and makes no network request.

The extension is host-limited to ESPN, Yahoo, and CBS Fantasy Football, ESPN's fantasy read API,
and the exact Lineup Beat production and development My Team and League History
routes. It cannot run on other Lineup Beat routes, localhost, or loopback addresses.

Privacy policy:
`https://lineupbeat.com/my-team/extension/privacy/`
