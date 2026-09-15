# NFL Week 2 release and Week 1 accuracy

The Week 2 board covers 418 current QB/RB/WR/TE players across 32 teams. Rankings and projections share one source, dated September 15, 2026, 15:54 UTC. Current depth charts, roster identity, injury statuses and Week 1 opportunity counts update the independent LineupBeat model. No outside projections are copied.

The model retains its historical team volume and efficiency priors, conserves team passing/carry/target workloads, and reconciles receiving yards/TDs to passing production. Matched Week 1 opportunity shares receive 20% weight. Current QB depth assigns 95%/4.5%/0.5% of passing opportunity to the first three depth positions before normalization, so a demoted QB's preseason share cannot outweigh the current starter. These are explicit model assumptions, not proven predictive improvements. Questionable and Doubtful do not reduce points. Confirmed unavailable players have zero projections; non-active roster players outside the resolved population are withheld.

No qualified current TheRundown capture was available. This release includes no betting adjustment and reuses no Week 1 market lines. The daily workflow now captures free NFL inputs for Week 2, preserves projections after individual game kickoffs, validates the candidate, and dispatches the existing production build when data changes. It no longer attempts the obsolete Week 1 / Odds API capture. The old Week 1 artifact remains unchanged.

## Week 1 comparison

| Format | Average absolute miss | Matched top 120 | Within 5 points |
|---|---:|---:|---:|
| Half-PPR | 6.4 | 119 | 60 |
| PPR | 7.0 | 119 | 53 |
| Non-PPR | 6.0 | 119 | 64 |

Half-PPR position error: QB 7.3, RB 6.7, WR 6.6, TE 5.1. Across the full population, 323 of 423 archived forecasts matched. Tua Tagovailoa is the single ungraded player in the top-120 sample. Missing statistical rows are not assigned zero.

The grader selects the last committed forecast before each game, including an earlier snapshot for the Wednesday New England–Seattle opener. It verifies stable GSIS identity, team and opponent against all 16 completed games. Top 30 at each position is selected by frozen projected points separately for each scoring format. Original forecast positions are preserved, including two-way Travis Hunter, whose actual source labels him CB.

Scoring includes passing, rushing, receiving, lost fumbles, two-point conversions and special-teams touchdowns. The nflverse supplied fantasy totals omit two return-fumble deductions (Jimmy Horn Jr. and Chimere Dike); the report applies the same lost-fumble rule as our model and reconciles that explicit difference. No yardage bonuses or custom league settings apply.

The public scorecard appears above both Week 2 boards, switches with scoring format, and links to `/nfl/week-1/results/`. The full result table supports player search, position, format and top-30/all-player filtering. The homepage, NFL navigation, Decision Room and season-page weekly links point to Week 2. Week 1 remains accessible as an archive. My Team/My League remain development-only.

## Verification and release boundaries

- Wire publication count: 86 before / 86 after. No Wire content changes.
- Sports-data calls during preparation: 10 free nflverse assets and 2 free ESPN requests. Zero paid API or model calls; $0 paid-provider cost.
- Reader-facing files: weekly projection/result JSON, weekly/results builders, shared navigation, homepage/Decision Room renderer and season-page weekly links.
- NFL Week 2 rankings/projections changed. Week 1 forecasts, season projections, ADP, scoring settings and roster source files remain unchanged.
- Rollback: revert this release's source/workflow commit and rebuild; Week 1 data is preserved.

Validation: 7 Week 2 release tests, 29 existing Week 1 tests, 10 workload tests, 8 daily-refresh tests, 30 Decision Room tests, 18 My Team tests, 7 comparison-tool tests and 8 public-release tests passed. Full offline site build passed. Final production pruning preserved both Week 2 boards, the Week 1 report, navigation and sitemap entries. Two earlier full builds differed in eight legacy player pages outside these weekly changes; no claim of whole-site byte reproducibility. Browser visual QA was not performed.

Final release review corrected a stale availability-test fixture that had blocked production since the feed changed Bowers from Doubtful to Out. The test now explicitly models a fresh lagging feed and verifies zero stats and points after the reviewed report. Navigation tests now expect separate Week 2 destinations. The deployment verifier checks each week against its own source, Week 2 against Decision Room, the distinct table columns, and the complete results archive after pruning.

All existing production Gates commands pass locally after those fixes. The final production artifact passes 877 sitemap routes, 919 HTML pages and 49,779 internal links, plus the complete artifact verifier. The homepage, NFL Decision Room, four weekly boards and results page are identical across two production rebuilds and pruning. Initial local attempts used the development target or lacked the rapidfuzz dependency; the final checks use the production target and the installed dependency. This browser cannot open the local preview; live interaction checks follow deployment. No new sports-provider or model requests during final review.
