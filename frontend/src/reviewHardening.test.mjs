import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequestScope } from "./requestScope.js";
import { chinaDate, firstAvailableDay, matchesOnScheduleDate, shiftDate, matchStart } from "./matchCalendar.js";
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

test("calendar uses Beijing dates and still finds nearby scheduled days", () => {
  assert.deepEqual(firstAvailableDay([], "2026-10-01"), { date: "2026-10-01", matches: [] });
  const rows = [{ match_id: "next", start_time: "2026-10-03 18:00:00" }, { match_id: "previous", start_time: "2026-09-30 18:00:00" }];
  assert.equal(matchesOnScheduleDate(rows, "2026-10-03")[0].match_id, "next");
  const choice = firstAvailableDay(rows, "2026-10-01");
  assert.equal(choice.matches[0].match_id, "next");
  assert.equal(firstAvailableDay([rows[1]], "2026-10-01").date, "2026-09-30");
});

test("calendar and voting popup retain the whole Beijing day across timezone boundaries", () => {
  assert.equal(chinaDate(new Date("2026-10-02T17:00:00Z")), "2026-10-03");
  assert.equal(chinaDate(new Date("2026-10-02T15:59:59Z")), "2026-10-02");
  assert.equal(shiftDate("2026-12-31", 1), "2027-01-01");
  const rows = [
    { match_id: "evening", start_time: "2026-10-03 20:00:00" },
    { match_id: "early", start_time: "2026-10-03 00:30:00" },
    { match_id: "tomorrow", start_time: "2026-10-04 18:00:00" },
    { match_id: "unscheduled", start_time: null },
  ];
  assert.equal(matchStart(rows[1]).toISOString(), "2026-10-02T16:30:00.000Z");
  const day = matchesOnScheduleDate(rows, "2026-10-03");
  assert.deepEqual(day.map((match) => match.match_id), ["early", "evening"]);
  // Even when these fixtures are in the past, opening the calendar keeps every
  // fixture for the selected day, just like the voting popup.
  assert.deepEqual(firstAvailableDay(rows, "2026-10-03"), { date: "2026-10-03", matches: day });
});

test("calendar initialization uses the same API day and matches as the voting popup", async () => {
  const payload = { date: "2026-10-03", matches: [
    { match_id: "early", start_time: "2026-10-03 00:30:00" },
    { match_id: "today", start_time: "2026-10-03 18:00:00" },
    { match_id: "next", start_time: "2026-10-04 18:00:00" },
  ] };
  const context = { loading: ref(false), error: ref(""), matches: ref([]), selectedDate: ref(""),
    fetchMatchCalendar: async () => payload, firstAvailableDay,
    chinaDate: () => "2026-10-02" };
  const load = componentFunctions("./DailyMatchesWidget.vue", ["loadFirstAvailableDay"], { ...context, loadVersion: 0 });
  await load.loadFirstAvailableDay();
  assert.equal(context.selectedDate.value, payload.date);
  assert.deepEqual(context.matches.value, matchesOnScheduleDate(payload.matches, payload.date));
  assert.equal(context.loading.value, false);
});

test("simulator selects the scheduled teams before they have season observations and advances to new fixture teams", async () => {
  for (const emptyRoster of [false, true]) {
    let fixture = { match_id: "first", bo: 5, teams: [
      { team_id: "wb", team_name: "WB" }, { team_id: "lgd", team_name: "LGD" },
    ] };
    const priorTeams = [{ team_id: "wolves", team_name: "Wolves" }, { team_id: "ttg", team_name: "TTG" }];
    const context = {
      leagueId: ref("20260004"), modelLoadVersion: 0, lineupScoreRequestNumber: 0,
      TEAM_A: "team-a", TEAM_B: "team-b", liveRequests: { disposed: false },
      modelSession: { version: async () => "pinned-model" },
      fetchDraftModel: async () => ({ model_reference_teams: priorTeams }),
      fetchSeasonTeams: async () => {
        if (emptyRoster) throw Object.assign(new Error("No recorded teams"), { status: 404 });
        return priorTeams;
      },
      fetchUpcomingMatch: async () => fixture,
      emptyBoard: () => ({}), t: (key) => key,
    };
    for (const name of ["loading", "error", "result", "lineupScore", "lineupScoreLoading", "lineupScoreError",
      "model", "modelVersion", "seasonTeams", "upcomingMatch", "liveMatch", "liveFollowDismissed",
      "liveAppliedGameSignature", "selectedTeamIds", "board", "history", "bpOrder", "globalMode",
      "seriesGame", "bestOf", "pickerTarget"]) context[name] = ref(null);
    for (const name of ["invalidateLiveMatch", "clearCommentary", "stopFollowingLiveMatch", "stopLiveMatchPolling",
      "stopLiveMatchCheckSchedule", "stopLiveScheduleClock", "clearVersionTree", "resetSeriesTeams"]) context[name] = () => {};
    const functions = componentFunctions("./DraftSimulatorPage.vue",
      ["teamsWithUpcomingFixtureFirst", "loadModel", "moveToNextScheduledFixture"], context);
    await functions.loadModel();
    assert.equal(context.error.value, "");
    assert.deepEqual(context.selectedTeamIds.value, { "team-a": "wb", "team-b": "lgd" });
    assert.deepEqual(context.seasonTeams.value.slice(0, 2).map((team) => team.team_id), ["wb", "lgd"]);
    assert.equal(context.seasonTeams.value[0].evidence_scope, "season_fixture");
    assert.equal(context.seasonTeams.value[0].battle_count, undefined);
    assert.equal(context.bestOf.value, 5);

    fixture = { match_id: "second", bo: 7, teams: [
      { team_id: "jdg", team_name: "JDG" }, { team_id: "edg", team_name: "EDG" },
    ] };
    context.liveMatch.value = { match: { match_id: "first" } };
    await functions.moveToNextScheduledFixture();
    assert.equal(context.upcomingMatch.value.match_id, "second");
    assert.deepEqual(context.selectedTeamIds.value, { "team-a": "jdg", "team-b": "edg" });
    assert.equal(context.bestOf.value, 7);
    assert.equal(context.liveMatch.value, null);
  }
});
