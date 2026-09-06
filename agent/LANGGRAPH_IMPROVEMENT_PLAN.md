# KPL Draft Coach: LangGraph-First Improvement Plan

Prepared: 2026-09-04. Status: implementation brief; no runtime changes made.

## 1. Mission and implementation instructions

Improve the existing KPL Draft Coach so it provides useful, evidence-backed analysis, understands follow-up questions, exposes its supporting data, and handles slow or incomplete requests gracefully.

**LangGraph is the primary architecture and the destination for all new orchestration work.** The existing legacy loop is an emergency escape hatch, not a second implementation that must receive every feature. Do not replace LangGraph, create another hand-written orchestration loop around it, or make legacy feature parity a release requirement.

Work through the phases below in order. Deliver working, tested increments rather than a framework rewrite. Reinspect the repository before implementing: the audit is a starting point, not a substitute for checking the current code. Run relevant checks after each phase and update the project ledger. Do not stop after writing another plan when asked to execute this brief.

Do not commit, deploy, change the provider/model, call paid APIs, or rebuild production data without separate authorization. If live evaluation is not authorized, finish the offline implementation and identify the remaining live-validation gate explicitly.

### Product outcomes

- A quick question receives a direct, concise answer with accessible evidence.
- An analytical question receives a supported conclusion, comparison, trade-offs, and specific limitations, not merely a longer generic answer.
- “Why that one?” and “What about the other team?” resolve against the correct conversation, season, and draft snapshot.
- A factual answer cannot be marked verified merely because some tool ran.
- Missing evidence produces a useful partial result or clarification, not invented facts or a misleading off-topic refusal.
- Users see meaningful progress, can stop waiting, and can retry without unknowingly duplicating a paid run.

### Non-negotiable boundaries

1. Preserve registered, validated, read-only tools. No arbitrary SQL, shell execution, filesystem exploration, or unrestricted web browsing inside the coach.
2. Preserve application-authoritative season, team identities, draft legality, and Global-BP exclusions. Conversation text and model output cannot override these.
3. Keep heavy training, synchronization, corpus ingestion, and artifact rebuilding outside chat requests.
4. Continue distinguishing selection likelihood, descriptive historical win rate, relative lineup advantage, and calibrated win probability. Do not relabel one as another.
5. Treat conversation text, retrieved documents, and model-generated summaries as data, not instructions or permission grants.
6. Keep credentials out of prompts, graph state, checkpoints, logs, events, and browser responses. Never expose internal reasoning or raw graph state to users.
7. Preserve existing JSON endpoint fields and ordinary simulator/scout-report behavior unless an intentional change is documented and tested.
8. Do not make LangSmith, another hosted tracing service, Redis, a vector database, or a multi-agent system mandatory for this work.

## 2. Audit baseline to reproduce

The audit observed the following local configuration and behavior. These are not claims about the currently deployed server.

| Area | Observed baseline | Desired change |
| --- | --- | --- |
| Runtime | `kimi-k2.6`; LangGraph default; 16 registered tools | Retain provider and framework initially |
| Limits | 3 tool rounds, 8 tool calls, 600 output tokens | Preserve hard bounds; separate answer-depth budgets |
| Answer style | Three short sentences normally; six for compound questions | Explicit quick/analysis behavior across every synthesis path |
| Evidence UI | Patch cards render; other tool evidence has no equivalent display | Render statistical/recommendation evidence without new model calls |
| Scope gate | Receives the latest message without conversation context | Supply bounded, scoped reference context before classification |
| Follow-up cost | A mocked one-tool follow-up with four history turns made 7 provider calls | Eliminate repeated classification of unchanged prior turns |
| Context history | Old answers are sent without original season/board metadata | Preserve provenance and exclude invalid/stale factual context |
| Grounding | A fake factual answer with zero tools was returned unchanged | Require task-appropriate evidence or an explicit non-factual outcome |
| Evaluation | A deliberately false 999% statistic passed `season-meta` assessment after a successful tool result | Validate numeric consistency and useful task coverage |
| Input limits | UI/API accept 4,000 characters; gate rejects over 2,000 as off-topic | One documented limit and correct validation errors |
| Reliability | SDK retries plus application retries; no overall deadline | One retry owner and one request-wide budget |

