# Draft Atlas

## Frontend architecture

The homepage's hero matchup and lineup tools render from a bundled, names-and-lanes
catalog immediately. Pinned model vocabulary and selected-season evidence load
in the background; model actions wait for readiness. Explore fetches its feature
map only when opened, and the historical-lineup selector fetches battles on demand.

The Vue frontend uses history-mode Vue Router for page URLs and route-level lazy
loading. Shared `useSeasonCatalog`, `useLatestRequest`, and `usePolling`
composables keep season readiness filtering, cancellation, and monitor lifecycle
consistent across views. Published JSON artifacts are cached with bounded
freshness; publishing explicitly invalidates the affected season cache.

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
- Hero feature-space explorer exported from the active production policy’s frozen bag representation
- An evidence-backed Kimi Draft Coach (optional; the key stays on the backend)
- A repeatable data pipeline: sync → export → model/analysis → publish

## Architecture

```text
KPL public APIs
      │
      ▼
FastAPI ──► SQLite job ledger ──► sequential API job runner ──► trainer subprocesses
  ▲                                                │
  │                                                ▼
  │                                   SQLite + published JSON assets
  │                                                │
  └──────────── Vue + Vite UI ◄─────────────────────┘
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

Maintenance updates execute directly in the API process. The API starts one
background runner automatically, so running Uvicorn is sufficient. There is no
separate pipeline worker or scheduler process to start.

Requests are committed to SQLite before returning HTTP 202 and a progress URL.
The API runner handles one job at a time, launches sequential trainer scripts,
and preserves queued work across API restarts. The current all-data model
recipe and versioned activation remain unchanged. Run one Uvicorn process.

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
assets. The UI reports queued job progress and which artifacts are ready for
the chosen season. The API automatically executes these updates in its
background runner; the production Compose file needs no separate worker. Maintenance mutation calls return HTTP
202 with a job ID and `status_url` to poll.

Automatic model training is enabled by default (`AUTO_MODEL_TRAINING_ENABLED=true`).
Set it to false only to temporarily keep factual analysis and publication running
while retaining the existing model. Jobs explicitly report deferred model training.
See `deploy/README.md` for small-host monitoring and deployment instructions.

The daily 03:00 China-time job refreshes the official league catalog and picks
the newest started competition with a completed match. This job policy is
independent of the website default. New visits open the season saved by
Management, or the newest full locally synced season before a default is saved.
More changes the season for that visit across public pages; reloads start again
from the site default. Older browser season preferences are ignored.

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
| `/rankings` | Season-only team Elo plus player rankings by position and hero |
| `/feature-space` | Learned hero representation plus favorite-aware, multi-opponent hero recommendations |
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
  personalized_draft_choice_model.json
  personalized_draft_probability_calibration.json
  player_draft_context.json
  lineup_value_model.json
  ban_value_model.json
  power_rankings.json
  team_*.jsonl

analysis/published/data/
  browser-ready JSON assets
```

The derived statistics include ban responses, pick synergies, counter-picks,
counter-bans, opening-priority meta heroes, team-specific combinations, and
season-only power rankings. Each season resets team Elo to 1500; no earlier
season contributes to team or player boards. Rankings retain a 180-day
evidence half-life within the selected season: team
scores blend opponent-adjusted Elo with a decayed Bayesian win rate, while
player scores blend role-normalized KDA and performance metrics with
small-sample shrinkage. Player boards are available both by position across all
heroes and by individual hero.
Management’s year and season controls immediately save the site-wide default in SQLite. New visits and full reloads start from that setting, ignoring older browser season preferences. Before a default has been saved, the newest season in the full locally synced league catalog is selected, including seasons with zero artifacts.

More is the only public season selector. Its choice is temporary for that page-load session and shared by hero, lineup, simulator, Rankings, BP Data, and Teams pages. It does not save the default or change Management’s independent operational target. A successful Management save immediately updates the public selection in that same tab, including a previous More choice. Visitors in other tabs or sessions keep their current selection until their next visit. Initialization, refreshes, and job completion never save a default; submitted jobs retain their original season.

The uncached public read `/api/site-default` is independent of analysis publication. Management writes `/api/leagues/site-default` through the existing authenticated production Nginx boundary. Public season options use the uncached live `/api/leagues?factual=true` full catalog; the ordinary league response is unchanged. Missing factual observations or published artifacts still show “No current information,” and season-only ranking calculations remain unchanged. Model algorithms and source seasons are unchanged.

