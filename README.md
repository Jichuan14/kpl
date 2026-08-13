# Draft Atlas

Draft Atlas is a local-first exploration tool for **King Pro League (KPL)**
ban/pick data. It downloads official match data into SQLite, turns completed
seasons into analysis artifacts, and presents the results through an interactive
Vue application.

It is designed for studying drafts, not for making unsupported claims: every
relationship is computed from observed, legal draft opportunities and carries
its sample size, baseline, and confidence information.

## What it includes

- A season-aware Draft Atlas with hero relationships and meta signals
- An interactive BP simulator with statistical and learnable draft models
- Team Synergy Lab for team-specific hero pair tendencies
- Hero feature-space explorer produced by the learnable model
- An evidence-backed Kimi Draft Coach (optional; the key stays on the backend)
- A repeatable data pipeline: sync → export → model/analysis → publish

## Architecture

```text
KPL public APIs
      │
      ▼
FastAPI service ──► SQLite ──► analysis scripts ──► published JSON assets
      │                                                        │
      └──────────────────── REST API ◄─────────────────────────┘
                                                               │
                                                               ▼
                                                        Vue + Vite UI
```

The SQLite database is the source of truth. Analysis outputs are scoped to a
league ID under `analysis/`; browser-ready assets are generated from those
outputs rather than treated as source data.

## Quick start

### Prerequisites

- Python 3.12 or newer
- Node.js 20 or newer
- npm

### 1. Start the API

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install --index-url https://download.pytorch.org/whl/cpu \
  -r requirements-training.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

The API is available at [http://localhost:8000/docs](http://localhost:8000/docs).
It creates `backend/data/kpl_bp.db` on first start.
PyTorch is needed only when the private management pipeline retrains the
chronological model; normal inference remains NumPy-only.

### 2. Start the web app

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). During development, Vite
proxies `/api` calls to the API on port 8000.

### 3. Load a season and build its artifacts

Use the **Management** screen to refresh the league catalog, select a season,
download its finished matches, run the analysis pipeline, and publish frontend
assets. The UI is the recommended path because it reports which artifacts are
ready for the chosen season.

For a small API smoke sync instead:

```bash
curl -X POST http://localhost:8000/api/sync/league-bp \
  -H 'Content-Type: application/json' \
  -d '{"league_id":"20260003","match_limit":3}'
```

`league_id` must be a league already present locally; fetch the current catalog
first with `POST /api/sync/leagues`. Leave `match_limit` out to process all
available finished matches. Normal syncs are incremental and avoid re-fetching
complete battles.

Newly downloaded battles persist official player performance values, including
K/D/A, KDA, gold, damage, participation, and MVP metrics. To backfill battles
downloaded before this support was added, run the endpoint once with
`incremental` set to `false` (use `match_limit` for a small validation batch):

```bash
curl -X POST http://localhost:8000/api/sync/league-bp \
  -H 'Content-Type: application/json' \
  -d '{"league_id":"20260003","match_limit":3,"incremental":false,"run_analysis":false}'
```

`performance_rows_written` reports how many player rows contained usable
performance data. Historical all-zero API placeholders are retained with
`performance_data_available = 0`.

## Application areas

| Route | Purpose |
| --- | --- |
| `/` | Multi-season Draft Atlas relationship explorer |
| `/simulator` | Live draft board, recommendations, and Draft Coach |
| `/teams` | Team-specific synergy patterns and draft tendencies |
| `/rankings` | Time-decayed team Elo plus player rankings by position and hero |
| `/feature-space` | Learned hero representation for the selected season |
| `/methodology` | Definitions, caveats, and calculation explanations |
| `/management` | Local data sync, analysis, and asset publishing |

## Data and analysis pipeline

One sync stores league, match, battle, BP action, hero, team, and player data.
The analysis pipeline then produces a selected season's exports and derived
artifacts:

