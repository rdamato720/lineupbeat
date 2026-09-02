# Lineup Beat ESPN My Team BETA — Chrome Web Store submission sheet

Prepared for an **Unlisted** beta. This document is paste-ready, but it does
not authorize an upload, review submission, or publication.

**Release block:** version 0.2.6 is a development product-polish build.
The label and roster-intelligence polish awaits Ralph's live ESPN save test, so Chrome Web Store
upload and submission remain blocked.

## Package tab

- Upload file: `lineupbeat-espn-my-team-beta-0.2.6.zip`
- Manifest version: 3
- Extension version: 0.2.6
- Extension name: `Lineup Beat ESPN My Team BETA`
- Manifest description (104 characters):

  `THIS EXTENSION IS FOR BETA TESTING. Saves a visible ESPN fantasy roster locally for Lineup Beat My Team.`

The ZIP contains `manifest.json` at its root. Do not upload the listing bundle
or either source SVG.

## Store listing tab

### Language

`English (United States)`

### Extension name

`Lineup Beat ESPN My Team BETA`

### Short summary

`Save your visible ESPN fantasy roster locally, then review Lineup Beat My Team decisions without uploading roster data.`

### Detailed description

```text
THIS EXTENSION IS FOR BETA TESTING.

Lineup Beat ESPN My Team BETA has one purpose: save the fantasy roster already visible on your ESPN Fantasy Football team page and make it available locally to Lineup Beat's development My Team experience.

After you explicitly choose Save roster locally for My Team, the extension reads visible player names, ESPN player IDs, NFL teams, positions, lineup slots, league ID and name, season, team ID and name, plus the reception-scoring format you select. It stores that roster only in chrome.storage.local in your browser profile.

My Team opens automatically after a successful save. If Chrome cannot open it automatically, the extension shows a prominent Open My Team action. On My Team, supported QB, RB, WR and TE players are matched against the public Week 1 model. D/ST and unresolved players remain clearly labeled instead of being guessed.

The extension does not request cookie access and does not collect ESPN passwords, cookies, session tokens, authentication tokens or manager identities. It contains no analytics or advertising code. Roster data is not placed in a URL or uploaded to Lineup Beat.

Choose Disconnect & clear on My Team to delete the chrome.storage.local roster copy. Uninstalling the extension also removes its local storage.

This unlisted beta works only on ESPN Fantasy Football and the isolated Lineup Beat development My Team route. It has no production, localhost or broad website access.
```

### Category

`Sports`

### Homepage URL

`https://lineupbeat-dev.pages.dev/my-team/`

### Support URL

`https://lineupbeat-dev.pages.dev/my-team/extension/`

### Graphics

- Store icon: `store-icon-128.png` — 128×128 PNG
- Screenshot 1: `screenshots/01-local-connection-1280x800.png` — 1280×800 PNG
- Screenshot 2: `screenshots/02-lineup-decision-1280x800.png` — 1280×800 PNG
- Screenshot 3: `screenshots/03-local-roster-1280x800.png` — 1280×800 PNG
- Small promotional tile: `small-promo-440x280.png` — 440×280 PNG
- YouTube video: leave blank; no video is required for this beta.
- Marquee promotional tile: leave blank; it is optional.

The screenshots use a deterministic sample team and public NFL player/model
data. They contain no private league member, credential, or personal roster
information.

## Privacy practices tab

### Single purpose

```text
Save the ESPN Fantasy Football roster currently visible to the user in browser-local extension storage, then pass it locally to the Lineup Beat development My Team page for roster matching and lineup comparisons.
```

### Permission justification: `storage`

```text
Required to retain the roster locally between the ESPN roster page and the Lineup Beat development My Team page, and to delete that local copy when the user chooses Disconnect & clear. The stored roster is never uploaded to Lineup Beat.
```

### Host justification: `https://fantasy.espn.com/football/*`

```text
Required only to display the explicit save panel and, after the user's save action, read the roster and league fields visible on an ESPN Fantasy Football page. Access does not extend to other ESPN products or websites.
```

### Host justification: `https://lineupbeat-dev.pages.dev/my-team/*`

```text
Required only to pass the extension-local roster to the isolated development My Team page, provide the deterministic reviewer demo, and honor the user's Disconnect & clear request. The extension cannot run on other Lineup Beat routes, production, localhost or loopback addresses.
```

### Remote code

`No.` All executable extension code is authored, reviewable JavaScript in the
submitted package. The extension downloads and executes no remote code.

### Data-use selections

- Select **Website content**. The extension locally handles the visible roster,
  lineup and league/team labels described above.
- Do not select personally identifiable information, health information,
  financial/payment information, authentication information, personal
  communications, location, web history, or user activity. Manager identity,
  passwords, cookies and authentication tokens are not collected.
- Certify that data is not sold or transferred to third parties.
- Certify that data is not used or transferred for purposes unrelated to the
  extension's single purpose.
- Certify that data is not used or transferred to determine creditworthiness
  or for lending purposes.
