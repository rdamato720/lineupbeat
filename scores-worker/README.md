# Homepage football scores

This separate Cloudflare Worker serves normalized NFL and FBS-involving football games. The homepage uses `/nfl` and `/college` at `https://scores.lineupbeat.com`. It does not fetch ESPN, odds, or secrets in the browser. No fantasy inputs are changed.

TheRundown's private `THERUNDOWN_API_KEY` is installed using Wrangler's secret command. NFL sport ID is 2 and college is 1. An authenticated server request uses `X-TheRundown-Key`, narrow markets, and `hide_no_markets=false` to retain games without a betting market. The provider's college team directory determines FBS membership from conference/division classifications; unknown classifications or a suspicious team count fail deployment. Games with one FBS team are included. This still requires validating the real directory with the account before launch.

A single Durable Object shares both sports' cache and usage accounting across visitors and locations. It stores normalized display fields, not raw odds. Cached provider data expires within 24 hours; cleanup runs on requests and after inactivity. The window is yesterday through six days ahead using the provider's fixed UTC-5 date boundary. User-facing kickoff times use the visitor's timezone.

Active games refresh at most once a minute when visitors are present; scheduled games refresh less often. The browser polls every minute while visible. The account tested on September 10 reports a 30-second provider delay; additional polling latency applies. No real-time clock or WebSocket access is assumed. Stale scores are visibly marked and lose the active highlight.

The ticker reserves 10,000 points before each external call, then accounts for `x-datapoints`. It stops requests at a rolling seven-day budget of 1,000,000 points and preserves at least 100,000 reported account points. Unexpected billing, 401, and 403 stop further requests. Failed requests conservatively retain their reservation. This is a guard, not a vendor billing guarantee; an unexpectedly large single response can exceed a reservation. Other applications' usage is outside this counter. Account headers remain authoritative.

## Deployment

From an isolated checkout of this reviewed branch, run `bash scripts/deploy_rundown_scores.sh`. Requires Node 22+, npm, git, curl, GitHub CLI, access to the LineupBeat GitHub repo, and the Cloudflare account hosting lineupbeat.com. The script opens normal authentication flows and securely prompts for the Rundown key; do not pass it on the command line.

It deploys the worker/custom domain and private cache, installs the key, warms and validates both feeds, then marks the PR ready, merges the exact checked-out commit and starts the existing production refresh with `skip_fetch=true`. Finally it verifies the deployed homepage's score strip. A failed provider check prevents the merge. No automatic workaround for a provider refusal is included. `/health` reports configuration and a sanitized block reason, never the key. A permanent access block requires explicit operator review and cache-block recovery after resolving access; redeploying alone does not clear it.

The script does not deploy fantasy-data changes or fetch new Wire stories. The normal site's existing build, prune, and artifact checks still apply. Future source updates require the normal site refresh, since its existing push-path filter does not include ticker files.

## Verification and rollback

Run `npm ci`, `npm test`, and `npm run check` in this directory, plus `node scripts/test_score_ticker.cjs` and `python scripts/test_decision_room.py` from the repository root. These checks use fixtures and spend zero provider points. Actual hosted access is checked separately by `node scripts/verify_rundown_scores.mjs`; it consumes the normal narrow feed budget.

To roll back the frontend, revert the ticker PR and run the existing `refresh.yml` workflow with `skip_fetch=true`; verify the deployed homepage. This restores the previous ticker behavior. Disable the score worker route if retiring the service. Do not delete shared Cloudflare resources or unrelated application secrets.
