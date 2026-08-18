# College fantasy projections, 2026, v1.0

Immutable. Nothing here is edited after publication; a correction
becomes `v1.1` rather than a change to this directory, so a page can
always be reproduced from the release it was built against.

**The build reads two files:** `college_site_projections_2026.json`
and `manifest.json`. Everything under `provenance/` exists so a number
on the page can be traced back to the model that produced it, and must
never be imported by frontend code or shipped to a browser.

Manifest SHA-256, verified at build time:

    01b87ca7a3abdec0c5ab0e11b162f5edc7305ee843d44e28d6065b82aa90ea7a

## Contents

| file | rows | bytes | purpose |
|---|---|---|---|
| `college_site_projections_2026.json` | 2351 | 905,612 | page input. The only file the frontend reads. |
| `manifest.json` | - | 2,956 | page input. Frozen models, QA status, product claim, limitations. |
| `provenance/college_player_projections_2026_v1.0.csv` | 2351 | 777,679 | provenance. Full player stat lines, all 33 columns. |
| `provenance/college_projection_qa_v1.0.json` | - | 2,265 | provenance. Fifteen reconciliation gates and their deltas. |
| `provenance/college_team_projections_2026_v1.0.csv` | 68 | 9,902 | provenance. Frozen team totals every player row reconciles to. |

### SHA-256

```
b3553fc64e62c6915d3a0f555b03211d0b7e13969dbaa736f46c3c219257e2ad  college_site_projections_2026.json
01b87ca7a3abdec0c5ab0e11b162f5edc7305ee843d44e28d6065b82aa90ea7a  manifest.json
bcce6dc122331d21f78c0d7268b0d9d4cf6e08aa22719e69e0efe8bb3493d974  provenance/college_player_projections_2026_v1.0.csv
e1733b388413f1ddf4c45df1859a3dcb41a1e444400fbeaf619bfdbba4ca20bc  provenance/college_projection_qa_v1.0.json
e3b8282b31cb80b67492bd90b464e164b7030b7b4054b144b0748924cabb5768  provenance/college_team_projections_2026_v1.0.csv
```

## What this release establishes

2,351 players across 68 teams, four positions, built from fifteen
frozen models. Fifteen reconciliation gates hold to twelve decimal
places: every player total sums exactly to the frozen team figure it
came from.

Quarterback passing and rushing efficiency, and running back rushing
efficiency, carry calibrated player-history adjustments. Receivers and
tight ends are differentiated by projected opportunity and share their
team's receiving rates. That is a real difference in model depth,
stated on the page rather than implied away.

## What it does not establish

**Platform eligibility.** Positions come from school roster listings
via CFBD. No player carries a Yahoo id, so no position has been
checked against any fantasy platform. `platform_eligibility` is blank
on every row and is never inferred from the roster position. The
product uses Yahoo scoring, not Yahoo eligibility.

Twenty-two players have rushing production material to their value,
including a tight end whose carries decide his rank. Their carries and
rushing yards appear in the published tables so the ranking explains
itself.

## Known limitations, each with a named v1.1 task

- **No player-level receiving efficiency** for WR and TE.
- **No projected targets.** Receptions are allocated directly by the
  frozen models. Targets were never modelled and are not derived:
  invented targets would weaken an otherwise exact release.
- **Flatter running back tail** than historical backfields, with
  top-two concentration at 67.9% against 79-81%.
