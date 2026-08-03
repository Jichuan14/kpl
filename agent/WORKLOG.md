# Agent Work Log

## 2026-08-02 — Draft Coach session conversation UI

- Changed suggestion buttons to submit immediately instead of copying text into
  the composer.
- Replaced the single-answer state with a scrollable multi-turn conversation.
- Persisted up to 20 completed display turns in browser session storage and
  attached the latest six question/answer turns to Kimi for follow-ups.
- Removed expandable evidence/source payloads from the chat UI while retaining
  backend tool grounding and material warnings.
- Added a compact clear-history control, answered-turn count, and cleaner turn
  separators.
- Verified direct submission, empty composer state, hidden sources, response
  rendering, and history restoration after refresh.

## 2026-08-02 — Phase 2 landing defaults and strict scope

- Made the learnable hybrid the default simulator model whenever that season's
  artifact is available, with statistical fallback.
- Defaulted current-season Game 1 teams to Wolves on Blue and AG on Red.
- Added a randomized three-question coach welcome set that always contains at
  least one Phase 1 and one Phase 2 question.
- Restricted Kimi to KPL/Honor of Kings professional draft topics and added an
  unrelated general-knowledge case to the Phase 2 evaluation catalog.
- Verified the landing page displays the learnable model, default teams, mixed
  suggestions, and KPL-only input wording.

## 2026-08-02 — Phase 2 team-aware coach

- Added a season-team roster endpoint and backend validation that accepts only
  two distinct teams recorded in the selected season.
- Replaced free-text simulator team names with searchable, selection-only Blue
  and Red comboboxes; simulation remains locked until both teams are selected.
- Added six offline artifacts for team tendencies, opening sequences,
  side/opponent combinations, player pools, recent trends, and roster metadata.
- Registered five Phase 2 evidence tools backed by the version-aware artifact
  cache.
- Added a confidence-weighted acting-team adjustment to both statistical and
  learnable next-action forecasts, with opponent-slot, team-slot, and league
  fallback behavior.
- Propagated authoritative team IDs/names through simulation, Global BP side
  swaps, the coach request, and model-requested draft tools.
- Added a ten-case Phase 2 evaluation catalog and expanded regression coverage
  to 48 backend tests.
- Verified the Phase 2 catalog offline and built the production Vue frontend.

This file is append-only. Each entry records the bounded change, verification,
and next task.

## 2026-08-02 — Phase 1 scope and project ledger

Changed:

- Defined the Phase 1 supported-question catalog.
- Added roadmap, decision log, work log, and evaluation locations.
- Recorded scope controls for analysis, tools, SQL, and outcome claims.

Verification:

- Documentation paths and cross-references were checked locally.

Next:

- Complete and test fast next-action inference.

## 2026-08-02 — Fast next-action inference

Changed:

- Added public `predict_next_action()` service behavior.
- Extracted shared state validation and next-step preparation so fast inference
  and rollout simulation use the same rules.
- Added a result limit while preserving the full legal candidate count.
- Added focused tests for bounded results, Global BP conflicts, BP-order
  validation, and the no-rollout guarantee.

Verification:

- Backend test suite: 6 tests passed.
- Python compilation completed successfully.
- Git whitespace validation passed.

Next:

- Create the backend agent package and register `predict_next_draft_action` as
  the first tool handler.

## 2026-08-02 — First registered agent tool

Changed:

- Created the backend agent package and draft tool module.
- Added strict, model-facing argument validation for the active draft state.
- Registered `predict_next_draft_action` as the only approved tool.
- Added OpenAI-compatible tool-definition generation for later Kimi use.
- Added bounded dispatch, unknown-tool rejection, and structured completion,
  validation, rejection, and failure logs without logging raw arguments.

Verification:

- Backend test suite: 10 tests passed.
- Python compilation completed successfully.
- Git whitespace validation passed.

Next:

- Add the reusable, modification-time-aware JSONL artifact cache.

## 2026-08-02 — Version-aware artifact cache

Changed:

- Added safe season and JSONL filename resolution.
- Added thread-safe row caching keyed by modification time, size, and inode.
- Added automatic reload when an artifact is replaced or changed.
- Added reusable named indexes that are invalidated with their source rows.
- Added structured cache-load, cache-hit, and index-build logs without logging
  artifact contents.
- Added clear errors for malformed JSONL and non-object rows.

Verification:

- Backend test suite: 15 tests passed.
- Cache tests cover hits, reloads, index reuse, path safety, malformed JSON,
  and invalid row types.
- Python compilation completed successfully.
- Git whitespace validation passed.

Next:

- Implement relationship, team-synergy, meta, and hero-stat tools using the
  cache and existing SQLite aggregates.

