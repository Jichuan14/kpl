# BP model upgrade results — 2026-09-05

## Decision

The temporally safe familiarity residual is a meaningful research improvement, but it is **not eligible for production promotion yet**. Across three non-overlapping 10-series holdout folds and seeds 7/17/29, uncalibrated NLL improved by 0.2046 with a paired series-bootstrap 95% interval of [-0.2168, -0.1915]. The direction held in all three folds. Top-1 improved 1.89 percentage points, top-5 improved 4.94 points, and Brier improved 0.0145.

Promotion is blocked because the unchanged `legacy_role_filter_v1` serving policy excluded 2/840 calibration targets and 19/1091 latest-fold holdout targets. Its full-distribution NLL is infinite, so no eligible serving temperature can be fitted. The complete-coverage results use the diagnostic `game_availability_v1` policy; that mask has not replaced the production default.

## Locked data contract

- Seasons: `20250003,20250004,20260001,20260002,20260003`.
- Whole-series windows: training followed by V=10, C=10, H=10; rolling H offsets 0, 10, and 20.
- General history cutoff: calendar dates strictly earlier than the series date. Same-day and current-series records are excluded.
- Hero catalogue limitation: the current pinned local feature artifact is used because historical catalogue snapshots do not exist.
- Latest split manifest: `split_manifest.json`; rolling manifests: `fold2_split_manifest.json`, `fold3_split_manifest.json`.

## Headline results

Three folds × three seeds, with seed differences averaged within each actual series before the 5,000-sample series bootstrap:

| Metric | Baseline | Familiarity | Difference |
| --- | ---: | ---: | ---: |
| Uncalibrated NLL | 3.0961 | 2.8894 | -0.2067 |
| Uncalibrated Brier | 0.8908 | 0.8763 | -0.0145 |
| Top-1 | 22.26% | 24.15% | +1.89 pp |
| Top-5 | 51.43% | 56.38% | +4.94 pp |
| Independently calibrated NLL | 3.0561 | 2.8628 | -0.1932 |
| Independently calibrated Brier | 0.8828 | 0.8692 | -0.0136 |

Calibrated paired NLL difference: -0.1917, 95% interval [-0.2016, -0.1816]. Fold differences were -0.2142, -0.1839, and -0.1770. Average calibrated phase NLL differences were: opening -0.0650, first picks -0.0748, second bans -0.3075, closing picks -0.3311. No major phase regressed.

## Model-stage results

- E0/E1: uniform winner weighting was selected on V (2.9355 NLL versus 2.9600 for existing winner weighting).
- E2: latest-fold familiarity H NLL was 2.9570 versus baseline 3.1910; top-5 was 55.36% versus 48.95%.
- E3: the 85,173-parameter attention model reached H NLL 2.9359, but did not clearly beat the 20-parameter familiarity model: paired interval [-0.0380, +0.0032], top-5 -0.46 pp, and worse Brier. The simple model was selected.
- E4/E5 were not expanded after the attention model failed the predeclared E3 complexity gate. Its implementation and generated artifact were removed during production cleanup; the negative result is retained here for provenance.
- E6: latest-fold temperatures were baseline 1.1688 and familiarity 1.1096. Latest-fold calibrated H NLL was 3.1367 versus 2.9251.

## Coverage, fallback, and runtime

- Latest H context reliability coverage: mean 0.6884, range 0.6501–0.7278; all 1,091 decisions were in the high-history slice. Cold-start quality therefore remains synthetic-only.
- Zero-history context produces exact packaged-baseline logits and probabilities at T=1.
- NumPy/PyTorch parity tests pass at <1e-5 for the hierarchical candidate and <1e-6 for the selected familiarity candidate.
- Warm p95: baseline/candidate single state 0.200/0.224 ms (1.12×); 50-rollout completion 214.6/309.6 ms (1.44×). Both meet the provisional 2× budget after state-safe role/familiarity caching.

## Candidate artifacts

- Selected experimental artifact: `simple_seed7/personalized_draft_choice_model.json`
- Selected checkpoint: `simple_seed7/simple_seed7.pt`
- Independently fitted experimental sidecar: `simple_seed7/draft_probability_calibration.json`
- Pinned static serving-context experiment: `simple_seed7/player_draft_context.json`
- Runtime benchmark: `simple_seed7/runtime_benchmark.json`
- Confirmatory aggregate: `confirmatory_3fold_3seed.json`
- The rejected full-attention artifact and runtime were removed after the familiarity model was promoted.

