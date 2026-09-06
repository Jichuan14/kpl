# Agent handoff: BP probability calibration and DraftRec-inspired model

Prepared September 5, 2026. This is an implementation specification, not an implemented feature.

## User objective and scope

Improve Draft Atlas's next-action BP predictions in two work packages:

1. Add trustworthy probability calibration to the existing sequence model.
2. Build and evaluate a compact DraftRec-inspired policy that incorporates historical player preferences, uncertain rosters, and current draft interactions.

**Do not download matches, synchronize remote data, or undertake the stale-data/pipeline-refresh project.** The user will handle new match downloads separately. Work from available local exports. Creating new derived experiment files from those exports is in scope. If a field is missing, implement the documented fallback and report its coverage; do not make downloading data a prerequisite.

Read and execute these specifications in order:

- [01 — Calibration implementation](01_CALIBRATION.md)
- [02 — DraftRec-inspired model design](02_DRAFTREC_MODEL.md)

Repository root at planning time: `/Users/jichuan/Desktop/kpl`.

## What success means

The deliverable is working, tested code, reproducible experiments, an exportable candidate, and an evidence-based adoption decision. Improved results are not guaranteed. A negative experiment with a correct implementation and a clear diagnosis is an acceptable research outcome; inventing gains or weakening acceptance criteria after seeing results is not.

Keep the current production default and existing value recommenders intact while developing candidates. Candidate inference and calibration should be available for explicit testing. Do not publish, deploy, replace the default model, or overwrite release artifacts as part of these work packages. Save those changes as a concrete later promotion step.

No new agent tasks, scheduled jobs, or external services are necessary. Follow repository instructions discovered at execution time. Preserve unrelated edits and do not reset or commit someone else's work.

## Current code landmarks

| Area | Existing files |
| --- | --- |
| PyTorch definitions and preparation | `analysis/sequence_training/models.py` |
| Training and release refit | `analysis/sequence_training/train.py`, `analysis/train_sequence_draft_choice_model.py` |
| Model export | `analysis/export_sequence_draft_choice_model.py` |
| NumPy inference | `backend/app/services/sequence_model_runtime.py` |
| Candidate filters, probabilities, simulator | `backend/app/services/draft_simulator.py` |
| Requests and API | `backend/app/schemas.py`, `backend/app/api/simulation.py` |
| Pipeline | `backend/app/services/analysis_pipeline.py` |
| Historical player rows | `analysis/export_match_data.py`, `backend/app/models.py` |
| Existing player summaries | `analysis/compute_team_draft_profiles.py` |
| Earlier investigation | `analysis/investigations/bp_improvement_20260905/REPORT.md` |

Check actual implementations before editing: documents and line numbers may have changed. The current local baseline is schema-v4 frozen bag + GRU with team embeddings and prior-game hero usage. Some older Markdown still describes schema v3 or the older learnable default.

## Shared constraints

- Predict the next **observed choice**, not a win probability or optimal action.
- Split and resample by whole match series; keep all games/actions together.
- Keep training, checkpoint-selection validation, calibration, and evaluation windows distinct.
- Use only information available before the decision. Final lineups, current-game player/hero assignments, and full-season player summaries are not pre-draft inputs.
- Pin exact season lists and input hashes for experiment reproducibility. This is experiment bookkeeping, not a data refresh task.
- Train with PyTorch, retain NumPy-only inference for ordinary backend requests.
- Do not load experiment code from `poc/` into production services.
- Do not hard-code the earlier temperature `1.2558` or treat old August results as a pristine test set.

## Suggested implementation sequence

1. Build shared chronological split manifests, per-decision evaluation records, and candidate-policy identity.
2. Implement global temperature fitting, sidecar export/loading, and simulator integration behind an explicit switch.
3. Finish calibration correctness, rollout, and fallback checks; write a calibration report.
4. Build the model's as-of player/roster context and leakage tests.
5. Train the simple familiarity baseline before the neural personalization branch.
6. Implement the hierarchical residual model and staged ablations.
7. Calibrate the selected candidate independently, export it, and verify NumPy parity and latency.
8. Produce a final comparison and adoption recommendation; leave deployment/default promotion as a later action.

## Required final handoff from the implementation agent

Provide changed files, runnable commands, exact data/split/model identifiers, baseline/candidate metrics, uncertainty intervals, candidate-coverage failures, cold-start behavior, latency measurements, and tests run. Clearly distinguish completed implementation from empirically justified promotion. Include one command to reproduce each headline experiment and an explicit rollback/configuration path.

## Copyable assignment

Implement the two specifications in `analysis/plans/bp_model_upgrade/01_CALIBRATION.md` and `analysis/plans/bp_model_upgrade/02_DRAFTREC_MODEL.md`, starting with calibration and the shared evaluation contract. Use existing local data; do not download matches or work on stale-data refresh. Build and test the candidates, run the staged experiments, and leave production defaults unchanged. Follow the documented leakage controls, fallbacks, artifact binding, and acceptance criteria. Report measured results honestly, including unsuccessful experiments, and leave reproducible commands and candidate artifacts for later promotion.
