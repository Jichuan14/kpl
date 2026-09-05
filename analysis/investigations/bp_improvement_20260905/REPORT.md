# BP prediction investigation — September 5, 2026

There is room to improve the model. The strongest measured improvement in this investigation is better probability calibration. The biggest operational gap is that the database contains newer games than the model's training exports. For larger accuracy gains, prioritize time-varying preferences, opponent-aware ban prediction, and player/role context before replacing the sequence encoder.

This investigation concerns predicting the next observed ban/pick. Selecting the strategically best draft is a separate objective; the project already has separate lineup and ban-value recommenders for that purpose. No production model, source database, or serving code was changed by this investigation.

## What is actually running

I read the root README, calculation methodology, artifact guide, analysis README, and sequence POC documentation, then checked training code, saved results, model artifacts, inference code, exports, and the SQLite database.

The current local artifact is **schema v4, frozen bag + GRU residual**, with team embeddings and prior-game hero usage for both teams. It covers 123 heroes, has 99 input features including the feature-known indicator, and uses 48-dimensional branch representations. The management pipeline enables series context and refits on all available training matches. Simulator metadata prefers the sequence model when its artifacts are available. This is a local repository assessment, not a check of a remote deployment.

Some documents are behind the implementation:

- `analysis/README.md` describes schema v3 and says the learnable model remains the default.
- `CALCULATION_METHODOLOGY.md` primarily describes the statistical model; its statement that the current next-action model lacks team identity does not describe the sequence model.
- The earlier POC comparison is useful historical evidence, but the current model already incorporates its stronger bag-plus-GRU direction. “Add a GRU” and “add previous-game hero usage” are therefore not new recommendations.

Relevant code: [sequence model definitions](../../sequence_training/models.py), [pipeline settings](../../../backend/app/services/analysis_pipeline.py), [serving and candidate filters](../../../backend/app/services/draft_simulator.py).

## Measured findings

### 1. Training inputs are stale even though the games are available locally

For season `20260003`:

| Local source | Series with BP data | Games | BP rows | Latest completed data |
| --- | ---: | ---: | ---: | --- |
| Existing training exports | 100 | 398 | 7,959 | July 31 |
| SQLite / temporary refreshed export | 124 | 490 | 9,799 | August 23 |

The existing match export lists the 24 August series but gives them empty battle lists. The database has their 92 games and 1,840 additional BP rows. This is an **export/model refresh gap**, not a lack of collected data. One of the added decisions is excluded by the trainer's existing usability checks, leaving 1,839 evaluation decisions.

The local release artifact was generated August 18 but its source training decisions still end July 31. Displaying the artifact creation date alone hides this problem.

**Recommendation:** track latest completed-game time and data hashes through database → match export → decisions → checkpoint → published artifact. Refresh exports before retraining, and retain a later evaluation window before refitting the final release. I did not measure how much retraining on August improves accuracy, so this is an identified bottleneck rather than a claimed accuracy gain.

### 2. Calibration helps on games excluded from the release's training data

I first reproduced the preserved July evaluation checkpoint's NLL to within `1e-5`. This avoids mistakenly evaluating the release refit on July matches it has already seen.

For the newer check, I verified the release checkpoint's SHA-256 against the exported model, then used:

- **Calibration:** August 12–16, 12 series, 859 usable decisions.
- **Evaluation:** August 19–23, 12 later series, 980 usable decisions.
- Temperature chosen only from calibration-window NLL, on a predefined grid from 0.5 to 2.0.
- The neural model weights remain fixed throughout.

