import test from "node:test";
import assert from "node:assert/strict";
import { createManagementSeasonState } from "./managementSeasonState.js";
import { createPublicSeasonState } from "./publicSeasonState.js";

const s3 = { league_id: "20260003", year: 2026, season: 3 };
const s4 = { league_id: "20260004", year: 2026, season: 4 };
const prior = { league_id: "20250004", year: 2025, season: 4 };

test("only explicit management selection saves; initialization and refresh do not", async () => {
  const writes = [];
  const state = createManagementSeasonState(async () => [prior, s3, s4], async () => ({ default_league_id: s4.league_id }), async (id) => writes.push(id));
  await state.loadLeagues(); await state.loadLeagues();
  assert.equal(state.leagueId.value, s4.league_id); assert.deepEqual(writes, []);
  await state.chooseLeague(s3.league_id);
  assert.equal(state.leagueId.value, s3.league_id); assert.equal(state.selectedYear.value, "2026");
  await state.chooseYear("2025");
  assert.equal(state.leagueId.value, prior.league_id); assert.equal(state.selectedYear.value, "2025");
  await state.loadLeagues(); await state.chooseYear(""); await state.chooseLeague("");
  assert.deepEqual(writes, [s3.league_id, prior.league_id]);
});

test("failed management save restores previous target and year", async () => {
  const state = createManagementSeasonState(async () => [prior, s4], async () => ({ default_league_id: s4.league_id }), async () => { throw Error("write rejected"); });
  await state.loadLeagues(); assert.equal(await state.chooseYear("2025"), false);
  assert.equal(state.leagueId.value, s4.league_id); assert.equal(state.selectedYear.value, "2026");
  assert.equal(state.defaultError.value, "write rejected"); assert.equal(state.savingSiteDefault.value, false);
});

test("visitor selection cannot save defaults or retarget management", async () => {
  const writes = [];
  const management = createManagementSeasonState(async () => [s3, s4], async () => ({ default_league_id: s4.league_id }), async (id) => writes.push(id));
  const visitor = createPublicSeasonState(async () => [s3, s4], async () => ({ default_league_id: s4.league_id }));
  await Promise.all([management.loadLeagues(), visitor.loadSeasons()]); visitor.selectLeague(s3.league_id);
  assert.equal(management.leagueId.value, s4.league_id); assert.deepEqual(writes, []);
  await management.chooseLeague(s3.league_id); visitor.selectLeague(s4.league_id); await visitor.loadSeasons();
  assert.equal(visitor.selectedLeagueId.value, s4.league_id); assert.equal(management.leagueId.value, s3.league_id);
  assert.deepEqual(writes, [s3.league_id]);
});

test("management job submission captures target and late status cannot overwrite another season", async () => {
  globalThis.document = { documentElement: {}, title: "" };
  const { createManagement } = await import("./composables/useManagement.js");
  const queued = [];
  let resolveStatus;
  const state = createManagement({
    polling: () => ({ start() {}, stop() {} }),
    fetchLeagues: async () => [s3, s4], fetchSiteDefault: async () => ({ default_league_id: s4.league_id }),
    saveSiteDefault: async () => {}, fetchPipelineJobs: async () => [],
    fetchDataStatus: (id) => id === s4.league_id ? new Promise((resolve) => { resolveStatus = resolve; }) : Promise.resolve({ league_id: id }),
    queueFullUpdate: async (id) => { queued.push(id); return { id: "job", league_id: id, kind: "full_update", status: "pending" }; },
  });
  await state.loadLeagues();
  const pending = state.loadStatus();
  await state.runFullUpdate();
  await state.chooseManagementLeague(s3.league_id);
  resolveStatus({ league_id: s4.league_id }); await pending;
  assert.deepEqual(queued, [s4.league_id]);
  assert.deepEqual(state.dataStatus.value, { league_id: s3.league_id });
});

test("pending management save clears old status and prevents unconfirmed-target jobs", async () => {
  globalThis.document = { documentElement: {}, title: "" };
  const { createManagement } = await import("./composables/useManagement.js");
  let finishSave, jobs = 0;
  const state = createManagement({
    polling: () => ({ start() {}, stop() {} }),
    fetchLeagues: async () => [s3, s4], fetchSiteDefault: async () => ({ default_league_id: s4.league_id }),
    saveSiteDefault: () => new Promise((resolve) => { finishSave = resolve; }),
    fetchPipelineJobs: async () => [], fetchDataStatus: async (id) => ({ league_id: id }),
    queueFullUpdate: async () => { jobs++; },
  });
  await state.loadLeagues(); await state.loadStatus();
  const saving = state.chooseManagementLeague(s3.league_id);
  assert.equal(state.dataStatus.value, null); assert.equal(state.savingSiteDefault.value, true);
  await state.runFullUpdate(); assert.equal(jobs, 0);
  finishSave(); await saving;
  assert.deepEqual(state.dataStatus.value, { league_id: s3.league_id });
});