The audit ran 125 affected offline tests successfully, not the entire repository suite. Both offline catalogs validated. Saved live reports were dated August 3 UTC, before the August 31 LangGraph change; the saved Phase 2 report recorded 9/10 passing. Reproduce current results instead of treating these records as a current quality guarantee. The 999% example was deliberate fault injection, not a measured Kimi hallucination rate.

### Existing files to inspect

Paths in this brief are relative to `/Users/jichuan/Desktop/kpl`.

| Responsibility | Existing files |
| --- | --- |
| Graph and orchestration | `backend/app/agent/graph.py`, `backend/app/agent/service.py` |
| Scope and prompts | `backend/app/agent/scope.py`, `backend/app/agent/prompts.py` |
| Tool contracts and evidence | `backend/app/agent/tool_registry.py`, `backend/app/agent/tools/` |
| Fixed evidence workflow | `backend/app/agent/scout_report.py`, `backend/app/agent/scout_report_cache.py` |
| HTTP, limits, identity | `backend/app/api/coach.py`, `backend/app/config.py`, `backend/app/services/coach_rate_limit.py`, `backend/app/services/request_identity.py` |
| Frontend | `frontend/src/DraftCoachPanel.vue`, `frontend/src/api.js`, `frontend/src/i18n.js` |
| Deployment and lifecycle | `backend/app/main.py`, `backend/requirements.txt`, `frontend/nginx.conf`, `docker-compose.production.yml` |
| Tests and evaluation | `backend/tests/test_coach_*`, `backend/tests/test_scope_gate.py`, `backend/app/agent/eval_*.py`, `agent/evals/` |
| Ledger | `agent/README.md`, `agent/ROADMAP.md`, `agent/DECISIONS.md`, `agent/WORKLOG.md` |

Create small modules such as `evidence.py`, `answer_validation.py`, or `conversation.py` only when they establish clear boundaries. Do not move unrelated code or split everything into a new abstraction layer.

## 3. Target LangGraph architecture

Evolve the current `StateGraph`. Keep deterministic policy enforcement in Python nodes and retain the existing tool registry. A generic prebuilt agent must not replace the current security boundary.

Conceptual execution flow; adapt node names to the current implementation rather than replacing working nodes gratuitously:

```text
START
  -> prepare_turn / load_scoped_context
  -> scope_gate
       denied -------------------------------> finalize
       missing essential context ------------> clarify -> finalize
       allowed -> plan_evidence -> call_model
                    tool requests -> enforce_budget
                                  -> execute_tools
                                  -> register_evidence / check_coverage
                                  -> call_model
                    answer candidate -> sanitize_candidate
                                     -> validate_answer
                                          valid -> finalize
                                          repairable -> bounded_repair -> call_model
                                          incomplete/exhausted -> finalize_partial
  -> record_completed_turn
  -> END
```

The missing-context branch applies when clarification prevents all useful work. For compound requests, answer independent supported parts and ask for the missing context once.

Use explicit conditional edges for denial, clarification, tool execution, repair, and partial completion. Every loop shares the same request-wide limits; the repair branch must not create a second hidden budget. Graph recursion limits remain an emergency ceiling, not the primary business-level stopping policy.

Keep raw typed data in graph state and construct provider messages at the point of use. LangGraph supports state schemas, nodes, and conditional execution; verify APIs against the installed version before introducing features from current documentation. [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).

### State and ownership

| State group | Fields to represent | Owner / rule |
| --- | --- | --- |
| Current turn | Request ID, question, requested response mode, validated season/board snapshot | HTTP validation and server code |
| Conversation reference | Session/conversation identity, resolved entities, prior intent, provenance, pending clarification | Server-scoped records; text remains untrusted |
| Capability boundary | Allowed intents/tools, derived analysis scope, draft-context permission | Existing Python policy; never restored as authority from old state |
| Evidence plan | Requested subquestions, required evidence groups, optional enrichment, exclusions | Validated planner output plus deterministic policy |
| Execution | Tool requests/results, evidence records, coverage, usage, attempts, repair count | Graph nodes and tool adapter |
| Candidate | Structured answer sections and claim references | Model-proposed, untrusted until validation |
| Result | Validated content, status, limitations, suggested follow-ups | Finalizer |

