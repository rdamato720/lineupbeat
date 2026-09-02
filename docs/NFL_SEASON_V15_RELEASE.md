# NFL season v1.5 development release

The September 2, 2026 release replaces remaining QB/TE structural holds with one documented general allocation method and publishes all 505 active projections and corresponding PPR, Half-PPR and Non-PPR point rankings to the isolated development site. Production inputs remain unchanged. See `NFL_SEASON_V15.md` for the frozen methodology.

The numerical artifact was frozen before private FantasyGuru QA: SHA-256 `b2aa5bbf43ea98ae07919cdcf42a6d3bb8814227e8c2b2d964550016ae0fd6b2`. Numerical tuning after the comparison is prohibited. The independent projections show broad rank agreement, with meaningful player-level differences.

| Position | Active | Benchmark matches | Rank correlation | Mean absolute PPR difference | Mean signed difference |
| --- | ---: | ---: | ---: | ---: | ---: |
| QB | 87 | 84 | 0.8930 | 35.15 | -3.77 |
| RB | 114 | 112 | 0.8860 | 27.80 | +0.21 |
| WR | 183 | 172 | 0.9004 | 26.16 | -1.78 |
| TE | 121 | 113 | 0.9041 | 17.55 | -10.13 |

Coverage is 481/505, with zero ambiguous matches. Tight end totals are systematically lower on average. There are 262 material disagreements under the private QA rule of at least 30 PPR points or 12 positional ranks. These are disclosure items, not manual-adjustment targets. No proprietary benchmark values or file paths reach the public artifact.

All 32 teams reconcile within 0.01, all scoring formats reconcile from components, and every overall/positional rank is unique and follows the corresponding point projection. The frozen review contains 474 required queue entries and dispositions for all 505 active players: 268 approved, 144 corrected by the general formula, 93 explicitly disclosed low-evidence projections. This records computational review under Ralph's general-model authorization; it does not claim separate per-player human signoff.

The QB/TE general formulas change components for 208 players; 39 move at least 30 points in one format. The approved RB/WR components remain unchanged. Examples of PPR changes from v1.4: Charlie Kolar -58.9, Joe Flacco +52.4, Joe Burrow -52.4, Oronde Gadsden II +46.6, Jayden Daniels -45.2, Brock Purdy -44.6. The previous v1.4 ranks used replacement value; v1.5 uses the requested direct point order, so ranking movements also reflect that change in definition.

| Approved RB case | Team / role | PPR | Half-PPR | Non-PPR | Carries |
| --- | --- | ---: | ---: | ---: | ---: |
| James Cook | BUF RB1 | 251.6 | 234.4 | 217.2 | 248.5 |
| Tony Pollard | TEN RB1 | 191.7 | 169.8 | 147.8 | 227.1 |
| Rico Dowdle | PIT RB2 | 140.1 | 126.0 | 111.9 | 162.2 |
| Bhayshul Tuten | JAX RB1 | 186.7 | 170.0 | 153.2 | 205.6 |

Current injuries, refreshed ADP and season sportsbook props/futures are unavailable. Dated ADP and production/historical workload are QA context. Existing 60-second-delayed sportsbook market evidence for Week 1 is excluded from season inputs. Availability is not a guarantee of appearances.

## Development implementation and preservation

`build_nfl_v15_development.py --development` requires `DEV_PROJECT=lineupbeat-dev`. It uses the committed public feed snapshot, denies Python network connections, installs explicit season display adapters, and builds the existing College, Week 1, My Team and extension code unchanged. It does not write the production workbook or ranking JSON. `build_pages.py` recognizes the final model only under the explicit development environment switch. Default production behavior is unchanged.

The development workflow compares two complete artifact manifests, then validates final numerical payloads, player links, ranking rows, internal links, shared navigation, development protections and deployment artifacts. Source templates are kept outside the deployed directory to prevent protected output becoming the next build's input. Optional historical-database destinations clearly state when their source data is absent from the offline checkout. Superflex and dynasty keep their existing general format transformations using final season values and captured ages, with named-player editorial adjustments removed from this development build.

Reader-reaching changes are the development NFL season projection/ranking pages, season sections of canonical player pages, season comparison/draft-value data, and associated public season JSON and metadata. Approved Wire text, publications and identities are unchanged: 86 publications before and after. Raw evidence never enters the new season display payload. Week 1 and My Team recommendation gates remain disabled. College and extension sources, scoring configuration, ADP sources and roster source files are unchanged.

Finalization made zero provider/model/API requests and incurred $0 model/API cost. The earlier separately authorized The Rundown capture remains in its ignored audit cache: 234 terminal-test datapoints + 232 preserved-capture datapoints = 466 cumulative. No additional capture, polling, cursor or WebSocket was used.

## Local verification

Passed: v1.1, v1.2, v1.3, v1.4 and v1.4 final-validation regressions; 13 v1.5 model tests; identity/player-page/ranking-format tests; Decision Room, comparison and Comparison Engine tests; My Team and browser/ESPN diagnostics; College isolation; navigation; development safety; feedback and extension bundle tests; Wire review/mobile/core/homepage/TAPI/fixtures/health/doctor. All scoring, availability, 32-team allocation and queue-completeness checks are part of the v1.5 tests.

The full initial regression run passed 32/33 commands. `test_wire_page.py` has four obsolete homepage-card assertions against the current development Decision Room layout; the separate current deployment verifier confirms the complete 86-card reviewed archive and approved player-page impact wording. The retired interface was not restored.

The workbook has nine sheets, formula-driven three-format scoring and team sums, no formula errors, and completed visual QA. Desktop (1440px) and mobile (390px) automated browser checks cover all three formats, full ranking order, search, scoring values, representative primary/backup/rookie/low-evidence player pages, visible images, overflow, and development protections. Deployment status and final URLs are reported separately after the Action completes.

## Rollback

Revert only the scoped v1.5 release commit on `develop`, then push `develop` to rebuild the previous development version. Do not reset the user's unrelated work or touch `main`. Example: `git revert <v1.5-release-commit>` followed by `git push origin develop`.