test("same-tab management season and year saves update every shared public alias after success", async () => {
  globalThis.document = { documentElement: {}, title: "" };
  const { createManagement } = await import("./composables/useManagement.js");
  const { publicSeasonState, selectedLeagueId } = await import("./selectedLeague.js");
  const { selectedFactualLeagueId } = await import("./selectedFactualLeague.js");
  let persistedDefault = s4.league_id;
  const management = createManagement({
    polling: () => ({ start() {}, stop() {} }),
    fetchLeagues: async () => [prior, s3, s4], fetchSiteDefault: async () => ({ default_league_id: persistedDefault }),
    saveSiteDefault: async (id) => { persistedDefault = id; },
    fetchDataStatus: async (id) => ({ league_id: id }),
    onSavedDefault: publicSeasonState.applySavedDefault,
  });
  publicSeasonState.applySavedDefault(s4.league_id);
  await management.loadLeagues();
  publicSeasonState.selectLeague(prior.league_id); // More override must yield to deliberate save.
  await management.chooseManagementLeague(s3.league_id);
  assert.equal(selectedLeagueId.value, s3.league_id);
  assert.equal(selectedFactualLeagueId.value, s3.league_id);
  assert.equal(publicSeasonState.savedDefaultLeagueId.value, s3.league_id);
  await management.chooseManagementYear("2025");
  assert.equal(selectedLeagueId.value, prior.league_id);
  assert.equal(selectedFactualLeagueId.value, prior.league_id);
  assert.equal(publicSeasonState.savedDefaultLeagueId.value, prior.league_id);
  const reload = createPublicSeasonState(async () => [prior, s3, s4], async () => ({ default_league_id: persistedDefault }));
  await reload.loadSeasons(); assert.equal(reload.selectedLeagueId.value, prior.league_id);
});

test("failed, invalid, no-op, initialization and refresh never publish a default change", async () => {
  const visitor = createPublicSeasonState(async () => [prior, s3, s4], async () => ({ default_league_id: s4.league_id }));
  await visitor.loadSeasons(); visitor.selectLeague(prior.league_id);
  const published = [];
  const management = createManagementSeasonState(async () => [prior, s3, s4], async () => ({ default_league_id: s4.league_id }), async () => { throw Error("write failed"); }, (id) => { published.push(id); visitor.applySavedDefault(id); });
  await management.loadLeagues(); await management.loadLeagues();
  await management.chooseLeague(s4.league_id); await management.chooseLeague("invalid");
  await management.chooseLeague(s3.league_id); await management.chooseYear("2025");
  assert.deepEqual(published, []);
  assert.equal(visitor.selectedLeagueId.value, prior.league_id);
  assert.equal(visitor.savedDefaultLeagueId.value, s4.league_id);
  assert.equal(management.leagueId.value, s4.league_id);
});

test("successful management save supersedes a pending initial public default response", async () => {
  let finishInitialDefault;
  let writes = 0;
  const visitor = createPublicSeasonState(async () => [s3, s4], () => new Promise((resolve) => { finishInitialDefault = resolve; }));
  const pending = visitor.loadSeasons();
  const management = createManagementSeasonState(async () => [s3, s4], async () => ({ default_league_id: s4.league_id }), async () => { writes++; }, visitor.applySavedDefault);
  await management.loadLeagues(); await management.chooseLeague(s3.league_id);
  finishInitialDefault({ default_league_id: s4.league_id }); await pending;
  assert.equal(visitor.selectedLeagueId.value, s3.league_id);
  assert.equal(visitor.savedDefaultLeagueId.value, s3.league_id);
  assert.deepEqual(visitor.seasons.value, [s3, s4]);
  visitor.selectLeague(s4.league_id); await visitor.loadSeasons();
  assert.equal(visitor.selectedLeagueId.value, s4.league_id);
  assert.equal(visitor.savedDefaultLeagueId.value, s3.league_id);
  assert.equal(management.leagueId.value, s3.league_id); assert.equal(writes, 1);
});
