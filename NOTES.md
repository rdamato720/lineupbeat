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

**Sorsa went dark, all of it at once.** It is wired up as a second X
provider (`beatwire/sorsa.py`, `BEATWIRE_X_PROVIDER=sorsa`) and it is
genuinely cheaper: a flat rate per request against twitterapi's per-tweet
billing, roughly a sixth of the cost for the same 109 handles. It is still
not the primary, and the price is not the reason. It had a full outage —
not thin coverage on some handles, every source dark at once — and the wire
went silent until we switched back. A provider whose failure mode is total
is a hedge, not a primary, and the arithmetic that says otherwise is only
counting the good days. The two `sorsa` rows in `api_spend` dated
2026-08-12 are the tail of that switch-back, not evidence it works.

Keep it wired and keep it working, so the variable is there when
twitterapi is the one having the bad day. Do not promote it on cost alone.

**Thread continuations never arrived.** `last_tweets` takes an
`includeReplies` parameter, it defaults to false, and the fetch never sent
it. So every comment in this codebase about beat writers filing practice
notes as threads -- the reason `stitch_threads` exists at all -- described
something that had never once happened on the X path. Measured on three
handles: zero self-replies without the flag; with it, the continuations
appear. It costs nothing, because the page is twenty posts either way and
the bill is per post -- the flag changes which twenty. Both the search path
and the timeline fallback ask for replies now.

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

## The X read path

109 handles polled every two hours is the entire API bill, so it is worth
knowing what one poll does.

**A poll asks for what is new, not for the last twenty posts.**
`last_tweets` returns the twenty most recent posts and bills for all twenty,
every time, with no parameter to ask for fewer or newer -- the documented
options are `userId`, `userName`, `cursor` and `includeReplies`, and that is
all. So twelve polls a day paid for the same twenty posts twelve times to
collect the two that were new. `advanced_search` takes
`from:<handle> since_time:<unix>` and bills for what it returns, with a floor
of one tweet's worth per request. Measured against the account balance over a
real day on one handle: 3,600 credits the old way, 465 the new one. Across
109 handles that is about $118/month against $15.

Coverage was checked before the switch, because a cheaper call that quietly
misses reporting is not cheaper. Over the same window search missed nothing
the timeline had, returned two posts it did not, carried every field
`parse_timeline` reads including `extendedEntities` for the video section,
and returned self-replies that `stitch_threads` reassembled into real
threads.

**The stored timestamp is not the 2018 cursor.** Anyone who has read the
walked-backwards entry above will flinch at the word, and should: that was a
pagination token meaning *the page before this one*, and persisting it
marched into the past forever. What is stored now is the time of the newest
post actually received. It only moves forward, and it answers "what is new
since I last looked", which is the question a poll is asking.

**An empty answer never moves the mark.** Search returned an empty page once
for a writer who had posted an hour earlier -- transient, corrected on the
next call. Had the mark been set to the clock rather than to a real post,
that window would have been skipped permanently and those posts never seen.
So the mark advances only from a post in hand, and an empty response leaves
it exactly where it was.

**Silence past a day buys a full timeline read.** The cheap call cannot
prove a negative: "nothing new" and "this source has fallen out of the
index" look identical. A source that has returned nothing for
`RECONCILE_AFTER_HOURS` gets one ordinary timeline read to settle it. It
costs more, which is the point -- it is what stops a search-side problem
from looking like a quiet news week.

**`BEATWIRE_TAPI_MODE=timeline` is the rollback**, one variable, every
source back on the old path.

**Three handles have stopped posting.** An audit of all 109 in August 2026
found `@Jeff_Legwold` silent since September 2024, `@Andrew_Krammer` since
January 2025, and `@adamteicher` for forty days; `@JCAllenNFL` and
`@Demetrius82` post one to three times a month. They are still enabled. Under
the old fetch they cost full price for a page of years-old posts that the age
filter discarded every time; under search they cost the floor, so this is now
a coverage question rather than a billing one -- Denver, Minnesota and Kansas
City may be short a live beat writer.

Two things that audit taught, worth keeping: reading the credit balance
(`GET /oapi/my/info`) is itself free, which makes it the honest way to price
a call; and the balance settles a few seconds after the request, so reading
it immediately reports zero and makes a paid call look free.

---

## Rankings

Five URLs -- `/nfl/rankings/` and `/qb`, `/rb`, `/wr`, `/te` -- from one
component in `scripts/build_rankings.py`. There is no second template and no
hand-kept HTML: the position pages are the same render with a filter, so the
board cannot drift between them.

