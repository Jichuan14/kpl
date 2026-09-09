# Draft Atlas repository guide

Read this file before searching the repository. Use the map below to open the smallest relevant set of files. Start with the listed documentation for explanation questions; inspect implementation only when the documentation is insufficient or the task requires a code change. Keep this guide current when responsibilities move.

## Product map

Draft Atlas is a local-first KPL draft-analysis system:

`KPL APIs -> FastAPI/SQLite -> analysis pipeline -> published JSON -> Vue UI`

Primary overview and setup: `README.md`

Calculation definitions and interpretation limits: `CALCULATION_METHODOLOGY.md`

Generated artifact locations and Git policy: `ARTIFACTS.md`

## Where to look

| Question or task | Start here | Related locations |
| --- | --- | --- |
| App routes, navigation, season selection, management UI | `frontend/src/App.vue` | `frontend/src/selectedLeague.js`, `frontend/src/style.css` |
| Hero comparison, matchup recommendations, feature space | `frontend/src/HeroFeatureSpacePage.vue` | `frontend/src/LineupAnalyzerWidget.vue`, `frontend/src/heroAssets.js` |
| BP relationship evidence and season priorities | `frontend/src/VisualizationPage.vue` | `frontend/src/api.js`, `CALCULATION_METHODOLOGY.md` sections 3–6 |
| BP simulator | `frontend/src/DraftSimulatorPage.vue` | `frontend/src/DraftCoachPanel.vue`, `backend/app/api/simulation.py`, `backend/app/services/draft_simulator.py` |
| Team pair analysis | `frontend/src/TeamSynergyPage.vue` | `analysis/compute_team_synergies.py`, `backend/app/api/visualization.py` |
| Team and player rankings | `frontend/src/RankingsPage.vue` | `analysis/compute_power_rankings.py`, `backend/app/api/visualization.py` |
| Method explanations | `frontend/src/MethodologyPage.vue` | `CALCULATION_METHODOLOGY.md` |
| Frontend API calls | `frontend/src/api.js` | matching module under `backend/app/api/` |
| Chinese/English text | `frontend/src/i18n.js` | page-local copy in the relevant Vue component |
| League, match, hero, team, and player endpoints | `backend/app/api/leagues.py`, `backend/app/api/bp.py`, `backend/app/api/data.py` | `backend/app/models.py`, `backend/app/schemas.py` |
| Syncing official KPL data | `backend/app/api/sync.py` | `backend/app/services/sync.py`, `backend/app/clients/kpl_api.py` |
| Analysis and publishing pipeline | `backend/app/services/analysis_pipeline.py` | `backend/app/api/pipeline.py`, `backend/app/services/static_publisher.py` |
| Hero relationship statistics | `analysis/compute_bp_statistics.py` | `analysis/build_bp_decisions.py`, `analysis/common.py` |
| Draft models and calibration | `analysis/train_production_draft_policy.py` | `analysis/sequence_training/`, `analysis/fit_draft_calibration.py` |
| Lineup value and Ban value | `analysis/lineup_value/`, `analysis/train_lineup_value_model.py`, `analysis/train_ban_value_model.py` | `backend/app/services/lineup_value.py`, `backend/app/services/ban_recommender.py` |
| Coach/RAG behavior | `backend/app/agent/` | `backend/app/api/coach.py`, `backend/app/knowledge/` |
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
