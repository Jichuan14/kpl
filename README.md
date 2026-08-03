# KPL Hero BP Analysis

Backend for ingesting KPL official match/BP data into SQL and serving hero ban/pick stats for a future frontend.

## Stack (this repo)

| Piece | Choice | Why |
|---|---|---|
| API | **FastAPI** (Python) | Easy REST + great for later pandas analysis |
| DB | **SQLite** locally → **Postgres** when hosting | Relational fit for matches/BP; simple hosting |
| ORM | SQLAlchemy 2 | Clean models + raw SQL when needed |
| HTTP client | httpx | Calls official KPL open APIs |

### Compared to what you know / the reference project

| | Your past | Reference `kpl-agent` | This project |
|---|---|---|---|
| Runtime | Node.js | Java 17 / Spring Boot | Python / FastAPI |
| Database | MongoDB | MySQL | SQLite → Postgres |
| Cache | — | Redis | process-local versioned artifact cache |
| Frontend | — | Vue 3 | Vue 3 + Vite |

Mongo is fine for flexible docs; BP analysis is mostly **joins + rates by hero/league**, which SQL handles more naturally.

## Quick start

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

uvicorn app.main:app --reload --port 8000
```

Open docs: http://localhost:8000/docs

### Configure and test Kimi

Add the real API key only to the ignored `backend/.env` file:

```env
MOONSHOT_API_KEY=your-real-key
```

The endpoint must match where the key was created:

- `platform.kimi.ai` key: `KIMI_BASE_URL=https://api.moonshot.ai/v1`
- `platform.kimi.com` key: `KIMI_BASE_URL=https://api.moonshot.cn/v1`

Do not add it to `.env.example`, frontend code, or a committed file. From the
`backend` directory, run the inexpensive live connectivity test:

```bash
./.venv/bin/python -m app.agent.smoke_test
```

The test hides the key, caps the response at 64 tokens, and exits nonzero for a
missing or rejected key, provider error, or incompatible tool loop.

Validate the Phase 1 and team-aware Phase 2 question catalogs without making a paid API call:

```bash
./.venv/bin/python -m app.agent.eval_phase1
./.venv/bin/python -m app.agent.eval_phase2
```

The opt-in `--live` form runs the bounded end-to-end Kimi routing evaluation
and writes its redacted report to `agent/evals/phase_1_live_report.json`.

With the backend running, test the full HTTP endpoint:

```bash
curl -X POST http://localhost:8000/api/coach \
  -H 'Content-Type: application/json' \
  -d '{
    "message":"What are the top five meta heroes this season? Include evidence.",
    "league_id":"20260003"
  }'
```

Questions about an active draft may also include `draft_state` with `bp_order`,
the four current pick/ban lists, the two previous-battle-used lists, and the
selected `model_type`. The response separates the answer, structured evidence,
warnings, token usage, model, and request ID.

### Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open:

- `http://localhost:5173/` — public multi-season Draft Atlas visualization
- `http://localhost:5173/simulator` — live BP simulator with the Kimi Draft Coach
- `http://localhost:5173/teams` — team-specific hero synergy analysis
- `http://localhost:5173/methodology` — calculation definitions and examples
- `http://localhost:5173/management` — local download and analysis management

Vite proxies `/api` to the backend on `:8000`.

The Draft Coach automatically sends the selected season, validated Blue/Red
teams, and current simulator
board to the backend. Its response includes evidence, warnings, model and token
usage, and a request ID. The Kimi key remains in `backend/.env` and is never sent
to the frontend.

### Smoke sync (selected league, first 3 finished matches)

```bash
curl -X POST http://localhost:8000/api/sync/league-bp \
  -H 'Content-Type: application/json' \
  -d '{"league_id":"20260003","match_limit":3}'
```

Each battle detail is downloaded once and used to store battle metadata, BP
actions, team/player mappings, and every encountered hero. Normal syncs are
incremental: they refresh the match list but deep-download only finished matches
without complete local battle data, then rebuild the season analysis and public assets
when new data was added. The management page
at `http://localhost:5173` lets you select the year and season before starting
the download. A completed download automatically exports match and decision
JSONL, computes relationship statistics, and ranks opening meta heroes. The
same analysis stages can also be run separately from the management page.

Season artifacts are isolated under:

```text
analysis/exports/{league_id}/
analysis/outputs/{league_id}/
```

Then:

```bash
curl 'http://localhost:8000/api/bp/heroes?sort=presence&limit=20'
```

## Main endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/api/leagues` | Leagues in DB |
| POST | `/api/sync/leagues` | Pull league list from KPL |
| POST | `/api/sync/league-bp` | Sync matches + BP for a league |
| GET | `/api/bp/heroes?sort=presence` | Hero ban/pick/win aggregates |
| GET | `/api/bp/battles/{battle_id}` | Raw BP sequence for one game |
| POST | `/api/bp/recompute` | Rebuild aggregate table from BP rows |
| POST | `/api/coach` | Ask the evidence-backed Kimi Draft Coach |

`POST /api/sync/league-bp` body:

```json
{
  "league_id": null,
  "match_limit": 3,
  "recompute_stats": true,
  "incremental": true
}
```

Omit `league_id` to use the latest league. Omit `match_limit` to consider all
finished matches. `incremental` defaults to `true`; set it to `false` only for
a deliberate full repair/backfill of a season.

## Data model (BP-focused)

```text
leagues
matches
battles          (includes win_camp)
battle_bps       (action_type: 0=ban, 1=pick)
hero_bp_stats    (precomputed rates for frontend)
```

Win rate for a hero = picks where `pick.camp == battle.win_camp` / pick count.

## Hosting

This project uses one SQLite database stored at `backend/data/kpl_bp.db`.
Keep the API to one process and retain that file alongside the analysis and
published-asset directories. See `deploy/README.md` for the ECS deployment.
