# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Two things that share one SQLite database and one deploy.

**`beatwire/`** — a sport-agnostic pipeline that turns local beat reporting into
structured, attributed, deduplicated player nuggets:
`ingest → prefilter → extract (LLM) → resolve → merge → export`.

**`scripts/build_*.py` + `site/`** — LineupBeat, a static fantasy football site.
The wire feeds it, but most pages are built from a hand-maintained spreadsheet
(`data/projections.xlsx`) and imported public stats.

`README.md` explains the pipeline's design decisions, `NOTES.md` the site's
history and open items, `LAUNCH.md` the ordered runbook. Read `NOTES.md` before
changing anything under `scripts/`.

## Commands

```bash
pip install -r requirements.txt
```

Gates — all three run in CI, run them before shipping:

```bash
python3 scripts/test_resolve.py                # 25 resolver regressions
python3 scripts/build_rankings.py --dry-run    # rankings gates, writes nothing
python3 -m beatwire.cli doctor    --sport nfl  # registry team codes vs roster team codes
python3 -m beatwire.cli preflight --sport nfl  # GO / NO-GO on the silent failure modes
```

There is no pytest and no runner. Tests are plain scripts that exit non-zero:
`scripts/test_resolve.py` (resolver) and `scripts/test_engine.py` (the unused
projection engine, 71 assertions). Run one by running the file.

Pipeline:

```bash
export ANTHROPIC_API_KEY=sk-...
python3 -m beatwire.cli run    --sport nfl
python3 -m beatwire.cli run    --sport nfl --only nyj      # substring match on source id
python3 -m beatwire.cli run    --sport nfl --offline --stub # fixtures + keyword extractor, no spend
python3 -m beatwire.cli export --sports nfl --out site/data/feed.json --site site/index.html
python3 -m beatwire.cli verify --sport nfl --fix           # disable dead feeds in the yaml
python3 -m beatwire.cli unresolved --sport nfl             # the work queue
python3 -m beatwire.cli spend
```

Site build — **order matters** and is encoded in `.github/workflows/refresh.yml`;
mirror that order locally:

```bash
python3 scripts/build_projections.py data/projections.xlsx  # board first
python3 scripts/build_pages.py --base https://lineupbeat.com
python3 scripts/build_sos.py
python3 scripts/build_coaching.py
python3 scripts/build_draft_value.py
python3 scripts/build_college_projections.py
python3 scripts/build_404.py
python3 scripts/build_pages.py --base https://lineupbeat.com  # again, last
```

`build_pages.py` runs twice on purpose: the boards only link to `/nfl/<slug>/`
where that directory already exists, and the sitemap it writes must come after
every other page. `build_pages.py --dry-run` reports without writing.

Routine projections update (the only recurring manual task, from `NOTES.md`):

```bash
cp NEW_SHEET.xlsx data/projections.xlsx
python3 scripts/build_projections.py data/projections.xlsx
python3 scripts/build_draft_value.py
python3 scripts/build_pages.py
```

## Architecture

### The pipeline (`beatwire/`)

`pipeline.py` contains **zero sport-specific logic** — that is the design test.
Adding a sport is three files and no code:
`sources/<sport>.yaml` (a `profile` block + `sources` list), `rosters/<sport>.csv`,
and optionally `fixtures/<source_id>.json`.

- `registry.py` loads the yaml + roster into `Registry(sport)`.
- `ingest.py` — `ADAPTERS` keyed by `Source.kind`: `rss`, `podcast`, `bluesky`,
  `fixture`. `kind: x` dispatches out to `tapi.py`, or `sorsa.py` when
  `BEATWIRE_X_PROVIDER=sorsa` (unsetting that variable is the whole rollback).
  Self-replies are stitched into threads; replies to others are dropped.
- `extract.py` — `mentions_any_player()` prefilters for free before any model
  call; `SYSTEM` is the prompt and is where quality tuning happens. Model is
  `claude-haiku-4-5`, overridable with `BEATWIRE_MODEL`. The skill-position
  filter applies to **both** items and the nuggets they produce.
- `resolve.py` — deterministic, team-scoped, with `position_hint` for baseball.
  An ambiguous mention resolves to nothing rather than guessing. This is the
  only component whose failures are invisible, hence the regression file.
- `local_model.py` — optional ollama path (`BEATWIRE_LOCAL`). It asks for
  verbatim spans, never names; the resolver still does the identifying.
- `store.py` — SQLite. `seen_items` is what stops re-paying the model for an
  article already processed. Nuggets merge on `dedupe_key` and accumulate
  attributions rather than stacking.

Unresolved nuggets are **stored and published** (marked `unmatched`), never
dropped — a dropped nugget is indistinguishable from a quiet news day.

### The site (`scripts/`, `site/`)

`site/template.html` is the design. `export` inlines the feed into it and writes
the self-contained `site/index.html`. The `build_*.py` scripts write crawlable
static pages next to it; `scripts/seo.py` is the shared layer (`site_nav`,
`social_meta`, `breadcrumbs`, `faq_html`/`faq_schema`, `itemlist_schema`,
`byline_html`, `related_html`, `check_page`) so every page carries the same
structures. New pages should go through it rather than hand-rolling meta.