Live clients, database sessions, cancellation handles, locks, and clocks belong in runtime dependencies, not serialized graph state. A monotonic deadline is process-local: do not persist it and reuse it after restart. Reset per-turn messages, budgets, evidence, errors, and pending tool calls explicitly when a new turn starts.

Compile/reuse graphs through an appropriate application lifecycle rather than constructing an expensive graph for every HTTP call. Ensure no request's mutable context is captured in a shared service object. Defer parallel tools until reducer semantics, deterministic ordering, and per-task database sessions are tested.

## 4. Public contracts and answer behavior

### Request additions

Add optional, validated fields; existing callers must remain valid:

- `response_mode`: `quick` or `analysis`, initially defaulting to `quick` for omitted fields.
- `conversation_id`: server-issued identifier, bound to an authorized session; never a raw user-selected LangGraph checkpoint key.
- `client_request_id`: bounded UUID used to identify one logical submission and avoid duplicate execution on transport retry.

Do not allow the browser to set evidence status, tool privileges, server budgets, or checkpoint IDs. The browser's board remains subject to current server validation. Preserve an explicit immutable snapshot for the submitted turn while the user continues editing the board.

### Response additions

Keep `answer`, `evidence`, `warnings`, `usage`, `model`, and `request_id`. Add versioned, optional fields for new clients:

- `answer_version` and `response_mode`.
- `status`: `complete`, `partial`, `needs_clarification`, or `refused` for successful domain-level responses. Infrastructure failures retain appropriate HTTP errors.
- `sections`: validated, renderable explanation/comparison/limitation sections.
- `evidence_cards`: normalized public cards that reference the existing raw evidence payload.
- `coverage`: which requested parts were answered, missing, or not applicable.
- `follow_up_actions`: bounded contextual suggestions, not tool calls to execute automatically.
- `conversation_id` when a session-backed conversation is enabled.

The backward-compatible `answer` string and structured sections must come from the same validated result. Do not maintain two separately generated answers that can contradict each other. Preserve existing warning fields while normalizing singular `warning` and plural `warnings` inside tool results.

### Quick versus Analysis

Quick mode stays concise but must answer the actual question. Remove blanket instructions that suppress necessary evidence, comparisons, or an explicitly requested explanation. Analysis mode should normally provide:

1. A direct conclusion.
2. Two or three material reasons grounded in evidence.
3. A comparison with requested alternatives when data permits.
4. The main uncertainty and what additional information could change the conclusion.

Do not fill every section mechanically. A roster question does not need a counterfactual, and an unsupported question does not need a long disclaimer. Match English/Chinese input and retain compact mobile-friendly rendering.

Start with the current quick token budget and a separately configurable analysis budget, provisionally 1,800 output tokens. Treat that figure as an experiment, not a measured optimum. Include structured-output overhead in the budget and handle provider truncation explicitly. Keep thinking mode disabled during this project unless separately authorized and compatibility-tested.

Do not impose one universal sentence limit on Quick, Analysis, scout reports, repair, and sanitization. Prompt instructions, response metadata, rendering, and evaluators must agree. An explicitly requested detailed explanation may select Analysis within the approved budget; it must not expand tool privileges.

## 5. Implementation phases

### Phase 0 — Establish the baseline and executable acceptance cases

**Work**

- Inspect repository instructions, working-tree changes, installed Python/LangGraph/provider SDK versions, and supported runtime APIs.
- Reproduce the audit with fake providers and deterministic fixtures. Do not use real credentials for these tests.
- Add a Phase 3 section to the ledger and document intentional changes to old style/graph-shape assertions.
- Add regression cases for unsupported factual completions, contradictory metrics, contextual follow-ups, wrong-season history, long inputs, and exhausted budgets.
- Record current provider-call counts and mock timing by task type. Treat historical live reports separately.

