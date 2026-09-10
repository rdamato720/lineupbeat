# NFL Week 1 workload repair — September 10, 2026

## Problem and change

The published model lost team opportunity volume after filtering the season
population to the active weekly roster, blending player-specific historical
shares, applying extra depth discounts, and zeroing unavailable players.
Passing and receiving output were also calculated independently. The model
gave even a tiny player-efficiency sample the same 70% historical weight as a
full sample.

This change allocates the existing 70% 2025 / 30% 2024 historical team attempt,
carry, and target budgets across available players. It retains relative
current-role weights, removes the duplicate non-QB depth multiplier, and keeps
QB reserve discounts as relative weights. Targets cannot exceed team passing
attempts. All receiver yards and receiving TDs reconcile with team passing
yards and passing TDs after the bounded private market adjustment step.
Reconciliation can move a final component beyond its standalone market blend;
the two steps are recorded separately and are not represented as the same cap.

Historical efficiency now approaches the existing 70% ceiling at 200 pass
attempts or 100 carries/targets. Below those thresholds, the reviewed current
efficiency prior has more weight. These thresholds are explicit modeling
assumptions, not empirically optimized parameters.

A depth-chart RB1/RB2/RB3 with fewer than one modeled carry receives a fallback
from the observed 2024/2025 median workload-rank share before normalization,
using league RB rushing efficiency. The historical pool contains 1,088 team
games, includes zero third-back workloads, and produces a 20.34% RB2 share of
team carries. In this snapshot the fallback applies only to Sione Vaki. It
prevents the absent backup prior from routing effectively all Detroit RB work
to Gibbs. Workload rank is a fallback for depth rank, not proof of an exact
future usage split. No player-specific values were chosen to match another
projection source.

## Candidate, inputs and limits

- Same 421 stable player identities, 32 teams and 16 games as the source snapshot.
- Source market timestamp remains September 10, 2026 at 14:03:10 UTC. Existing
  injury timestamps and reviewed game-specific reports remain intact.
- The repair uses the committed owned stat lines, their recorded bounded
  consensus inputs, and SHA-256-verified 2024/2025 team and player statistics.
  It reverses the old bounded blend where necessary, updates the own-model
  rate, and reapplies the same recorded consensus. The source had three-decimal
  stat rounding, so this replay has that precision limit.
- No newly fetched market, roster or injury data. Zero new provider or model
  API calls and zero incremental provider/model cost. Existing provenance call
  counts describe the original capture, not this repair.
- No uploaded comparison CSV is read by the builder, allocation module or repair.
- The unchanged historical backtest is explicitly a proxy context backtest;
  it does not validate this production formula or establish predictive lift.
- Repairs are applied to the shared weekly source for rankings, projections,
  comparisons, Decision Room and the development My Team consumer. Season
  projections, season rankings, ADP, scoring rules and roster sources are unchanged.

## Selected PPR changes

| Player | Before | Candidate |
| --- | ---: | ---: |
| Jacoby Brissett | 8.8 | 13.0 |
| Jayden Daniels | 13.4 | 15.5 |
| Kyler Murray | 12.9 | 14.0 |
| MarShawn Lloyd | 4.0 | 8.9 |
| Travis Etienne Jr. | 9.4 | 11.3 |
| Rhamondre Stevenson | 8.1 | 12.4 |
| Omarion Hampton | 14.3 | 14.8 |
| Jonathan Taylor | 20.4 | 20.9 |
| Jahmyr Gibbs | 22.1 | 24.7 |
| Matthew Golden | 4.5 | 9.9 |
| Kayshon Boutte | 3.9 | 4.5 |
| Brock Bowers | 0.0 | 0.0 |

374 players change PPR points. The candidate is intentionally not calibrated
to the uploaded forecasts; several original disagreements remain.

## Verification and release boundary

104 tests pass across weekly intelligence (including 10 new allocation/rate
regressions), daily validation, reviewed availability, Decision Room,
comparisons, My Team, weekly market inputs and ESPN injury inputs. The daily
publication validator also accepts the repaired candidate against the actual
previous snapshot without relaxing freshness, identity or volatility gates.

The new publication checks independently sum output stats. All 32 teams
conserve attempts, carries and targets and reconcile receiving to passing
production within three-decimal rounding tolerance (maximum yardage difference
0.002). Confirmed unavailable players retain zero stats and points. New tests
cover out-player redistribution, repeated application, missing allocation
weights, tampered totals, duplicate depth discounts, sparse roles, small
efficiency samples, and bounded-blend replay.

Rankings and projections were each built twice with byte-identical output.
Both contain all 421 players and retain Bowers at zero. Decision Room and
My Team builds consume the same repaired source. The complete live provider
capture/build was not rerun; the candidate is a documented offline repair of
the current snapshot, and the normal builder has the same allocation/rate
logic for its next capture.

No Wire publication is created or edited. No deployment is performed by this
change preparation. Roll back by reverting this change as a unit (model,
validator, availability integration and weekly snapshot), then rebuilding
readers from the restored snapshot. Do not revert only the snapshot while
leaving the new consistency gate expecting allocated output.
