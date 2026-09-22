# NFL usage inputs and role revision — September 15, 2026

Snap counts and team play-by-play are already captured for all 16 completed
2026 Week 1 games. Current route counts are not in that capture.

| Input | Available now | Source/use |
|---|---|---|
| Offensive snap count/share | Yes | PFR via nflverse; capture includes all 16 games. |
| Team run/pass rate | Yes | Derivable from existing team stats and play-by-play. |
| Neutral-situation passing tendency | Yes | Existing play-by-play reducer; definition must be stated when comparing providers. |
| Player targets/carries and team shares | Yes | Player and team weekly statistics. |
| Goal-line carries, red-zone/end-zone targets | Yes | Existing play-by-play reducer. |
| Routes run / route participation | No current feed | Cannot be inferred from offensive snaps or target counts. |

Sources checked:

- [nflverse snap counts](https://nflreadr.nflverse.com/reference/load_snap_counts.html)
  exposes offensive snaps and offensive snap share.
- [nflverse participation](https://nflreadr.nflverse.com/reference/load_participation.html)
  from 2023 onward is delivered after the postseason; it is not a current-week
  route feed. The separately available
  [FTN charting subset](https://nflreadr.nflverse.com/articles/dictionary_ftn_charting.html)
  does not contain every player's routes run.
- [FTN's commercial catalog](https://ftnfantasy.com/ftn-data-nfl-catalog)
  includes skill-player IDs and roles on plays, including route and blocking
  roles. This supports a route-participation integration after the actual feed,
  identity mapping and field semantics are verified.
- [FTN's commercial data FAQ](https://ftnfantasy.com/stats/sports-data)
  lists charting from approximately 24 hours after games, APIs and revision
  indicators, and a commercial starting price of $5,000 annually. The advertised
  individual plans are not proof of commercial API access. No plan was bought,
  no account was changed, and no vendor was contacted.

## Experiment completed

The isolated `scripts/nfl_role_candidate.py` tests three factual usage hypotheses:
weaker carryover for players changing teams, current-QB passing attempts based on
games with at least 80% offensive participation, and current-QB touchdown rates.
The existing direct snap-share coefficient was also reconsidered on 2024 only.

The finite search selected transfer factor 1, QB volume weight 0.25, QB TD weight
0.5 and direct snap weight 0. The previously inspected 2025 season was then
replayed as a regression check. It is not described as a new untouched holdout.

| Format | Published MAE | Candidate MAE | Matched player-weeks |
|---|---:|---:|---:|
| PPR | 6.0274 | 6.0290 | 1,816 |
| Half-PPR | 5.6046 | 5.6072 | 1,816 |
| Non-PPR | 5.2383 | 5.2413 | 1,816 |

The candidate failed the predefined requirement that overall MAE and RMSE not
increase in any scoring format. The differences are tiny, but provide no
evidence of an improvement. The experiment remains research-only. The production
model, model hashes, forecast files, ranking files and daily capture remain
unchanged. No expert projection rows are inputs to the experiment.

Reproduce with:

```bash
python scripts/test_nfl_role_revision.py
python scripts/evaluate_nfl_role_revision.py --cache PATH_TO_EXISTING_CAPTURE --output audit/role-revision
```

Four targeted tests cover future-data exclusion, team-volume conservation,
current-team role effects, and exclusion of unavailable quarterbacks from the
replacement passing environment. Replaying after isolating the candidate from
production reproduced the same evaluation results.

The remaining work is a stronger current-role model and a qualified current
route feed if an affordable option is available. Having snap data is not itself
proof that applying a larger snap multiplier improves projections.
