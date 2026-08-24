# Lineup Beat engineering rules

These instructions apply to the entire repository. Read
`docs/WIRE_HANDOFF.md` before changing anything under `wire/`, any
`scripts/wire_*` file, the Wire registries, the homepage Wire renderer, or the
refresh workflow.

## Product contract

- The NFL Wire lives on the homepage at `/#wire`. Do not restore a separate
  `/nfl/wire/` destination. Legacy forms redirect to the homepage anchor.
- Public cards cover useful fantasy-relevant QB, RB, WR, and TE information.
  That includes reported developments and clearly labelled fantasy analysis,
  opinion, speculation, ADP arguments, rankings and practice observations.
  Analysis must never be presented as firsthand reporting.
- Each public card keeps the established homepage design: team colour, player
  photo, team logo, mechanism, direction, positional rank, ADP, projected
  points, relative time, and attribution where those display values exist.
  Missing display values are omitted, never guessed or rendered as zero.
- Render one card per row. The source block is one short approved sentence
  labelled either `What changed` or `Fantasy analysis`; Lineup Beat analysis
  is a separate, visually dominant block. Never publish
  the stored evidence passage or a first-N-character truncation as the public
  summary.
- League News and the video section remain removed. National fantasy-relevant
  reports may appear in Recent News. YouTube ingestion remains paused.
- Use the phrase "trusted sources" in reader-facing descriptions. Do not name
  infrastructure providers or imply that a model is a reporter.

## Non-negotiable safety boundaries

- A model assessment never authorizes publication. Only a named human approval
  of the final public summary and final commentary can create a publication.
- A GitHub mobile approval counts only when the issue carries the immutable
  hash-bound Wire manifest, the comment comes from the allow-listed
  `rdamato720` account, and the comment names the exact cards or exact edited
  replacement sentences. A reaction, issue close, or model-generated comment
  is not approval.
- `PENDING`, `HOLD`, `ABSTAIN`, `NO_FANTASY_IMPACT`, unresolved identity,
  failed validation, evidence-integrity failure, and claim-subject conflict can
  never auto-publish.
- Identity comes from `sources/wire_players.json`, using stable player id or
  exact name + team + position. Do not ask a model whether a player is on a
  team. Do not fuzzy-match an unresolved player.
- Hash evidence text by itself. Generator/reviewer request hashes are separate
  fields and must never be treated as evidence hashes.
- A reviewer saying the passage names a different claim subject routes the
  item to human review. It must never manufacture a rejection or approval.
- Rolling/live/tracker pages require a span-level event timestamp. An article
  page's updated timestamp is not proof that every embedded update is current.
- Official team designations are authoritative for the club's own acts and
  participation labels, but never count as independent corroboration.
- Paid/discovery-only sources contribute no evidence. Manual submission does
  not bypass a blocked, paid, or publisher-refused source.
- Relayed reporting is labelled and linked to the underlying report. It never
  becomes firsthand merely because another outlet rewrote it.
- Secrets are environment variables or GitHub secrets only. Never log, commit,
  serialize, or paste API keys. Scrub provider errors before logging.

## Fantasy-data isolation

- The Wire may read the prebuilt, identity-keyed display and relevance files.
  It must not read projection workbooks, ranking source files, ADP source
  files, or `rosters/nfl.csv` during interpretation or publication.
- The Wire must never write projections, rankings, ADP, scoring configuration,
  or roster source data. `projection_action` is advisory metadata only.
- Display joins happen after interpretation by stable player id. No display
  number may be sent to a semantic model or used to manufacture relevance.

## Source and authority rules

- A source name, reporter name, series name, URL path, or config flag does not
  grant firsthand authority by itself. Authority must come from the researched
  author registry and its evidence-access classification.
- On SI articles must match the exact team `/onsi/` canonical path and the
  researched byline. Broad team landing-page placement is not team identity.
- Official team sites are team-owned and labelled as such.
- Mixed publications require an explicit team filter. Wrong-team and national
  syndication remain visible in the inclusive review catalog with their
  provenance. Analysis, aggregation, mailbags, mock drafts, fantasy advice,
  rankings, betting angles and opinion may enter named-human review.
  Promotional and sponsor copy remains outside the Wire.

## Editing and migration rules

- Preserve source articles, evidence rows, model responses, human decisions,
  superseded records, and audit history. Retire or supersede; do not silently
  delete reviewed evidence.
- Migrations must be atomic, idempotent, and refuse to collapse a row carrying
  a non-pending decision. Re-running a build must be byte-stable where time is
  not intentionally part of the artifact.
- Run discovery separately from paid model interpretation. Discovery and plan
  commands must not consume model or transcript budget.
- Never change a human-approved sentence by changing only its structured
  direction/mechanism fields. Badge and prose must agree, or the item is not
  publishable as written.
- Mobile event deduplication may combine only the same stable player,
  mechanism, direction and content type inside its declared time window.
  Conflicting participation states, team-unit levels, transaction types or
  injury areas are materially different and must remain separate.

## Required verification

For every Wire change, run at minimum:

```bash
python scripts/test_wire_review.py
python scripts/test_wire_mobile.py
python scripts/test_wire.py
python scripts/test_wire_page.py
python scripts/test_wire_homepage.py
python scripts/test_resolve.py
python scripts/test_tapi.py
python scripts/wire_fixtures.py
python scripts/wire_health.py --check
python -m beatwire.cli doctor --sport nfl
```

For display/deployment changes, also build twice and verify the deployment
artifact. For provider changes, run the locked semantic corpus and report both
precision and recall with numerators/denominators. A provider that abstains on
everything is not accurate.

## Pull-request handoff

Every PR must state:

- the exact behavior changed;
- publication count before and after;
- model/API calls and cost, or explicitly zero;
- files that can reach the reader;
- evidence and identity invariants exercised;
- tests run and any unrelated failures;
- whether projections, rankings, ADP, scoring, or rosters changed;
- rollback procedure.

Do not call a change live until the deployed URL or homepage artifact has been
verified after every later build/prune step.
