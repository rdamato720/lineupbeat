# NFL Week 2 target allocation, v2.1

This revision uses free, already-captured offensive snaps, target/carry shares,
pregame depth and teammate competition to estimate receiving opportunity.
It does not copy expert forecasts or infer routes from snaps. All three scoring
formats use one reconciled stat line. Rankings remain separate, with 1QB and
Superflex available in each format.

## Model and safeguards

Later-2024 selection chose a 50% blend of a fitted RB/WR/TE target-share estimate
with the prior allocation. It has 100 regression trees, maximum depth 3, with
at least 40 observations per leaf, fitted on 10,126 matched player-games.
Portable inference matches the training library within 1e-10 on every training
feature vector. Daily inference needs only the Python standard library.

The learned estimate requires a recorded appearance in the team's previous
game. Missing observations retain the previous role; recorded zero offensive
involvement remains distinct from missing data. QB allocation stays separate.
Questionable/Doubtful tags do not discount projections; confirmed unavailable
players remain zero, with available teammates sharing the opportunities.

The finite search also considered learned carry allocation; it was not selected.
Team volume and efficiency retain their previously tested settings. This is an
improvement to one part of the model, not a fix for every reference discrepancy.

## Evaluation

Selection fits on 2023 Weeks 2–18 plus 2024 Weeks 2–9, chooses depth and blend on
2024 Weeks 10–18, then refits on all of those 2023–2024 weeks. Feature histories
stop before the forecast week. Names and external predictions are not features.

2025 was previously inspected, so these are regression results, not a new
untouched holdout. An initial candidate was inspected on 2025 and the current
slate. That review prompted the missing-input fallback and QB boundary. Settings
were selected again on 2024, then frozen before the guarded 2025 replay.

| Scoring | Published MAE | v2.1 MAE | Published RMSE | v2.1 RMSE |
|---|---:|---:|---:|---:|
| PPR | 6.0274 | 5.9908 | 7.7039 | 7.6761 |
| Half-PPR | 5.6046 | 5.5761 | 7.2066 | 7.1880 |
| Non-PPR | 5.2383 | 5.2192 | 6.7969 | 6.7860 |

The fixed sample matched 1,816 of 2,040 player-weeks; 224 remain ungraded. It
uses the pregame top 30 per position by prior eight-appearance Half-PPR mean,
not eventual top scorers. Gates require no overall MAE/RMSE increase in any
format and no position MAE increase above 0.15 points. All passed. Early-season
2025 Weeks 2–4 PPR MAE improved from 5.5539 to 5.5037 across 329 matches.
These small differences do not establish statistical or prospective superiority.
The sample underrepresents new starters and cannot reconstruct every injury.

Ranking weights are selected separately on 2024 ordering: 85% expected points,
15% prior scoring history. Superflex changes overall replacement value, not
scoring or within-position order. Week 1 original forecasts and grading remain.

## Reproduce and release

Training: scikit-learn 1.8.0 and NumPy 2.3.5. Inference: standard library.
Input sources and digests are recorded in capture/validation manifests.
Frozen original settings are in `data/nfl_weekly/model-v2/baseline-v2.0.json`;
the prior release is commit `0bb8aed173b133ffa11e7da16f96f53feddc70e7`.

```bash
python scripts/train_nfl_workload_model.py --cache PATH_TO_CAPTURE --output audit/workload-replay
python scripts/train_nfl_workload_model.py --cache PATH_TO_CAPTURE --output audit/workload-replay --verify-frozen
python scripts/test_nfl_workload_model.py
```

The release builder requires matching fitted-model, feature/inference and
validation hashes plus passed gates. The final integration replay reproduced
the frozen 2024 and 2025 summaries. The existing daily refresh, complete-slate
checks and kickoff locks apply. All 500 roster identities remain covered.

No current routes, weather or qualified TheRundown snapshot is added. Paid/API
model calls and cost are zero. No vendor contact or purchase occurred. Approved
commentary publications remain 86. News sources, approval records, ADP, scoring
rules and roster sources are unchanged. Reader changes are Week 2 projections,
rankings, dependent player displays and methodology.

Rollback this release's model, code, data and methodology changes together.
After kickoff, retain the locked pregame rows; do not reconstruct a past game's
forecast from later observations or replace Week 1 grading inputs.