**Acceptance**

- Existing affected tests are understood and pass before changes, or pre-existing failures are recorded without being hidden.
- New gap tests fail for the expected reasons and will pass in their implementing phases. Do not leave known failures permanently marked expected just to keep a green gate.
- The baseline records versions, fixtures, settings, and which checks are offline versus live.

### Phase 1 — Surface existing evidence and fix misleading errors

**Work**

- Introduce a small evidence-card adapter for existing tool result families: draft recommendations, hero relationships, team statistics, rosters/player pools, completed-lineup scores, and patch notes.
- Include sample sizes, metric definitions, confidence, applied filters, and source versions only when actually available. Do not interpret an artifact modification time as the date of the underlying match evidence.
- Use expandable cards and a compact main answer. Keep existing patch links and source boundaries intact. Avoid dumping raw JSON or giant model payloads.
- Distinguish missing data from zero values. Distinguish a sparse-result warning from tool failure. Surface successful-tool caveats, not only exceptions.
- Unify the user-message limit at 4,000 characters across UI, request validation, and gate preprocessing. Bound/classify the entire accepted input; do not silently truncate past safety checks. Test Unicode normalization and whitespace/empty input.
- Return explicit localized errors for oversized input, invalid context, missing configuration, rate limits, and malformed gate output. A provider parsing failure is not proof that the question is off-topic. Retain fail-closed tool access on invalid classification.
- Preserve failed questions and provide Retry/Edit affordances. Safely handle unavailable or full session storage; storing large raw evidence histories must not break chat.

**Acceptance**

- Statistical evidence is visible without additional model calls.
- A 2,100-character valid KPL question is not automatically refused as unrelated; over-limit input receives a validation error.
- Existing JSON clients and scout reports still render correctly.
- Mobile layouts, keyboard submission, Chinese input composition, and English/Chinese error wording work. Render text safely; never use unsanitized model HTML.

### Phase 2 — Add evidence planning, claim validation, and bounded recovery

**Work**

- Extend the graph's current tool-planning step to track requested subquestions and required evidence, not merely a union of tool names.
- Start with a few deterministic workflow templates: ordinary lookup, explain current recommendation, compare available alternatives, team tendency analysis, and patch/trend comparison.
- Retain the current scope taxonomy initially. Validate each planned tool against the server's intent/scope intersection. Add a narrowly documented supporting-tool mapping only where a workflow needs it; never unlock the whole registry for “analysis.”
- Detect compound requests beyond the supported planning bound. Explicitly state which parts can be covered or ask the user to narrow the question; do not silently drop a fourth ask.
- Reuse registered tools and scout-report collection helpers. Workflow templates are not new publicly exposed super-tools and must not bypass dispatch checks.
- Assign stable per-run evidence IDs and preserve tool name, validated context, metric semantics, source version, and result status.
- Represent verifiable numeric claims using subject IDs, metric identifiers, units, source references, and values. Populate or validate values against canonical tool fields rather than trusting numbers invented in prose.
- Check denominator/conditional-rate meaning, rounding tolerance, side/team/season, legality, missing values, unsupported win-probability claims, and the actual evidence for each requested comparison.
- Clarification, capability explanations, and honest lack-of-data responses may complete without successful tools. Factual assertions may use valid, fresh evidence from this turn or explicitly revalidated prior evidence; do not force unnecessary duplicate calls.
- Run any planning-text cleanup before final validation. A rewrite cannot bypass grounding or silently collapse Analysis into three sentences. Handle truncated and malformed provider output explicitly.
- Allow at most one shared repair attempt for recoverable output/evidence gaps within remaining budgets. On continued failure, return a deterministic supported partial result or a precise limitation.

**Important limits**

An evidence ID's existence does not prove its claim. Numeric equality somewhere in a tool payload does not prove subject, time period, or denominator alignment. Deterministic checks should protect structured claims and rendered metrics; use separate qualitative evaluation for narrative entailment. Do not claim that regex validation proves all prose is factual or add an expensive model judge to every production turn by default.

