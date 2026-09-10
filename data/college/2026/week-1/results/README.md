# College Week 1 results

The scorecard compares the immutable Week 1 v1.1 forecast with ESPN final
box-score offensive statistics saved during Week 2 preparation. It does not
modify either weekly forecast.

The forecast SHA-256 is recorded in actuals.json and checked on every build.
GitHub commit df83ea2a36b10abcb0c195f3f326255b6990d440 stored the complete
forecast at 2026-09-03T16:09:22Z, before the first scheduled kickoff at 22:00Z.
Each team's actuals retain its ESPN event ID, original box-score URL, final
game state and SHA-256 of the saved raw response. The normalized file retains
only the passing, rushing and receiving components used in the forecast.

Match rules: same scheduled ESPN event ID; exact team and player name after
Unicode/case/apostrophe normalization. Pittsburgh/Pitt is the existing team
alias (ESPN ID 221). A delayed actual kickoff does not change event identity.
Duplicate identities fail. Frozen position and rank are not retrospectively
corrected. Missing offensive stat lines remain ungraded, never assumed zero;
this includes inactive players and some players with no recorded offense.

Scoring is 0.04 passing yards, 4 passing TDs, -1 interception, 0.1 rushing or
receiving yards, 6 rushing or receiving TDs and 1 reception. Fumbles, return
scores, conversions and bonuses are excluded from both sides of this comparison.
Use the one-decimal published projection, unrounded component-weighted actuals
rounded to two decimals, and mean absolute error. Display metrics to one decimal.

The headline sample is the pregame top 30 at each position: 110 of 120 graded.
All 64 modeled teams have final box scores. The full matched set contains 807
of 2,205 projected players. The report explicitly discloses excluded players
and does not claim complete-player or percent accuracy. This is descriptive
performance of one week, not evidence that the model beats a benchmark.

Reproduce using scripts/college_projection_results.py evaluate(); the weekly
page builder calls the same evaluator for its strip and results page. Tests:
python scripts/test_college_projection_results.py. Rollback removes the strip,
results-page builder and sitemap entry together; forecast releases are untouched.
