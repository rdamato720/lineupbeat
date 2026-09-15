# NFL Week 2: independent projections and rankings

The old Week 2 release admitted players through the preseason workbook and
sorted its rankings by projected points. Version 2 forecasts the current active
QB/RB/WR/TE roster directly from stable identities and football data. It publishes
three reception-scoring formats and a separate ranking artifact for both 1QB and
Superflex. The reference CSVs supplied by the owner were used for the earlier
audit only: no external expert rows, values, ranks or prose enter this model.

## Data and calculation

- Current nflverse roster/depth, 2026 Week 1 player/team statistics, offensive
  snap counts and play-by-play. Captures must cover all 16 completed games.
- Strictly earlier player appearances and team volume supply usage/efficiency
  priors. Goal-line carries, red-zone/end-zone targets and neutral pass tendency
  inform the component estimates. Training selected zero extra opponent and
  snap-ratio multipliers; snaps still establish actual participation.
- Attempts, carries and targets are allocated across the complete available
  offense. Receiving yards, receptions and touchdowns reconcile with passing.
  Fumble expectations use the final allocated opportunities.
- Questionable/Doubtful do not reduce points. Confirmed Out/IR/suspended players
  receive zero and are unranked. No qualified current TheRundown snapshot was
  available; the model makes no market adjustment or paid provider call.
- Current injury facts join by mechanically normalized name plus exact team and
  position. Statistical identities use GSIS IDs and exact game/team/opponent.
  Ambiguous PFR-to-GSIS identifiers never join snap data.

The first v2 release contains 500 projected players, including 82 omitted by the
v1 workbook gate. One confirmed-unavailable player is unranked. More coverage is
not a claim of equal confidence: newcomers without history use broad workload
and position-efficiency priors.

## Two different objectives

Projection settings minimize mean squared point error, equally weighted across
position and scoring format, on 2024 Weeks 2–18. The ranking calculation selects
weights separately for within-position pair ordering on those training weeks.
It selected 85% modeled typical outcome and 15% recent recorded scoring history.
The typical outcome uses a touchdown-count mixture with non-TD dispersion fitted
on training residuals; it is not a calibrated floor or ceiling.

Overall rankings subtract the next available positional replacement after
filling a reference 12-team lineup (1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX). Superflex
adds one QB/RB/WR/TE slot per team. The league choice changes overall positional
demand, never a projected stat or scoring total. Positional order is identical
between 1QB and Superflex for the same reception rule.

## Historical evaluation

The fixed sample is the top 30 at each position according to a strictly prior
eight-appearance Half-PPR average. The same sample is used for all candidate
models and scoring formats. Missing actual stat lines remain ungraded.

2025 Weeks 2–18 matched 1,816 of 2,040 player-games; 224 were ungraded.

| Scoring | Model MAE | Recent-average MAE | Model RMSE | Recent-average RMSE |
|---|---:|---:|---:|---:|
| PPR | 6.0274 | 6.3643 | 7.7039 | 8.0233 |
| Half-PPR | 5.6046 | 5.9427 | 7.2066 | 7.5201 |
| Non-PPR | 5.2383 | 5.5746 | 6.7969 | 7.1075 |

Ranking pair accuracy was 61.78% PPR, 61.51% Half-PPR and 60.96% Non-PPR,
versus 61.68%, 61.24% and 60.65% for projection order. These are small gains.
There is no head-to-head accuracy claim against another fantasy provider.

Historical availability is limited to prior-week rosters and matched final stat
lines; it does not reconstruct every pregame injury report. 2024 depth uses the
preceding weekly snapshot; 2025 uses a timestamp before the first weekly kickoff.
Target and future game rows cannot change forecast features. The replay was also
verified identical using only 2023–2025 roster crosswalks, excluding current-season
identities. The public validation file records source digests, all training trials,
the fixed model parameters and the mechanical fumble-allocation correction made
after the initial holdout review. 2025 was never used to select a parameter.

## Reproduction and release

```bash
python scripts/capture_nfl_model_training.py --cache .cache/nfl-model-v2
python scripts/train_nfl_usage_model.py --cache .cache/nfl-model-v2 --frozen-parameters
python scripts/capture_nfl_week2.py --cache .cache/nfl-week2
python scripts/build_nfl_week2.py --cache .cache/nfl-week2
python scripts/test_nfl_usage_model.py
python scripts/test_nfl_week2.py
```

Compare downloaded source digests with the committed validation record; upstream
stat corrections can change a replay. Training is an explicit offline action,
not part of the daily refresh. The daily job uses the frozen committed settings,
requires a complete current capture and verifies file hashes before publication.
Forecasts for started games retain their original stats, status and forecast
timestamp. A fully started week is not rebuilt.

The v1.0 Week 2 artifact and original Week 1 forecasts/results remain unchanged.
Current readers load v2.0 and verify that ranking hashes match their projection
source. The Week 1 scorecard continues to use original committed pregame forecasts,
including the earlier opener snapshot; it is not a retrospective v2 backfill.

Reader changes are limited to the weekly boards, model-details page and current
Decision Room data. Season projections, season rankings, ADP, approved Wire
publications (86), and the development-only My Team/My League boundary are retained.
The regular news-only Wire update on main is preserved. Roll back by reverting
this feature commit; do not regenerate or replace historical forecasts.