- Certify compliance with the Chrome Web Store Limited Use requirements.

### Privacy policy URL

`https://lineupbeat-dev.pages.dev/my-team/extension/privacy/`

## Distribution tab

- Visibility: **Unlisted**
- Pricing: **Free**
- In-app purchases: **No**
- Regions: **All regions** unless Ralph intentionally chooses a narrower beta
  geography.
- Do not choose Public or Private by mistake. Unlisted allows installation only
  by people who know the item URL, while still requiring normal Store review.

## Test instructions tab

### Credentials

`No credentials are required for the deterministic reviewer path.`

### Paste-ready reviewer instructions

```text
1. Install the submitted extension package.
2. Open https://lineupbeat-dev.pages.dev/my-team/?reviewer=1
3. Confirm the page is visibly labeled DEVELOPMENT PREVIEW and click Load reviewer demo roster.
4. The page should show a connected sample team with four matched QB/RB/WR/TE players, one unsupported D/ST entry, zero unresolved players, and at least one starter/bench decision.
5. Confirm the page states that no roster data was uploaded.
6. Click Disconnect & clear. The connected roster should disappear and the status should confirm that extension-local storage was cleared.
7. Reload the page and click Connect ESPN extension. No roster should return after clearing.

The reviewer demo uses only public NFL player/model data and a generic sample team. It requires no ESPN account and does not expand the manifest host scope.

Optional ESPN capture test, if the reviewer already has access to any ESPN Fantasy Football roster:
1. Open a team roster under https://fantasy.espn.com/football/
2. Read the prominent local-storage disclosure, select reception scoring, and click Save roster locally for My Team.
3. Version 0.2.6 preserves ESPN's proven row-first capture and adds unambiguous visible league/team labels plus Week 1 roster intelligence. Confirm My Team opens and displays the saved roster.
4. If the empty-roster error still appears, click Copy safe diagnostics, confirm the copy status, and paste the JSON into Codex for investigation.

The extension should never request an ESPN password, cookie, session token or manager identity. No roster request should be sent to a Lineup Beat server.
```

## Ralph's manual installed-extension QA

This checklist remains outstanding until Ralph runs it in installed Chrome:

1. Install version 0.2.6 with **Load unpacked**, or reload the already installed
   unpacked extension and confirm the version is 0.2.6.
2. Open an ESPN Fantasy Football league roster under
   `https://fantasy.espn.com/football/`.
3. Choose the league's reception scoring and select **Save roster locally for
   My Team**.
4. Confirm My Team opens automatically and displays the captured starters,
   bench players, and honest unsupported-position labels.
5. Return to ESPN and confirm the **Open My Team** fallback works.
6. Use **Disconnect & clear** and confirm the locally stored roster is removed.
7. If capture still fails, select **Copy safe diagnostics**, confirm the copy
   status, and paste only that JSON into Codex.

## Future Chrome Web Store steps — currently blocked

Do not perform these steps while the version 0.2.6 release block is active.

The successful development workflow publishes two public GitHub Actions
artifacts containing only the reviewed extension package and store-listing
materials. They contain no credentials, private roster exports, cookies,
tokens, API keys or personal member information.

The same version 0.2.6 product-polish ZIP is also available from the development
support page for manual installed-extension QA. Its SHA-256 must match the
inventory below before use.

1. Download the `lineupbeat-espn-cws-submission-<run-id>` artifact and extract
   its `lineupbeat-espn-my-team-beta-0.2.6.zip` file.
2. Download the `lineupbeat-espn-cws-listing-<run-id>` artifact and extract its
   listing materials.
3. Confirm the submission ZIP SHA-256 matches `package-inventory.json` in the
   listing materials.
4. In the Chrome Web Store Developer Dashboard, manually choose **Add new
   item** and upload only the submission ZIP. This is the first Chrome Web
   Store action and is intentionally not performed by Codex.
5. Paste the Store listing fields and upload the icon, screenshots and promo
   tile from the listing artifact.
6. Paste the Privacy practices answers and working privacy-policy URL.
7. Set Distribution visibility to **Unlisted**, pricing to Free, purchases to
   No, and choose the intended regions.
8. Paste the reviewer instructions. No private ESPN credentials should be
   supplied.
9. Review the dashboard's automatically generated permission warnings against
    this sheet.
10. Manually record the new item ID and unlisted Store URL; neither exists
    before the first upload.
11. Decide whether to enable deferred publishing. Do not click **Submit for
    Review** until Ralph has approved the complete dashboard draft.
12. After submission, any review response, approval and final publication are
    separate manual decisions.

Official references: [Prepare the ZIP](https://developer.chrome.com/docs/webstore/prepare),
[listing fields](https://developer.chrome.com/docs/webstore/cws-dashboard-listing),
[privacy fields](https://developer.chrome.com/docs/webstore/cws-dashboard-privacy),
[distribution](https://developer.chrome.com/docs/webstore/cws-dashboard-distribution),
and [image requirements](https://developer.chrome.com/docs/webstore/images).
