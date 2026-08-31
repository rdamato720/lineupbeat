# College fantasy Week 1 projections, 2026, v1.1

Immutable weekly release derived from the reviewed `2026/v1.1` season
projection allocation and a private multi-book market snapshot.

- 2,205 players on 64 modeled teams scheduled from September 3–7.
- 55 games involving at least one modeled team.
- Yahoo scoring rules.
- Position rankings for QB, RB, WR and TE, plus an overall points order.
- 39 qualified game environments calibrated from multi-book consensus.
- 525 player projections changed conservatively from v1.0.
- Available player props failed the multi-book quality threshold, so none were
  allowed to change a projection.
- No raw line, price, bookmaker, total, spread or prop is published.
- No unconfirmed injury, availability or depth-chart change is inferred.

The compact public schedule and reproducible player-level output are stored
under `provenance/`. Private sportsbook inputs remain only in the ignored
runtime database; the manifest records snapshot identifiers and aggregate QA
counts without exposing the inputs.

## Reproduce

```bash
python3 scripts/generate_college_week1.py \
  --scoreboard data/college/2026/week-1/v1.0/provenance/college_week1_schedule_2026.json \
  --odds-db /path/to/private/beatwire.db \
  --output /tmp/college-week1-v1.1 \
  --release-version v1.1 \
  --status PUBLISHED
```