**Projections are the source, not a second spreadsheet.** A rankings workbook
of its own would be a second copy of a number the site already publishes,
free to disagree with the projections page. So the builder reads
`data/projections.xlsx` and applies the published formula. `--export` takes
the rankings workbook itself -- `.xlsx` (it reads `Source Data`, falling back
to `Site Export`), `.csv` or `.json` -- through exactly the same validation.

**The formula.** Replacement level is the 13th QB, 37th RB, 49th WR and 13th
TE by projected points -- what a 12-team league starting 1/2/2/1 and two flex
actually leaves on the board. VORP is projected Half-PPR points minus that,
ranking score is VORP plus any editorial adjustment, and the published Top
200 is the first 200 after sorting.

**The score is compared at one decimal.** The published record stores
`ranking_score` to one decimal and the page prints it, so that is the
precision the sort uses: score, then projected points, then name, each at one
decimal. Coarser comparison was tried and rejected -- rounding to whole
numbers made visibly different scores tie, handed the decision to a tiebreak
the reader cannot see, and amounted to inventing a rule to reproduce an
expected order. The tiebreak is rare by design; it is for a genuine tie at
the published precision, not a second sort.

That precision decides RB8. McCaffrey is 288.1 - 128.1 - 33.0 = 127.0 and
James Cook III is 255.2 - 128.1 = 127.1, so Cook is RB8 and McCaffrey RB9.
Do not close that 0.1 by trimming the adjustment or coarsening the sort.

**Never the workbook's own `Rank` or `Pos Rank`.** Not a theoretical risk:
in `LineupBeat_2026_Half_PPR_Rankings_v1.0.xlsx`, James Cook III is listed
RB9 with a ranking score above the RB7 and RB8 in the same sheet. That field
is not a reliable Half-PPR position rank, and the ranks are recomputed here
for that reason.

**Which is also why the workbook's replacement-point cells are not used.**
It carries 290.6/124.5/110.6/143.3, which were calculated from that same
unreliable Rank field -- against the frozen projections those figures sit at
ranks 13, 39, 48 and 12, not the stated 13/37/49/13. The stated ranks are
authoritative, so the published values are QB13 290.6, RB37 128.1, WR49
109.8, TE13 142.4. Within a position this changes nothing; it moves positions
against each other in the overall list.

**Tiers are rank ranges, not clusters.** The workbook defines them on its
Assumptions sheet: overall 1-8, 9-24, 25-48, 49-72, 73-100, 101-130, 131-165,
166-200; positions 1-3/4-8/9-14/15-24 where one starter is used and
1-6/7-18/19-36/37-60 where two are. That is also why the brief asks for the
first eight overall to be marked -- tier one *is* the top eight. A
gap-clustered scheme was built first and thrown out: it disagreed with the
published methodology, which is the thing the reader is being asked to trust.

**The JSON is the whole board, not the page.** All 615 players, each with a
position rank against the full pool at his position. The top 200 carry
`overall_rank` 1-200 and a tier; everyone else carries null for both. A
position rank that only counted players who had made some other cut would be
a different number wearing the same name.

**The pages follow from that.** Overall lists exactly 200. A position page
lists its entire pool -- 255 wide receivers -- with the first fifty visible
and the rest written into the markup behind `hidden`, cleared by one "Show
more". Not paginated and not lazy-loaded, because a crawler and the search
box both need the whole pool in the HTML. Search ignores the cap entirely: a
player the board projects is findable whether or not he sits past the
fiftieth row.

**An adjustment moves draft order and nothing else.** The projected-points
column stays frozen, and a non-zero adjustment without a published reason
fails the build -- an unexplained thumb on the scale is indistinguishable
from a bug. Two are approved: Jeanty +16.0, McCaffrey -33.0. Neither is in
the workbook, which carries zero in every adjustment cell, so they live in
`ADJUSTMENTS` in the builder and are applied on import.

**The board is reconciled against its source, not against a number in the
code.** There was a `TOTAL_EXPECTED = 615` constant for about an hour and it
was wrong in a specific way: signing a player would have failed the build,
and a gate that fails for a legitimate reason is a gate somebody deletes. So
the count is counted from the source at read time, published in the JSON's
metadata beside the source filename and its SHA-256, and pinned for the
frozen artifact in `data/nfl_rankings_source.json`. A real addition moves the
manifest, the SHA and the count together in one commit and shows in the diff.
A swapped or truncated artifact moves the SHA and fails.