The static context snapshot is dated 2026-09-06, while calibration was validated over rolling as-of snapshots. It is suitable for opt-in runtime testing but must not be represented as historically valid for earlier requests. Production defaults were not changed and no release artifact was overwritten.

## Reproduction

Build the latest manifest:

```bash
/opt/homebrew/bin/python3 analysis/build_draft_split_manifest.py --target-season 20260003 --source-seasons 20250003,20250004,20260001,20260002,20260003 --validation-series 10 --calibration-series 10 --holdout-series 10 --output analysis/experiments/personalized_draft/20260905_bp_upgrade/split_manifest.json
```

Train the matched baseline:

```bash
/opt/homebrew/bin/python3 analysis/sequence_training/train.py --target-season 20260003 --previous-seasons 4 --validation-matches 10 --holdout-matches 20 --epochs 30 --batch-size 256 --hidden-dim 48 --learning-rate 0.003 --weight-decay 0.0001 --recency-decay 0.65 --winning-pick-weight 1.0 --use-series-context --seed 7 --threads 4 --models bag_ablation,hybrid_bag_gru --output-dir analysis/experiments/personalized_draft/20260905_bp_upgrade/baseline_uniform_seed7
```

Replay and fit the baseline calibrator:

```bash
/opt/homebrew/bin/python3 analysis/evaluate_draft_policy.py --checkpoint analysis/experiments/personalized_draft/20260905_bp_upgrade/baseline_uniform_seed7/hybrid_bag_gru.pt --artifact analysis/experiments/personalized_draft/20260905_bp_upgrade/baseline_uniform_seed7/sequence_candidate.json --split-manifest analysis/experiments/personalized_draft/20260905_bp_upgrade/split_manifest.json --candidate-policy game_availability_v1 --output-dir analysis/experiments/personalized_draft/20260905_bp_upgrade/baseline_uniform_seed7/eval_game_availability
/opt/homebrew/bin/python3 analysis/fit_draft_calibration.py --predictions analysis/experiments/personalized_draft/20260905_bp_upgrade/baseline_uniform_seed7/eval_game_availability/calibration_predictions.npz --manifest analysis/experiments/personalized_draft/20260905_bp_upgrade/baseline_uniform_seed7/eval_game_availability/prediction_manifest.json --output analysis/experiments/personalized_draft/20260905_bp_upgrade/baseline_uniform_seed7/eval_game_availability/draft_probability_calibration.json
```

Train the selected familiarity model:

```bash
/opt/homebrew/bin/python3 analysis/train_personalized_draft_choice_model.py --checkpoint analysis/experiments/personalized_draft/20260905_bp_upgrade/baseline_uniform_seed7/hybrid_bag_gru.pt --split-manifest analysis/experiments/personalized_draft/20260905_bp_upgrade/split_manifest.json --seed 7 --epochs 30 --batch-size 128 --threads 4 --output-dir analysis/experiments/personalized_draft/20260905_bp_upgrade/simple_seed7
```

Aggregate confirmatory runs and benchmark the selected runtime:

For rolling confirmation, repeat the baseline/evaluator/simple commands above for each `(holdout_offset, seed)` in `(0,7)`, `(0,17)`, `(0,29)`, `(10,7)`, `(10,17)`, `(10,29)`, `(20,7)`, `(20,17)`, `(20,29)`. Pass the offset to both `build_draft_split_manifest.py --holdout-offset-series` and `train.py --holdout-offset-matches`; use the matching manifest/checkpoint for `evaluate_draft_policy.py` and `train_personalized_draft_choice_model.py`. The committed experiment directories use `fold1_*`, `fold2_*`, and `fold3_*` names (with the original fold-1 seed-7 directories named `baseline_uniform_seed7` and `simple_seed7`). Then run:

```bash
/opt/homebrew/bin/python3 analysis/aggregate_confirmatory.py
/opt/homebrew/bin/python3 analysis/benchmark_personalized_rollout.py --league-id 20260003 --artifact analysis/experiments/personalized_draft/20260905_bp_upgrade/simple_seed7/personalized_draft_choice_model.json --context analysis/experiments/personalized_draft/20260905_bp_upgrade/simple_seed7/player_draft_context.json --decision-row analysis/exports/20260003/bp_decisions.jsonl --repetitions 10 --rollouts 50 --output analysis/experiments/personalized_draft/20260905_bp_upgrade/simple_seed7/runtime_benchmark.json
```

Production promotion now uses `model_type=personalized` as the default and an eligible, exact-model sidecar under `analysis/outputs/{league_id}`. Selecting `sequence`, `learnable`, or `stats` explicitly remains the rollback path.
