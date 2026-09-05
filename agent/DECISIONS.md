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

## ADR-006: Make LangGraph the sole feature orchestration path

- Date: 2026-09-04
- Status: Accepted

Evidence planning, validation, bounded repair, conversation checkpoints, and
streaming are implemented as LangGraph state and conditional paths. The legacy
loop remains a small emergency fallback and does not receive duplicate feature
implementations.

## ADR-007: Separate conversation authority from LangGraph persistence

- Date: 2026-09-04
- Status: Accepted

The browser receives a server-issued conversation ID bound to an opaque
HttpOnly session cookie. That application boundary authorizes access; the same
ID is then used as LangGraph's thread ID. Conversation metadata and LangGraph
checkpoints use dedicated SQLite files rather than the match-data database.

## ADR-008: Validate structured claims before publication

- Date: 2026-09-04
- Status: Accepted

Successful tool execution alone does not verify an answer. The graph records
subject/metric/value evidence, rejects impossible, subject-mismatched, and
metric-mismatched percentages, and treats empty results as no affirmative
evidence. It prevents relative advantage from being labeled win probability
and returns a supported partial result when repair or time budgets are exhausted.
This deterministic validation protects structured metrics; it is not presented
as proof that every qualitative sentence is entailed.

## ADR-009: Keep rollout evaluation offline until explicitly authorized

- Date: 2026-09-04
- Status: Accepted

The Phase 3 catalog validates case structure and scores routing, numeric
grounding, context, coverage, and usefulness independently without creating a
provider client. Live Quick/Analysis comparisons require a separate cost/token
ceiling and authorization. Until then, no end-to-end quality percentage or
p50/p95 provider latency is claimed; retry-induced latency must be reported
separately when that review eventually runs.
