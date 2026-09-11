#!/usr/bin/env bash
set -euo pipefail
set +x
cd "$(dirname "$0")/.."
REPO=rdamato720/lineupbeat
BRANCH=codex/rundown-home-scores
if command -v brew >/dev/null; then
  command -v gh >/dev/null || brew install gh
  if ! command -v node >/dev/null || ! node -e 'if(Number(process.versions.node.split(".")[0])<22) process.exit(1)'; then
    brew install node@22
    export PATH="$(brew --prefix node@22)/bin:$PATH"
  fi
fi
for command in node npm git gh curl; do
  if ! command -v "$command" >/dev/null; then
    echo "Missing $command. Install Node.js 22+ and GitHub CLI (brew install node gh), then rerun." >&2
    exit 1
  fi
done
node -e 'if(Number(process.versions.node.split(".")[0])<22) process.exit(1)' || { echo 'Node.js 22 or newer is required.'; exit 1; }
gh auth status >/dev/null 2>&1 || gh auth login --hostname github.com --web --git-protocol https
HEAD_SHA=$(git rev-parse HEAD)
PR_HEAD=$(gh pr view "$BRANCH" --repo "$REPO" --json headRefOid --jq .headRefOid)
[ "$HEAD_SHA" = "$PR_HEAD" ] || { echo 'The reviewed branch changed. Stop and obtain the updated command.'; exit 1; }
node scripts/test_score_ticker.cjs
cd scores-worker
npm ci --no-audit --no-fund
npm test
npx --no-install wrangler deploy --dry-run --outdir /tmp/lineupbeat-scores-build
printf '\nSign in to the Cloudflare account hosting lineupbeat.com.\n'
npx --no-install wrangler login
# Create the worker and its private cache before installing the secret.
npx --no-install wrangler deploy
printf '\nPaste your TheRundown key (hidden), then press Enter: ' >/dev/tty
IFS= read -r -s THERUNDOWN_API_KEY </dev/tty
printf '\n' >/dev/tty
export THERUNDOWN_API_KEY
trap 'unset THERUNDOWN_API_KEY' EXIT
[ -n "$THERUNDOWN_API_KEY" ] || { echo 'No key supplied.'; exit 1; }
printf '%s' "$THERUNDOWN_API_KEY" | npx --no-install wrangler secret put THERUNDOWN_API_KEY
unset THERUNDOWN_API_KEY
cd ..
node scripts/verify_rundown_scores.mjs
# The homepage switches only after both production endpoints pass.
if [ "$(gh pr view "$BRANCH" --repo "$REPO" --json isDraft --jq .isDraft)" = true ]; then
  gh pr ready "$BRANCH" --repo "$REPO"
fi
gh pr merge "$BRANCH" --repo "$REPO" --squash --match-head-commit "$HEAD_SHA"
MERGED_SHA=$(gh pr view "$BRANCH" --repo "$REPO" --json mergeCommit --jq .mergeCommit.oid)
[ -n "$MERGED_SHA" ] && [ "$MERGED_SHA" != null ] || { echo 'Merge is pending; homepage deployment has not started.'; exit 1; }
STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
gh workflow run refresh.yml --repo "$REPO" --ref main -f skip_fetch=true
RUN_ID=''
for attempt in 1 2 3 4 5 6; do
  RUN_ID=$(gh run list --repo "$REPO" --workflow refresh.yml --event workflow_dispatch --branch main --limit 10 --json databaseId,headSha,createdAt | node -e 'let x="";process.stdin.on("data",c=>x+=c);process.stdin.on("end",()=>{const r=JSON.parse(x).find(r=>r.headSha===process.argv[1]&&r.createdAt>=process.argv[2]);if(r)process.stdout.write(String(r.databaseId));});' "$MERGED_SHA" "$STARTED")
  [ -n "$RUN_ID" ] && break
  sleep 5
done
[ -n "$RUN_ID" ] || { echo 'Could not identify the production build. Check GitHub Actions before retrying.'; exit 1; }
gh run watch "$RUN_ID" --repo "$REPO" --exit-status
node scripts/verify_rundown_scores.mjs --homepage
printf '\nVerified: homepage uses TheRundown for NFL and college scores.\n'
