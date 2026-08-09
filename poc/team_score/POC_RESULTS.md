# POC results

## Outcome

The proof of concept is functional and promising, but it does not pass the
material-improvement gate for production use as a win probability.

It trained on 2,476 complete labeled battles across six chronological seasons.
Rolling validation evaluated 1,902 battles from five held-out seasons. Every
feature for a held-out battle was computed before that battle's result was
allowed to update history.

## Aggregate chronological validation

| Model | Log loss | Brier | Accuracy | AUC | Calibration error |
|---|---:|---:|---:|---:|---:|
| Intercept only | 0.693697 | 0.250274 | 0.507361 | 0.521422 | 0.022136 |
| Elo only | 0.665117 | 0.236419 | 0.594637 | 0.634117 | 0.023173 |
| Elo + familiarity | 0.664722 | 0.236263 | 0.593586 | 0.634144 | 0.025361 |
| Elo + synergy | 0.665725 | 0.236654 | 0.593586 | 0.633866 | 0.022088 |
| Elo + counter | 0.664523 | 0.236147 | 0.593060 | 0.635356 | 0.021301 |
| Full model | 0.664115 | 0.235924 | 0.597266 | 0.637432 | 0.024346 |

Compared with Elo alone, the full model improves:

- log loss by 0.001002;
- Brier score by 0.000495; and
- AUC by 0.003315.

The full model improves held-out log loss in four of five seasons. It regresses
in `20260001`, so the additional hero signal is not fully stable. Counter signal
is the strongest individual addition in this version. Familiarity adds a small
gain. Ally synergy alone does not improve pooled log loss, which confirms the
earlier data-sparsity concern and warrants a better synergy representation.

## Interpretation

The current score is useful for demonstrating the complete workflow:

- accept two teams and two legal five-hero compositions;
- compute team strength, familiarity, synergy, and directional counters;
- return complementary 0-100 scores;
- expose component contributions and evidence coverage; and
- fall back safely for unseen teams, heroes, pairs, or counters.

It should continue to display `proof_of_concept` and its warning. The release
gate requires at least 0.005 pooled log-loss improvement over Elo, plus stable
chronological behavior. This version reaches 0.001002.

## Best next experiment

Replace the single league-wide ally-pair residual with a hierarchical synergy
feature combining hero mechanics, role coverage, league pair history, and a
heavily shrunk team-specific adjustment. Keep the current counter and Elo
features as baselines, then rerun exactly the same rolling validation.