Unplayed fixture teams are unranked at 1500 Elo, with no invented scores or win
rates. Unknown rosters remain empty. Legacy cross-season ranking artifacts are
unavailable in factual views until that season's `power_rankings` step is rerun
and its frontend assets are published; no model retraining is required.

Candidate rates use legal opportunities as their denominator, with smoothing
and confidence intervals so sparse observations remain visible as sparse.

For manual runs, script descriptions and commands live in
[analysis/README.md](analysis/README.md). A complete map of the JSON and JSONL
files used by the site is in [ARTIFACTS.md](ARTIFACTS.md). The pipeline
endpoints are:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/sync/leagues` | Queue a league catalog refresh |
| `POST` | `/api/sync/league-bp` | Queue an incremental match and BP sync |
| `POST` | `/api/pipeline/run` | Queue one analysis step or the full pipeline |
| `POST` | `/api/pipeline/publish` | Queue browser-ready asset publication |
| `POST` | `/api/jobs/scheduled` | Queue the idempotent daily 03:00 China-time refresh; optional `league_id` pins a league |
| `POST` | `/api/jobs/full-update` | Queue a forced data, model, and site refresh |
| `GET` | `/api/jobs`, `/api/jobs/{id}` | Inspect persisted job progress and results |
| `GET` | `/api/data/status` | Inspect local source and artifact readiness |
| `POST` | `/api/simulations/recommend-lineup` | Rank realistic next picks or bans through policy-guided completed-draft rollouts |
| `POST` | `/api/simulations/score-lineup` | Directly score one complete legal 5v5 lineup comparison |

The interactive API reference at `/docs` is the authoritative request schema.

## Lineup recommendation workflow

After every BP-state change, the BP Simulator automatically evaluates the ten
highest-probability legal actions from the selected draft policy. For each
candidate it forces that action, samples 24 legal completions of the remaining
draft, scores every completed 5v5 lineup, and displays the top three choices.

The ranker combines:

- the existing statistical, learnable, or sequence model as the behavior
  policy;
- role and Global-BP legality at every simulated action;
- the v3 lineup-advantage artifact for team strength, team/hero familiarity,
  role coverage, team-specific pairs, and historical counters;
- a risk-mode uncertainty penalty over the rollout distribution.

The management page's full analysis flow retrains that value model for the
selected season and stores it beside the other season outputs. Training uses
only that season and chronologically earlier seasons, so rebuilding an older
season cannot learn from future match results. The API automatically prefers
the managed season artifact and falls back to the bundled snapshot until the
first successful build.

Ban turns use a separate opponent-denial model. It combines opponent hero
preference and results, visible-pick synergy and counter evidence, historical
ban outcomes, the acting team's opportunity cost, and the BP policy's behavior
probability. Pick turns continue to use completed-lineup rollouts. The API
selects the appropriate recommender automatically from the next BP action.

The returned `expected_advantage` is a relative ranking score, not a calibrated
or guaranteed win probability. Tank, engage, hard-control, and mage counts are
returned as explanations; they are not hard-coded automatic bonuses. The
current production implementation optimizes the current game's completed
lineup while enforcing prior-game Global-BP exclusions. A completed what-if
sample can be promoted into a conditional BO5/BO7 timeline; the user supplies
each assumed result and next-game side assignment, while validated transitions
carry team-specific hero pools and lineup-value guidance across games. A
bounded batch mode accepts a complete result/side schedule and samples up to
five coherent remaining-series draft trajectories (50 total completions),
stopping at the earliest series win or pausing for manual BO7 game-seven peak
duel lineups. It does not forecast winners or optimize recursive series-level
hero-pool opportunity cost.

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

Public commentary and Draft Coach share the same paid-provider admission
budget, including concurrency, per-client and server-wide request limits.
Live-match requests and public writes have separate budgets. Live requests
validate local fixtures before contacting KPL and use bounded, expiring caches.
Visitor and prediction identity comes from a signed HttpOnly cookie; the old
client UUID remains accepted for request compatibility but cannot create a
second vote within that cookie session. Predictions require the authoritative
fixture, its best-of format, and an open pre-match or current-game window.
Anonymous cookies identify sessions, not unique people. Analytics preserves
its historical aggregate totals and stores only canonical public route names.

The visitor signing key is generated once at
`backend/data/public_session.key` with owner-only permissions and persists
through API restarts. It is ignored by Git. A configured
`PUBLIC_SESSION_SECRET` (at least 32 random characters) overrides that file.
Proxy identity settings remain opt-in; sanitize forwarded headers at the
trusted gateway before enabling them. `CF-Connecting-IP` is not used as client
authority and is removed by the supplied Nginx proxy configuration.

Coach retention deletes expired conversation metadata and checkpoints
together. Service initialization also reconciles checkpoints orphaned by
older versions, and session activity triggers expiry cleanup at most once per
minute. Match-data SQLite remains separate from Coach persistence.

The public calendar uses one shared, short-lived 17-day range request for the
widget and welcome popup. The API still supports existing single-day calls.
Both views group fixtures by Beijing date. The widget labels times as Beijing
time and includes the full day's schedule, including matches already started.
The simulator's ordinary moves use `/api/simulations/next-action` to return the
full legal distribution without completing 50 unused drafts. Recommendations
run separately in the background; only one search runs at a time and queued
work uses the newest board. What-if simulations retain their completion budget.
The simulator chooses its current or next unfinished fixture from the selected
season's full schedule, including teams without recorded games. Fixture teams
are selectable without being counted as player observations or ranked teams.
Ranking readiness and JSONL record counts are cached by file identity, size,
and modification timestamps; the public season catalog and default remain
uncached HTTP reads. Sync skips only series with the expected game count,
complete supported drafts and player detail, and persists each fetched battle
in a short transaction. Publication replaces all supported relationship shards,
including empty ones.

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

### Active-model management updates

Full Management updates synchronize the selected operational season, rebuild and publish its factual display statistics, then update one shared rolling production bundle. Model scope is independent of the public season selector. A failed model update leaves published facts and the active bundle intact.

Production trains all eligible complete series across available exports. There are no reserved evaluation windows or minimum number of new Season 4 games: the first complete usable series enters the next update. The established .65 season-recency weights give the newest observed season the highest weight. Bag, GRU and familiarity stages use fixed 30-epoch training; lineup fitting uses the established fixed configuration, without a parameter search. Ban, player context, references, map and all neural components share the pinned complete-series corpus. Unchanged eligible content, maintained inputs and recipe produce `NO_CHANGE` and skip retraining.

Initialize from an existing verified compatible historical collection if needed:

```sh
python analysis/seed_model_bundle.py --league-id 20260003 --version historical-seed-20260003 --activate
```

Run the normal all-data update:

```sh
python analysis/train_rolling_bundle.py --activate
```

The bundle validates exact component lineage, hashes, vocabulary, lane legality, finite numeric inputs and calibration contracts before atomic publication. Activation uses an incumbent comparison under a process lock; previously activated versions remain available for rollback and pinned sessions. Refitted production probabilities are explicitly **uncalibrated, temperature 1**. Historical temperatures are never transferred to new weights. Factual Season 4 counts and fixtures remain Season 4 only.

New observed/legal hero IDs expand the maintained feature vocabulary automatically. Missing verified capability traits use an explicitly unknown neutral encoding, with reduced mechanics coverage. Catalog lanes come from pinned completed rosters and official Tencent lane data. Missing ban-only lane evidence triggers a bounded official-catalog refresh; unavailable authoritative lanes produce a clear failed update while preserving the active version. Existing hero vectors and old immutable bundles are preserved.

`/api/simulations/active-model` reports version, sources, cutoffs and calibration status. Each mounted tool pins one version through recommendations, scoring, Coach and what-if requests. Explicit unversioned legacy APIs and season trainers remain supported.

Historical evaluation remains an optional offline research path:

```sh
python analysis/backtest_rolling_bundle.py --cutoff '2026-04-01 23:59:59' --output-root /tmp/rolling-backtest --epochs 1 --trials 1 --threads 1 --alternative date_half_life --half-life-days 120
```

The prior evaluated-candidate command is available through `train_rolling_bundle.py --evaluation-mode`. These paths retain separate chronological windows and comparison gates. Low-budget runs are experimental and cannot activate. To exercise the all-data path separately:

```sh
python analysis/train_rolling_bundle.py --output-root /tmp/all-data-smoke --epochs 1 --threads 1 --smoke
```

A smoke run checks execution and component consistency; it establishes neither model quality nor Season 4 validation. The learned map describes the bag branch, while displayed pick/ban counts use only selected-season observations.
