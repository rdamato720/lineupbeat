# Lineup Beat NFL Wire: complete engineering handoff

Last verified: 2026-08-22

Repository: `rdamato720/lineupbeat`

Baseline containing the semantic-boundary repair: `1dfa6f5`

Public destination: `https://lineupbeat.com/#wire`

## 1. What this system is

The NFL Wire turns reporting from trusted sources into concise,
fantasy-relevant homepage cards. It is not a general news scraper, a projection
engine, or an autonomous publisher.

Each card answers two different questions:

1. **What changed?** One short, human-approved factual sentence.
2. **Lineup Beat impact.** A separate, human-approved fantasy interpretation
   that states what the evidence supports and what it does not establish.

The Wire may display existing positional rank, ADP, and projected points, but
those values are a display-only join. The evidence pipeline neither modifies
them nor sends them to a model.

## 2. Current production state

At this checkpoint:

- The homepage publication file contains six reviewed cards.
- Nothing in the new semantic-review repair automatically publishes.
- The Wire is homepage-only. Legacy `/nfl/wire` and `/nfl/wire/` routes belong
  on redirects to `/#wire`, not in navigation or the sitemap.
- Cards are one per row, not two or three columns.
- Player photos, team logos, team colours, mechanism, direction, position
  rank, ADP, projection, relative time, and attribution are preserved when a
  real value exists.
- Public evidence is a single approved sentence capped by the public-summary
  validator. The full evidence passage remains in the evidence store and
  review artifacts, not on the homepage.
- League News, the separate video strip, Fantasy Data, and Moving Now were
  removed from the homepage. Recent News remains.
- YouTube is paused in `sources/wire_youtube.yaml`. Its registry, cache, budget
  controls, and tests remain, but production must spend no caption or model
  calls on video evidence.
- `sources/wire_articles.yaml` currently loads 80 registered article sources;
  the last verified health run reported 74 active sources and zero fatal
  problems.
- `data/wire_publications.json` is the only Wire publication input read by the
  public builder. Evidence candidates and review artifacts are not public.

The six records currently in `data/wire_publications.json` are the historical
reviewed launch set. Do not infer that all six would pass a newly invented rule
without a migration and explicit review. Existing human decisions are data,
not fixtures to rewrite casually.

## 3. High-level architecture

```mermaid
flowchart TD
  A["Trusted website sources"] --> B["Discovery and capture"]
  B --> C["Segmentation and evidence classification"]
  C --> D["Deterministic identity, currentness, relevance and dedup"]
  D --> E["Semantic interpretation"]
  E --> F["Independent semantic review"]
  F --> G["Deterministic validation"]
  G --> H["Human review and final wording"]
  H --> I["wire_publications.json"]
  I --> J["Homepage #wire"]
```

There is no valid path from E or F directly to I.

## 4. Repository map

| Area | Purpose |
|---|---|
| `sources/wire_articles.yaml` | Article-source registry, filters, ownership, paid/blocked state |
| `sources/wire_si_authors.json` | Researched SI/On SI author classifications |
| `sources/wire_players.json` | Wire-only stable identity registry |
| `sources/wire_youtube.yaml` | Paused video registry and transcript safeguards |
| `wire/capture.py` | Fetch and extraction policy |
| `wire/segment.py` | Evidence boundaries: headings, bullets, dated entries, observations |
| `wire/evidence.py` | Evidence classification and relay/authority handling |
| `wire/currentness.py` | Rolling-page detection and event-time eligibility |
| `wire/players.py` | Exact deterministic identity resolution |
| `wire/relevance.py` | Rosterable/evidence-created fantasy relevance gate |
| `wire/claims.py` | Claim identity and deduplication |
| `wire/semantic.py` | Shared interpretation schema and prompt |
| `wire/semantic_validate.py` | Deterministic interpretation validation |
| `wire/evidence_integrity.py` | Evidence-only and request hashing |
| `wire/independent_review.py` | Independent-review schema, validation, deterministic overrides |
| `wire/providers/` | Rules, Anthropic, OpenAI, and independent-review transports |
| `wire/store.py` | Candidate, impact, decision, audit, and publication persistence |
| `scripts/wire_backfill.py` | Rolling-window discovery/interpretation/reporting |
| `scripts/wire_independent_review.py` | Second-pass review; writes no publications |
| `scripts/wire_review_package.py` | Human-review HTML and JSON package |
| `scripts/wire_publish.py` | Only reviewed-card publication route |
| `scripts/wire_homepage_replacement.py` | Homepage Wire renderer/application logic |
| `scripts/test_wire_review.py` | Focused semantic-boundary regression suite |
| `.github/workflows/refresh.yml` | Scheduled/manual build and deployment pipeline |

