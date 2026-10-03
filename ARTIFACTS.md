# Website JSON and JSONL artifact inventory

This file records the JSON and JSONL artifacts used by the website and its API.
`{league_id}` means a season/competition ID such as `20260003`.

The normal data flow is:

```text
SQLite
  -> analysis/exports/{league_id}/*.jsonl
  -> analysis/outputs/{league_id}/*.{json,jsonl}
  -> analysis/published/data/**/*.json
  -> frontend pages
```

The SQLite database remains the source of truth. Export, output, and published
files are derived artifacts and can be rebuilt from the Management page or the
pipeline API.

Update requests now create rows in the `pipeline_jobs` table of
`backend/data/kpl_bp.db`. These durable SQLite rows hold the queue,
status, attempts, progress, and results. The API background runner executes
one job at a time, launching trainer scripts sequentially. The database is not a generated artifact to commit. Back up
SQLite with the season exports, analysis outputs, and published files. A
scheduled 03:00 China-time job refreshes the official catalog, selects the
newest started league with a completed match, performs incremental sync,
rebuilds missing or stale analysis, and publishes changed assets; manual Full update forces a
rebuild. See `deploy/README.md` for API job recovery.

## Season exports

| Artifact | Built by | Used by |
| --- | --- | --- |
| `analysis/exports/{league_id}/matches.jsonl` | `export_match_data.py` (`export`) | BP decision generation, team profiles, power rankings, and learnable-model training |
| `analysis/exports/{league_id}/bp_decisions.jsonl` | `build_bp_decisions.py` (`decisions`) | Relationship statistics, meta heroes, team synergies, team profiles, and both draft models |

## Season analysis outputs

| Artifact | Built by / pipeline step | Website or API use |
| --- | --- | --- |
| `analysis/outputs/{league_id}/ban_response_stats.jsonl` | `compute_bp_statistics.py` (`statistics`) | Ban-response visualization, Draft Coach, and `patterns/ban_response/*` publishing |
| `analysis/outputs/{league_id}/pick_synergy_stats.jsonl` | `compute_bp_statistics.py` (`statistics`) | Pick-synergy visualization, Draft Coach, and `patterns/pick_synergy/*` publishing |
| `analysis/outputs/{league_id}/counter_pick_stats.jsonl` | `compute_bp_statistics.py` (`statistics`) | Counter-pick visualization, Draft Coach, and `patterns/counter_pick/*` publishing |
| `analysis/outputs/{league_id}/counter_ban_stats.jsonl` | `compute_bp_statistics.py` (`statistics`) | Counter-ban visualization, Draft Coach, and `patterns/counter_ban/*` publishing |
| `analysis/outputs/{league_id}/meta_hero_stats.jsonl` | `compute_meta_heroes.py` (`meta`) | Meta-hero cards, season overview, and Draft Coach |
| `analysis/outputs/{league_id}/team_synergy_stats.jsonl` | `compute_team_synergies.py` (`team_synergy`) | Teams page, team tools, Draft Coach, and `team-synergies.json` publishing |
| `analysis/outputs/{league_id}/season_teams.jsonl` | `compute_team_draft_profiles.py` (`team_profiles`) | Management artifact/readiness reporting |
| `analysis/outputs/{league_id}/team_action_tendencies.jsonl` | `compute_team_draft_profiles.py` (`team_profiles`) | Draft simulator calibration and team-profile tools |
| `analysis/outputs/{league_id}/team_opening_sequences.jsonl` | `compute_team_draft_profiles.py` (`team_profiles`) | Team-profile tools and Draft Coach |
| `analysis/outputs/{league_id}/team_combo_performance.jsonl` | `compute_team_draft_profiles.py` (`team_profiles`) | Team-profile tools and Draft Coach |
| `analysis/outputs/{league_id}/player_hero_pools.jsonl` | `compute_team_draft_profiles.py` (`team_profiles`) | Player-pool tools and Draft Coach |
| `analysis/outputs/{league_id}/team_recent_trends.jsonl` | `compute_team_draft_profiles.py` (`team_profiles`) | Recent-form context in Draft Coach |
| `analysis/outputs/{league_id}/power_rankings.json` | `compute_power_rankings.py` (`power_rankings`) | Season-only schema 3 source for team Elo and player boards; legacy cross-season schemas need this season step rerun before factual presentation |
| `analysis/outputs/{league_id}/draft_model.json` | `build_draft_model.py` (`draft_model`) | Draft Simulator and published draft-model metadata |
| `analysis/outputs/{league_id}/learnable_draft_choice_model.json` | `train_learnable_draft_choice_model.py` (`learnable_draft_model`) | Legacy fallback scoring in Draft Simulator; explicit legacy training only |
| `analysis/outputs/{league_id}/learned_hero_feature_space.json` | `export_production_hero_feature_space.py` after `sequence_draft_model` (explicit legacy training can still export legacy maps) | Feature Space, favorite-based recommendations, and management readiness; production fingerprint, bag branch, and vector dimension recorded |
| `analysis/outputs/{league_id}/personalized_draft_choice_model.json` | `train_production_draft_policy.py` (`sequence_draft_model`) | Self-contained chronological bag + GRU policy with the selected 20-parameter familiarity residual |
| `analysis/outputs/{league_id}/personalized_draft_probability_calibration.json` | `train_production_draft_policy.py` (`sequence_draft_model`) | Calibration temperature bound to the exact composite model and candidate policy |
| `analysis/outputs/{league_id}/player_draft_context.json` | `train_production_draft_policy.py` (`sequence_draft_model`) | Point-in-time roster, role, and player–hero familiarity context used by the production policy |
| `analysis/outputs/{league_id}/lineup_value_model.json` | `train_lineup_value_model.py` (`lineup_value_model`) | Completed-lineup value ranking for automatic recommendation rollouts |
| `analysis/outputs/{league_id}/lineup_value_validation.json` | `train_lineup_value_model.py` (`lineup_value_model`) | Chronological validation and final-season benchmark for the lineup value model |
| `analysis/outputs/{league_id}/lineup_value_parameter_search.json` | `train_lineup_value_model.py` (`lineup_value_model`) | Reproducible season-scoped hyperparameter search record |
| `analysis/outputs/{league_id}/ban_value_model.json` | `train_ban_value_model.py` (`ban_value_model`) | Opponent-denial ranking for automatic ban recommendations |
| `analysis/outputs/{league_id}/ban_value_validation.json` | `train_ban_value_model.py` (`ban_value_model`) | Chronological actual-ban ranking evaluation |

