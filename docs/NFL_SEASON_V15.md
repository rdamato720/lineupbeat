# NFL season model v1.5 final

This is the final 505-player development release using the validated September 2, 2026 local input capture and approved v1.4 identity/availability work. It makes no provider requests. Main and production are not authorized deployment targets. Week 1 and My Team recommendations remain disabled.

## Quarterbacks

Select the primary by exact current offensive-depth rank, then established passing-attempt prior, historical per-game attempts, and stable GSIS id. This hierarchy also handles rookies and teams lacking exact depth evidence. The same calculation applies to all 32 teams.

Let `a = primary expected active games / 17`. Expected active games are the unchanged v1.4 historical snap-appearance availability estimate, not an ACT-to-17 assumption. Let `r` be limited relief attempts per team game observed for current backups in 2023–2025: a backup attempted 1–5 passes in a game where another QB attempted at least 15. Recency weights are 20%, 30%, 50%. This is limited relief context, not a claim of a designed passing package. `q = min(0.02, sum(r) / (team season attempts / 17))`.

Primary passing share is `a × (1 − q)`. Conditional share while active is `1 − q`; missed games do not dilute that conditional role. The backup absence pool is `1 − a`, and the separate limited-relief pool is `a × q`. The absence pool is distributed using 65% normalized established backup-attempt priors plus 35% exponential current-depth weights, or 100% depth weights if all backup priors are zero. Relief is distributed only to its observed historical participants. Backup opportunity remains nonzero. No individual QB is overridden.

This replaces the old season-share calculation; it does not multiply old expected-season player projections by availability. Team attempt, completion, passing-yard, passing-TD and interception budgets stay fixed. Completions are capacity-bounded by attempts. Existing per-attempt efficiency relationships distribute the remaining passing budgets. Original QB carries, rushing yards and rushing TDs remain unchanged, with designed rushes and scrambles reported separately from historical PBP rates; missing classification remains explicit.

The correction can lower a QB's season total when the old allocation implied an impossible healthy-game share. It does not guarantee a boost to every QB1. The model distinguishes complete active appearances from starting-game coverage, and the limited history proxy remains a disclosed limitation.

## Tight ends

Preserve every team's v1.4 TE-room targets, receptions, receiving yards and receiving TDs. RB and WR components remain unchanged. For each TE, the primary opportunity input is established expected-season targets; otherwise historical targets per active game times expected active games once; otherwise the current-depth share of the TE room. Current depth has exponential decay `exp(−0.9 × (rank − 1))`, with missing depth assigned fallback rank 6.

Normalize these primary inputs within each room and blend 80% primary opportunity with 20% normalized current-depth share. Historical snap participation contributes zero numerical weight: it remains context, removing overlapping historical usage/snap influence. Existing per-target efficiency relationships distribute TE receiving yards and TDs; receptions are bounded by targets. Prior-supported multi-TE usage is retained. Every team uses the same weights and fallback hierarchy. No player-specific adjustment is permitted.

## Availability and scoring

Expected active games and availability rates remain exactly those approved in v1.4. Prior season totals are already expected totals and are not discounted a second time. Fallback historical per-active-game targets are multiplied by expected active games once. Conditional per-active-game rates are computed from season totals divided by expected active games, never by mechanically dividing all players by 17.

Passing yards score 0.04, passing TDs 4, interceptions −2, rushing/receiving yards 0.1, rushing/receiving TDs 6, and lost fumbles −2. Receptions score 1 / 0.5 / 0 in PPR / Half-PPR / Non-PPR. Decimal arithmetic reconciles scoring exactly from six-decimal components. All 32 team budgets must reconcile within 0.01; components are nonnegative and opportunity shares cannot exceed 100%.

## Rankings, review and private QA

All 505 active players receive unique overall and positional ranks in each format, sorted by the corresponding projected points, with stable GSIS id as the tie-breaker. These season-output rankings apply no manual or editorial boosts. Overall ranks are explicitly points order, not replacement-value draft rankings.

The final queue covers every active player and evaluates every required trigger: remaining Tier 1, corrected QB/TE, ≥30 points, ≥24 ranks, top-12/24/36/48 crossings, the four explicitly requested RB cases, missing prior and missing current depth. Dispositions apply Ralph's instruction-level authority and computational review; they do not claim an additional per-player human signoff. Historical inactive players are explicitly excluded.

Freeze the model and inputs by SHA-256 before opening FantasyGuru. FantasyGuru is a private QA benchmark only; no proprietary values or file paths enter site artifacts. Compare match coverage, within-position rank correlation, signed/absolute differences and material disagreements. Do not tune the frozen output after seeing that comparison. The user's “close but our own” direction means broadly comparable outcomes with defensible differences, not per-player copying or fixed-band enforcement.

Current injuries, refreshed ADP, season sportsbook props and futures are unavailable in this version. Dated ADP is QA only. The isolated 60-second-delayed Week 1 game-market capture is not a season input. Missing evidence adds uncertainty, and rankings are model outputs rather than guarantees. No predictive superiority or sportsbook validation is claimed.