Page → source of truth:

| Page | Built from | By |
|---|---|---|
| `/nfl/rankings/` + `/qb|rb|wr|te/` | `data/projections.xlsx` + `data/nfl_rankings_config.json` | `build_rankings.py` |
| `/nfl/projections/` | `data/projections.xlsx` | `build_projections.py` |
| `/nfl/draft-value/` | that workbook + roster ADP | `build_draft_value.py` |
| `/nfl/strength-of-schedule/` | `games` + `weekly_stats` | `schedule_strength.py` → `build_sos.py` |
| `/nfl/coaching/` | `data/coaching.csv` | `build_coaching.py` |
| `/nfl/durability/` | `weekly_status` + `injuries` | `build_pages.py` |
| `/nfl/<slug>/`, team pages, hub, sitemap | `beatwire.db` + the built boards | `build_pages.py` |
| `/college-fantasy-football/projections/` | frozen release under `data/college/` | `build_college_projections.py` |

The college builder verifies the release manifest against a pinned SHA and is
the one build step in CI deliberately **without** `|| true` — the active version
is `data/college/config.json`.

`scripts/import_*.py` populate the stats tables (`games`, `weekly_stats`,
`weekly_status`, `injuries`, `ol_team_season`, `rb_ngs_weekly`, `rb_pbp_season`).

### The engine, which nothing uses

`scripts/engine.py`, `evidence.py`, `freeze_release.py`, `project3/4/5.py` are a
complete, tested projection engine with a frozen release under `releases/`. It
is **not wired into the live site** — projections come from the spreadsheet.
`evidence.py` is frozen: no architectural changes without a real correctness bug.
Do not assume a `scripts/*.py` file is live; check whether the workflow calls it.

## Standing rules

Each exists because breaking it caused a real problem (see `NOTES.md`).

- **Eastern time for every displayed date.** UTC rolls over at 8pm ET, so an
  evening build stamped tomorrow's date on today's data. Every builder has its
  own `eastern_now()`.
- **Proprietary data must be in the initial HTML.** Boards render their default
  view at build time; JavaScript replaces the table rather than creating it. A
  client-rendered board is invisible to crawlers.
- **Never double-count a signal.** Coaching multipliers live in
  `data/coaching.csv` and are deliberately never applied — if coaching already
  moved a projection, applying it again on draft value counts it twice.
- **One number, read twice.** Durability and draft value read the same ADP; the
  projections page and the player-page chips read the same board. Do not add a
  second source for a number that already exists.
- **`site/nfl/`, `site/index.html`, `site/data/`, `site/404.html` and
  `beatwire.db` are gitignored.** CI rebuilds them. A page built on a laptop
  does not exist in production.
- **Two kinds of editorial decision, and they must not be confused.** A
  manual adjustment is a number and moves `ranking_score`; an editorial order
  constraint moves rank only and touches no number. Both live in
  `data/nfl_rankings_config.json` with a published reason. A player passed by
  an override is never marked down for it, and an override never displays a
  fabricated figure. Cycles are detected before sorting; on a cycle the build
  stops and the previous board stands.
- **The board is reconciled against its source, never a constant.** The
  player count is counted from the artifact and published in the JSON's
  `metadata` alongside both input SHA-256s. Adding a player, or changing an
  adjustment, must not require a code change.
- **`data/nfl_rankings_2026.json` is `{metadata, players}`, the whole board.** The top 200
  carry `overall_rank`; everyone else carries null and is ranked only at his
  position. The overall page lists 200, a position page lists its full pool.
- **Say `player`, not `man`.**
- **No promotional language** — "must draft", "league winner" and friends are
  out. The numbers do the work.

## CI and deploy

`.github/workflows/refresh.yml` is the whole production path. It has no GitHub
schedule — cron-job.org triggers it via `workflow_dispatch` (both would mean two
runs and double the API spend). `skip_fetch: true` rebuilds pages from the
existing database without spending anything.

**Do not replace `refresh.yml` wholesale — patch it in place.** A bundled copy
overwrote it twice and silently dropped `TWITTERAPI_IO_KEY`; every source then
failed in a way that looked like a quiet news day.

`beatwire.db` lives in the Actions cache, not the repo, keyed to restore from
the most recent run. Losing it costs real money. Runs use
`concurrency: cancel-in-progress` because an overlapping run restores a database
from before the run ahead of it and re-pays for the same articles.

Deploy is **Cloudflare Pages** via wrangler (project `lineupbeat`).
`netlify.toml` is left over from the previous host and is not the deploy path.

## Environment variables

`ANTHROPIC_API_KEY` (extraction), `TWITTERAPI_IO_KEY` / `SORSA_API_KEY` +
`BEATWIRE_X_PROVIDER` (X reads), `BEATWIRE_MODEL`, `BEATWIRE_SKILL_ONLY`,
`BEATWIRE_LOCAL` / `BEATWIRE_LOCAL_MODEL` / `BEATWIRE_LOCAL_TIMEOUT` (ollama),
`CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` (deploy). A missing key means
the run quietly produces nothing rather than erroring — `preflight` checks for
exactly this.
