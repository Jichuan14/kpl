# Draft Atlas codebase review

Reviewed October 1, 2026. Scope: public API abuse controls, frontend request lifecycle and efficiency, data synchronization, publication, and Coach persistence. Season 4 (`20260004`) remains the default context; historical data was used only to measure existing local artifact size.

The original review used source inspection and bounded local reproductions with mocked external services and temporary databases/files. It did not exercise production infrastructure or perform an exhaustive dependency audit. The approved implementation is recorded below; the original findings are retained as review history, and their line references describe the pre-fix revision.

Priority: **P1** = address promptly because an anonymous caller can incur cost or consume shared service resources. **P2** = concrete correctness, reliability, or retention defect to fix next.

## Implementation status

All ten reviewed failure cases and the three efficiency steps have implementation changes and regression coverage. Visual verification is reserved for the user.

| Review item | Implemented behavior |
| --- | --- |
| 1. Paid commentary | A route dependency acquires the same provider budget as Coach before model resolution/generation and releases it on errors. |
| 2. Live-match abuse | Local fixtures and teams are checked before outbound work; separate request/concurrency limits apply; fixture fetches are shared and both caches evict expired/old entries. |
| 3. Public writes | Signed HttpOnly visitor cookies replace client UUID authority; fixture, format, and prediction windows are enforced; inserts and legacy score completion preserve the first committed vote; writes are rate limited and analytics paths are canonicalized. Lifetime analytics totals remain available. |
| 4. Sync writer lock | Network requests precede writes; each battle commits separately, and failed persistence rolls back. |
| 5. Partial series | Incremental completeness checks expected games, distinct game sequence, complete picks/bans, player rosters, and observed winners. |
| 6. Old evidence shards | All eight supported relation/context shards are replaced, including explicit empty rows. |
| 7–8. Matchup requests | Input/season/model changes invalidate and abort pending requests, clear loading, and reject stale results. |
| 9. Simulator polling | Disposal and target changes invalidate requests; disposed views cannot start polling or schedule new live checks. |
| 10. Coach retention | Expiry deletes checkpoints before metadata; startup reconciles old orphan threads; session activity performs bounded-frequency cleanup. Concurrent service initialization is serialized. |
| Calendar | Widget and popup share one cached 17-day range response instead of repeated date requests. Existing single-day API calls remain supported. |
| Ranking readiness | Bounded summary caching uses file identity, size, and nanosecond modification/change times; file replacement invalidates cached results. |
| Artifact counts | JSONL record counts use the same bounded file-summary cache. |
| Proxy observation | Provider-specific caller headers are ignored for identity and stripped by Nginx; forwarded-header trust remains explicit. |

The generated visitor key is owner-readable/writable only, persists through restarts, and is excluded from Git. Anonymous cookies still represent sessions rather than verified individuals. Existing source data and published artifacts were not rebuilt: repairs take effect when the affected season is successfully synchronized and published. Model calculations, season selection rules, route URLs, and visual styles are preserved.

**Completed verification:** 361 backend tests and 45 subtests passed; 13 relevant analysis tests passed; 49 frontend tests passed; production build succeeded; whitespace/diff checks passed. New tests cover request-budget exhaustion, signed-cookie persistence/tampering, invalid fixtures and closed prediction windows, cache eviction and atomic replacement, stale shard removal, SQLite writes during upstream waits, persistence rollback, checkpoint expiry/orphan cleanup, and frontend request races. Backend checks used the existing Python 3.11.1 environment.

**Local optimization measurement:** warm factual-catalog calls no longer read the six ranking files (2,948,863 bytes). The five runs after this change were 40.92 ms cold, then 2.98, 2.81, 2.61, and 2.64 ms; warm median **2.725 ms**, compared with the original **18.97 ms** median. The shared empty-calendar regression requires **one** HTTP request for two concurrent consumers, replacing the reviewed initial 17-request search. These are local measurements, not production load-test results.

## Confirmed findings

### 1. [P1] Public commentary can bypass paid-model request limits

**Location:** [simulation.py:75](/Users/jichuan/Desktop/kpl/backend/app/api/simulation.py:75), [draft_commentary.py:955](/Users/jichuan/Desktop/kpl/backend/app/services/draft_commentary.py:955).

