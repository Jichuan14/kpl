# Draft sequence-model POC results

## Outcome

The new `hybrid_app_lag_gru` is a viable improvement to the architecture used
by the current app. Across nine leakage-free matched rolling runs, it lowers
NLL in all 9 runs, improves top-1 in 7, and improves top-5 in 8. The mean gains
are `-0.0687` NLL, `+1.163` top-1 percentage points, and `+2.189` top-5 points.

For the motivating case—predicting two actions after an opponent pick—the
hybrid lowers NLL in all 9 runs and improves mean top-1 by `2.548` points and
top-5 by `2.848` points. This is evidence that the chronological branch learns
useful response context that the app's order-insensitive state cannot express.

The earlier `hybrid_bag_gru` remains the strongest model overall by NLL and
top-5. The current-app hybrid is the lower-risk integration path because it
preserves the app's existing score distribution as a frozen base.

## Model implemented

The current-app branch is a PyTorch architectural port of production schema v2:

- feature projection plus a learned residual for every candidate hero;
- the exact 20 `(action, side, team-action-number)` context categories;
- sum, max, and count features for own picks, opponent picks, own bans, and
  opponent bans;
- per-hero embeddings in those same four source roles;
- acting-team and opponent-team embeddings;
- a legal-candidate masked bilinear softmax.

That branch is trained first, selected by validation NLL, copied into the
hybrid, and frozen. The correction branch represents every prior action as:

`hero + action type + side + relation + absolute position + relative lag`

A one-layer GRU encodes the ordered sequence. Its legal-candidate logits are
centered and normalized before a state-dependent gate applies the correction:

`final_logits = frozen_app_logits + gate(state) * normalized_gru_residual`

The gate is bounded at `0.5` and uses draft position, history length, and the
frozen app model's prediction entropy. A scale penalty discourages the GRU from
overriding a confident baseline without evidence.

## Evaluation protocol

- PyTorch 2.10.0 on CPU with four threads.
- Three non-overlapping chronological 10-match holdouts.
- A separate preceding 10-match validation window for every fold.
- Seeds 7, 17, and 29 for every fold: nine matched runs per model.
- For older folds, every later match is excluded from training.
- Thirty epochs with best-validation-NLL checkpoint selection.
- Identical hero features, legal masks, recency weights, winning-pick weights,
  and conditional-softmax loss.
- The app port matches its Adam optimizer, `0.005` learning rate, batch size
  512, and weight-decay exception for `hero_bias`.

The deployed JSON artifact is not used as the holdout baseline because it was
trained on the holdout matches. Instead, the same app architecture is retrained
inside each fold. This is the clean comparison to the current method.

## All-model rolling comparison

| Model | Parameters | Mean NLL | Mean top-1 | Mean top-5 | CPU prediction |
|---|---:|---:|---:|---:|---:|
| Current app architecture | 25,691 | 3.0735 | 21.810% | 52.805% | 0.115 ms |
| Bag ablation | 21,579 | 2.9281 | **22.914%** | 54.653% | **0.109 ms** |
| Standalone GRU | 33,387 | 2.9367 | 22.086% | 55.274% | 0.227 ms |
| Pairwise response | 23,931 | 3.0041 | 21.491% | 54.208% | 0.143 ms |
| Frozen bag + GRU residual | 54,967 / 33,388 trainable | **2.9017** | 22.838% | **56.188%** | 0.348 ms |
| Frozen app + lag-GRU residual | 59,082 / 33,391 trainable | 3.0048 | **22.973%** | 54.994% | 0.485 ms |

Timings measure one already-prepared CPU prediction and exclude JSON parsing
and the app's full 100-rollout simulation. The app hybrid averages about `4.2x`
the current model's neural scoring time; its median is `0.408 ms` and the saved
single-split checkpoint measured `0.383 ms`. The sub-millisecond absolute
latency is small enough for a production-path benchmark.

The table also reveals a separate issue: the current schema-v2 architecture is
weaker than the richer POC bag control in this evaluation. Adding order helps,
but baseline representation capacity still limits the app hybrid. This is why
the frozen-bag hybrid remains stronger overall.

## Direct comparison with the current app method

| Metric | Current app | App + lag-GRU | Change | Matched wins |
|---|---:|---:|---:|---:|
| NLL | 3.0735 | **3.0048** | **-0.0687** | 9/9 |
| Top-1 | 21.810% | **22.973%** | **+1.163 pp** | 7/9 |
| Top-5 | 52.805% | **54.994%** | **+2.189 pp** | 8/9 |
| Mean CPU scoring | **0.115 ms** | 0.485 ms | +0.370 ms | — |

The NLL result is the most convincing: every seed and fold improved, and the
worst matched improvement was still `-0.0261`. Accuracy gains are useful but
less uniform and should be confirmed on more future matches.

## Two-actions-after-opponent-pick comparison

This slice contains about 330 holdout decisions per run and directly targets
the concern that a choice may respond to an opponent pick two actions earlier.

| Model | Lag-2 NLL | Lag-2 top-1 | Lag-2 top-5 |
|---|---:|---:|---:|
| Current app architecture | 3.0848 | 20.445% | 52.875% |
| Bag ablation | 2.9515 | 22.032% | 54.630% |
| Standalone GRU | 2.9512 | 22.230% | 56.330% |
| Pairwise response | 3.0642 | 19.324% | 53.736% |
| Frozen bag + GRU residual | **2.9053** | 21.974% | **57.804%** |
| Frozen app + lag-GRU residual | 2.9762 | **22.993%** | 55.723% |

Against the current app, the new hybrid improves lag-2 NLL by `0.1086` in all
9 runs, top-1 by `2.548` points in 8 runs, and top-5 by `2.848` points in 8
runs. It has the best lag-2 top-1 of the six models, although the frozen-bag
hybrid still has better calibrated probabilities and top-5 coverage.

## Recommendation and next improvements

Use the frozen-app plus lag-GRU model for a shadow integration experiment, not
an immediate replacement. Benchmark the complete 100-rollout simulator path
and collect a genuinely future holdout before changing production traffic.

The next highest-value changes are:

1. Distill or cache the GRU state during rollouts so unchanged draft prefixes
   are encoded once, reducing the observed scoring overhead.
2. Replace the gate's redundant position/history inputs with phase, next action
   type, side, and explicit recent-opponent-pick indicators.
3. Test a stronger frozen base—the `hybrid_bag_gru` result shows that improving
   the order-insensitive representation may matter more than a larger sequence
   encoder.
4. Calibrate on a future season window and report confidence intervals before
   using top-1 gains as a launch criterion.

The standalone pairwise model should not be advanced in its current form. Its
additive attributions are useful for explanation, but its predictive results
are inconsistent. The GRU is most useful as a residual branch rather than as a
standalone replacement.

## Verification and artifacts

- Nine focused unit tests cover order sensitivity, explicit lag sensitivity,
  exact app context selection, legal masking, baseline freezing, and residual
  equivalence at a near-zero gate.
- `artifacts/rolling_results.json` contains all six models and paired metrics.
- `artifacts/v2_rolling_results.json` contains the current-app comparison runs.
- `artifacts/v2_single/app_current.pt` and
  `artifacts/v2_single/hybrid_app_lag_gru.pt` are loadable PyTorch checkpoints.
- `artifacts/results.json` contains the merged single-split results.
