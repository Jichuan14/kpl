# Implementation specification: BP probability calibration

Status: planned. Scope: the current `sequence` behavior policy, reusable by the new policy in [02_DRAFTREC_MODEL.md](02_DRAFTREC_MODEL.md).

## 1. Objective

Make next-action probabilities more representative of observed outcomes while preserving the model's candidate ordering. These probabilities drive both displayed predictions and sampled draft completions, so calibration belongs in the shared scoring path.

Implement **one global positive temperature per exact model and candidate policy** first. Phase-specific temperatures, recent-meta blending, isotonic calibration, and training-loss changes are excluded from this first implementation. They can be separate experiments after the global baseline is complete.

The investigation found an August offline NLL improvement from 3.1002 to 3.0207 and 10-bin confidence error from 12.55% to 5.03%. This motivates the work; those numbers are not acceptance promises. Temperature scaling is described by [Guo et al., 2017](https://proceedings.mlr.press/v70/guo17a.html).

## 2. Exact scoring contract

Let `z[h]` be the final finite score for candidate hero `h`, after all active model branches, priors, and soft adjustments. Let `L(s)` be the candidates accepted by the selected serving policy for state `s`.

```text
p(h | s, T) = exp((z[h] - max(z[L])) / T)
              / sum_j_in_L exp((z[j] - max(z[L])) / T), h in L
p(h | s, T) = 0, h outside L
```

Requirements:

- `T` is finite and strictly positive. Compute the denominator stably.
- Apply temperature exactly once, after assembling scores and selecting the final candidate set, before softmax/sampling.
- Do not calibrate only the visible top-k list. Normalize over the full accepted set, then truncate for display.
- Do not change logits inside the individual frozen bag/GRU branches separately.
- `T=1` must reproduce the existing path within float32 tolerance and use the existing deterministic tie ordering.
- One remaining candidate has probability 1. Empty sets retain the existing explicit error/empty-result behavior, not NaN probabilities.
- Candidate IDs/order, team context, series context, and hero features must agree between fitting and inference.
- User-specified candidate subsets can use the same temperature mathematically, but their calibration is unvalidated unless represented in evaluation. Report `custom_candidate_subset` metadata rather than claiming guaranteed calibration for arbitrary restrictions.
- Do not apply this calibration to lineup advantage, win probabilities, ban-value outputs, or statistical/learnable policies without separate fitting.

Prefer a pure `masked_softmax(logits, mask, temperature)` helper plus a separate resolver for the appropriate temperature. Resolve once per request and reuse within rollouts; do not parse JSON or hash artifacts for every simulated action.

## 3. Candidate-policy coverage is part of evaluation

`draft_simulator._legal_heroes` currently adds inferred role constraints to game availability. The earlier investigation found observed picks removed by this policy. A temperature cannot restore a candidate with zero probability.

Keep this implementation bounded:

1. Name and fingerprint the current policy as `legacy_role_filter_v1` (proposed ID; verify it is unused).
2. Implement offline replay of that exact policy. Count every usable observed choice, including excluded targets.
3. Also support a diagnostic `game_availability_v1` policy using board exclusions, provided game availability, and appropriate Global-BP rules without the extra inferred role filter. This is an experimental evaluator choice, not an automatic change to serving defaults.
4. Never silently add a selected hero back to a serving mask, omit its row, or cap its NLL to claim a finite overall score.

If any calibration-window target is excluded, the unconstrained full-sample NLL is infinite for every `T`. Return `not_fit_due_to_candidate_exclusion` for that policy, with IDs/counts. Continue implementing/testing the fitter on the diagnostic policy and valid fixtures. A conditional-on-coverage temperature may be computed only as a clearly labeled research diagnostic and is not an eligible production calibrator.

If exclusions occur only in evaluation, preserve the fitted temperature but mark promotion ineligible and report infinite full-sample NLL. Do not substitute the diagnostic policy during live scoring. Resolving role filtering is a separate candidate-policy change that requires a new fingerprint, evaluation, and calibration.

This allows useful calibration engineering to finish without quietly expanding the assignment into a role-model rewrite.

## 4. Chronological windows and checkpoint lifecycle

Use complete series and their verified timestamps. Default partition of the target season's eligible series:

```text
earlier training series | V: 10 series | C: 10 series | H: 10 series
```

- Earlier eligible seasons may contribute to training according to a pinned explicit list.
- `V`: checkpoint/epoch selection only.
- `C`: fit temperature after model selection; do not use it to select architecture or model weights.
- `H`: evaluate the chosen model plus temperature once per locked experiment.
- Require at least 10 target-season training series and all three windows. If insufficient, create unit/smoke fixtures and a report of insufficient evidence; never silently split actions from one series across windows.
- Development calibration windows should have at least 200 usable decisions and include both picks and bans. Treat this as a coverage gate, not a statistical guarantee.
- For rolling evaluation use three non-overlapping 10-series H windows where feasible, with preceding V/C windows. Record overlap of training/validation windows across folds. Use one actual match series only once as an H observation in pooled confidence intervals.
- Exclude all later series from each older training fold. Equal-start-date groups must not straddle adjacent windows; move a boundary earlier as needed and record actual sizes.

Validate timestamps across all included seasons, not just their numeric IDs: no training record may occur at or after the V boundary. For a claimed chronological legacy-role-policy comparison, build its role map from training-only observations plus explicitly versioned prior metadata. A current full-season map can be replayed as an operational diagnostic, but cannot silently supply future role evidence to an earlier holdout. Apply the same provenance rule to any fitted priors or feature normalizers. If historical catalogue snapshots do not exist, disclose the fixed-feature-vintage limitation.

**Critical release rule:** a fitted temperature belongs to the exact frozen model that produced its calibration logits. The current `--train-on-all-data` path refits the neural model after evaluation. It must not inherit the earlier temperature automatically.

Supported lifecycle for this work package:

1. Train on the training block; choose checkpoint using V.
2. Freeze that checkpoint, fit T on C, evaluate on H.
3. Export that exact checkpoint and its eligible sidecar as a candidate.

If a later release is refit on training+V+C+H, its old sidecar becomes invalid. Export it with calibration disabled/T=1 until genuinely out-of-training predictions are available for fitting that exact release. Out-of-fold estimates from other weights are research evidence, not an exact release calibration. No downloads are needed to implement this lifecycle using existing historical folds.

## 5. Fitter and saved predictions

Fit only temperature, never neural weights:

```text
objective(log_T) = mean_i [ logsumexp(z_i[L_i] / exp(log_T))
                          - z_i[y_i] / exp(log_T) ]
```

Implementation defaults:

- Float64 fitting; inference may remain float32.
- Deterministic grid of 161 log-spaced temperatures in `[0.5, 2.0]`, union `{1.0}`. Choose minimum calibration NLL; ties choose the value closest to 1.
- No need to add an optimization dependency for one scalar. A later continuous optimizer must reproduce or beat this grid on C and have its own tests.
- An optimum at either boundary is marked `boundary_optimum`; keep it experimental rather than silently expanding the search after reading H.
- Calibration weights are one per observed decision, with no winner or recency weighting. Also report series-balanced metrics separately.
- Reject nonfinite accepted logits, missing/duplicate candidate IDs, target exclusion, malformed shapes, and an empty calibration window.
- Record objective at T=1 and at fitted T. Fit success requires calibration NLL not to increase beyond numerical tolerance.

Save per-decision data sufficient for independent scoring: model and policy fingerprints, series/game/order, action/phase, hero-ID vocabulary hash, accepted mask, target index, raw logits, split name, and inference-context ID. Store arrays in `.npz` plus a readable manifest; do not duplicate private environment/configuration values.

## 6. Artifact and loading design

Use an optional versioned **sidecar** so old schema-v3/v4 neural artifacts remain loadable. Proposed output name: `draft_probability_calibration.json`, under an isolated candidate directory until promotion.

Example contract (illustrative placeholders, not executable values):

```json
{
  "schema_version": 1,
  "method": "global_temperature",
  "status": "experimental",
  "policy_model_type": "sequence",
  "temperature": 1.2,
  "model_fingerprint": "<semantic-sha256>",
  "candidate_policy_id": "legacy_role_filter_v1",
  "candidate_policy_fingerprint": "<sha256>",
  "context_contract_version": "bp_context_v1",
  "calibration_match_ids": ["<ids>"],
  "model_training_match_ids_sha256": "<sha256>",
  "split_manifest_sha256": "<sha256>",
  "calibration_decisions": 0,
  "coverage": {"target_excluded": 0},
  "fit": {"range": [0.5, 2.0], "boundary_optimum": false},
  "metrics": {"calibration": {}, "evaluation": {}},
  "generated_at": "<UTC timestamp>"
}
```

Semantic model fingerprint must cover model type/schema, parameter hash, relevant config, ordered hero/team vocabularies, feature names and **actual feature matrix**, and any other active scoring priors. Do not hash only numeric weights; changing the feature file or vocabulary can change predictions. Exclude generated timestamps and calibration fields from that identity.

Candidate-policy fingerprint covers its version, role-map contents where applicable, and all scoring/availability configuration relevant to the fitted distribution. The per-request mask itself is stored in prediction records, not used as a separate sidecar key.

Loading rules:

- No sidecar or feature disabled: T=1, status `uncalibrated`.
- Fingerprint mismatch, invalid T, unsupported version, corrupted JSON, or a sidecar in an ineligible state: T=1 with a precise management diagnostic. Do not crash an otherwise valid legacy model.
- Invalid neural model remains an error under existing validation; do not use the calibration fallback to hide corrupted neural weights.
- Candidate testing may opt into a valid `experimental` sidecar; normal serving requires an explicitly promoted/eligible one and the server feature switch.
- Cache invalidation includes main model, feature matrix, candidate-policy artifact, and calibration sidecar changes. A sidecar-only update must reload.
- Write sidecar atomically. A transient main/sidecar mismatch falls back to T=1. Do not allow an old temperature to apply to newly swapped weights.
- Metadata should expose method/status/temperature/model fingerprint for diagnostics without a user-facing implementation checklist.

## 7. Concrete work breakdown

Proposed new files can be renamed to match repository conventions; preserve these responsibilities.

| Step | Deliverable | Likely files |
| --- | --- | --- |
| C1 | Disjoint V/C/H split manifest and replay records | `analysis/sequence_training/splits.py`, `analysis/evaluate_draft_policy.py` |
| C2 | Pure fit/score functions and fit CLI | `analysis/calibration.py`, `analysis/fit_draft_calibration.py` |
| C3 | Sidecar schema, identity and fallback resolution | `backend/app/services/draft_calibration.py` |
| C4 | Apply T at shared final softmax; reuse across rollouts | `backend/app/services/draft_simulator.py` |
| C5 | Candidate export/lifecycle integration | existing train/export scripts; explicit flags only |
| C6 | Tests, results and operational notes | focused tests; `analysis/experiments/calibration/<run-id>/` |

Add explicit CLI options for the split manifest, checkpoint, candidate policy, prediction-record output, calibration output, and dry-run evaluation. Proposed command shapes below are **interfaces to implement**, not existing commands:

```bash
python analysis/evaluate_draft_policy.py --checkpoint <path> --split-manifest <path> --candidate-policy <id> --output-dir <isolated-dir>
python analysis/fit_draft_calibration.py --predictions <calibration-npz> --manifest <path> --output <sidecar>
```

Do not add calibration automatically to the management `all` path while it still performs an all-data refit. Add an explicit candidate/evaluation path and document why the ordinary refit produces an uncalibrated new model.

## 8. Verification and adoption criteria

Necessary correctness tests:

1. T=1 reproduces existing probabilities; any T>0 preserves rankings/ties for the same mask.
2. Probabilities sum to 1 over accepted heroes; excluded heroes remain exactly zero.
3. Large-magnitude logits, single candidates, empty sets, and invalid temperatures are handled.
4. The fitted T does not increase C NLL relative to T=1.
5. Fingerprint mismatch and changed feature/role files invalidate the sidecar.
6. V/C/H/training overlap is rejected; all-data refits cannot claim the earlier calibrator.
7. Target exclusions are counted and prevent an eligible full-distribution fit.
8. NumPy and evaluation probabilities agree within max absolute error `1e-5` on representative complete prefixes.
9. All rollout steps use the resolved temperature exactly once. Same seed/config is reproducible. Temperature can change sampled trajectories; it must not change the initial next-action ordering.
10. A calibration-only artifact update invalidates cache; an absent sidecar preserves legacy behavior.

Report unweighted NLL, multiclass Brier, top-1/3/5, 10-bin ECE, mean confidence, candidate coverage, and by-phase metrics. Include per-series records and 5,000 paired series-block bootstrap intervals for NLL/Brier differences. ECE alone is not a promotion criterion.

Suggested promotion gates, fixed before H is examined:

- All correctness/coverage checks pass for the target serving policy.
- Pooled NLL improves by at least 0.01 and its paired 95% interval excludes zero; Brier does not worsen materially (tolerance 0.002).
- Top-k changes are zero for identical states/masks, aside from documented numerical ties.
- No phase with at least 100 test decisions has NLL regression exceeding 0.05 without an explicitly reviewed reason.
- No more than 5% increase in warmed single-prediction and full-rollout p95 latency in paired benchmarks on the same machine. Treat sub-millisecond noise appropriately and report absolute times too.
- At least two of three feasible chronological folds improve NLL. If data supports fewer folds, label evidence insufficient for default promotion; complete the implementation anyway.

These are proposed engineering decision thresholds, not guarantees of scientific significance across patches. If a gate fails, leave the candidate disabled and report the result. Do not modify thresholds after seeing H.

## 9. Completion checklist

- [ ] Correct implementation and isolated candidate artifacts exist.
- [ ] Exact model/policy identities and split manifests are recorded.
- [ ] Calibration and test are independent of neural training/selection.
- [ ] Coverage failures are visible and cannot produce a falsely eligible sidecar.
- [ ] Shared serving/rollout integration and legacy fallback pass tests.
- [ ] Results, limitations, benchmark commands and activation/rollback instructions are written.
- [ ] Production default remains unchanged.