The JSON is `{metadata, players}` for that reason -- the numbers have to
travel with what produced them, or a board and its provenance drift apart.

**Production reads the live projection board.** `data/projections.xlsx` for
the numbers, `data/nfl_rankings_config.json` for everything else -- the
league shape, the replacement ranks, the tier bands and the approved
adjustments. Replacement points, VORP, ranking scores and both sets of ranks
are recalculated from whatever the board says today, so updating the
projections updates the rankings in the same run. The alternative was tried
for one build: pinning the frozen workbook as the production source would
have let the projections page move while the rankings page kept quoting last
week's points for the same player.

The config is a tracked file rather than constants in the builder because an
editorial adjustment is a decision, and a decision should arrive as a data
change with a diff and a reviewer, not as a deploy. Adjustments are keyed
`name|TEAM|POS`: a bare name would follow a player to a new team, and a
reason written about one situation is not automatically true of another. An
adjustment matching nobody fails the build -- an approved decision that
silently did not happen is worse than one that never existed.

**The frozen workbook is provenance, not a feed.**
`data/archive/LineupBeat_2026_Half_PPR_Rankings_v1.0.xlsx` is what the
rankings were first cut from, pinned by `data/nfl_rankings_source.json`.
`--export` rebuilds from it for an audit or a historical board, says so on
the way past, and reconciles it against that manifest. The live board is
deliberately not pinned: it is supposed to move.

**The metadata names both inputs.** `projection_source_file` and its SHA-256,
`ranking_config_file` and its SHA-256, the counted player total and the
published 200. Two inputs decide a ranking, so a board that recorded only one
of them could not be reproduced from what it says about itself.

**Editorial order is separate from editorial adjustment, and they are not
interchangeable.** An adjustment is a number: it moves `ranking_score` and
shows on the page as `+16.0`. An order constraint is a preference: it moves
where a player sits and touches no number at all -- not `projected_points`,
not `replacement_points`, not `vorp`, not `ranking_score`. Both live in
`nfl_rankings_config.json`, both need a published reason, and a player can
carry both. Jeanty does.

**Lift, do not demote.** When a constraint says A ranks above B, A is moved
to sit immediately above B, rather than B being pushed below A. The two give
different boards and it matters: with Chase over Nacua and Jeanty over
Taylor, lifting produces the approved top six -- Gibbs, Robinson, Chase,
Nacua, Jeanty, Taylor -- where demoting would leave two receivers between
Nacua and Jeanty. Everyone unaffected keeps his relative order; the only
players who move are the one being lifted and the ones he passes.

**The board shows ranks, not arguments.** The table carried an ADJUSTMENT
column and a WHY column with the published reason behind a disclosure; both
were removed as a product decision. The columns made every adjusted row two
or three lines tall against one for everybody else, which is a lot of weight
for something that applies to seven players out of six hundred.

What that costs is worth stating, because it will look like a bug to
somebody: Chase now sits above Nacua, and Jeanty above Taylor, with more
projected points on the row below and nothing on the page saying why. The
reasons are still published -- every adjustment and every override carries
one in `nfl_rankings_2026.json`, and a gate still refuses to build without
it -- and the methodology section still explains that adjustments exist. They
are simply no longer on the board itself. If that reads as an error to
readers, the cheapest fix is a marker beside the name rather than the two
columns coming back.

**Nobody is marked down for being passed.** Taylor, Jacobs, Nacua and Irving
carry no adjustment and no override. Being ranked below somebody is not a
downgrade, and printing a negative number against them would invent a
statistic to explain a preference. For the same reason an override shows as
"Editorial ranking decision" and never as a fabricated figure.

**Cycles are checked before anything is sorted.** Two constraints that each
demand the other player is above cannot both be honoured, and the reorder
pass would swap them back and forth until it hit its cap and published
whichever side it stopped on. So the graph is walked first; a cycle is
reported by name -- "Ashton Jeanty -> Jonathan Taylor -> Ashton Jeanty" --
nothing is reordered, and the build stops with the previous board still
published.

**Both boards come off one order.** Position ranks are cut from the final
overall sequence rather than sorted separately, so the overall page and a
position page cannot disagree about two players. There is only one sequence
to read them from.