`POST /api/simulations/commentary` calls the Kimi-backed narrator without acquiring either the Coach or simulation limiter. Production Nginx exposes the simulations prefix anonymously. When Kimi is configured, distinct commentary inputs cause paid requests without consuming the existing server/per-client budgets. Caller-controlled `bp_order` participates in the commentary cache key, so the cache is not an admission control.

**Evidence:** An isolated HTTP test with a fake provider and a simulation limiter configured to deny all acquisitions submitted two valid requests differing in `bp_order`. Both returned 200; the provider was called twice and the limiter zero times. No real tokens were spent.

**Fix:** Admit every paid-model operation through a shared provider budget and concurrency limit, including commentary. Add a route-level guard and release admission in `finally`. Validate/canonicalize draft state; retain caching only as an optimization. Test that exhausted budgets prevent provider calls.

### 2. [P1] Live-match parameter changes bypass refresh throttling and grow memory

**Location:** [live_match.py:133](/Users/jichuan/Desktop/kpl/backend/app/services/live_match.py:133), [live_match.py:145](/Users/jichuan/Desktop/kpl/backend/app/services/live_match.py:145), [leagues.py:159](/Users/jichuan/Desktop/kpl/backend/app/api/leagues.py:159).

Public live-match reads and refreshes accept caller-selected league, match, and team identifiers. The only refresh protection is a cache keyed by all those values. Each new combination reaches `get_matches`, even when the requested match or teams do not exist. `_fetch_state` does not reuse the separate league fixture cache. Expired entries are retained indefinitely unless overwritten at the same key; there is no capacity bound or public-route rate limit here.

**Evidence:** Seven requests for the same league/match while varying one team identifier caused seven mocked upstream calls and seven cache entries. Artificially aging the first six beyond their TTL did not remove them when a seventh key was requested. This establishes the bypass and retention behavior; a production denial of service was not attempted.

**Fix:** Validate/canonicalize the fixture against local catalog data before outbound work, share upstream fixture fetches by league, apply per-client and global rate/concurrency limits, and bound cache entries with actual eviction. Cache negative results with bounded keys and lifetime.

### 3. [P2] Public votes and analytics accept fabricated, unlimited writes

**Location:** [leagues.py:228](/Users/jichuan/Desktop/kpl/backend/app/api/leagues.py:228), [analytics.py:62](/Users/jichuan/Desktop/kpl/backend/app/api/analytics.py:62), [schemas.py:42](/Users/jichuan/Desktop/kpl/backend/app/schemas.py:42).

Winner predictions check only that the claimed winner belongs to the two teams supplied in that same request. They do not verify the season, fixture, actual teams, game, or whether predictions are still open. The immutable-vote constraint relies on a client-generated UUID; replacing it permits another vote. Analytics likewise accepts any syntactically valid page path and another UUID, creating persistent visitor/page rows. Neither public write route has a request limiter.

**Evidence:** In an empty in-memory database with zero leagues and zero matches, six requests for a nonexistent fixture and invented teams all returned 200 and produced six votes. Six analytics requests created six visitors and six nonexistent page-path rows. No real analytics were modified.

**Impact:** Poll and visitor metrics are easily manipulated, and sustained requests can grow the database and contend with legitimate SQLite writes.

**Fix:** Validate votes against the authoritative fixture and prediction window; issue a signed anonymous session identifier; add rate/concurrency limits and retention. Map analytics paths to a fixed public-route allowlist. Anonymous sessions do not establish unique humans, so retain that limitation in metric interpretation.

### 4. [P2] Sync holds SQLite's write lock while waiting on upstream requests

**Location:** [sync.py:349](/Users/jichuan/Desktop/kpl/backend/app/services/sync.py:349).

The sync loop flushes a battle write, sleeps, then fetches remote detail. It commits only after processing the entire series. A slow upstream therefore extends an open write transaction across several network calls, blocking other writers such as management saves, analytics, job submission, and worker heartbeats. The local database uses SQLite's `delete` journal mode.

**Evidence:** While a mocked battle-detail fetch was pending after the flush, a second database connection attempting a settings write failed with `database is locked`. The reproduction used a 50 ms timeout to keep it bounded; it does not claim production always fails within that duration.

**Fix:** Fetch and validate remote payloads before opening the write transaction, then persist each battle in a short atomic transaction. Keep retry/completeness tracking explicit. WAL may help reader concurrency, but it does not remove SQLite's single-writer constraint.