## 2026-08-02 — Evidence retrieval tools

Changed:

- Added cached league-wide pick synergy, counter-pick, counter-ban, and
  ban-response retrieval with consistent evidence fields.
- Added cached team hero-pair retrieval with team and optional hero filters.
- Added cached season meta-priority retrieval.
- Added SQLite-backed hero pick, ban, presence, and descriptive win-rate
  retrieval.
- Added exact normalized hero and team-name resolution without fuzzy guessing.
- Registered all four tools with model-facing schemas and bounded filters.
- Added concise no-data logging for unknown teams and heroes.

Verification:

- Backend test suite: 21 tests passed.
- Tests cover relationship filtering, peak exclusion, team/hero filtering,
  priority sorting, SQLite aggregates, unknown entities, registry schemas, and
  JSON serialization.
- Python compilation completed successfully.
- Git whitespace validation passed.

Next:

- Add the bounded future-draft simulation tool using the existing rollout
  simulator.

## 2026-08-02 — Bounded future-draft simulation tool

Changed:

- Added an optional action horizon to the existing simulator while preserving
  full-draft behavior for current API callers.
- Added validated `simulate_future_draft` arguments with bounded horizon,
  rollout count, candidates per action, and optional deterministic seed.
- Returned side, action, and team-action-slot metadata for every simulated BP
  order.
- Clearly labeled action probabilities as marginal rollout frequencies rather
  than one guaranteed sequence or battle-win probabilities.
- Registered the tool as the sixth approved agent capability.
- Tightened all agent argument models to reject unexpected fields.

Verification:

- Backend test suite: 24 tests passed.
- Tests verify horizon enforcement, no extra rollout steps, output metadata,
  registry schemas, and existing simulation compatibility.
- Python compilation completed successfully.
- Git whitespace validation passed.

Next:

- Add the Kimi client and bounded multi-round tool loop with mocked provider
  integration tests.

## 2026-08-02 — Kimi client and bounded tool loop

Changed:

- Added environment-only Kimi configuration with a masked API-key type and a
  blank, safe-to-commit example value.
- Added a lazy OpenAI-compatible Moonshot client so importing and testing the
  backend does not require credentials.
- Added a bounded coach loop that exposes only registered tools, validates
  every tool request, returns tool evidence to Kimi, and caps rounds, calls,
  timeout, and output tokens.
- Added an evidence-first system prompt that prevents unsupported statistics
  and distinguishes draft-selection probability from battle-win probability.
- Added structured request, provider, and tool logs without API keys or raw
  prompts.
- Added the compatible Python SDK dependency and installed it in the local
  backend virtual environment.

Verification:

- Backend test suite: 30 tests passed.
- Mocked integration tests cover missing configuration, masked secrets, direct
  answers, tool use, malformed arguments, and the tool-round limit.
- Python compilation and Git whitespace validation passed.
- Git confirms `backend/.env` is ignored; tracked files contain only the blank
  `MOONSHOT_API_KEY` example.

Next:

- Add the validated `/api/coach` endpoint and map provider/configuration errors
  to safe HTTP responses.

## 2026-08-02 — Safe live Kimi smoke test

Changed:

- Added an opt-in live smoke-test command that uses the same settings and coach
  service as the future API endpoint.
- Capped the check at 64 output tokens and one tool call.
- Added safe diagnostics for missing credentials, authentication failure,
  quota/rate limits, provider HTTP errors, and connection failures.
- Documented where to place the local key and the exact test command.

Verification:

- The smoke-test source never prints or serializes the API key.
- The normal mocked backend test suite remains the no-cost regression test.

Next:

- Run the smoke test after `MOONSHOT_API_KEY` is added to `backend/.env`, then
  add the validated `/api/coach` endpoint.

## 2026-08-02 — Draft Coach HTTP endpoint

Changed:

- Added `POST /api/coach` and registered it with the FastAPI application.
- Added strict active-draft validation for BP order, model type, hero IDs,
  board lists, previous-battle usage, legal heroes, and unexpected fields.
- Added a stable response containing answer, evidence, warnings, model, token
  usage, and a server-generated request ID.
- Added safe HTTP mappings for missing/authentication configuration, provider
  rate limits, timeouts, connection/status failures, bounded-loop exhaustion,
  and unexpected internal failures.
- Added a documented curl example for the full backend route.

Verification:

- Backend test suite: 34 tests passed.
- Endpoint tests cover the successful response contract, validation before
  provider use, safe configuration failure, and safe tool-loop failure.
- All endpoint tests use a mocked coach service and make no paid API calls.
- Python compilation and Git whitespace validation passed.

Next:

- Add the Draft Coach frontend panel and automatically attach the selected
  season and current simulator board to each question.

## 2026-08-02 — Current-season consistency audit

Changed:

- Confirmed SQLite identifies `20260003` (2026 KPL Summer) as the latest
  league.
- Confirmed exports, statistical artifacts, draft models, learnable artifacts,
  and published frontend assets exist for `20260003`.
- Centralized the manual analysis-script current season as
  `CURRENT_LEAGUE_ID` and pointed standalone defaults at it.
- Updated backend curl, sync, QA, export, decision, statistics, and meta
  examples from the previous `20260002` tournament to `20260003`.
- Preserved `20260002` only in isolated tests and rolling-history model
  examples, where it is intentional historical input rather than a runtime
  default.

Verification:

- Frontend default league is `20260003`.
- Manual analysis defaults resolve to `20260003` paths.
- Runtime artifacts and published assets are present for `20260003`.

Next:

- Add the Draft Coach frontend panel using the existing selected-league state,
  which currently defaults to `20260003`.

## 2026-08-02 — Draft Coach frontend panel

Changed:

- Added a reusable Draft Coach panel to the live draft simulator.
- Automatically attaches the selected league, model type, current BP order,
  current picks and bans, and each side's Global BP previous-battle usage.
- Added bilingual suggested questions, loading and safe error states, duplicate
  submission protection, structured evidence, warnings, model and token usage,
  and request-ID display.
- Marks an answer as stale when the user changes the league or BP board after
  the answer was generated.
- Kept the Kimi API key entirely in the backend; the browser calls only the
  local `/api/coach` route.

Verification:

- Frontend production build completed successfully.
- Backend regression suite: 34 tests passed.
- Browser QA passed at desktop and mobile widths with no console errors.
- A live question for league `20260003` completed through the full frontend,
  backend, Kimi, and `get_meta_heroes` tool path and rendered its evidence.
- Changing the draft board after that response displayed the stale-answer
  warning as expected.

Next:

- Run the Phase 1 evaluation suite across supported, combined, ambiguous, and
  unsupported questions.

## 2026-08-02 — Concise human-readable coach answers

Changed:

- Tightened the Kimi response contract to match the user's language, answer
  directly, and keep normal responses to at most three short sentences.
- Prohibited Markdown tables, headings, separators, code blocks, and automatic
  methodology sections in the final answer.
- Kept detailed artifact evidence in the existing expandable evidence area
  instead of repeating it in the conversational answer.
- Added a safe display fallback that removes common Markdown markers and turns
  an occasional model-generated table into readable labeled lines.

Next:

- Run the Phase 1 evaluation suite and include response brevity and formatting
  checks in its acceptance criteria.

## 2026-08-02 — Draft Coach conversation sidebar

Changed:

- Moved Draft Coach from a full-width block below the board into a sticky
  right-side conversation rail on desktop and laptop layouts.
- Reworked the panel into a compact chat interface with a scrollable message
  thread, user and coach bubbles, suggested starters, an anchored composer,
  loading animation, and automatic scrolling to the latest message.
- Kept evidence, warnings, token usage, request IDs, and stale-board status
  available inside the assistant response without crowding the draft board.
- Added a single-column responsive layout and Chinese translations for the new
  conversation UI.

Verification:

- Production frontend build completed successfully.
- Browser QA confirmed side-by-side layout at 1280px and 1024px, responsive
  stacking at 620px, no horizontal overflow, and no draft-slot overflow.

Next:

- Run the Phase 1 evaluation suite.

## 2026-08-02 — Avoid overlapping synergy tool calls

Changed:

- Routed league-wide hero pairing questions exclusively to
  `get_hero_relationships` with `relation=pick_synergy`.
- Restricted `get_team_synergies` to questions that explicitly name a team and
  made `team_name` required in its Moonshot-compatible tool schema.
- Added prompt rules preventing overlapping tools from being used as redundant
  cross-checks and made the missing-team validation message actionable.
- Added a Chinese regression case for the exact 鲁班大师 pairing question.

Verification:

- All 49 backend tests passed.
- A live Kimi request for `本赛季鲁班大师最常搭配的英雄是什么？` called only
  `get_hero_relationships`; the call succeeded and no team-synergy call was made.

Next:

- Re-run the Phase 2 live evaluation suite before adding another overlapping
  analytics tool.

## 2026-08-02 — Phase 1 completion gate

Changed:

- Added the previously documented but missing `get_battle_draft` tool, backed
  by season-scoped, read-only SQLite queries over `battles` and `battle_bps`.
- Expanded the executable catalog to 12 cases covering all seven registered
  tools plus supported, combined, clarification, and unsupported behavior.