A recommendation tool's top-K results are not automatically a fair evaluation of an arbitrary user-named alternative. If that alternative was not evaluated, say so. Only add a bounded comparison adapter if existing services can evaluate both candidates using the same legal board, seed, policy, and scoring procedure, with tests and updated scope documentation. Do not invent an absent candidate's score or retrain a model.

**Acceptance**

- The injected 999% case cannot be finalized as supported, even with a successful but contradictory tool result.
- A factual zero-tool completion triggers recovery or an honest limitation, not silent acceptance.
- Explanations of relative advantage never become calibrated win probabilities.
- The existing 3-round/8-tool-call ceilings cannot be bypassed through repair, workflow helpers, or error handling.
- Failure after one successful source preserves that source and identifies the unverified portion.

### Phase 3 — Introduce useful Analysis mode

**Work**

- Add Quick/Analysis selection and the optional contract fields from section 4.
- Align all prompt, synthesis, sanitization, and formatting paths with the selected mode.
- Render structured sections without the current blanket Markdown-stripping behavior erasing useful organization. Prefer Vue-rendered typed content over adding a general HTML renderer.
- Add contextual actions such as “Explain the difference,” “Compare alternatives,” and “Show the sample.” Suggestions must reference available entities/data and require a user click.
- Avoid hard-coded team/player/date examples unrelated to the selected season when a context-aware suggestion is possible.
- Keep the detailed evidence collapsible. Do not use verbosity as the success metric.

**Acceptance**

- A quick lookup remains compact and direct.
- An available-alternative comparison includes evidence-supported differences and a material caveat, not repeated generic advice.
- Asking for more detail does not silently lose the prior topic or request new tool privileges.
- Output-budget truncation cannot surface broken structured content as a completed answer.
- Old clients still receive a usable plain answer, and existing scout-report formatting remains supported.

### Phase 4 — Add scoped LangGraph conversation memory

**Work**

- Use LangGraph's supported checkpoint/thread mechanisms for multi-turn continuity rather than a second bespoke conversation engine. Use an in-memory checkpointer in tests. For production, select a supported persistent backend compatible with the current single-host deployment; prefer a separate local SQLite checkpoint store if validated for this workload, rather than introducing a new database service by default.
- Keep checkpoint data separate from the match-data SQLite database, with bounded retention, deletion, schema/version handling, and restart behavior. Add/pin only the dependencies actually required and verify compatibility with the installed LangGraph release.
- Establish a server-issued session boundary. The existing IP-based rate-limit key is not authentication and must not authorize conversation access. For anonymous users, use an appropriately protected opaque session cookie/token and bind conversation IDs to it; test cross-session access denial.
- Feed the scope gate the current message plus a small reference record: previous supported intent, entity IDs/names, prior season/board provenance, and pending clarification. It still classifies the current request and cannot inherit permissions from earlier turns.
- Stop reclassifying every old user message on every turn. Persist bounded completed-turn metadata, not an ever-growing untrusted transcript treated as authoritative.
- For old clients with raw history, use a bounded compatibility path: treat it as untrusted, filter it, and do not grant privilege or preserve unverifiable numeric claims from it. Never weaken the existing protection simply to save calls.
- Treat a new user turn as a fresh graph invocation with explicitly reset transient state. Do not accidentally resume a prior failed tool call or replay provider calls from an incomplete checkpoint.
- Handle new season, team swap, new game, changed Global-BP exclusions, and changed artifacts explicitly. Retain UI history with labels, but re-fetch evidence before applying old results to a new board. Store board provenance separately from season-level research provenance so not every historical answer becomes misleadingly “stale.”
- Use stable request identifiers and serialization of runs within one conversation. A duplicate submission must not append history twice or race over the same checkpoint. Different users must remain isolated.
- “Clear chat” must clear retained server conversation state as well as browser history. Define expiry behavior: ask for clarification or create a new conversation, never fabricate continuity.

LangGraph persistence associates execution state with a configurable thread identifier; checkpointing alone is not authorization, freshness validation, or application-level exactly-once execution. [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence).

**Acceptance**