## Browser-published assets

These are compact copies written by `backend/app/services/static_publisher.py`.
The frontend requests them under `/assets/data/...`.

| Published artifact | Frontend consumer | Derived from |
| --- | --- | --- |
| `analysis/published/data/factual-seasons.json` | Rankings, BP Data, Teams and their route-aware selector; includes empty seasons, actual fixture teams and published availability | All locally synced League records and current SQLite observation counts; rebuilt after catalog sync or publication |
| `analysis/published/data/seasons.json` | Season selectors and page availability; includes league start time for newest-published default selection | League records with an existing published `overview.json` |
| `analysis/published/data/meta-history.json` | Cross-season meta history | Every published `overview.json` |
| `analysis/published/data/{league_id}/overview.json` | Main visualization overview and meta heroes | Relationship statistics, meta heroes, and league metadata |
| `analysis/published/data/{league_id}/patterns/{relation}/{context}.json` | Main relationship tables | The four relationship-stat JSONL files; `relation` is `ban_response`, `pick_synergy`, `counter_pick`, or `counter_ban`, and `context` is normally `overall` or `slot_context` |
| `analysis/published/data/{league_id}/hero-responses.json` | Feature Space hero-response details | Selected rows from the published relationship data |
| `analysis/published/data/{league_id}/battle-lineups.json` | Feature Space historical-lineup selector | Completed 5v5 battle lineups from `matches.jsonl` |
| `analysis/published/data/{league_id}/team-synergies.json` | Teams page | `team_synergy_stats.jsonl` plus league/team metadata |
| `analysis/published/data/{league_id}/rankings.json` | Rankings page | `power_rankings.json` plus league metadata |
| `analysis/published/data/{league_id}/draft-model.json` | Browser-ready draft-model metadata | `draft_model.json` |
| `analysis/published/data/{league_id}/feature-space.json` | Feature Space board and hero catalog | Validated `learned_hero_feature_space.json` plus draft-model catalog metadata |

`patterns.json` is a retired monolithic artifact. Publishing removes it and
uses the smaller relation/context files above instead.

## Offline intention research

`analysis/outputs/draft_intention_research/` contains optional continuation-probe
JSON reports and a generated `summary.md`, built by
`analysis/run_intention_case_study.py`. These are explicitly ignored research
outputs, not published website inputs or model training targets. Individual
probes can be generated with `analysis/explore_draft_intentions.py`; every report
records the query, source fingerprints, sample exclusions, and comparison
denominators. Reproduce them from the season exports rather than editing the
generated reports. The methodology and findings live in
`analysis/DRAFT_INTENTION_RESEARCH.md`.

## Shared JSON inputs

These files are not season exports, but they supply hero definitions and
features used by website models and explanations.