```text
analysis/exports/{league_id}/
  matches.jsonl
  bp_decisions.jsonl

analysis/outputs/{league_id}/
  *_stats.jsonl
  *_draft_model.json
  sequence_draft_choice_model.json
  power_rankings.json
  team_*.jsonl

analysis/published/data/
  browser-ready JSON assets
```

The derived statistics include ban responses, pick synergies, counter-picks,
counter-bans, opening-priority meta heroes, team-specific combinations, and
cross-season power rankings. Rankings use a 180-day evidence half-life: team
scores blend opponent-adjusted Elo with a decayed Bayesian win rate, while
player scores blend role-normalized KDA and performance metrics with
small-sample shrinkage. Player boards are available both by position across all
heroes and by individual hero.
Candidate rates use legal opportunities as their denominator, with smoothing
and confidence intervals so sparse observations remain visible as sparse.

For manual runs, script descriptions and commands live in
[analysis/README.md](analysis/README.md). A complete map of the JSON and JSONL
files used by the site is in [ARTIFACTS.md](ARTIFACTS.md). The pipeline
endpoints are:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/sync/leagues` | Refresh the locally stored league catalog |
| `POST` | `/api/sync/league-bp` | Incrementally sync matches and BP actions |
| `POST` | `/api/pipeline/run` | Run one analysis step or the full pipeline |
| `POST` | `/api/pipeline/publish` | Write browser-ready assets for a season |
| `GET` | `/api/data/status` | Inspect local source and artifact readiness |

The interactive API reference at `/docs` is the authoritative request schema.

## macOS visitor widget

An optional, read-only SwiftBar menu-bar plugin displays today's unique
visitors and page views. It uses a dedicated Bearer token stored in the macOS
Keychain; setup and token-rotation instructions are in
[`macos/swiftbar/README.md`](macos/swiftbar/README.md).

## Optional: enable Draft Coach

Draft Coach is disabled unless `MOONSHOT_API_KEY` is configured in the ignored
`backend/.env` file. Copy the provided environment template, then add your key:

```env
MOONSHOT_API_KEY=your-key
KIMI_BASE_URL=https://api.moonshot.ai/v1
KIMI_MODEL=kimi-k2.6
```

Use `https://api.moonshot.cn/v1` for a key issued by `platform.kimi.com`.
Never place the key in frontend code or a committed environment file.

Check the integration from `backend/`:

```bash
./.venv/bin/python -m app.agent.smoke_test
```

The coach endpoint is `POST /api/coach`. It returns an answer together with
structured evidence, warnings, token usage, model, and request ID. It can also
accept a current draft state from the simulator.

## Development checks

Run the backend test suite from the repository root. `pytest` is intentionally
not a runtime dependency, so install it once in the backend environment:

```bash
./backend/.venv/bin/pip install pytest
./backend/.venv/bin/python -m pytest backend/tests
```

Build the frontend before release:

```bash
cd frontend
npm run build
```

The agent's non-billed evaluation catalogs can be checked with:

```bash
cd backend
./.venv/bin/python -m app.agent.eval_phase1
./.venv/bin/python -m app.agent.eval_phase2
```

## Project layout

```text
backend/       FastAPI app, database models, sync service, and coach tools
frontend/      Vue 3 + Vite interface
analysis/      Reproducible exports, statistics, and draft-model scripts
deploy/        Single-host Docker/ECS deployment material
agent/         Product decisions, roadmap, and evaluation notes
```

## Deployment

`docker-compose.production.yml` runs the frontend and API on one host. It
persists the SQLite database and generated artifacts on that host's disk, and
the production API intentionally uses a single Uvicorn worker.

This is a single-host SQLite deployment: do not share the database over network
storage or run multiple API instances against it. See
[deploy/README.md](deploy/README.md) for the ECS setup, access control,
backups, and update procedure.

## Notes on data use

KPL source availability and completeness can vary by season. Treat the app's
outputs as exploratory, season-scoped evidence, and inspect sample sizes and
quality indicators before drawing conclusions.