Temperature scaling softens or sharpens probabilities without changing the ordering of legal candidates. It is an established calibration technique; its benefit here comes from the local experiment, not from assuming published results transfer to KPL. [Guo et al., 2017](https://proceedings.mlr.press/v70/guo17a.html).

| Model / adjustment | NLL ↓ | Top-1 ↑ | Top-5 ↑ | Confidence error ↓ |
| --- | ---: | ---: | ---: | ---: |
| Existing release, offline candidate mask | 3.1002 | 21.33% | 51.02% | 12.55% |
| Temperature scaling, T = 1.2558 | 3.0207 | 21.33% | 51.02% | 5.03% |
| Calibrated model + recent-meta mixture | 2.9906 | 21.43% | 51.43% | 4.40% |

“Confidence error” is top-label expected calibration error with 10 equal-width bins. It depends on binning and sample size; Brier scores also improve, from **0.9073 → 0.8872 → 0.8837**. Lower NLL rewards assigning higher probability to the choice actually made.

The calibration NLL change is **−0.0795**, with a paired 5,000-resample, series-block bootstrap interval of **[−0.1074, −0.0527]**. This interval describes uncertainty within these 12 series, not future seasons or all patches. A temperature fitted on the earlier July validation window also helps on August: NLL **3.0299**, without using August labels to fit it.

These are **offline-model probabilities**, before the app's additional role filter. Calibrate the final serving distribution after resolving the filter issue below; do not copy this temperature directly into production and assume identical results.

### 3. Recent-meta blending gives a small additional gain

I tested a lightweight alternative to a larger neural network:

`p = 0.9 × calibrated_sequence_probability + 0.1 × recent_draft_probability`

The recent model uses BP-order choice counts, a 14-day half-life, and shrinkage toward action-level counts. Both the half-life and mixture weight were selected on August 12–16 only, from `{7,14,28}` days and `{0,0.1,0.25,0.5}`. Counts update using only earlier calendar days; no current-series or same-day choices enter a prediction. Earlier evaluation days can inform later evaluation days, making this an **online chronological evaluation**, not a fully frozen predictor.

The idea is to retain stable learned interactions while giving recent preferences a small influence. Modeling preferences over time has precedent in recommendation research, although this simple count mixture is my adaptation, not a reproduction of Koren's model. [Koren, 2009](https://cseweb.ucsd.edu/classes/fa17/cse291-b/reading/p447-koren.pdf).

The extra accuracy is modest: **one additional top-1 hit and four additional top-5 hits out of 980 decisions**. This supports further testing, not a claim of a reliable accuracy breakthrough. The recent component uses selection counts rather than a full legal-opportunity model and does not model team-specific preferences yet.

### 4. Offline and live candidate sets differ

Training/evaluation use the exported game-availability mask. The live simulator additionally requires picks to fit distinct roles and bans to fit an opponent's open role. These are stronger assumptions than game availability.

Using the current local role map:

- The extra filter excludes **9 of 7,957 usable July decisions**: six picks and three bans. None are in the old 899-decision holdout.
- On the August evaluation window, it excludes **2 of 980 actual choices**.
- Both occur in `2026082002`, battle `1222656528_6_1787231255`: 梦奇 at order 18 and 戈娅 at order 19.
- The role map assigns 梦奇 only role `5`, but the exported player mapping for that game puts it in role `6`. The following pick is affected by the already-infeasible role assignment. Both decision rows have no quality flags.

The hard filter raises August top-1 to **22.76%** and top-5 to **56.12%**, but gives those two observed choices zero probability. Thus an uncapped end-to-end NLL is infinite. Reporting only metrics on surviving choices would conceal the failure.

**Recommendation:** distinguish hard game rules from inferred role preferences. Keep actual availability rules hard; make uncertain role evidence a soft penalty or use probabilistic role assignments with an explicit fallback. Refresh role coverage and evaluate every serving-stage exclusion. This is not a recommendation to discard role information: it improves many rankings.

I also compared 14 spread-out August prefixes through the actual NumPy scorer against checkpoint scoring with the same mask. Maximum probability difference was **3.28 × 10⁻⁷**, with no prefix reconstruction errors in that sample. The evidence points to candidate-policy differences rather than a numerical conversion bug.

### 5. Second-phase bans are the clearest modeling weakness

On the 980-decision August offline evaluation:

| Phase | Decisions | Top-1 | Top-5 | NLL |
| --- | ---: | ---: | ---: | ---: |
| Opening bans + first pick | 245 | 36.33% | 67.76% | 2.4659 |
| Remaining first-phase picks | 245 | 22.04% | 56.33% | 2.8877 |
| Second-phase bans | 294 | 11.22% | 35.71% | 3.7201 |
| Closing picks | 196 | 16.84% | 46.43% | 3.2290 |

This weakness is consistent with the saved July results. Simply weighting second-phase bans more heavily has already been tried: the saved `sequence_training_series_ban15` experiment worsened overall NLL **2.8295 → 2.8952** and top-5 **58.18% → 54.62%** on its shared July holdout. It slightly improved that phase's NLL but reduced its top-5. Repeating that weight change alone is a low-priority direction.

## New methods worth testing next

These are proposals, not measured improvements in this investigation.

| Priority | Method | Concrete application to this project |
| --- | --- | --- |
| 1 | Dynamic meta and team preferences | Add small time-varying hero/action biases to the existing model; shrink team effects toward league effects. Use short- and long-window evidence, patch indicators, and recent choice rates. Extend the tested mixture before attempting a large temporal model. |
| 2 | Dedicated ban-prediction head | Share hero/state representations, but score bans from the opponent's likely remaining picks, known player pools, and uncertain open roles. This predicts what coaches ban; the existing ban-value recommender solves a different task. |
| 3 | Player/roster context | Add pooled pre-match roster representations and historical player–hero familiarity. Shrink sparse players toward role/team baselines. Never use the current pick's eventual player assignment or same-game performance as an input. |
| 4 | Candidate-conditioned attention | Keep a strong bag baseline and add a small residual where each candidate hero attends to relevant own/opponent draft tokens. Include action type, relation, position, and lag. A candidate can then focus on different interactions instead of every candidate sharing the same pooled state query. |
| 5 | Remaining-pool context | Encode which alternatives remain, role scarcity, both teams' remaining specialty heroes, game number, known series score, and best-of length. Prior-game hero masks already exist; extend them rather than rediscovering them. |
| 6 | Prediction-objective ablation | Compare winning-pick training weight `1.5` with `1.0`, keeping splits, seeds, features, and architecture identical. Winner weighting changes the behavior distribution being fitted; it is not automatically label leakage, but may conflict with predicting all observed choices. |

**Research supporting these directions:**

- **DraftRec** explicitly models player preferences and match interactions. Its datasets are much larger than this KPL corpus, so borrow the hierarchical personalization idea with strong shrinkage rather than its capacity blindly. [Lee et al., 2022](https://arxiv.org/abs/2204.12750).
- **Set Transformer** provides attention-based modeling of interactions within sets. A small candidate-attention residual is a plausible adaptation; a full set-only model would discard the order information this project's GRU already demonstrated to be useful. [Lee et al., 2019](https://proceedings.mlr.press/v97/lee19d.html).
- **Context-dependent choice models** study how the alternatives themselves affect choices. This motivates representing the remaining hero pool rather than only removing unavailable candidates at the final softmax. [Seshadri et al., 2019](https://arxiv.org/abs/1902.03266).
- **JueWuDraft** is particularly relevant if the goal becomes better BO5/BO7 draft recommendations: it studies Honor of Kings drafting with neural networks, tree search, and long-term multi-round value. It does not establish that tree search improves imitation of the next human choice. Apply it to the recommendation/value layer, with calibrated value estimates and supported actions. [Chen et al., revised 2021](https://arxiv.org/abs/2012.10171).

For patch features, map each match to its **actual tournament patch**. A public patch announcement's publication date is not sufficient evidence that the tournament had switched versions. Use only mechanics, role evidence, rosters, and past statistics available before the prediction cutoff.

## Evaluation and implementation order

1. **Fix freshness accounting and preserve snapshots.** Store exact season lists, export/feature hashes, latest training-game time, and separate evaluation versus full-data-refit checkpoints. A newly added `20250004` export now changes which four predecessor seasons the default loader selects, whereas the saved release used a different list. Hold this constant in comparisons.
2. **Make evaluation replay serving behavior.** Report coverage and exclusions along with NLL, Brier, top-1/3/5, and calibration. Use complete series as split/bootstrap units. Audit inferred role masks instead of silently dropping failures.
3. **Test calibration and the recent mixture in shadow.** Fit on chronologically earlier calibration data. Refit calibration after changing the role policy or neural checkpoint. Evaluate the whole rollout path because probability changes affect sampled completions even when top-1 stays the same.
4. **Run bounded ablations.** First compare uniform winner weights and richer time/roster/ban features. Then test a small candidate-attention residual against the current series-aware bag+GRU with identical data and three seeds.
5. **Confirm on new series or held-out patches.** August labels have now been examined during this investigation; do not reuse this window indefinitely as a pristine final test. Multiple seeds are repeated optimization runs, not extra independent matches.

The current results support improving probabilities immediately as an engineering candidate. They do **not** yet establish a large, durable increase in exact BP prediction accuracy, or a win-rate benefit from recommendations.

## Reproduction and files

Run from the repository root using its existing Python environment, in this order:

```bash
./backend/.venv/bin/python analysis/investigations/bp_improvement_20260905/audit.py
./backend/.venv/bin/python analysis/investigations/bp_improvement_20260905/august_check.py
./backend/.venv/bin/python analysis/investigations/bp_improvement_20260905/recent_meta.py
```

The August script opens SQLite in read-only mode, exports to a temporary directory, and limits the target data to dates before August 24. The recent-meta script uses that temporary snapshot, so rerun the August script if it has expired. Historical source exports and features remain dependencies; changing them may change results or trigger the vocabulary/window assertions.

- [July checkpoint reproduction, calibration, and role audit](audit_results.json)
- [August release evaluation, calibration, confidence interval, and serving checks](august_results.json)
- [Recent-meta validation search and later evaluation](recent_meta_results.json)

Verification included checkpoint/export hash identity, exact hero-vocabulary agreement, explicit team-ID remapping, disjoint chronological windows, reproduced saved NLL, and sampled PyTorch/NumPy probability agreement. No remote deployment, production retraining, or full application test suite was needed for this isolated investigation.
