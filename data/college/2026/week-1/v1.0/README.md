# College fantasy Week 1 projections, 2026, v1.0

Immutable weekly release derived from the reviewed `2026/v1.1` season
projection allocation.

- 2,205 players on 64 modeled teams scheduled from September 3–7.
- 55 games involving at least one modeled team.
- Yahoo scoring rules.
- Position rankings for QB, RB, WR and TE, plus an overall points order.
- Per-game baselines adjusted by the market-implied team total, game total
  and expected game script.
- No unconfirmed injury, availability or depth-chart change is inferred.

The four v1.1 teams that played in Week 0 and do not have a game in this
window are omitted. Positions remain school roster listings sourced through
CFBD and are not a claim of Yahoo or other platform eligibility.

## Reproduce

```bash
python3 scripts/generate_college_week1.py \
  --scoreboard /path/to/frozen/espn-scoreboard.json \
  --generated-at 2026-08-30T16:15:00+00:00 \
  --output /tmp/college-week1-v1.0
```

The compact schedule and odds inputs required to audit the release are stored
under `provenance/`. The complete raw ESPN response is not shipped to readers.
