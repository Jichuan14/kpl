# Version 2 results

## Verdict

Version 2 is implemented and works, but the improvement over version 1 is
negligible. It remains experimental and is not release-ready.

The same 1,902 battles and five rolling season holdouts used by version 1 were
used here. Historical statistics were computed before each battle outcome was
observed.

## Selected scoring configuration

The exploratory validation selected these active features:

- team strength;
- team/hero familiarity;
- mechanics-derived role coverage;
- league-level ally-pair synergy; and
- directional counter advantage.

Damage diversity, control/mobility, sustain/support, and team-specific pair
adjustments remain in the experiment artifact but are inactive in the scoring
model because they did not improve the rolling aggregate.

| Model | Log loss | Brier | Accuracy | AUC | Calibration error |
|---|---:|---:|---:|---:|---:|
| Elo only | 0.665117 | 0.236419 | 0.594637 | 0.634117 | 0.023173 |
| Version-1 equivalent | 0.664115 | 0.235924 | 0.597266 | 0.637432 | 0.024346 |
| Selected version 2 | 0.664066 | 0.235883 | 0.597792 | 0.637944 | 0.026115 |
| Version 1 + team pair | 0.664387 | 0.236023 | 0.596215 | 0.637312 | 0.024520 |
| Every V2 candidate | 0.666953 | 0.237188 | 0.592534 | 0.632983 | 0.028023 |

Selected V2 improves log loss over the version-1-equivalent model by only
`0.000049`. It improves four of five held-out seasons but does not meet the
predeclared `0.002` material V2-gain threshold. Its calibration error is also
slightly worse.

## Finding

Role coverage is the only new synergy layer with a positive aggregate result,
and the gain is extremely small. The team-specific pair layer remains too
sparse even with strong shrinkage. Combining all mechanics makes the result
worse, especially in earlier seasons, suggesting patch-specific interactions
or an overly coarse hand-designed composition summary.

The V2 scorer therefore keeps the better-performing role coverage signal and
assigns no coefficient to the unsuccessful experimental layers. The detailed
validation remains available so this decision is auditable.