## 5. Source model

### 5.1 Source classes

The article registry supports these conceptual classes:

- **Independent local publication.** Best source class when an individually
  researched reporter has repeated evidence access.
- **SI On SI.** Independent from the team but must match both the exact team
  `/onsi/` canonical path and researched author policy. A broad SI team landing
  page is discovery only; its placement does not establish article subject.
- **Official team site.** Authoritative for the club's own transactions,
  designations, and statements. It is team-owned and contributes zero to an
  independent corroboration count.
- **Discovery-only paid source.** May establish that an article exists, but its
  body contributes no evidence. Manual submission cannot bypass the block.
- **Mixed publication.** Requires an explicit team/category/author filter.

### 5.2 Authority is earned, never configured into existence

`reporter_name`, `source_name`, `series_name`, a URL path, or a YAML status is
descriptive metadata. None grants firsthand authority. A reporter becomes
`FIRSTHAND_APPROVED` only after individual research establishes repeated
practice, locker-room, press-conference, or other direct evidence access.

An approved reporter's plain declarative practice sentence may count as an
approved-reporter declaration even when it does not contain a magic phrase
such as "I saw." An unresearched byline does not receive that benefit. Hedges,
relay language, and attribution still override the byline.

### 5.3 Refuse before capture or interpretation

Reject wrong-team canonicals, national syndication, aggregation, betting,
fantasy-advice articles, mock drafts, power rankings, mailbags, community
posts, roster predictions, and marketing content before paying for semantic
interpretation. Record refusals rather than making them disappear from health
accounting.

## 6. Evidence model

Important evidence classes include:

- `FIRSTHAND_OBSERVATION`: direct observation from approved evidence access.
- `APPROVED_REPORTER_DECLARATION`: a clear unhedged declarative report from an
  approved byline.
- `DIRECT_QUOTATION`: material player/coach/club words tied to a named speaker.
- `OFFICIAL_DESIGNATION`: a club's own participation or transaction label;
  eligible evidence but not independent firsthand reporting.
- `RELAYED_REPORTING`: another reporter/outlet's work being repeated; linked to
  its underlying report and never promoted to firsthand.
- `ANALYSIS_OR_OPINION`: prediction, synthesis, hedge, anonymous consensus, or
  analysis rather than original evidence.
- `UNCERTAIN`: evidence whose speaker, subject, attribution, or provenance
  cannot be safely established.

Segment boundaries are semantic boundaries. Windows may not cross headings,
bullets, numbered observations, dated entries, transaction lines, live-blog
timestamps, or footer biographies. That rule prevents one player's quote,
another player's heading, and a third player's availability update from
becoming one claim.

Do not use punctuation balance as a universal completeness test. Publishers
omit terminal punctuation, windows can begin inside a longer quotation, and
caption-style attribution can be complete without balanced quotes inside one
segment. Integrity should prove stored text identity and source offsets, not
invent grammar rules with high false-positive rates.

## 7. Identity and claim subject

Identity is settled before a model call:

1. Exact stable player id, or
2. exact normalized name + team + position, or
3. unresolved and routed to a human.

No fuzzy resolution. A bare surname, wrong team, wrong position, or misspelled
name resolves to nobody. Diacritics and punctuation normalize, but identity is
not guessed.

The independent reviewer is not allowed to override this registry from model
memory. It may still identify a different **claim subject** in the passage. A
claim-subject conflict routes to human review and blocks automatic approval;
it does not manufacture a rejection.

Examples of the distinction:

- "Kyler Murray does not play for Minnesota" is a stale-roster objection and
  is worthless when the supplied registry says otherwise.
- A D'Andre Swift candidate whose supporting passage is actually about Roschon
  Johnson is a real claim-subject conflict and must be reviewed.

