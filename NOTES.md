# LineupBeat, working notes

For whoever picks this up next, including me in a new session. The code
explains what it does; this explains why, what is unfinished, and which
mistakes are already paid for.

---

## What the site is

A fantasy football site with two halves.

**The wire** reads roughly 174 local NFL beat writers, extracts one claim
per player per item, and shows them newest first. Every claim is
paraphrased and linked back to the reporter. Skill positions only.

**The data pages** are proprietary boards built from a spreadsheet and
public stats:

| Page | Source | Rebuilt by |
|---|---|---|
| `/nfl/projections/` | `data/projections.xlsx` | `build_projections.py` |
| `/nfl/draft-value/` | that workbook + roster ADP | `build_draft_value.py` |
| `/nfl/strength-of-schedule/` | `games` + `weekly_stats` | `schedule_strength.py` then `build_sos.py` |
| `/nfl/coaching/` | `data/coaching.csv` | `build_coaching.py` |
| `/nfl/durability/` | `weekly_status` + `injuries` | `build_pages.py` |

Player pages, team pages, the data hub and the sitemap all come from
`build_pages.py`.

---

## Standing rules

These are not preferences. Each one exists because breaking it caused a
real problem.

**Eastern time for every displayed date.** UTC rolls over at 8pm Eastern,
so an evening build stamped pages with tomorrow's date while showing
today's data. Every builder has an `eastern_now()`.

**Proprietary data belongs in the initial HTML.** The tables were rendered
client-side, so a crawler read a page about 614 players containing none of
them. Draft value, projections and strength of schedule now write their
default view at build time; JavaScript replaces the table rather than
creating it. Any new board must do the same.

**Never double-count a signal.** Coaching, durability, schedule and
projections are separate pages answering separate questions. If coaching
already moved a projection, applying a coaching multiplier again on the
draft value page counts it twice. The coaching multipliers are stored in
`data/coaching.csv` and deliberately never applied.

**`site/nfl/` is gitignored.** Pages built locally never reach production;
CI rebuilds them and Netlify deploys from there. A page that only exists on
a laptop does not exist.

**Say `player`, not `man`.** Fantasy football has women playing it.

**No promotional language.** "Must draft", "can't miss", "league winner"
are all out. The numbers do the work.

---

## Bugs that were expensive to find

Worth reading before assuming something similar is working.

**The timeline fetcher walked backwards through history.** `last_tweets`
returns newest-first with a `next_cursor` meaning *the page before this
one*. Storing it and sending it back resumed each poll a page deeper into
the past, forever. The oldest post collected was February 2018, and the
model was paid to read all of it: roughly 2,500 items an hour against a
real publishing rate near a thousand a day across every source. Fixed by
taking page one every time; `seen_items` already knows where we got to,
which is what a cursor was supposed to do. See `tapi.py`.

**The skill filter only filtered items, not nuggets.** An item mentioning a
receiver passed, and the model dutifully produced a claim about the
linebacker in the same paragraph. Over half the wire was linemen and
defenders. Both halves are filtered now: `extract.py`.

**The X API key was missing from CI three times.** Every source failed with
`TWITTERAPI_IO_KEY not set`, the run completed, and it looked like a quiet
day rather than a missing credential. Twice it was lost because a bundled
`refresh.yml` overwrote it. **Do not ship that file wholesale**; patch it in
place.

**Projections were read from `run_projections`.** That table only exists
where the engine has been run, so CI had no projections while local builds
worked. Both the export and the page builders now read
`data/projections.xlsx`.

**Position came from the roster rather than the projection sheet.** A
quarterback ranked "LB1" with an empty stat line because the roster had him
miscoded. The sheet's own tab is what the projection is for.

**Name suffixes differ between sources.** The board says "Luther Burden
III", the roster says "Luther Burden". Slugs are stripped of `jr|sr|ii|iii|iv|v`
on both sides, and where a resolved slug already exists it is reused rather
than derived twice.

---

## The engine, and why nothing uses it

`scripts/engine.py`, `evidence.py`, `freeze_release.py` and friends are a
complete projection engine with a frozen release (`engine-1.0`), an
evidence layer with 139 passing assertions, and a shadow-candidate system
that cannot publish.

**It is not wired into the live site.** Projections come from a hand-built
spreadsheet instead, which was a deliberate call: the engine was taking far
longer than shipping pages, and a working spreadsheet ships today.

The engine is correct and tested. If projections ever need to update
themselves from evidence rather than from a new workbook, it is there.
`scripts/test_engine.py` (71 assertions) and `scripts/test_evidence.py`
(139 assertions across 21 groups) both pass.