| Artifact | Purpose | Git status |
| --- | --- | --- |
| `analysis/hero_ability_mechanics.json` | Mechanic tags used by hero feature vectors and Draft Coach explanations | Tracked |
| `analysis/hero_draft_feature_vectors.json` | Hero IDs, names, and model features used by training, simulation, and ranking hero catalogs | Tracked |
| `analysis/hero_tactical_roles.json` | Tactical-role descriptions used by Draft Coach | Tracked |
| `analysis/hero_features.json` | Source specialty data for tactical roles and learnable-model metadata | Generated/supporting input |
| `analysis/hero_specialty_vectors_thermometer.json` | Legacy fallback feature source used when current draft vectors are unavailable | Generated/supporting input |
| `analysis/artifacts/lineup_value_model.json` | Bundled fallback used only when the selected season has not yet built its managed lineup-value artifact | Rebuildable model snapshot |

`analysis/hero_feature_coverage.json` is a build-quality report and
`analysis/example_draft_state.json` is a manual example. They are JSON files in
the repository workspace, but they are not loaded by a live website page.

## Rebuild and Git policy

- **Run/rebuild entire analysis** executes the season export and analysis steps
  in dependency order.
- **Populate frontend assets** converts available output artifacts into the
  browser-published JSON files.
- `*.json` and `*.jsonl` are ignored by default because most are generated and
  season-specific. The shared tracked files listed above, plus any already
  tracked model snapshots, are exceptions.
- Do not edit a published JSON file as the source of a fix. Change its producer
  or source artifact, rerun analysis, and publish again.
### Historical draft evidence

`analysis/build_draft_evidence.py` builds model-free, descriptive evidence from
all compatible local season exports. Shared corpus manifests live under
`analysis/outputs/draft_evidence/{corpus_id}/`; target manifests and match
shards live under `analysis/outputs/{league_id}/draft_evidence/`. These are
generated artifacts and follow the same Git policy as other analysis outputs.
They are optional research artifacts: the management display/full pipelines do
not build or publish them, and the simulator intent widget computes directly
from the exported BP corpus.
The schema and interpretation limits are defined in
`analysis/DRAFT_EVIDENCE_SPEC.md`.

Production feature maps derive vectors from the active artifact’s self-contained hero feature matrix and frozen bag projection/residual parameters. Their PCA coordinates and full-vector nearest neighbors require no legacy learned-choice artifact. `counts_scope=target_season_observed_decisions` and `count_weighting=equal_weight_per_observed_action` identify actual selected-season pick/ban counts; the compatibility `weighted_bp_action_count` equals the unweighted action count. Existing legacy maps remain readable, but do not satisfy full-update production readiness.

## Shared immutable model registry

`analysis/outputs/models/versions/{version}/` contains a complete copied bundle and `manifest.json` with component SHA-256 fingerprints, actual source seasons, training/reference cutoffs, exact all-data or evaluation split lineage, metrics, calibration status and promotion status. `current.json` is the sole active pointer; `activations/` retains verified activation history for rollback. Keep old versions for pinned sessions. Candidates and their `latest_status.json` live under `analysis/outputs/models/candidates/`; historical backtest roots are explicitly chosen and never promote.

`analysis/published/data/models/versions/{version}/` contains immutable `feature-space.json`, `draft-model.json` and `metadata.json`, verified and readable by the web server before activation. These registry, candidate, report and browser artifacts remain generated and ignored, like season model outputs. Back up the complete server registry together with its published versions; restoring a pointer alone is insufficient. Existing season artifacts are preserved for explicitly requested legacy calls.

All-data production records `production_all_data`, a canonical retrain identity, fixed recipe, pinned maintained-input hashes and an explicit uncalibrated temperature1 sidecar. `analysis/outputs/models/inputs/herolist.json` is a generated, validated Tencent catalog cache used when a new legal hero lacks a lane in maintained data. It is copied into each new bundle. Automatic vocabulary expansion changes only missing rows in maintained `analysis/hero_draft_feature_vectors.json`; such rows mark unknown traits explicitly and preserve existing vectors. Historical seed bundles retain copied old inputs.

Production training appends diagnostic stage/process peaks and available Linux
cgroup memory, swap, OOM and pressure readings to
`analysis/outputs/models/candidates/training_memory.jsonl`. `KPL_MEMORY_LOG`
can redirect these diagnostics. `deploy/profile-update.py` writes isolated
full-update benchmark logs, sampled process/container memory, summary and job
result to `analysis/outputs/memory_profiles/{run}/`. Its temporary snapshot owns
a separate SQLite backup, exports, registry and publication; it forces a fresh
full-budget model fit without changing the normal workspace's active pointer.
These diagnostic and benchmark artifacts remain generated and ignored.
