# NFL Week 1 projections v1.1

Development release generated September 7, 2026.

This is a LineupBeat-owned weekly projection model, not season totals divided
by 17 and not a copy of another publisher's rankings. It covers 424 current
active QB/RB/WR/TE players with one exact match to the reviewed LineupBeat
season baseline. Seventy-nine active players without a single exact reviewed
baseline match are withheld instead of guessed.

## Current inputs

- NFL Week 1 schedule release updated September 7, 2026.
- NFL roster and offensive depth-chart releases updated September 6, 2026.
- 2024 and 2025 nflverse player, team, snap and play-by-play history.
- Reviewed LineupBeat 2026 season stat projections as efficiency and workload
  priors only.
- Bounded 2025 opponent-position and venue context.

Every captured source is recorded with its release URL, retrieval timestamp,
license, byte size and SHA-256 digest in `provenance.json`.

## Publication guardrail

These are real Week 1 point and stat-line projections, but the release does
not enable unqualified start/sit recommendations. No official 2026 injury
report was available at capture time, no sportsbook or player-prop request was
made, and the current walk-forward test is a context-model proxy rather than a
reproduction of the complete production formula. The Decision Room may show a
projection leader or Toss-Up, but it must disclose that the call is not yet a
validated lineup recommendation.
