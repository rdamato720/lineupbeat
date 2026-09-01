# Lineup Beat ESPN My Team — development extension

This unpacked Manifest V3 extension reads only the roster rows visible on an
ESPN Fantasy Football team page. It does not request cookie access, read or
store passwords or session tokens, collect manager identities, or send a
roster to a Lineup Beat server.

1. Unzip the development package.
2. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**,
   and select the unzipped directory.
3. Open the ESPN team roster page, select its reception scoring, and choose
   **Save roster locally for My Team**. The extension does not guess league scoring.
4. Open `https://lineupbeat-dev.pages.dev/my-team/` and choose **Connect ESPN**.
5. Use **Disconnect & clear** on My Team to delete the extension-local copy.

The extension is host-limited to ESPN Fantasy Football, the isolated Lineup
Beat development My Team route. It cannot run on other development routes,
localhost, loopback addresses, or production.
