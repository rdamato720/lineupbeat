# beatwire

**Launching? Follow [LAUNCH.md](LAUNCH.md).** It is the ordered runbook.

A sport-agnostic pipeline that turns local beat reporting into structured,
attributed, deduplicated player nuggets.

```
sources/<sport>.yaml ─┐
rosters/<sport>.csv  ─┴─► ingest ─► prefilter ─► extract ─► resolve ─► merge ─► feed
                          (RSS,      (free,       (LLM,      (team-     (cross-   (per
                          podcast)   drops most)  cheap)     scoped)    source)   roster)
```

The design constraint that shaped everything: **adding a sport must be config,
not code.** NFL and MLB ship here and `pipeline.py` has zero sport-specific
branches. Nothing in `beatwire/` knows what a bullpen is.

## Quickstart

```bash
pip install -r requirements.txt

# 1. Real rosters. Public APIs, no keys. --check writes nothing.
python scripts/import_rosters.py nfl mlb --check
python scripts/import_rosters.py nfl mlb

# 2. Resolver must pass before anything else matters
python scripts/test_resolve.py

# 3. Feeds rot constantly. Always verify before trusting a registry.
python -m beatwire.cli verify --sport nfl
python -m beatwire.cli verify --sport mlb

# 4. Run it
export ANTHROPIC_API_KEY=sk-...
python -m beatwire.cli run    --sport nfl
python -m beatwire.cli run    --sport mlb
python -m beatwire.cli export --sports nfl,mlb

# No network, no API key, no cost: fixtures plus a keyword extractor
python -m beatwire.cli run --sport nfl --offline --stub
```

## The four decisions worth arguing about

**1. Bluesky yes, X no.**

The AT Protocol AppView serves public reads with no auth, no key, and no paid
tier, so a Bluesky adapter cannot be repriced out from under you. That is the
entire difference. Add sources with `kind: bluesky` and a `handle`.

It polls each writer's author feed rather than consuming the Jetstream
firehose. The firehose is right for keyword discovery across the network, but
you are watching a known list of accounts, and filtering ~850 MB/day down to a
trickle to find them is strictly worse than asking for them directly.

Free access is necessary, not sufficient. Coverage decides whether it earns
its place:

```bash
python scripts/bluesky_audit.py --names writers.txt --sport nfl
python scripts/bluesky_audit.py --names writers.txt --sport nfl --emit >> sources/nfl.yaml
```

It searches for each writer, scores the match against display name, handle,
and profile description, checks how recently they posted, and prints a verdict
on whether the coverage justifies the integration at all. Confident, recently
active hits get emitted as ready-to-paste config, so the audit doubles as the
registry work. Anything ambiguous is flagged `CHECK` rather than emitted: a
same-named non-reporter scores around 0.5 and stays out, because attributing a
claim to the wrong person is worse than missing it.

**2. There is no X adapter, on purpose.** X moved to pay-per-use in February
2026: $0.005 per post read, hard-capped at 2M reads a month, no free tier,
full-archive search behind an Enterprise contract. Polling a few hundred beat
writers in near real time is a metered cost on a platform that has repriced
this exact use case repeatedly. Everything here is RSS and podcast RSS, which
is free and stable. If you later want X, add an adapter to `ingest.ADAPTERS`
and treat it as one source among many rather than the foundation.

**3. Threads are stitched, not dropped.**

Beat writers file practice reports as multi-post threads. The first post is
usually a header ("Practice notes, Wednesday") and the reporting lives in
posts two through six. Both platforms default to excluding replies, which
throws away everything except the part with no content in it. On a real
four-post thread that is the difference between zero nuggets and three:

```
replies excluded   1 item -> 0 nuggets
thread stitched    1 item -> 3 nuggets   (Hall, Allen, Wilson)
```

So self-replies are kept and reassembled in order, while replies to other
people stay dropped as conversation rather than reporting. Reassembly matters
as much as retention: a lone post reading "he was limited again" has no
antecedent and is useless to the resolver, but reads normally inside its
thread. Numbering like "2/4" is stripped as noise.

Note the cost implication on X: keeping self-replies means fetching more
posts, so a thread of six bills six reads rather than one. Watch it in
`spend`, but the alternative is paying for headers and discarding the news.