Note `evidence.py` is **frozen**: no architectural changes without a real
correctness bug found through source integration.

---

## Phase 2, the mobile rebuild

**Mobile is the primary layout, not a compressed desktop.** Design at 390px
first, then expand. Most readers are on a phone.

Six groups. Screenshots at 390, 430, 768 and 1366 for each one before
moving to the next. Shared components go in `seo.py` from the start: the
nav, social metadata and breadcrumbs were each duplicated across builders
before being consolidated, and anything new will drift the same way if it
is not shared from the beginning.

| # | Group | State |
|---|---|---|
| 1 | Mobile header and menu | done, `478161d` |
| 2 | NFL and college projection tables | |
| 3 | Draft Value mobile cards | |
| 4 | Durability and other data tables | |
| 5 | Team and player pages | |
| 6 | Homepage, Data hub and About | |

### Group 2 requirements

- Tables in a horizontal scroll container
- Rank and Player sticky left, if that can be made reliable
- Table text no smaller than 13px
- A swipe hint or a right-edge fade, so a reader knows there is more
- Numbers never wrap
- The Points column prominent
- The hybrid rushing columns are never hidden: they explain the rankings

**Mobile column order puts Points immediately after Team.** A reader should
not have to scroll the full width to reach the number the ranking is based
on.

```
QB       Rank, Player, Team, Points, Pass Yards, Pass TD, Carries,
         Rush Yards, Rush TD, Pass Attempts, Completions, INT
RB       Rank, Player, Team, Points, Carries, Rush Yards, Rush TD,
         Receptions, Receiving Yards, Receiving TD
WR, TE   Rank, Player, Team, Points, Receptions, Receiving Yards,
         Receiving TD, Carries, Rush Yards, Rush TD
```

---

## Open items

**CI passes almost nothing through the prefilter.** Local runs get ~200 new
items with ~48 reaching the model; CI gets 16 new with zero. Same code,
same hour. Suspect CI's roster state. A temporary roster-check step was
added to the workflow to compare position counts — read its output.

**`build_projections.py` runs before `build_pages.py` in CI**, so the
board's player links come out empty in production ("0 of 614 link to a
player page") while working locally, because local runs already have the
directories from a previous build. Move it after, then rebuild pages so the
projection chips pick up the board.

**Adjusted fantasy points allowed** is the next methodology upgrade for
strength of schedule. Raw points allowed flatters defences that faced weak
offences. The intended metric is points allowed above or below opponent
expectation, game by game. Deliberately not rushed: bad data on a page
people trust is worse than a simpler honest number.

**Custom week ranges** on strength of schedule, so a reader can pick weeks
5–10 rather than only the three preset windows.

**Half PPR and Standard ADP.** Draft value is PPR only because the roster
holds one ADP column. Adding `adp_half` and `adp_std` columns switches the
other formats on automatically; the page reads `ADP_COLUMNS` rather than
assuming.

**29 sources produced nothing** in a fresh database after the skill filter
went on. Worth re-running that count after a week of real coverage before
cutting any of them: a writer who happened to post nothing about a skill
player one afternoon looks identical to one who never does.

---

## Updating projections

The only routine task.

```
cp NEW_SHEET.xlsx data/projections.xlsx
python3 scripts/build_projections.py data/projections.xlsx
python3 scripts/build_draft_value.py
python3 scripts/build_pages.py
git add -A && git commit -m "Projections update" && git push
```

Order matters: `build_pages.py` reads the built projections page for the
player-page chips, and draft value reads the workbook.

The sheet needs one tab per position (QB/RB/WR/TE) with columns for Rank,
Player, Team, the stat line, and PPR / Half PPR / Non-PPR. Column names are
matched loosely, so minor variations are fine. The builder reports anything
odd — rank gaps, missing formats, a player scoring less in PPR than
standard — without refusing to build.

---

## Things that are true and easy to forget

The wire finds news **before the roster does**. A player who signs today is
unresolvable until Sleeper catches up, which is why the roster imports
before the pipeline runs.

**Moving Now** shows players with two or more reports today, which is a
different question from the wire beneath it. One mention is news; two is a
developing story.

**Durability and draft value must read the same ADP.** One number read
twice, not two numbers that happen to agree.

**The projections page and the player-page chips read the same board**, so
they cannot disagree. Do not add a second source for the same number.

Cost is per item reaching the model, currently Haiku. Real volume is a few
hundred extractions a day. If a bill looks like thousands, something is
re-reading items rather than the model being expensive.