- Multi-turn fixtures resolve “Why that one?”, “And on Red?”, and “What about the other team?” correctly; ambiguous cases ask one useful question.
- A normal one-tool follow-up with four prior turns uses at most three provider calls without retries/repair: one contextual gate, one tool-selection call if needed, and one synthesis call. Deterministic workflows may use fewer.
- Season/board changes cannot reuse old facts as current evidence or expand tool access.
- Cross-session checkpoint reads/writes, malformed conversation identifiers, duplicate submissions, and overlapping turns are covered by tests.
- The graph test that currently requires no checkpointer is intentionally replaced with scoped-persistence tests; it is not preserved as an architectural requirement.

### Phase 5 — Enforce request budgets and graceful completion

**Work**

- Introduce one request-wide deadline and provider/tool/repair counters covering scope, planning, tools, synthesis, repair, and retries.
- Choose one retry owner. Prefer explicit SDK retry configuration and graph/node-level bounded retry behavior; do not stack SDK, application, and node retries unintentionally.
- Respect provider retry hints only when enough request budget remains. Otherwise return a retryable error or supported partial result without sleeping past the user-visible deadline.
- Set each provider timeout from remaining budget, reserve time for finalization, and stop starting new tools/model calls when the budget is exhausted.
- Start with a configurable overall deadline below the current 120-second proxy timeout, provisionally 90 seconds, and reserve roughly 10 seconds for completion/fallback. Validate these settings with measured behavior; never assume the model finishes in a promised duration.
- Prefer a cancellation-aware async provider path inside async graph execution. Keep bounded synchronous analysis off the event loop without sharing mutable database sessions across workers.
- Explicitly account for non-cancellable work: aborting a browser request or cancelling `asyncio.to_thread` does not terminate its worker. Stop further work, track the actual remaining tool until it ends, and do not release active-work capacity prematurely. Avoid a large process-worker infrastructure change unless measurements show it is necessary.
- On exhausted limits, preserve verified evidence and use a deterministic partial finalizer if no model budget remains. If there is no usable evidence, return a clear retryable failure rather than a success-shaped factual answer.
- Keep graph recursion limits aligned with the revised node/repair paths and test them independently of semantic budgets.

**Acceptance**

- Fake-clock tests cover repeated 429s, timeouts, malformed output, tool failures, finalization reserve, and exhausted tool budgets without actual sleeps.
- No unexpected nested retry multiplication; every attempt counts toward telemetry and budgets.
- Rate-limiter/active-work resources are released exactly once when the corresponding work really ends, including disconnect/error paths.
- A timed-out request cannot continue launching new paid calls after the response or cancellation.

### Phase 6 — Stream safe progress and support cancellation

**Work**

- Add an optional `POST /api/coach/stream` transport over the same graph and service contracts. Keep the existing JSON route; do not create separate business logic for streaming.
- Use LangGraph streaming to observe execution, then map only allowlisted public events: accepted, progress, validated evidence, final result, and safe error.
- Include request ID, monotonically increasing event sequence, and a versioned event type. Define pre-stream HTTP errors versus error events after headers have been sent.
- Emit honest milestones such as “Checking the selected season” or “Comparing available alternatives.” Progress reflects real work, not a fake timer or disclosure of internal deliberation.
- Initially stream progress/evidence and publish answer content only after validation. Do not stream an unverified model answer and attempt to retract false claims later.
- Never forward raw `values`, `updates`, checkpoints, provider tool payloads, or model reasoning to the browser. Private state channels are not automatically private in streaming output; explicitly serialize the public event contract.
- Use a fetch-based streaming client compatible with POST bodies and an `AbortController`. Add Stop, Retry, and Edit actions; handle UTF-8 and event boundaries split across network chunks.
- Connect disconnect/Stop to server cancellation and the active-work accounting in Phase 5. Do not claim that Stop reverses already completed provider billing.
- Check Nginx buffering, middleware compression, connection timeouts, and keep-alive behavior for this route. Retain current access controls and request limits.
- If the connection fails after work started, do not silently submit the same question through the JSON endpoint. Use the scoped request identifier to recover an existing result when supported, or present a deliberate user retry.

