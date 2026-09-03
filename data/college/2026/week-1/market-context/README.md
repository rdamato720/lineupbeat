# College Week 1 market context

This directory contains a derived game-environment snapshot for the 64 teams
in LineupBeat's published 2026 College Week 1 model.

- Source: TheRundown Pro capture supplied by the account owner
- Capture date: 2026-09-03
- Plan delay at capture: 30 seconds
- Sportsbooks requested: Pinnacle, DraftKings, FanDuel
- Markets: full-game moneyline, spread, and total
- Coverage: 64/64 modeled teams with a spread and total

The published artifact contains consensus spread, game total, implied team
totals, book coverage, and cross-book range. It does not contain raw book
prices, provider credentials, player props, or a projection adjustment.

Rebuild locally, without a network request:

```sh
python scripts/build_college_week1_market_context.py \
  /path/to/lineupbeat-college-week1-2026-pro.zip
```

The generated manifest protects the derived artifact from silent changes.