### 5. [P2] Incremental sync can permanently skip incomplete series

**Location:** [sync.py:246](/Users/jichuan/Desktop/kpl/backend/app/services/sync.py:246); skip decision at [sync.py:184](/Users/jichuan/Desktop/kpl/backend/app/services/sync.py:184).

Completeness means only that every *locally known* battle has at least one BP row. It does not check expected game count from the final series score, complete draft records, or player details. A partially available official response can consequently become a permanent local omission under ordinary incremental updates.

**Evidence:** A finished 3–1 series with only one stored game and one BP action was classified as complete. The incremental path skips matches in that set.

**Fix:** Record detail completeness explicitly and compare observed games with the authoritative series result, accounting for exceptional official outcomes. Revisit recently finished or incomplete fixtures with a bounded repair policy. Preserve missing-data labels until observations are actually present.

### 6. [P2] Publication retains obsolete relationship evidence

**Location:** [static_publisher.py:255](/Users/jichuan/Desktop/kpl/backend/app/services/static_publisher.py:255); consumer at [api.js:198](/Users/jichuan/Desktop/kpl/frontend/src/api.js:198).

Publishing writes only relation/context combinations present in the new rows. If a previously nonempty combination becomes empty after correction or regeneration, its old JSON file is left on disk. The browser requests those stable file paths directly, so obsolete evidence can remain visible beside a newly published overview.

**Evidence:** Publishing one `ban_response/overall` row and subsequently publishing zero rows left the original row readable in the old shard.

**Fix:** Publish explicit empty shards for supported combinations, or remove stale shards as part of publishing a complete generation. Test the transition from nonempty to empty, and do not treat missing compact rows as proof of no underlying evidence.

### 7. [P2] A season switch can permanently disable matchup recommendations

**Location:** [HeroFeatureSpacePage.vue:575](/Users/jichuan/Desktop/kpl/frontend/src/HeroFeatureSpacePage.vue:575), [HeroFeatureSpacePage.vue:378](/Users/jichuan/Desktop/kpl/frontend/src/HeroFeatureSpacePage.vue:378).

Changing the season increments the recommendation request number but does not reset `matchupLoading`. The pending request becomes stale, so its `finally` block deliberately skips clearing that flag. All later calls return early while it remains true, until the page is remounted.

**Evidence:** Controlled execution of the actual component functions started a recommendation, switched season, and completed the old request. Loading stayed true and a retry issued no request.

**Fix:** Centralize request invalidation, abort the old request, and reset loading when the owner of that state changes. Keep the existing shared, visit-only public season behavior.

### 8. [P2] Edited matchup inputs can receive an outdated recommendation

**Location:** [HeroFeatureSpacePage.vue:321](/Users/jichuan/Desktop/kpl/frontend/src/HeroFeatureSpacePage.vue:321), [HeroFeatureSpacePage.vue:371](/Users/jichuan/Desktop/kpl/frontend/src/HeroFeatureSpacePage.vue:371).

Opponent/favorite/lane changes clear displayed results but do not invalidate the pending request identity. Its later response is accepted even though the visible inputs have changed.

**Evidence:** A request started with one opponent; a second opponent was added before it completed. The UI then contained two selected opponents and a result computed for the original one.

**Fix:** Associate each result with a complete input snapshot or invalidate and recalculate on all relevant input changes. Share this lifecycle implementation with the season-switch fix.

### 9. [P2] Simulator polling can restart after navigation

**Location:** [DraftSimulatorPage.vue:1732](/Users/jichuan/Desktop/kpl/frontend/src/DraftSimulatorPage.vue:1732), [DraftSimulatorPage.vue:943](/Users/jichuan/Desktop/kpl/frontend/src/DraftSimulatorPage.vue:943).

Unmount clears existing timers but leaves an in-flight live-match request valid. When that response arrives, it can start another 30-second polling interval in the disposed component. Repeating the navigation race can accumulate background requests. Related team/season changes also need request invalidation.

**Evidence:** Start a controlled live-match request, run the actual unmount callback, then resolve the response: one new recurring interval is created.

**Fix:** Abort and invalidate outstanding requests on unmount and target changes, then check disposal/current request identity after awaits before restarting timers. Reuse the existing shared polling/request-lifecycle helpers where suitable; no simulator visual change is required.

### 10. [P2] Expired Coach conversations leave orphaned checkpoints

