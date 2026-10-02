import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequestScope } from "./requestScope.js";
import { firstAvailableDay, matchesOnLocalDate } from "./matchCalendar.js";
import { fetchMatchCalendar, resetCalendarCacheForTests } from "./api.js";
import { ref, watch } from "vue";

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

// Execute the component's actual request functions with controlled I/O.
function componentFunctions(file, names, context) {
  const source = readFileSync(new URL(file, import.meta.url), "utf8");
  const functions = names.map((name) => {
    const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
    assert.ok(match, name);
    const start = match.index;
    let end = source.indexOf("{", start), depth = 1;
    const open = end++;
    while (depth && end < source.length) {
      if (source[end] === "{") depth++;
      if (source[end] === "}") depth--;
      end++;
    }
    assert.ok(end > open);
    return source.slice(start, end);
  }).join("\n");
  return new Function(...Object.keys(context), `${functions}\nreturn {${names.join(",")}};`)(...Object.values(context));
}

test("request scope aborts obsolete work and never accepts results after disposal", () => {
  const scope = createRequestScope();
  const first = scope.begin();
  const second = scope.begin();
  assert.equal(first.signal.aborted, true);
  assert.equal(first.isCurrent(), false);
  assert.equal(second.isCurrent(), true);
  scope.dispose();
  assert.equal(second.signal.aborted, true);
  assert.equal(second.isCurrent(), false);
  assert.equal(scope.begin(), null);
});

test("season/input invalidation clears matchup loading and rejects the old response", async () => {
  const pending = deferred();
  let calls = 0;
  const context = {
    matchupRequests: createRequestScope(), opponentHeroIds: ref([1]), favoriteHeroIds: ref([2]),
    supportedHeroIds: ref(new Set([1, 2, 3])), preferredLane: ref(""), leagueId: ref("20260004"), modelVersion: ref("version"),
    matchupLoading: ref(false), matchupError: ref(""), matchupResult: ref(null), matchupRecommendationsExpanded: ref(false),
    INITIAL_MATCHUP_RECOMMENDATION_LIMIT: 6, t: (text) => text,
    fetchHeroMatchupRecommendations: async () => { calls++; return calls === 1 ? pending.promise : { current: true }; },
  };
  const { recommendForMatchup, invalidateMatchup } = componentFunctions("./HeroFeatureSpacePage.vue", ["recommendForMatchup", "invalidateMatchup"], context);
  const source = readFileSync(new URL("./HeroFeatureSpacePage.vue", import.meta.url), "utf8");
  const watchStart = source.indexOf("watch(() => [favoriteHeroIds");
  const watchEnd = source.indexOf("</script>", watchStart);
  const stop = new Function("watch", "invalidateMatchup", ...Object.keys(context),
    `return ${source.slice(watchStart, watchEnd).trim()}`)(watch, invalidateMatchup, ...Object.values(context));
  const old = recommendForMatchup();
  assert.equal(context.matchupLoading.value, true);
  context.opponentHeroIds.value = [1, 3];
  pending.resolve({ obsolete: true });
  await old;
  assert.equal(context.matchupLoading.value, false);
  assert.equal(context.matchupResult.value, null);
  await recommendForMatchup();
  assert.deepEqual(context.matchupResult.value, { current: true });
  assert.equal(calls, 2);
  stop();
});

test("a simulator response arriving after disposal cannot restart polling", async () => {
  const ref = (value) => ({ value });
  const pending = deferred();
  const liveRequests = createRequestScope();
  let intervals = 0;
  const context = {
    liveRequests, leagueId: ref("20260004"), teamsReady: ref(true), upcomingMatch: ref({ match_id: "fixture" }),
    selectedTeamIds: ref({ a: "a", b: "b" }), TEAM_A: "a", TEAM_B: "b",
    liveMatchLoading: ref(false), liveMatch: ref(null), liveFollowing: ref(false),
    fetchLiveMatch: () => pending.promise,
    requestLiveMatchRefresh: () => pending.promise,
    shouldRestoreLiveFollow: () => false, isOfficialSeriesComplete: () => false,
    startLiveMatchPolling: () => { intervals++; },
  };
  const { refreshLiveMatch } = componentFunctions("./DraftSimulatorPage.vue", ["refreshLiveMatch"], context);
  const request = refreshLiveMatch();
  liveRequests.dispose();
  pending.resolve({ is_live: true });
  await request;
  assert.equal(intervals, 0);
  assert.equal(context.liveMatch.value, null);
});

test("calendar consumers share one bounded request, expire cached results, and retry failures", async () => {
  resetCalendarCacheForTests();
  const previousFetch = globalThis.fetch;
  const previousNow = Date.now;
  let now = 1000, calls = 0;
  Date.now = () => now;
  globalThis.fetch = async (path) => {
    calls++;
    const url = new URL(path, "http://test");
    assert.equal(url.searchParams.get("start_date"), "2026-09-23");
    assert.equal(url.searchParams.get("end_date"), "2026-10-09");
    return { ok: true, json: async () => ({ data: { date: "2026-10-01", matches: [] } }) };
  };
  try {
    const [first, second] = await Promise.all([fetchMatchCalendar({ date: "2026-10-01" }), fetchMatchCalendar({ date: "2026-10-01" })]);
    assert.deepEqual(first, second);
    assert.equal(calls, 1);
    now += 60_001;
    await fetchMatchCalendar({ date: "2026-10-01" });
    assert.equal(calls, 2);
    resetCalendarCacheForTests();
    globalThis.fetch = async () => { throw new Error("offline"); };
    await assert.rejects(fetchMatchCalendar({ date: "2026-10-01" }));
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ data: { matches: [] } }) });
    assert.deepEqual(await fetchMatchCalendar({ date: "2026-10-01" }), { matches: [] });
  } finally {
    globalThis.fetch = previousFetch;
    Date.now = previousNow;
    resetCalendarCacheForTests();
  }
});

test("an empty calendar needs no further requests and local timezone conversion is preserved", () => {
  assert.deepEqual(firstAvailableDay([], "2026-10-01"), { date: "2026-10-01", matches: [] });
  const rows = [{ match_id: "next", start_time: "2026-10-03 18:00:00" }, { match_id: "previous", start_time: "2026-09-30 18:00:00" }];
  const date = new Date("2026-10-03T18:00:00+08:00");
  const localDate = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  assert.equal(matchesOnLocalDate(rows, localDate)[0].match_id, "next");
  const choice = firstAvailableDay(rows, "2026-10-01", new Date("2026-10-01T00:00:00").getTime());
  assert.equal(choice.matches[0].match_id, "next");
});
