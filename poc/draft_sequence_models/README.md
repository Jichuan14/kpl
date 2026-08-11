# Draft sequence-model POC

This isolated proof of concept compares six PyTorch conditional-choice models:

- `app_current`: an architectural port of the app's current schema-v2 model;
- `bag_ablation`: an order-insensitive control using the same POC embeddings;
- `gru`: a one-layer chronological GRU;
- `pairwise`: an additive candidate/action response model with absolute BP
  position and relative lag embeddings.
- `hybrid_bag_gru`: a selected, frozen bag checkpoint plus a bounded and
  regularized GRU correction to its candidate logits.
- `hybrid_app_lag_gru`: a selected, frozen current-app checkpoint plus a
  normalized lag-aware GRU residual controlled by a state-dependent gate.

The historical POC commands and tests now use compatibility wrappers around the
maintained model definitions in `analysis/sequence_training`. POC commands
continue to write only beneath this directory's `artifacts/` folder.

## Run

Use a Python environment with PyTorch installed:

```bash
python poc/draft_sequence_models/train.py
```

The default evaluation uses the 10 completed matches immediately before the
holdout as a chronological validation window for checkpoint selection, then
reserves the latest 10 completed matches from season `20260003` as an untouched
chronological holdout. All actions from both windows are excluded from training.
Results and checkpoints are written beneath
`poc/draft_sequence_models/artifacts/`.

For a faster smoke run of the production-comparable hybrid:

```bash
python poc/draft_sequence_models/train.py --epochs 2 \
  --models app_current,hybrid_app_lag_gru
```

Run the focused tests with:

```bash
python -m unittest discover -s poc/draft_sequence_models/tests -v
```

The pairwise model emits an attribution example showing the learned logit
contribution of every earlier action to the observed holdout choice. Evaluated
current-app and hybrid checkpoints are in `artifacts/v2_single/`.

## Rolling evaluation

Run the leakage-free three-fold, three-seed comparison with:

```bash
python poc/draft_sequence_models/run_rolling_evaluation.py
```

For older folds, every match later than that fold's holdout is excluded from
training. The aggregate report also isolates predictions made one through five
actions after an opponent pick and includes paired comparisons against both
the bag control and current app architecture. Raw metrics are written to
`artifacts/rolling_results.json`; the interpreted results are in
`POC_RESULTS.md`.
