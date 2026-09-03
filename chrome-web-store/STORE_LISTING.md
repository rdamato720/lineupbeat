# Lineup Beat ESPN Connector BETA — Chrome Web Store submission sheet

Prepared for an **Unlisted** beta. This document does not authorize upload,
review submission, or publication.

**Release block:** version 0.3.0 needs a live ESPN import test. Chrome Web Store
upload and submission remain blocked.

## Package tab

- Upload file: `lineupbeat-espn-my-team-beta-0.3.0.zip`
- Manifest version: 3
- Extension version: 0.3.0
- Extension name: `Lineup Beat ESPN Connector BETA`

## Store listing tab

### Short summary

`Save an ESPN roster or import league history locally for review in Lineup Beat, without uploading private league data.`

### Detailed description

```text
THIS EXTENSION IS FOR BETA TESTING.

Lineup Beat ESPN Connector BETA supports two explicit, browser-local flows.

Save roster locally for My Team reads the roster visible on the open ESPN team page and passes it locally to Lineup Beat My Team. Import league history requests up to 25 available seasons for the open ESPN league, stores the normalized snapshot locally, and opens a commissioner review for manager names and possible identity merges.

League history includes league/team names, seasons, manager labels and IDs, standings, records, matchup weeks, and scores. The extension does not request cookie permission and never reads or stores ESPN passwords, cookie values, session tokens, or authentication tokens. It contains no analytics or advertising code. Private roster and history data is not uploaded to Lineup Beat.

Users can clear each local dataset from its destination page. Uninstalling the extension also removes extension-local storage.

This beta is limited to ESPN Fantasy Football, ESPN's fantasy read API, and the exact Lineup Beat development My Team and League History routes. It has no production, localhost, loopback, or broad website access.
```

### Single purpose

Save user-requested ESPN fantasy roster or league-history data in browser-local
storage and pass it to the matching Lineup Beat development review experience.

### Links

- Homepage: `https://lineupbeat-dev.pages.dev/league-history/`
- Support URL: `https://lineupbeat-dev.pages.dev/my-team/extension/`
- Privacy policy URL: `https://lineupbeat-dev.pages.dev/my-team/extension/privacy/`

## Permission justification

### `storage`

Retains the explicitly captured roster, history snapshot, and commissioner
identity review locally until the user clears them.

### `https://fantasy.espn.com/football/*`

Shows the capture panel on ESPN Fantasy Football. Visible roster fields are read
only after Save roster locally for My Team is selected.

### `https://lm-api-reads.fantasy.espn.com/*`

Requests only the selected league's available seasons after Import league
history is selected. The active ESPN session authorizes the request; the
extension does not inspect or store the session value.

### Lineup Beat development routes

`/my-team/*` receives the local roster. `/league-history/*` receives the local
history snapshot, stores commissioner identity approval, and honors clear
requests. Other Lineup Beat routes and production are excluded.

### Remote code

No. All executable code is included in the package.

## Data-use selections

- Select **Website content** for roster and league-history fields.
- Select **Personally identifiable information** if Chrome classifies manager
  labels or ESPN member IDs in that category.
- Do not select authentication information: credential and session values are
  neither read nor stored.
- Certify that data is not sold, transferred, used for advertising,
  creditworthiness, lending, or an unrelated purpose.

## Distribution

- Visibility: **Unlisted**
- Pricing: **Free**
- In-app purchases: **No**

## Test instructions

No credentials are required for the deterministic My Team reviewer path.

1. Install version 0.3.0.
2. Open `https://lineupbeat-dev.pages.dev/my-team/?reviewer=1`.
3. Choose Load reviewer demo roster and confirm the sample roster renders.
4. Choose Disconnect & clear and confirm the roster disappears.

### Live installed-extension QA

1. Sign in to ESPN and open a fantasy football league page.
2. Choose Import league history.
3. Confirm League History opens with season, game, team, and manager counts.
4. Review names, test one merge choice, and choose Approve identities.
5. Reload and confirm the approval remains local.
6. Clear local import and confirm it no longer returns.
7. Return to ESPN, select scoring, and test Save roster locally for My Team and
   Open My Team.

If history authorization fails, reload the signed-in ESPN league page and try
again. Never send ESPN passwords, cookie values, session tokens, or private
exports for troubleshooting.

## Graphics

- `store-icon-128.png` — 128×128 PNG
- `small-promo-440x280.png` — 440×280 PNG
- Three 1280×800 screenshots in `screenshots/`

Current screenshots cover My Team. Capture new fictional League History review
screenshots before Store submission; do not use private league data.

## Future Chrome Web Store steps — currently blocked

Upload and submission remain blocked until the live ESPN import test and updated
fictional screenshots pass review. After that, compare the ZIP SHA-256 with
`package-inventory.json`, complete the dashboard fields, keep visibility
Unlisted, and obtain explicit approval before Submit for Review.

Official references: [Prepare the ZIP](https://developer.chrome.com/docs/webstore/prepare),
[listing fields](https://developer.chrome.com/docs/webstore/cws-dashboard-listing),
[privacy fields](https://developer.chrome.com/docs/webstore/cws-dashboard-privacy), and
[distribution](https://developer.chrome.com/docs/webstore/cws-dashboard-distribution).