**Location:** [conversation.py:195](/Users/jichuan/Desktop/kpl/backend/app/agent/conversation.py:195), [conversation.py:328](/Users/jichuan/Desktop/kpl/backend/app/agent/conversation.py:328), [service.py:250](/Users/jichuan/Desktop/kpl/backend/app/agent/service.py:250).

Expiry removes conversation metadata without deleting LangGraph checkpoints. Later session clearing enumerates checkpoint thread IDs from the surviving metadata, so it cannot discover the expired records. Conversation content remains on disk and storage accumulates despite expiry/clearing.

**Evidence:** Temporary SQLite stores and a tiny local graph containing synthetic text were used to expire a conversation and clear its session. Metadata rows were zero, but its checkpoint remained.

**Fix:** Coordinate deletion across both stores before discarding the identity mapping. Add periodic retention cleanup and an orphan-reconciliation operation. Preserve existing session ownership checks.

## Additional efficiency opportunities

1. **Reduce initial calendar requests.** [DailyMatchesWidget.vue:123](/Users/jichuan/Desktop/kpl/frontend/src/DailyMatchesWidget.vue:123) searches adjacent dates on mount, even while collapsed. An empty nearby catalog produced exactly **17 distinct HTTP requests**; [App.vue:61](/Users/jichuan/Desktop/kpl/frontend/src/App.vue:61) separately requests daily matches. Replace the sequential date search with a bounded date-range/nearest-fixture endpoint and share results between the widget and popup. Deferring optional work until opening the widget is another option, subject to its intended minimized display.

2. **Cache ranking readiness without caching the public season decision.** [factual_seasons.py:21](/Users/jichuan/Desktop/kpl/backend/app/services/factual_seasons.py:21) reads and parses every published ranking payload during each factual-catalog request, although readiness needs only metadata. On the existing local dataset, each call parsed **6 files totaling 2,948,863 bytes**. Five read-only runs took **39.27, 18.21, 26.30, 18.55, and 18.97 ms**; median **18.97 ms** on this machine. Cache validated readiness by file identity/mtime/size or publish a small validated manifest, while keeping the HTTP catalog/default uncached and zero-artifact seasons selectable. This is a measured optimization opportunity, not a claim that the current endpoint is already a production bottleneck.

3. **Avoid recounting artifacts on each management status request.** [data.py:70](/Users/jichuan/Desktop/kpl/backend/app/api/data.py:70) scans every JSONL line to calculate record counts. Cache counts by file identity/mtime/size or include them in the artifact manifest. This path was inspected but not benchmarked, so prioritize it after the measured request amplification and confirmed defects.

## Conditional deployment observation

When `COACH_TRUST_PROXY_HEADERS` or `SIMULATION_TRUST_PROXY_HEADERS` is enabled, [request_identity.py:26](/Users/jichuan/Desktop/kpl/backend/app/services/request_identity.py:26) prioritizes `CF-Connecting-IP`. The supplied proxy files do not explicitly sanitize that header. Verify the actual trusted proxy chain and overwrite/remove caller-supplied identity headers before enabling these settings. The shipped examples/defaults leave them false; **this review did not establish an active default-deployment bypass**.

The reviewed Coach conversation lookup checks session ownership. No cross-session read bypass or remotely exploitable Nginx management-auth bypass was confirmed. These statements apply to the inspected paths, not to every possible deployment.

## Verification and recommended order

- **74 targeted backend tests passed**, with **3 subtests** in the second batch: live match, winner predictions, analytics, Nginx public routing, unified sync, publication/lineups, commentary, and Coach service.
- **44 frontend tests passed**, and the production frontend build succeeded.
- Additional focused security tests also passed. They overlap the above checks and are not added to the total.
- The findings above use separate bounded reproductions because the existing passing tests do not cover these failure modes. Frontend race reproductions controlled promises/timers around actual extracted component functions; they were not full browser end-to-end tests.
- Backend verification used the existing Python **3.11.1** environment; README specifies **3.12+**. Production-version parity was not established.

Fix paid-provider admission and live-match abuse controls first. Next address public write validation, sync transactions/completeness, and stale publication. Then consolidate frontend request lifecycles and Coach retention. Finally measure the calendar/catalog/status optimizations under realistic traffic. Add focused regression tests for each confirmed failure rather than changing model calculations or inventing current-season evidence.
