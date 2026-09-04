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
| `analysis/outputs/{league_id}/power_rankings.json` | `compute_power_rankings.py` (`power_rankings`) | Source for the Rankings page's team Elo and player-by-position and player-by-hero boards |
| `analysis/outputs/{league_id}/draft_model.json` | `build_draft_model.py` (`draft_model`) | Draft Simulator and published draft-model metadata |
| `analysis/outputs/{league_id}/learnable_draft_choice_model.json` | `train_learnable_draft_choice_model.py` (`learnable_draft_model`) | Learned scoring in Draft Simulator and model-status API |
| `analysis/outputs/{league_id}/learned_hero_feature_space.json` | `train_learnable_draft_choice_model.py` (`learnable_draft_model`) | Feature Space page and model-status API |
| `analysis/outputs/{league_id}/sequence_draft_choice_model.json` | `train_sequence_draft_choice_model.py` (`sequence_draft_model`) | NumPy chronological bag + GRU scoring in Draft Simulator and Draft Coach |
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
| `analysis/published/data/seasons.json` | Season selectors and page availability | League records plus each season's published files |
| `analysis/published/data/meta-history.json` | Cross-season meta history | Every published `overview.json` |
| `analysis/published/data/{league_id}/overview.json` | Main visualization overview and meta heroes | Relationship statistics, meta heroes, and league metadata |
| `analysis/published/data/{league_id}/patterns/{relation}/{context}.json` | Main relationship tables | The four relationship-stat JSONL files; `relation` is `ban_response`, `pick_synergy`, `counter_pick`, or `counter_ban`, and `context` is normally `overall` or `slot_context` |
| `analysis/published/data/{league_id}/hero-responses.json` | Feature Space hero-response details | Selected rows from the published relationship data |
| `analysis/published/data/{league_id}/battle-lineups.json` | Feature Space historical-lineup selector | Completed 5v5 battle lineups from `matches.jsonl` |
| `analysis/published/data/{league_id}/team-synergies.json` | Teams page | `team_synergy_stats.jsonl` plus league/team metadata |
| `analysis/published/data/{league_id}/rankings.json` | Rankings page | `power_rankings.json` plus league metadata |
| `analysis/published/data/{league_id}/draft-model.json` | Browser-ready draft-model metadata | `draft_model.json` |

`patterns.json` is a retired monolithic artifact. Publishing removes it and
uses the smaller relation/context files above instead.

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