**The order gate rebuilds the board rather than trusting it.** "Scores must
descend" cannot survive an approved override, and relaxing it to "unless
somebody is overridden" would excuse any reordering at all. So validation
re-sorts every player by the published key, re-applies each override from the
comparison player named in the JSON, and requires the result to equal the
published order exactly. That catches the knock-on moves too: Jeanty passes
two receivers on his way over Taylor and neither is named anywhere.

**The published JSON is the last-known-good board, so writing it is careful
in two ways.** Every CI run starts from a fresh checkout: when a gate fails,
the only rankings that exist anywhere are the ones committed to the
repository. That is why the file is tracked rather than gitignored like the
built pages, and why the build never leaves it half-written -- the new board
goes to a sibling temp file, is read back and re-validated from disk, and
only then replaces the real one in a single atomic move. A failure unlinks
the temp file and the committed board is untouched to the byte.

It also does not rewrite the file for nothing. If both input SHAs match the
ones in the existing metadata and every player record is identical, the board
did not change: the previous `generated_at` is kept and nothing is written.
Otherwise the timestamp moves with the board. Without that, a rebuild every
two hours would put a diff on twelve thousand lines whether or not a single
rank had moved, and the one commit where something actually changed would be
impossible to find.

**The gates stop the build.** Every configured player and comparison player
resolving to exactly one player, both sides of a constraint sharing a
position, no cycle, a published reason behind every adjustment and every
override, no override touching a number, the five approved relative orders,
the approved top of the overall and RB boards, McCaffrey RB9, the overall and
position boards agreeing, published count and unique-player count both equal
to the source count, exactly 200 non-null
overall ranks sequential from 1, null overall rank and tier for everyone
else, every player position-ranked, position ranks sequential and unique
within each position, both orderings matching the one-decimal sort key, no
blank name or team or position, nothing outside QB/RB/WR/TE, no missing or
negative points, nobody twice, no market label while ADP is null, Jeanty RB4
and McCaffrey RB9. That last pair is a test of the sort, never an input to
it, and it has already earned its keep twice.

**The two sources agree exactly.** `data/projections.xlsx` and the rankings
workbook's `Source Data` sheet carry the same 615 players with the same
Half-PPR points, checked player by player. So the default derivation is not
an approximation of the workbook, it is the same board, and `--export` is
there for when that stops being true.

**ADP is deliberately absent.** `adp` and `value_label` are null in every
record and a gate rejects a label without an ADP behind it. Market-value
labels wait for a verified ADP source.

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
| 2 | NFL and college projection tables | done |
| 3 | Draft Value mobile cards | done |
| 4 | Durability and other data tables | done |
| 5 | Team and player pages | done |
| 6 | Homepage, Data hub and About | done |

Phase 2 is complete. What it left behind, for the next person:
`seo.py` now owns the header (`site_nav`, `NAV_CSS`, `NAV_JS`), the wide
table treatments (`SCROLLTABLE_CSS`, `scroll_hint`, `CARDTABLE_CSS`) and
the projection stat columns (`STAT_COLUMNS`). Put anything new beside
them rather than in a builder.

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

## One design system

**The homepage is the reference.** It was rebuilt to a supplied design and
the eight data pages were not, so the site read as two sites sharing a
logo. Measured at 1366px before this was fixed:

| | homepage | data pages |
|---|---|---|
| heading | serif 61px / 400 | serif 27px / 700, and Barlow 34px / 600 on durability |
| control | 8px corners, Barlow 18px / 700 | 999px pills at 12px, and serif on one page |
| ink | `#F2F1EC` | `#E4E7E2` |
| label grey | `#9BA09C` | `#8C9691` |

Colours are settled in the template's `:root`, which every builder reads,
so the homepage's palette reaches every page automatically. `--muted` was
added for the lede grey the homepage used and the token set had no name
for. Headings and controls are settled in `seo.UI_CSS`, which names the
classes the builders already carry rather than requiring eight coordinated
markup edits.

**Anything new that a reader can see goes in `seo.UI_CSS`.** A page-local
heading or button rule is how this happened the first time.

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

**The duplicated stylesheets in `build_pages.py` are mostly cleaned.**
33 byte-identical rules are gone from the durability sheet, and `.dmgrid`
— the one selector where the two copies genuinely disagreed — now has a
single canonical treatment. What is left of the duplication is `PAGE_CSS`,
where the `.proj` chip rules still appear twice. A mechanical merge is not
safe there: collapsing rules by declaration reorders shorthand against
longhand, and it silently moved `.dmgrid div` borders and `.dtab .n`
colour when it was tried. **New rules still go at the bottom of either
string.**

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