**4. The prefilter runs before the model.** `mentions_any_player()` is pure
string matching against the relevant roster slice and costs nothing. On a real
beat feed it discards most items (recaps, columns, ticket promos) before you
spend anything. Watch the "skipped" number in the run report; it is your gross
margin.

**5. Team-scoped entity resolution, plus position hints for baseball.** A beat writer says "Allen," not "Braelon
Allen of the New York Jets." Because a single-team source carries a team hint,
the resolver collapses ambiguity that is otherwise miserable league-wide:

```
'Allen' + team=NYJ  -> Braelon Allen (NYJ RB)  conf=0.97
'Allen' + team=BUF  -> Josh Allen    (BUF QB)  conf=0.97
'Allen' + team=JAX  -> Josh Allen    (JAX LB)  conf=0.97
'Allen' + no team   -> UNRESOLVED    (best=0.36)
```

That last line is the important one. **An unresolved mention is dropped, not
guessed.** A nugget filed under the wrong player costs a user a lineup
decision and costs you the account. In production, route these to a review
queue rather than to `/dev/null`.

Team scoping alone is not enough for MLB. Rosters are bigger and surnames
repeat within a single clubhouse, so the extractor can also return a
`position_hint` drawn from context, which the resolver weighs against the
`position_groups` in the sport profile:

```
'Naylor' + ATL           -> UNRESOLVED     (two of them, correctly refuses)
'Naylor' + ATL + pos=C   -> Bo Naylor
'Naylor' + ATL + pos=IF  -> Josh Naylor
'Naylor' + ATL + pos=P   -> UNRESOLVED     (neither pitches)
```

`scripts/test_resolve.py` locks all of this down. Run it before every deploy.
The resolver is the only component whose failures are invisible: a dead feed
looks like a quiet news day and a weak extraction looks like a boring nugget,
but a misresolved player looks completely normal and is silently wrong.

**6. Unresolved is published, not discarded.**

An earlier version dropped any mention the resolver could not place. That was
wrong, and in a way that hid itself: a dropped nugget is indistinguishable
from a quiet news day, so the feed silently develops holes nobody can see.

Now every nugget is stored with its raw `mention` preserved. Resolved ones get
a player id and participate in roster filtering. Unresolved ones still appear
in the team feed, marked `unmatched` and unlinked. The count becomes a health
metric:

```bash
python -m beatwire.cli unresolved --sport nfl
```

Treat it as a work queue. Top entries are usually a missing alias or a player
signed since your last roster import. Watch the rate, not just the list.

This change paid for itself the hour it was made: the queue immediately
surfaced "CeeDee Lamb" and "DeVonta Smith" as unresolved even though both were
on the roster. A player name and one of its aliases normalized to the same
string, which put the same player in the candidate list twice and tripped the
ambiguity check with a gap of zero. Each was failing to resolve because he
matched himself. Under the old behaviour those nuggets would have silently
vanished and the bug would have lived until someone noticed a star receiver
never appearing in his own feed.

**7. Merging, not stacking.** When five writers report the same thing, the
store merges them into one nugget carrying five attributions. Corroboration
count is persisted rather than discarded because it is the raw material for
reporter accuracy scoring later.

## The site

```bash
python -m beatwire.cli export --sports nfl,nhl --out site/data/feed.json
open site/index.html
```

`site/template.html` is the design. `export` inlines the data bundle into it
and writes `site/index.html`, which is fully self-contained: no server, no
fetch, no CORS, no loading state. Drop it on Netlify the same way you deploy
the league site and let the scheduled Action rebuild it.

The design is built around one idea: **beat information expires, at very
different rates.** A posted lineup is worthless after first pitch. A
season-ending injury note stays relevant for weeks. So each
nugget carries a decay rail keyed to its own category window (`LIFE` and
`LIFE_OVERRIDE` in the template), and the card only raises its voice, turning
red and printing "stale in 3h", once it drops below a third of its useful life.
A rail that reads 95% on every card is decoration; this one is quiet until it
has something to say.

Filter state lives in the URL hash rather than local storage, so a filtered
view ("my roster, lineup-changing only") is a link you can send to someone.

Fixtures accept relative timestamps (`"-9h"`, `"-3d"`) so decay behaviour is
reproducible instead of drifting stale as the demo data ages.

## Gates

