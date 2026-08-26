# Lineup Beat feedback service

Cloudflare Worker + D1 backend for the site-wide reader feedback widget.
It stores feedback, rate-limits by a salted IP hash, and exposes a token-gated
admin view. Raw IP addresses are never stored.

## First deployment

```bash
cd feedback-worker
npm install
npx wrangler login
npx wrangler d1 create lineupbeat-feedback
```

Put the returned database ID in `wrangler.toml`, then run:

```bash
npx wrangler d1 migrations apply lineupbeat-feedback --remote
npx wrangler secret put ADMIN_TOKEN
npx wrangler secret put IP_HASH_SALT
npx wrangler deploy
```

Map `feedback.lineupbeat.com` to the Worker in Cloudflare. The public endpoint
is `https://feedback.lineupbeat.com/feedback`; the private review screen is
`https://feedback.lineupbeat.com/admin`.

Use a long random value for both secrets. Never commit them.