## 8. Currentness and rolling pages

An article's publication/update timestamp normally anchors currentness. It is
not sufficient for a rolling tracker, live blog, updates page, or similar
container whose old entries remain on a newly updated URL.

`wire/currentness.py` detects rolling-page signals. A claim from one requires
a span-level event timestamp. If no reliable event time exists, retain the
evidence but block automatic interpretation. Do not assume that an August page
timestamp makes a June minicamp passage current.

Backfill windows must record and use the same lower and upper bounds. The
original window may be preserved separately for audit history, but a cached
label must never describe a different rolling filter. Every per-run outcome
counter must be mutually exclusive and reconcile to the run total.

## 9. Fantasy relevance

Fantasy position is necessary but insufficient. The gate recognizes:

- `ROSTERABLE`: inside the prebuilt redraft boundary.
- `EVIDENCE_CREATED`: outside the normal boundary, but the evidence itself
  establishes material first-team opportunity, named promotion, starter
  declaration, meaningful replacement role, or another actionable change.
- `WATCHLIST`/context-only: recognized but not automatically card-worthy.

Rules to preserve:

- A backup quarterback needs a promotion, named-starter call, starter absence,
  or explicit first-team work that materially changes opportunity.
- An injury or absence alone does not make a WATCHLIST player relevant.
- A third running back being behind two players is not a positive depth-chart
  development.
- One isolated carry, catch, good throw, or practice performance does not
  establish role, usage, or projection impact.
- Defensive snaps do not prove reduced offensive usage for a two-way player.
- Evidence-created relevance must come from the passage, not ADP, rankings, or
  a model's general football knowledge.

`data/wire_fantasy_relevance.json` is a prebuilt identity-level gate and must
not contain ADP, rank, projected points, or fantasy statistics.

## 10. Semantic interpretation

### 10.1 Generator

The generator answers structured questions: decision, claim subject, mentioned
players and relationships, exact supporting quote, mechanism, direction,
strength, horizon, projection action, why it matters, limitations, and
confidence.

Its response is never trusted directly. `wire/semantic_validate.py` verifies
identity, exact quotation, named facts, mechanism support, directionality,
unit language, relayed provenance, ownership limits, and forbidden filler.
Any failed response becomes an abstention/human-review item, never a repaired
automatic answer.

### 10.2 Independent reviewer

The reviewer evaluates the generator assessment against the same full evidence
text. It does not judge roster correctness. It returns an auditable verdict
plus diagnostic flags, including claim-subject conflicts and whether an
isolated performance lacks role information.

The proposed `evidence_classification` is part of the reviewer input and has
its own required support flag. The reviewer must apply the same authority
boundary as the generator: `FIRSTHAND_OBSERVATION`, `DIRECT_QUOTATION`, and
`OFFICIAL_DESIGNATION` may support an interpretation;
`ANALYSIS_OR_OPINION`, `RELAYED_REPORTING`, and `UNCERTAIN` may not.

Deterministic enforcement runs after the reviewer:

- unresolved identity blocks the call and automatic publication;
- evidence-integrity failure routes to human review;
- `passage_names_a_different_subject=true` blocks `AUTO_APPROVE` and routes to
  `HUMAN_REVIEW`;
- an unsupported evidence classification blocks `AUTO_APPROVE` and routes to
  `HUMAN_REVIEW`;
- an independent model `REJECT` remains the model's rejection and is labelled
  as such; code does not invent one.

### 10.3 Provider state

- OpenAI generator: implemented in `wire/providers/openai.py` using the
  Responses API, strict JSON Schema, and `store: false`.
- OpenAI independent reviewer: implemented in
  `wire/providers/openai_review.py` as a separate Responses API pass.
- Generator prompt `wire-fantasy-2026-08-23d` treats source metadata as
  provenance context only, grounds all editorial fields in the evidence, and
  forbids `UPDATE_RECOMMENDED` on `LOW` evidence. Explicit "began practicing"
  language counts as a return event even when participation remains limited.
- Reviewer prompt `wire-independent-review-2026-08-23e` and schema
  `independent-review-v2` explicitly review the proposed evidence class,
  distinguish attributed speech from relayed reporting, and classify the
  supported claim rather than unrelated sentences in a mixed passage.