LangGraph supports custom streaming with raw provider clients; it does not require replacing the existing Kimi integration with a different model wrapper. Select APIs available in the installed runtime rather than adopting preview interfaces unnecessarily. [LangGraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming).

**Acceptance**

- A progress event arrives before final completion when the provider is deliberately delayed.
- Streaming and JSON yield equivalent validated results for identical fixtures.
- Chunked Chinese text, error events, disconnects, Stop, rapid retries, and concurrent sessions are tested.
- Prompt-injection fixtures cannot leak internal state through events.
- Retry deduplication is scoped to the session and logical request; an intentional new generation uses a new request ID.

### Phase 7 — Strengthen evaluation, provenance, and rollout

**Work**

- Extend evaluations beyond successful tool names, keyword matches, and short formatting. Separate policy/routing, numeric grounding, context correctness, answer coverage, and usefulness scores.
- Give each case explicit fixture context, required evidence, prohibited claims, expected completion status, and a budget class. Add EN/ZH and multi-turn cases, not just rewritten single-turn questions.
- Test individual graph nodes and full paths, including repair and partial completion. Prefer behavioral assertions over freezing node names unnecessarily.
- Preserve old quick-mode checks where still valid. Intentionally update tests that prohibit all useful structure, require no persistence, or assume every safe refusal uses particular words. Never relax grounding/scope tests merely to improve pass rates.
- Log structured request/node durations, call counts, retries, token usage, evidence coverage, completion status, and version identifiers. Do not enable external trace export by default or log full questions, raw history, prompts, secrets, or unrestricted tool payloads.
- Add provenance for analytical source period versus artifact build/version. For patch questions, report the latest indexed date when relevant and do not equate “latest in the index” with “latest official update.”
- Retain the restriction that general game-reference questions currently depend on patch evidence. Improving missing hero/item/mechanics sources is a separately scoped follow-up: list the missing capabilities, do not pretend retrieval coverage was fixed by a larger prompt. Do not add a crawler or live ingestion to this change.
- Run affected tests first, then the broader backend suite and frontend production build. Add focused frontend checks using existing capabilities; pure formatting/event parsing helpers can use Node's built-in test runner instead of introducing a large UI testing stack.
- Before public enablement, request authorization for a bounded live evaluation with a recorded cost/token ceiling. Compare Quick and Analysis, initial turns and follow-ups, and degraded behavior. Do not declare a catalog-only check an end-to-end pass.
- Update scope documentation and ledger rules for approved workflows so the old Phase 1-only new-tool rule does not contradict the accepted Phase 3 scope. Keep one roadmap task in progress at a time.

**Acceptance**

- All deterministic safety, context, grounding, and contract cases pass.
- Live evaluation, if authorized, has reviewed answers and recorded latency/cost. If not authorized, delivery states “offline verified; live release gate pending” rather than claiming production validation.
- Proposed initial quality target: at least 90% of reviewed in-scope cases answer the actual question with relevant evidence and appropriate limitations, and no critical unsafe-tool, cross-session, stale-board, or fabricated-metric failures. This is a release target, not a guarantee of future correctness.
- Quick-mode call counts do not regress without a documented benefit; the targeted follow-up path meets Phase 4's nominal budget. Report p50/p95 latency separately from retry-induced outliers.

## 6. Mandatory regression matrix

