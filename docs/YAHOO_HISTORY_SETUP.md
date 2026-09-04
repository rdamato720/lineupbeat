# Yahoo league-history setup

LineupBeat uses Yahoo's OAuth 2.0 authorization-code flow and Fantasy Sports API.
The user's Yahoo password never reaches LineupBeat. Access and refresh tokens are
kept only in an encrypted, HttpOnly browser cookie; they are not written to D1,
local storage, logs, or the published league archive.

## Yahoo application

Create one Yahoo developer application with Fantasy Sports read access. Register
these callback URLs exactly:

- `https://lineupbeat-dev.pages.dev/api/yahoo/callback`
- `https://lineupbeat.com/api/yahoo/callback`

Yahoo documentation:

- <https://sports.yahoo.com/developer/docs/>
- <https://developer.yahoo.com/oauth2/guide/>

## Development secrets

Add these GitHub Actions secrets to `rdamato720/lineupbeat`:

- `YAHOO_CLIENT_ID`
- `YAHOO_CLIENT_SECRET`
- `YAHOO_SESSION_SECRET` — a random value of at least 32 characters

The development deployment workflow copies non-empty values into the
`lineupbeat-dev` Cloudflare Pages project before deploying. If they are absent,
`/api/yahoo/status` reports `configured: false` and the League History page keeps
the Yahoo button disabled instead of beginning a broken authorization flow.

Production secrets must be configured independently on the `lineupbeat`
Cloudflare Pages project before production promotion.

## Runtime flow

1. `/api/yahoo/connect` creates a short-lived CSRF state cookie and redirects to Yahoo.
2. `/api/yahoo/callback` verifies state, exchanges the code, encrypts the token session,
   and returns to `/league-history/`.
3. `/api/yahoo/leagues` finds the signed-in user's NFL leagues and groups Yahoo renewal
   chains into one archive choice.
4. `/api/yahoo/season` reads settings, standings, and completed weekly scoreboards for
   one league season. The browser imports seasons one at a time and saves only after
   the entire archive validates.
5. The normalized archive uses `lineupbeat-history-capture-v1`, then follows the same
   manager-review, record-book, and publishing pipeline as ESPN.