- `openai==3.3.1` is pinned in `requirements.txt`.
- Anthropic transports remain as inert legacy/audit code but are absent from
  the active provider registry, workflow secrets, and installed requirements.
- OpenAI is the dark-launch provider, not production-approved until the locked
  corpus and labelled real-evidence gates pass.

Do not claim provider determinism. Temperature zero may reduce variability but
does not guarantee identical results. Store provider, model, schema, prompt,
corpus version, token use, cost, latency, evidence hash, and request hash for
every run.

## 11. Evidence integrity

Four actors must agree on the evidence-only SHA-256:

- stored evidence;
- generator evidence;
- reviewer evidence;
- human-review-package evidence.

`sha256(evidence_text)` hashes the exact evidence text and nothing else.
Generator and reviewer request hashes may include prompt, player ids, schema,
or metadata, but they are separate fields and excluded from evidence equality.

The review package must refuse to build when:

- evidence hashes disagree;
- the displayed identity disagrees with the registry;
- the item header and identity block disagree;
- one stale identity is reused across multiple cards;
- a source URL or full evidence passage is missing;
- an invalid/quarantined evaluation is presented as primary metrics.

Never reuse a display-snippet helper for model or reviewer input. Full evidence
travels through generator, reviewer, validator, JSON package, and review page.
Only explicitly labelled suppressed-item previews may be shortened.

## 12. Deduplication and provenance

Candidate identity is derived from canonical article URL, player, and evidence
span. Exact duplicates are superseded against a survivor, not deleted.
Different players legitimately linked to one evidence span share an
`evidence_group_id`; this is not duplicate evidence.

Semantic duplicate detection is scoped to a segment. It may not merge separate
paragraphs merely because the same player and generic camp vocabulary appear
in both. Rewrites of one underlying report link to the same
`underlying_report_id` and never increase independent-source count.

A migration must stop if a non-survivor carries a review decision. Human
decisions, publications, and audit history survive re-extraction and dedup.

## 13. Human review and publication

The review package is intentionally the final editorial boundary. Order it by
publication risk:

1. card-producing auto-approvals;
2. action-level disagreements;
3. remaining proposed cards;
4. suppression agreements;
5. other assessments.

An auto-approval on `NO_FANTASY_IMPACT` is a suppression agreement, not a
publishable card, and must be counted separately.

The public evidence sentence starts blank. It is authored or explicitly
approved by a human. Human decisions store reviewer, timestamp, rejection
reason, original generated text, and any edited replacement separately.

`scripts/wire_publish.py` is the only route into the publication store. It
requires an approved reviewer action and passes the finished card through the
publication-readiness validator. Run it in dry-run mode first:

```bash
python scripts/wire_publish.py --dry-run
python scripts/wire_publish.py --publish --actor <reviewer>
```

The second command is a material production-data mutation. Never run it merely
because a model evaluation completed.

## 14. Homepage rendering

The homepage replacement is built from reviewed publications, then display
metadata is joined by stable player id.

Required behavior:

- one card per row, capped to a readable width;
- no line clamp on Lineup Beat analysis;
- card count equals visible reviewed publications;
- no duplicate player/event card;
- reporting and analysis are structurally separate elements;
- missing rank/ADP/projection omitted rather than guessed;
- filters for direction, team, QB, RB, WR, and TE;
- excluded/held/abstained/no-impact players absent as reports even though their
  roster identity may remain in the page payload for search and My Roster;
- no separate Wire link or page;
- builds are idempotent and survive every later page-pruning step.

The deployed artifact, not an intermediate builder output, is the thing to
verify. A prior build created `site/nfl/wire/index.html` and a later stale-page
pruner deleted it before deployment while all builder tests remained green.
Artifact verification must run immediately before deploy.

## 15. Data isolation

The Wire may read:

- `sources/wire_players.json` for identity;
- `data/wire_fantasy_relevance.json` for the prebuilt relevance class;
- `data/wire_display_fantasy.json` for display-only rank/ADP/projection fields;
- Wire source, evidence, review, decision, and publication data.

The interpretation and publication path must not open:

- `data/projections.xlsx`;
- ranking source/config JSON files;
- ADP source files;
- `rosters/nfl.csv`;
- fantasy scoring configuration.

It must never write any of them. Projection changes remain a separate process
requiring their own evidence and approval.

## 16. Operating commands

### 16.1 Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 16.2 Read-only health and discovery

```bash
python scripts/wire_health.py --check
python scripts/wire_readiness.py
python scripts/wire_discovery_watch.py --run
python scripts/wire_backfill.py --discover --report --hours 48
```

Discovery must use no semantic-model budget. Confirm the reported lower and
upper timestamps equal the filter actually applied and that outcome counters
reconcile.

### 16.3 Evidence extraction

```bash
python scripts/wire_ingest.py --dry-run
python scripts/wire_extract.py --dry-run --limit 200
```

Remove `--dry-run` only when the intended source and persistence effects have
been inspected.

### 16.4 Locked provider evaluation

```bash
python scripts/wire_semantic_eval.py --providers rules
python scripts/wire_semantic_eval.py --providers rules,openai
```

For an OpenAI comparison, set `OPENAI_API_KEY` in the environment and run:

```bash
python scripts/wire_semantic_eval.py --providers rules,openai
```

Report zero-tolerance errors, correct/total, precision numerator/denominator,
recall numerator/denominator, false suppressions, false positives, abstentions,
validation failures, token use, cost, median latency, and p95 latency. Unlabelled
items are review material, not scored passes.

### 16.5 Dark-launch interpretation and review

These commands can spend provider budget. Confirm the cap and valid secret
before running:

```bash
python scripts/wire_backfill.py --plan --report --hours 48
python scripts/wire_backfill.py --interpret --report --hours 48 \
  --candidate-id ID_1 --candidate-id ID_2 \
  --exclude-candidate-id ALREADY_REVIEWED_ID \
  --cap 0.20 --max-calls 2
python scripts/wire_independent_review.py --cap 0.20 --max-calls 2
python scripts/wire_review_package.py
```

Expected primary artifacts:

- `data/wire_backfill.json`
- `data/wire_independent_review.json`
- `data/wire_review_package.html`
- `data/wire_review_package.json`

The independent reviewer and package scripts write zero publications. Confirm
that explicitly after every run.

For measured dark-launch batches, name every approved candidate with the
repeatable `--candidate-id` option. Use `--exclude-candidate-id` for an
already-paid case. Exact selection preserves the deterministic survivor order.
If an included id has aged out of the moving window or is no longer a
survivor, the command exits before the first API call; it never substitutes a
different candidate. The call limit and dollar cap remain independent.

### 16.6 Required tests

```bash
python scripts/test_wire_review.py
python scripts/test_wire.py
python scripts/test_wire_page.py
python scripts/test_wire_homepage.py
python scripts/test_resolve.py
python scripts/test_tapi.py
python scripts/wire_fixtures.py
python scripts/build_rankings.py --dry-run
python scripts/wire_health.py --check
python -m beatwire.cli doctor --sport nfl
```

At baseline `1dfa6f5`, the focused semantic suite passed 11/11, all existing
Wire/page/homepage/API tests passed, resolver passed 25/25, adversarial fixtures
passed 11/11, health reported 74 active sources with zero fatal problems, and
doctor matched all 32 NFL team codes.

## 17. Workflow and deployment

`.github/workflows/refresh.yml` is `workflow_dispatch`-only. An external
cron-job.org trigger invokes it; GitHub's own schedule is intentionally absent
to avoid duplicate model/API spend.

The workflow:

1. checks out the repository and installs dependencies;
2. restores `beatwire.db` from cache;
3. runs resolver, API, semantic-boundary, doctor, and navigation gates;
4. optionally fetches when `skip_fetch` is false;
5. rebuilds feed, player pages, projections pages, rankings, Wire output,
   homepage replacement, sitemap, and other static pages;
6. verifies the final deployment artifact;
7. deploys only after verification;
8. saves the database cache after a successful run.

Use `skip_fetch: true` for a page-only rebuild. That avoids external data/model
spend; it does not excuse publication or artifact validation.

Concurrency is queued with `cancel-in-progress: false`. Cancelling a running
job can lose the updated cache after paid calls and cause the next run to pay
for the same work again.

