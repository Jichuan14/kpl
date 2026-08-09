# Optimized advantage-score results

## Search design

The experiment tested 96 deterministic configurations drawn from variations
of Elo speed, season decay, evidence shrinkage, and logistic regularization.
The first five seasons were used for parameter selection. Season `20260003`
was not used until the configuration had been selected.

The objective was development AUC because the requested output is a relative
advantage ranking. Log loss was the tie-breaker. All coefficients were
constrained to be nonnegative so an evidence component can become neutral but
cannot silently reverse its displayed meaning.

## Best tested configuration

```json
{
  "elo_k": 16.0,
  "season_decay": 0.55,
  "familiarity_prior": 8.0,
  "synergy_prior": 48.0,
  "counter_prior": 20.0,
  "team_pair_prior": 120.0,
  "l2": 32.0
}
```

The high team-pair prior and strong L2 penalty confirm that sparse combination
history needs aggressive shrinkage. The lower Elo K makes team strength less
reactive to individual battle results.

## Untouched latest-season result

| Model | Log loss | Brier | Accuracy | AUC | Calibration error |
|---|---:|---:|---:|---:|---:|
| Elo only | 0.669511 | 0.238421 | 0.576023 | 0.628232 | 0.056323 |
| Team + hero familiarity | 0.669877 | 0.238600 | 0.587719 | 0.626889 | 0.054542 |
| Combined without mechanics | 0.669146 | 0.238249 | 0.573099 | 0.630780 | 0.056114 |
| Full optimized advantage model | 0.666453 | 0.236917 | 0.581871 | 0.639215 | 0.054908 |

The full score improves untouched-season AUC by `0.010983` over Elo and by
`0.008435` over the same historical model without mechanics. Its accuracy is
not the highest because AUC—not a fixed 50-point classification threshold—was
the optimization target.

## What the fitted model retained

Positive active signals:

- team strength;
- team/hero familiarity;
- mechanics-derived role coverage;
- heavily shrunk team-specific pair history; and
- historical directional counter performance.

Signals reduced to zero:

- hand-authored mechanics ally-compatibility rules;
- hand-authored mechanics counter rules; and
- league-wide pair residuals.

This is an important result: the broad mechanics catalogue helps through role
coverage, but the first hand-authored interaction rules do not add independent
predictive ranking power. They remain in the artifact and output as neutral
components rather than being represented as proven effects.

## Interpretation

The optimized score is a modest matchup-advantage indicator. On an untouched
season it ranks outcomes better than the team-strength and no-mechanics
baselines, but an AUC of `0.639` is not strong enough for deterministic winner
claims. Scores should be described as relative advantage, with small gaps
treated as effectively even.

