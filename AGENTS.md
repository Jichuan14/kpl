# Draft Atlas repository guide

Read this file before searching the repository. Use the map below to open the smallest relevant set of files. Start with the listed documentation for explanation questions; inspect implementation only when the documentation is insufficient or the task requires a code change. Keep this guide current when responsibilities move.

## Working season context

Unless the user specifies otherwise, treat all new work as targeting the newly started **2026 Season 4** (`league_id: 20260004`, local catalog name: `2026年KPL年度总决赛`). Use this context for website presentation, management workflows, model planning, and verification.

Account for zero or sparse current-season observations and unavailable current-season artifacts. Treat earlier seasons as explicitly labeled historical evidence or model-training sources; do not present them as Season 4 observations or assume historical model validation establishes Season 4 validity. Preserve historical-season functionality and avoid hardcoding the default context into season-independent calculations.

## Product map

Draft Atlas is a local-first KPL draft-analysis system:

`KPL APIs -> FastAPI/SQLite -> analysis pipeline -> published JSON -> Vue UI`

Primary overview and setup: `README.md`

Calculation definitions and interpretation limits: `CALCULATION_METHODOLOGY.md`

Generated artifact locations and Git policy: `ARTIFACTS.md`

## Where to look

| Question or task | Start here | Related locations |
| --- | --- | --- |
| App routes, navigation, season selection, management UI | `frontend/src/App.vue` | `frontend/src/selectedLeague.js`, `frontend/src/managementSeasonState.js`, `backend/app/services/site_settings.py`, `frontend/src/style.css` |
| Frontend routing and shared async behavior | `frontend/src/router.js` | `frontend/src/composables/`, `frontend/src/seasonStartup.js`, `frontend/src/storage.js` |
| Hero comparison, matchup recommendations, feature space | `frontend/src/HeroFeatureSpacePage.vue` | `frontend/src/LineupAnalyzerWidget.vue`, `frontend/src/lineupRelationships.js`, `frontend/src/heroAssets.js` |
| BP relationship evidence and season priorities | `frontend/src/VisualizationPage.vue` | `frontend/src/api.js`, `CALCULATION_METHODOLOGY.md` sections 3–6 |
| BP simulator | `frontend/src/DraftSimulatorPage.vue` | `frontend/src/DraftCoachPanel.vue`, `backend/app/api/simulation.py`, `backend/app/services/draft_simulator.py` |
| Team pair analysis | `frontend/src/TeamSynergyPage.vue` | `analysis/compute_team_synergies.py`, `backend/app/api/visualization.py` |
| Team and player rankings | `frontend/src/RankingsPage.vue` | `analysis/compute_power_rankings.py`, `backend/app/api/visualization.py` |
| Method explanations | `frontend/src/MethodologyPage.vue` | `CALCULATION_METHODOLOGY.md` |
| Frontend API calls | `frontend/src/api.js` | matching module under `backend/app/api/` |
| Chinese/English text | `frontend/src/i18n.js` | page-local copy in the relevant Vue component |
| League, match, hero, team, and player endpoints | `backend/app/api/leagues.py`, `backend/app/api/bp.py`, `backend/app/api/data.py` | `backend/app/models.py`, `backend/app/schemas.py` |
| Syncing official KPL data | `backend/app/api/sync.py` | `backend/app/services/sync.py`, `backend/app/clients/kpl_api.py` |
| Queue-backed update jobs and 03:00 China-time refresh | `backend/app/services/pipeline_jobs.py` | `backend/app/api/jobs.py`, `deploy/kpl-refresh`, `docker-compose.production.yml` |
| Small-host training deferral and worker memory failures | `deploy/README.md` | `backend/app/config.py` (`AUTO_MODEL_TRAINING_ENABLED`), `analysis/train_rolling_bundle.py`, `backend/app/services/analysis_pipeline.py` |
| Analysis and publishing pipeline | `backend/app/services/analysis_pipeline.py` | `backend/app/api/pipeline.py`, `backend/app/services/static_publisher.py` |
| Hero relationship statistics | `analysis/compute_bp_statistics.py` | `analysis/build_bp_decisions.py`, `analysis/common.py`, `analysis/statistical_helpers.py` |
| Researching ban/pick intentions | `analysis/DRAFT_INTENTION_RESEARCH.md` | `analysis/explore_draft_intentions.py`, `analysis/run_intention_case_study.py` (offline; no model changes) |
| Historical BP evidence dossiers and simulator move evidence | `analysis/DRAFT_EVIDENCE_SPEC.md` | `analysis/build_draft_evidence.py`, `analysis/draft_evidence/`, `backend/app/services/draft_evidence.py`, `frontend/src/DraftEvidenceExplorer.vue` |
| Draft models and calibration | `analysis/train_rolling_bundle.py`, `backend/app/services/model_registry.py` | `analysis/production_all_data.py`, `analysis/rolling_corpus.py`, `analysis/backtest_rolling_bundle.py`, `analysis/train_production_draft_policy.py`, `analysis/sequence_training/` |
| Production hero feature-map export | `analysis/export_production_hero_feature_space.py` | `backend/app/services/analysis_pipeline.py`, `backend/app/api/data.py` |
| Lineup value and Ban value | `analysis/lineup_value/`, `analysis/train_lineup_value_model.py`, `analysis/train_ban_value_model.py` | `backend/app/services/lineup_value.py`, `backend/app/services/ban_recommender.py` |
| Coach/RAG behavior | `backend/app/agent/` | `backend/app/api/coach.py`, `backend/app/knowledge/` |
| Public request budgets, signed visitor identity, and artifact summary caching | `backend/app/services/public_requests.py` | `backend/app/services/provider_budget.py`, `backend/app/services/request_identity.py`, `backend/app/services/file_summary_cache.py` |
| Deployment | `docker-compose.production.yml`, `deploy/` | `frontend/nginx.conf`, `frontend/Dockerfile` |
| macOS menu-bar companion | `macos/` | `README.md` section "macOS visitor widget" |

