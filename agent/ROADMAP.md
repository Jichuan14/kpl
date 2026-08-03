# Agent Roadmap

Statuses: `planned`, `in progress`, `completed`, `blocked`.

## Phase 1: Evidence-backed draft assistant

| Order | Task | Status | Completion gate |
|---:|---|---|---|
| 1 | Define supported questions and boundaries | completed | Question catalog lists supported, combined, and unsupported requests. |
| 2 | Add fast next-action inference | completed | Public function returns ranked legal probabilities without rollouts and has tests. |
| 3 | Create agent tool package and draft tool | completed | Registered `predict_next_draft_action` validates arguments and calls fast inference. |
| 4 | Add artifact cache | completed | JSONL files load once per version and reload after modification. |
| 5 | Add relationship, team-synergy, meta, and hero-stat tools | completed | Each tool returns evidence fields and passes unit tests. |
| 6 | Add future-draft simulation tool | completed | Tool wraps existing bounded rollouts and distinguishes simulation from win probability. |
| 7 | Add Kimi client and tool loop | completed | Approved tools run through a bounded, logged loop with mocked integration tests. |
| 8 | Add `/api/coach` | completed | Endpoint validates board context and returns answer, evidence, warnings, and request ID. |
| 9 | Add Draft Coach frontend panel | completed | Simulator sends current board automatically and renders evidence and errors. |
| 10 | Run Phase 1 evaluation suite | completed | All supported cases route correctly and unsupported cases state limitations. |

## Phase 2: Team-aware draft coach

| Order | Task | Status | Completion gate |
|---:|---|---|---|
| 1 | Define Phase 2 questions and boundaries | completed | Team, side, opponent, player, recent, and unsupported outcome questions are documented. |
| 2 | Build season team-profile artifacts | completed | Pipeline writes roster, tendencies, openings, combos, player pools, and recent trends. |
| 3 | Add Phase 2 evidence tools | completed | Five cached, validated tools are registered and tested. |
| 4 | Add team-conditioned live prediction | completed | Acting-team slot tendencies blend with the selected league model and fall back safely. |
| 5 | Add season-scoped team API | completed | API lists only teams recorded in the selected season and rejects invalid pairs. |
| 6 | Add searchable Blue/Red selectors | completed | Simulation is locked until two distinct dropdown results are selected. |
| 7 | Propagate authoritative team context | completed | Simulator, coach, tools, and Global BP color swaps retain exact team IDs and names. |
| 8 | Add Phase 2 evaluation suite | completed | Ten offline cases cover six Phase 2 routes and four response categories. |
| 9 | Regression verification | completed | Backend tests and production frontend build pass. |

## Deferred outcome-model work

A validated battle-win or optimal-draft model remains out of scope. It must be
trained, calibrated, and evaluated separately before the coach may make outcome
or optimality claims.