## 18. Secrets and provider configuration

Current workflow secrets/variables referenced by the broader project include:

- `OPENAI_API_KEY`
- `SORSA_API_KEY`
- `TWITTERAPI_IO_KEY`
- `BEATWIRE_X_PROVIDER` as a repository variable

The paused YouTube pilot reads `YOUTUBE_API_KEY` only when used manually.
ChatGPT subscriptions do not fund API calls; OpenAI API billing is separate.

Never echo secret values, return them from helper functions, serialize them,
put them in URLs that reach logs, or commit them. Provider error paths perform
both exact-held-key and key-shape redaction.

## 19. Rollback

Publication snapshots live under `data/wire_snapshots/`. Before a material
publication change:

```bash
python scripts/wire_health.py --snapshot
```

To restore the latest banked publication snapshot:

```bash
python scripts/wire_health.py --rollback
```

The rollback preserves the file it replaces. Homepage/feed rollback assets
also exist under `data/rollback/` where applicable. After rollback, rebuild and
verify the deployment artifact; restoring JSON without rebuilding does not
change the live site.

For a provider failure, disable the provider or omit its key. Evidence remains
pending for human review; the rules engine must not silently generate public
commentary as a fallback.

## 20. Known issues and next work

### Immediate next step

After the evidence-grounding changes land, rerun the same five-case exact
selection across distinct teams and sources, excluding every already-paid
candidate id. Run both OpenAI passes, generate the HTML and JSON review
package, and review every item manually. Publish nothing during that run. Do
not expand the batch until the generator, independent reviewer, deterministic
enforcement, and a human agree on all five.

### OpenAI production-promotion checklist

Before promoting the OpenAI dark-launch path to production semantics:

1. Pin the OpenAI SDK in `requirements.txt`.
2. Add an OpenAI independent-review transport using the same strict closed
   schema and deterministic enforcement.
3. Add provider-specific redaction and failure tests.
4. Run the locked gold corpus side by side; never change labels merely to make
   the new provider pass.
5. Run a labelled real-evidence review set.
6. Require zero wrong-player, wrong-subject, opposite-direction,
   unsupported-unit, relay-promotion, invented-fact, and quotation-integrity
   failures.
7. Set explicit precision and recall gates. Do not reward abstaining.
8. Keep publication human-gated through a measured dark-launch period.
9. Only then describe the OpenAI provider as production-approved.

### Review automation

The independent reviewer is a prioritization layer, not an editor-in-chief.
Previous reviewer auto-approval sets changed materially between runs, and
human review found that apparently clean auto-approvals could still be stale,
irrelevant, or non-actionable. Automation may safely suppress clear
`NO_FANTASY_IMPACT` agreements sooner than it may publish cards.

The first automation milestone should therefore be:

- automatically refuse deterministic failures;
- automatically suppress high-confidence no-impact agreements;
- prioritize disagreements and proposed cards for humans;
- generate no public summary automatically;
- publish nothing without a named human decision.

### Accounting

Keep these populations separate:

- all evidence rows ever stored;
- corpus-wide pending eligible rows;
- pending eligible rows inside the exact window;
- funnel candidates after deterministic filters;
- model calls;
- interpretations;
- proposed cards;
- human-approved publications.

Do not combine cumulative stored-row counts with per-run events. Every funnel
must partition one named population and print a reconciliation check.

## 21. Definition of done

A Wire change is done only when all of the following are true:

- source, evidence, player, event time, and claim subject are auditable;
- every model saw the full evidence and matching identity;
- evidence hashes match and request hashes remain separate;
- deterministic relevance and publication rules pass;
- final public summary and commentary have a named human approval;
- badge, mechanism, direction, and prose agree;
- publication count and visible-card count agree;
- held, rejected, abstained, no-impact, stale, paid, wrong-team, and unresolved
  items cannot render as reports;
- projections, rankings, ADP, scoring, and roster source files have no diff;
- all required suites pass;
- two consecutive builds are idempotent where expected;
- the final deployment artifact is verified after all later build steps;
- the live homepage is checked after deployment;
- rollback is banked and documented.

Until those are true, describe the work as a dark launch, review package, or
deployed code—not as an automated or completed public launch.