Three commands stand between you and shipping something broken. All three run
in CI.

```bash
python scripts/test_resolve.py                 # 24 resolver regressions
python -m beatwire.cli doctor    --sport nfl   # team codes line up
python -m beatwire.cli preflight --sport nfl   # GO / NO-GO
```

`preflight` checks the things that fail silently in production: a sample
roster still in place, drifted team codes, enabled sources still pointing at
TODO urls, a missing API key meaning you are quietly still on `--stub`, a
rising unresolved rate, and a feed whose newest item is two days old.

## Accuracy audit

The 200-nugget check decides whether this works, and it is the step most
likely to get skipped under time pressure. So it is built to take 30 minutes:

```bash
python scripts/audit.py --sport nfl --n 200
open audit/review.html      # 1 correct, 2 wrong player, 3 wrong category, 4 not news
python scripts/audit.py --score audit/results.json
```

Sampling is stratified across team and category so you are not grading 200
items from whichever team had a loud week. The scorer exits non-zero below 97%
resolution accuracy and tells you where failures cluster, which is the part
that saves time: one team dominating means a roster problem, spread across
teams means a prompt problem, and those have completely different fixes.

## The source registry

```bash
python scripts/build_registry.py nfl mlb     # all 62 teams
python -m beatwire.cli doctor --sport nfl    # team codes must line up
python -m beatwire.cli verify --sport nfl --fix
```

`build_registry.py` holds the team tables and generates from two URL patterns
I confirmed: SB Nation's `/rss/index.xml` and MLB.com's
`/feeds/news/rss.xml`. That is 32 NFL sources and 60 MLB sources. It preserves
the hand-written `profile` block and replaces only the `sources` list.

**These URLs are pattern-derived, not individually checked.** I could not
reach the hosts to verify them, and one spot-check attempt was refused by
robots.txt, which is itself worth knowing: offering an RSS feed is an
invitation to poll it, but a disallow rule means you are polling someone who
has expressed a preference. Stay conservative on frequency, always attribute,
always link back, and expect to need a relationship with these outlets rather
than only a scraper.

**Do not let SB Nation become a single point of failure.** It is 62 of the 92
generated sources, it is one company, and Vox Media has been reported to be
exploring a sale of it. That is why every team also gets a disabled `-local`
slot.

### Meta Threads (adapter written, deliberately not enabled)

The Profile Discovery API, `GET /profile_posts?username=...`, does technically
read arbitrary public profiles, and the API is free. The blocker is the stated
permitted use:

> "You may use the Threads API to enable people to create and publish content
> on a person's behalf on Threads, and to display those posts within your app
> **solely to the person who created it**."

That describes a publishing client. Reading other people's posts and showing
them to your subscribers is the opposite. The rate limit design agrees:
`calls = 4800 * impressions` on *your own* account, floored at 10, so a
headless aggregator sits at the floor forever.

There is genuine tension between that sentence and the existence of Profile
Discovery and Keyword Search. App Review is where Meta resolves it, which
makes approval a coin flip on use-case grounds rather than a formality. Do not
put it on the critical path.

The remaining constraints, if you pursue it anyway:

- `threads_profile_discovery` permission. With **standard access you only get
  Meta's own accounts** (@meta, @threads, @instagram, @facebook), so App
  Review and business verification are unavoidable.
- **1,000 requests per rolling 24 hours**, shared across every writer.
- Public profiles with 100+ followers only, which beat writers clear easily.

The quota is the real design constraint, not cost:

```
32 writers -> 31 polls each -> one every ~46 min
66 writers -> 15 polls each -> one every ~96 min
```

Fine for Wednesday practice reports. Useless for the Sunday inactives window,
where 96 minute granularity misses the event. Budget the quota toward high
value windows rather than spreading it evenly, and keep X or Bluesky for
anything time-critical.

One quality caveat: the response exposes no conversation id, so threads cannot
be reassembled here the way they can on X and Bluesky. Posts arrive as
fragments, extraction is correspondingly weaker, and the source should be
weighted accordingly.

The adapter is written and tested against documented payload shapes. Enabling
it is a terms decision, not an engineering one.

### Writer lists

`writers.nfl.txt` is a seed list of 66 beat writer handles covering all 32
teams. Published lists like this are the fastest way to bootstrap, and also
the fastest way to inherit someone else's staleness, so nothing goes into the
registry unvalidated:

```bash
python scripts/validate_writers.py --file writers.nfl.txt --check-bluesky
python scripts/validate_writers.py --file writers.nfl.txt --check-x --yes
```

Bluesky checks are free and try the obvious handle constructions before
falling back to search, since journalists usually reuse their handle stem.
X checks are metered, so the script prices the run up front and refuses to
spend without `--yes`. Both filter out accounts that have gone quiet and emit
ready-to-paste config for the survivors.

One thing worth internalising from that source list: it was compiled for
sports bettors, whose stated reason was that beat writers let them act on
injury and workload news before the books move. That is an independent party
concluding, for money, that beat writers are the highest-value real-time
information source in the sport. It is the same bet you are making.

### The 62 TODO slots

Each team has one disabled source with a `TODO` URL: the local daily or beat
podcast, which has no pattern and has to be found by hand. This is the single
highest-value manual task in the project and the one worth paying for. It is
deliberately shaped for delegation:

- the work is one row per team, obvious and repetitive
- the acceptance test is objective: `verify` passes and `doctor` is clean
- it needs diligence, not product judgment

`verify --fix` rewrites the yaml to disable anything that did not respond, so
a verification pass is one command rather than hand-editing a 200 line file.

### doctor

`doctor` cross-checks registry team codes against roster team codes. It exists
because that mismatch is the worst class of bug here: entirely silent. If your
registry says `ATH` and your roster import says `OAK`, that team's sources
carry no usable team hint, every bare surname in them goes unresolved, and the
feed just looks slightly quiet all season. Nothing errors.

```
[MISMATCH] registry 'ATH' has no players in the roster  <- roster uses 'OAK'
```

Run it after every roster import and every registry regeneration.

## Adding a sport

Three files, no code:

1. `sources/<sport>.yaml` — a `profile` block and a `sources` list
2. `rosters/<sport>.csv` — `id,name,team,position,aliases` (pipe-separated aliases)
3. `fixtures/<source_id>.json` — optional, for offline testing

The `profile` block is where sport knowledge lives, and it matters more than it
looks. "What counts as actionable" is genuinely different per sport, and so is
how fast it expires. Compare the two shipped profiles: the NFL is an
information-scarce weekly sport where a closed practice makes the beat writer
the only witness, and MLB is a daily one where the lineup card and the bullpen
picture reset every afternoon. Same pipeline, completely different rhythm.

## What is deliberately not built

- **Transcription.** `ingest.transcribe()` is a stub with a docstring
  explaining the options. This is the piece that would actually differentiate
  the product, and it is also the piece with real unit costs, so it deserves a
  measured decision on one team rather than a guess across thirty-two.
- **Reporter accuracy scoring.** The schema supports it (`Source.weight`,
  persisted attributions, corroboration counts) but the scorer does not exist.
  It needs a season of data before it can say anything, which is exactly why
  it is worth starting to collect now.
- **Auth, billing, front end.** `render.to_html()` produces a static file on
  purpose, so this deploys the same way your league site does: build, push,
  refresh on a schedule.

## Honest caveats on what shipped

The feed URLs in `sources/*.yaml` are **starting points I could not verify** —
I had no outbound network access to those domains. Run `verify` before
trusting any of them and expect roughly a third to need fixing. Feeds move
constantly, and a silently dead source looks exactly like a quiet news day,
which is the failure mode most likely to hurt you.

The rosters checked in here are small samples for exercising the resolver.
Run `scripts/import_rosters.py` to replace them with real ones. I could not
reach Sleeper or the MLB stats API from where that script was written, so the
response shapes come from documentation rather than a live call. Run it with
`--check` first: it parses, reports, and writes nothing, so you can eyeball
the mapping. If a field name has drifted, the fix is in the two `_parse_*`
functions and nowhere else.

The importer also reports same-surname collisions within a team, which is the
number that tells you how much work position hints have to do. Expect it to be
near zero for the NFL and meaningfully non-zero for MLB.

The `--stub` extractor is crude keyword matching that exists to prove the
plumbing runs without spending money. It is not the extractor. The real one
is the prompt in `extract.SYSTEM`, and that prompt is where most of your
quality tuning will happen.
