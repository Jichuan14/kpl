# Agent Architecture Decisions

## ADR-001: Restrict the model to registered tools

- Date: 2026-08-02
- Status: Accepted

The language model may request only backend tools registered by the
application. It may not generate or execute arbitrary SQL or filesystem
operations. This keeps metrics reproducible, queries bounded, and behavior
testable.

## ADR-002: Keep heavy analysis outside chat requests

- Date: 2026-08-02
- Status: Accepted

Season analysis and model generation run after data synchronization. Chat-time
tools perform cached artifact lookups, bounded SQLite queries, or model
inference only. A user question never retrains a model or rebuilds a season.

## ADR-003: Treat SQLite as source of truth and artifacts as read models

- Date: 2026-08-02
- Status: Accepted

Raw match, battle, BP, team, player, and hero data remain in SQLite. Generated
JSON/JSONL artifacts provide reusable statistical views. Artifact-backed tools
will index each file in memory and invalidate the index when its modification
time changes.

## ADR-004: Separate selection prediction from win prediction

- Date: 2026-08-02
- Status: Accepted

The current draft model estimates historically plausible selections. Agent
answers must not describe its output as battle-win probability, optimal play,
or a causal recommendation. Those claims require a separately validated
outcome model or an explicitly labeled heuristic.

## ADR-005: Keep documentation separate from executable agent code

- Date: 2026-08-02
- Status: Accepted

The top-level `agent` directory contains scope, decisions, work logs, and eval
cases. Runtime code belongs under `backend/app/agent` so imports and deployment
remain aligned with the existing FastAPI application.