- Added deterministic checks for tool routing, unnecessary tools, selected
  model enforcement, concise formatting, limitation language, and catalog
  coverage.
- Added a bounded live runner, cumulative token budget, redacted JSON report,
  targeted case retries, and no-cost reassessment when only a deterministic
  wording rubric changes.
- Clarified Phase 1 boundaries in the system prompt: predictions are
  league-wide; team pair statistics are not side-, opponent-, stage-, or
  time-filtered; no outcome/optimal model exists; battle questions require an
  exact battle ID.

Verification:

- Offline catalog gate: 12 cases, seven tools, four categories, all covered.
- Live Kimi gate for league `20260003`: 12/12 cases passed.
- The combined case correctly called both `get_meta_heroes` and
  `get_hero_bp_stats`; unsupported cases made no unsupported factual claims.
- The learnable-model case used `predict_next_draft_action` with the website's
  authoritative `learnable` selection.
- Cumulative live evaluation usage: 56,257 tokens, including targeted retries.
- Final report: `agent/evals/phase_1_live_report.json`.

Next:

- Phase 1 is complete. Begin Phase 2 only after choosing its first team-aware
  capability and adding it to the roadmap.

## 2026-08-02 — Authoritative website model context

Changed:

- Made the backend, rather than Kimi, authoritative for the selected league,
  forecast model, current BP order, board, and Global BP prior usage.
- Draft tools now accept only model-selected tuning options such as result
  limit or rollout horizon; application context is injected after the tool is
  selected.
- Applied the selected league to non-draft evidence tools as well, preventing a
  model-generated tool argument from querying the wrong season.
- Added regression coverage proving that incorrect model-generated season,
  model type, BP order, and hero lists cannot override the website state.

Next:

- Run the Phase 1 evaluation suite.

## 2026-08-02 — Team-aware learnable draft model

Changed:

- Upgraded the learnable artifact to schema version 2 with separate learned
  acting-team and opponent-team embeddings.
- Added team vocabulary, per-team training support, exported parameters, strict
  backend schema validation, and zero-vector fallback for unseen teams.
- Made learnable inference consume the selected Blue/Red teams directly and
  bypass the Phase 2 statistical tendency multiplier to prevent double-counting.
- Updated the model selector and methodology to describe team-aware behavior.
- Retrained the `20260003` artifact from 37,701 usable decisions across five
  recency-weighted seasons: 33 teams with 16-dimensional team embeddings.

Verification:

- All 51 backend tests passed, including direct team-ranking and no-double-blend
  regressions.
- The frontend production build completed successfully.
- The exported model loaded successfully and returned different direct learned
  distributions for Wolves, AG, and TES.A from the same empty BP board.
- In-sample weighted NLL improved from 2.7629 to 2.6815; weighted top-1 improved
  from 26.46% to 28.61%, and top-5 from 60.74% to 62.50%. These remain in-sample
  diagnostics, not chronological holdout results.

Next:

- Add a chronological match-level holdout before tuning the team embedding size
  or using the training metrics to compare production quality.

## 2026-08-02 — Complete How It Works redesign

Changed:

- Replaced the old long-form methodology article with a visual field guide
  organized around the current system: data pipeline, training rows, one
  prediction, team embeddings, simulator, AI coach, and model limits.
- Added live selected-season metadata for training actions, hero vocabulary,
  draft slots, and learnable-model availability.
- Added visual system, recency-weight, legal-gate, team-swap, simulator-loop,
  agent-routing, responsibility, and model-card components without external
  assets.
- Rewrote the Chinese translations for the complete new page while retaining a
  full English experience.

Verification:

- Frontend production build completed successfully.
- Browser QA passed at 1280px desktop and 390px mobile widths with no horizontal
  overflow or console warnings.
- Verified anchor navigation, Chinese and English language switching, live model
  metadata, and the dense prediction and agent diagrams at both breakpoints.

Next:

- Revisit methodology metrics after a chronological model holdout is available.

## 2026-08-02 — Native Chinese methodology copy

Changed:

- Rewrote the redesigned methodology page from literal translation into concise
  Chinese product copy using consistent KPL terms such as `BP 局面`, `换边`,
  `手次`, `全局 BP`, and `合法英雄`.
- Reframed the hero headline as `读懂数据，推演下一步。` and shortened section
  titles, explanations, agent responsibilities, and model-limit language.
- Split the prediction heading into intentional lines so Chinese phrases never
  break between individual characters at desktop widths.

Verification:

- The production frontend build completed successfully.
- Browser QA passed at 1280px and 390px with no overflow, console warnings, or
  awkward heading breaks.

Next:

- Apply the same native-copy audit to the simulator and coach UI if requested.
