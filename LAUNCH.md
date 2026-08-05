# LAUNCH

Everything below is copy-pasteable in order. Nothing here is optional except
where it says so.

---

## Day 1 — Get real data flowing

```bash
pip install -r requirements.txt

# Rosters. Public APIs, no keys. --check writes nothing so you can eyeball it.
python scripts/import_rosters.py nfl --check
python scripts/import_rosters.py nfl

# Team codes must line up or team scoping silently dies
python -m beatwire.cli doctor --sport nfl

# Feeds rot. --fix disables whatever does not respond.
python -m beatwire.cli verify --sport nfl --fix

# First real run. No --stub.
export ANTHROPIC_API_KEY=sk-...
python -m beatwire.cli run --sport nfl
```

**Expected:** roughly 2,000 players, 32 teams, and somewhere between 20 and 32
SB Nation feeds surviving `verify`. If far fewer survive, the pattern has moved
and you should check one URL in a browser before touching anything else.

Let it run a few times over 48 hours before you judge anything. One run is not
a sample.

---

## Day 2 — Validate the writer list

`writers.nfl.txt` holds 66 beat writer handles covering all 32 teams, taken
from a published list dated 2024-07-31. That is exactly two years old, so
every line is a lead, not a fact. Two years of outlet layoffs and beat changes
guarantees some of it is wrong.

```bash
# Free. Do this first.
python scripts/validate_writers.py --file writers.nfl.txt --check-bluesky
python scripts/validate_writers.py --file writers.nfl.txt --check-bluesky \
       --emit bluesky >> sources/nfl.yaml

# Metered. Prices itself up front and refuses to spend without --yes.
export X_BEARER_TOKEN=...
python scripts/validate_writers.py --file writers.nfl.txt --check-x --yes
```

The X pass costs about $7.50 for all 66, including a recency check. That buys
you certainty about which accounts still exist and which have gone quiet,
against a list where being wrong costs a team's coverage for a season.

Both modes print teams with **no active writer found**. Those are holes, and
they are the hand-research list. The Rams already start with one writer where
most teams have two, so that is the first gap to close.

---

## Day 3 — The gate

This is the only step that determines whether you have a product.

```bash
python scripts/audit.py --sport nfl --n 200
open audit/review.html          # grade with 1/2/3/4, ~30 minutes
# paste the JSON into audit/results.json
python scripts/audit.py --score audit/results.json
```

**Pass condition: resolution accuracy at or above 97%.**

If it fails, the scorer tells you where the failures cluster:

- **One team dominating** → roster or team-code problem. Run `doctor`.
- **Spread across teams** → prompt problem. Tune `extract.SYSTEM`.
- **Category errors** → fix `high_value` / `low_value` in `sources/nfl.yaml`
  before touching the prompt.

Also work the roster-health queue:

```bash
python -m beatwire.cli unresolved --sport nfl
```

Top entries are almost always a missing alias or a player signed since your
import. Both are cheap fixes.

---

## Day 4 — Ship it

```bash
python -m beatwire.cli preflight --sport nfl   # must print GO
python -m beatwire.cli export --sports nfl
open site/index.html                            # look at it yourself first
```

Then:

1. Push to GitHub.
2. Netlify: new site from the repo. Publish directory `site`.
3. Repo secrets: `ANTHROPIC_API_KEY`, `NETLIFY_AUTH_TOKEN`, `NETLIFY_SITE_ID`.
4. Run the workflow manually once via **Actions → refresh beat feed → Run
   workflow**. Do not wait for the cron to find out it is broken.

The Action gates on the resolver tests and `doctor`, so a bad build fails
instead of publishing.

---

## Ongoing — the only two numbers

Everything else is vanity.

1. **Do the same people open it on consecutive Tuesdays?**
2. **Resolution accuracy**, re-audited every couple of weeks with a fresh
   sample. It drifts as rosters churn.

Run `preflight` before every deploy. It catches the failures that are
otherwise silent: stale roster, drifted team codes, dead feeds, a rising
unresolved rate.

---

## Next, in priority order

**1. Fill the 32 hand-research slots.** One local daily or beat podcast per
team. This is the completeness work and the thing worth paying for. Acceptance
test is objective: `verify` passes and `preflight` says GO.

**2. Transcription on four teams.** Measure one number: nuggets per hour of
audio that text did not already give you. That decides whether audio is the
moat or a distraction. Batch rates run $0.15–0.26/hr, so the pilot is under
$100. Pass each team's roster in as keyterms; it sharply cuts name errors,
which feeds straight into resolution accuracy.

**3. The Sunday 11:30am view.** Inactives drop 90 minutes before kickoff and
every fantasy manager in America is scrambling. It is beat-sourced, it is
brutally time-decaying, and the decay rail already handles it. Owning that
30-minute window is how this becomes a habit rather than a bookmark.

**4. X, if the budget allows.** Now viable: pay-per-use, no monthly cap. Start
narrow and let the data widen it.

```bash
python -m beatwire.cli run   --sport nfl --x-daily-cap 2.00
python -m beatwire.cli spend --provider x
```

One writer per team with `since_id` runs about $96/month. Start there, look at
$/nugget after a week, and add writers who earn it. Never poll without a
cursor: the same workload without one is roughly $12,600/month.

**5. Meta Threads: skip.** The API is free and the read endpoint exists, but
the stated permitted use covers publishing on someone's behalf and showing
those posts back to their author, not aggregating other people's posts for
your subscribers. App Review is where that gets judged, so approval is a coin
flip on use-case grounds. The adapter is written if you ever want it; do not
spend launch calendar on the submission.

**6. Bluesky.** The adapter is built. Run the coverage audit before wiring
writers in, because free access is not the same as useful coverage:

```bash
python scripts/bluesky_audit.py --names writers.txt --sport nfl
```

It prints a verdict. Over half your writers active in the last 30 days means
build on it; under a quarter means recheck in a few months instead. Build
`writers.txt` during the same research pass that fills the `-local` slots, then
`--emit` turns the hits into config.

Note what preflight already tells you: 100% of your live sources are currently
SB Nation. Bluesky is the cheapest way to fix that concentration.

---

## Do not build yet

Accounts. Payments. A mobile app. A second sport. A backend. Static files on
Netlify will carry you well past your first thousand users, and every one of
these costs you days you do not have before Week 1.