## Data locations

- SQLite source of truth: `backend/data/`
- Season exports: `analysis/exports/{league_id}/`
- Generated analysis artifacts: `analysis/outputs/{league_id}/`
- Browser-ready published data: `analysis/published/data/{league_id}/`
- Shared maintained inputs: `analysis/*.json` files documented in `ARTIFACTS.md`

Do not infer that a missing compact published relationship means there is no evidence; consult the full relation artifact or endpoint. Keep legal-opportunity denominators, smoothing, baselines, confidence intervals, and descriptive-versus-causal distinctions intact.

## Change boundaries

- Public pages share one visit-only season selection (`selectedLeague.js` / `useSeasonCatalog.js`). More is the sole public season selector. Management year/season controls save a database-backed site-wide default for new visits and own a separate operational job target. Successful deliberate saves immediately update the saving tab’s shared public selection, overriding its temporary More choice; other visitor sessions are not broadcast to. Failed saves, initialization, and job refreshes never update the public default. Never persist visitor overrides, restore old season localStorage, silently select by artifact readiness, or let public choices retarget Management jobs. Initialization, catalog refresh, and job completion must not write the saved default. Before any admin choice, resolve the newest full local league catalog; zero-artifact seasons stay selectable. Factual pages preserve “No current information” for missing observations/artifacts.
- Power rankings are season-only (schema 3, `evidence_scope=season_only`): Elo resets to 1500, unplayed fixture teams stay unranked, and missing player observations never become prior-only scores. Legacy cross-season rankings need targeted regeneration and publication.

- Preserve the BP simulator's legacy visual treatment unless the user explicitly includes it.
- Existing mobile omissions of filters and statistics are intentional unless the user asks to change them.
- Do not add fallback statistics or invent model outputs when artifacts are unavailable.
- Preserve bilingual behavior, season scoping, real hero assets, calculations, and route URLs.
- Treat generated artifacts according to `ARTIFACTS.md`; do not casually commit databases, model outputs, or generated archives.

## Verification

Frontend:

```sh
cd frontend
npm test
npm run build
```

Backend and analysis tests are under `backend/tests/` and `analysis/tests/`. Run the smallest relevant test set first, then broaden when the change crosses subsystem boundaries.