| Case | Expected behavior |
| --- | --- |
| Quick season-meta lookup | Direct answer plus statistical evidence card |
| Analysis of two available legal alternatives | Supported differences, same evaluation context, specific caveat |
| Requested alternative absent from evaluated candidates | Explicit limitation; no invented score |
| “Why that one?” after a recommendation | Correct entity and original board provenance |
| “And on Red?” after a team-tendency lookup | Correct side, fresh filtered evidence |
| Ambiguous “the other team” | One clarification instead of a guessed identity |
| Change season with chat history present | Old numeric evidence not treated as current |
| Change board/model/Global-BP state during a request | Answer stays attached to submitted snapshot; stale label accurate |
| Scout-report follow-up | Correct matchup reference; no requirement to repeat known teams |
| Four-part request beyond plan capacity | Explicit prioritization/clarification, not silent omission |
| Fake factual answer with no evidence | Repair or honest limitation |
| Fake 999% statistic after successful tool | Rejected or replaced with supported content |
| Correct number attached to wrong team/metric | Rejected despite the number appearing somewhere in evidence |
| Relative advantage described as win probability | Rejected or corrected |
| Valid observed descriptive win rate | Accepted with its actual sample/meaning; no overbroad percent ban |
| Tool returns empty results or sparse warning | Unknown/limited evidence, not zero or nonexistence |
| One tool succeeds and another fails | Supported partial answer and visible missing part |
| Missing board plus answerable historical ask | Historical answer plus one board clarification |
| Ordinary capability question | Valid concise response without forced tool use |
| Relevant input between 2,001 and 4,000 characters | Classified normally within bounded processing |
| Oversized/empty input or malformed gate output | Correct safe error; no tool privilege or off-topic mislabel |
| Injection in current message/history/retrieved text | No policy override or unauthorized tool execution |
| Invalid conversation ID or another session's ID | No checkpoint access or existence disclosure |
| Duplicate HTTP request / overlapping conversation turns | No double append or unintended duplicate provider execution |
| Timeout, repeated 429, exhausted tool budget | Bounded recovery, no work launched past deadline |
| Repair produces another invalid answer | No second repair loop; safe partial/limitation |
| Model returns truncated structured output | Not reported as a completed answer |
| Stream split in middle of JSON/Chinese character | Correct parser behavior and display |
| Browser disconnect/Stop during model or tool work | No new calls; accurate active-work accounting |
| Browser storage fails / conversation expires | Chat remains usable; no invented memory |
| Clear chat followed by a new question | No previous server-side context leaks into new conversation |
| Existing JSON client, disabled new feature, legacy emergency mode | Defined compatible behavior, not an unhandled schema error |

## 7. Rollout and emergency behavior

Use a small number of meaningful feature switches for analysis, persisted conversations, and streaming if needed for staged exposure. Avoid building a matrix of flags for every node. Security invariants and numeric validation should not become optional user-controlled features.

Keep `coach_orchestration=langgraph` as the normal/default path. Keep legacy intact enough to start, enforce existing tool boundaries, and produce an ordinary old-contract response. New LangGraph-only fields must have defined behavior in emergency legacy mode: ignore optional presentation hints safely or return a documented unsupported-feature response, never crash or silently resume a checkpoint through legacy.

Do not duplicate memory, streaming, planning, or repair logic into the legacy loop. Keep a small emergency smoke/contract suite, not full new-feature parity. Remember that the orchestration switch does not undo changes to shared prompts, tools, or schemas: use a tested release rollback for those regressions. Do not migrate new conversation state into legacy.

Default rollout sequence:

1. Ship deterministic UI/error improvements with unchanged analytical results.
2. Enable validated Analysis internally after grounding tests pass.
3. Enable scoped memory after isolation and stale-context tests pass.
4. Enable progress streaming/cancellation after budget and disconnect tests pass.
5. Broaden exposure only after the authorized live gate and monitored comparison.

## 8. Definition of done and handoff

The work is complete only when the affected code, tests, documentation, and user-facing behavior agree. Do not report success solely because a graph compiles, a prompt was edited, or all legacy format checks pass.

Deliver:

- The LangGraph implementation and its updated state/node diagram.
- Backward-compatible request/response contracts and normalized evidence cards.
- Context-aware gate/history behavior with documented session isolation and retention.
- Grounding/coverage checks, bounded repair, deadlines, and partial finalization.
- Quick/Analysis UI and safe streaming/cancellation behavior.
- Deterministic regression results and any separately authorized live report.
- Dependency/configuration/deployment changes actually required, plus limitations and emergency instructions.
- Updated `agent/ROADMAP.md`, `agent/DECISIONS.md`, `agent/WORKLOG.md`, and relevant README/scope documentation.

For each phase, report what changed, which acceptance cases passed, and what remains. Keep unrelated features, model migration, source-corpus expansion, and statistical-model changes out of this implementation.
