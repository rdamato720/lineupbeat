# College fantasy projections, 2026, v1.1

Immutable. This release is derived reproducibly from the published v1.0
provenance and does not alter `2026/v1.0`.

The sole numerical change is
`RB_Final_Room_Concentration_Calibration_v0.1`: each backfield below a 79%
top-two carry share is concentrated to 79% while preserving every frozen team
carry, rushing-yard and rushing-touchdown budget. The aggregate top-two share
moved from 67.0988% to
79.7258% across
54 adjusted teams.

Manifest SHA-256, pinned by the site builder:

    4c2f35fec2eaa3d43d2e18a2956d3118a20a17805ff1d4c74989a5cc069d6eb0

## Reproduce

```bash
python3 scripts/generate_college_v1_1.py \
  --generated-at 2026-08-24T15:05:00+00:00 \
  --output /tmp/college-v1.1
```

## QA

- 2,351 players across 68 teams.
- 15 reconciliation gates: `PASS`.
- Largest reconciliation delta: `9.094947017729282e-13`.
- No duplicate players, multi-team players, negative values, or blocking
  failures.
- WR/TE player-level receiving efficiency and fantasy-platform eligibility
  remain explicitly unestablished.

## Files

| file | bytes | SHA-256 |
|---|---:|---|
| `college_site_projections_2026.json` | 1,163,067 | `740cf753e192925d083412229caaa1553038aefb101dc07409597a100077e921` |
| `provenance/college_player_projections_2026_v1.1.csv` | 784,491 | `afc62d4fb090346d97abe1e15d11719cc7a275728d6218feb1aceb69b416f5a5` |
| `provenance/college_team_projections_2026_v1.1.csv` | 9,833 | `9f8aff4392e29a11884d3d6d8e6dfad185a2d8b554cc68df0b7a67122633c63b` |
| `provenance/college_projection_qa_v1.1.json` | 2,197 | `408b4cc5034f13474d24aee723d70a6b90f78c0048a5359efa1bb947a8788541` |
| `manifest.json` | 3,274 | `4c2f35fec2eaa3d43d2e18a2956d3118a20a17805ff1d4c74989a5cc069d6eb0` |
