import test from "node:test";
import assert from "node:assert/strict";
import { createPublicSeasonState } from "./publicSeasonState.js";

const old = { league_id: "20260003", year: 2026, season: 3 };
const current = { league_id: "20260004", year: 2026, season: 4, artifacts: {} };
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

test("separate visits use server default and full reload ignores old localStorage", async () => {
  const previous = globalThis.window;
  globalThis.window = { localStorage: { getItem() { throw Error("Season storage must not be read"); }, setItem() { throw Error("Season storage must not be written"); } } };
  try {
    let defaultId = old.league_id;
    const visit = () => createPublicSeasonState(async () => [old, current], async () => ({ default_league_id: defaultId }));
    const a = visit(), b = visit();
    await Promise.all([a.loadSeasons(), b.loadSeasons()]);
    assert.equal(a.selectedLeagueId.value, old.league_id);
    assert.equal(b.selectedLeagueId.value, old.league_id);
    a.selectLeague(current.league_id);
    assert.equal(b.selectedLeagueId.value, old.league_id);
    defaultId = current.league_id;
    await b.loadSeasons();
    assert.equal(b.selectedLeagueId.value, old.league_id); // No live broadcast.
    const reload = visit(); await reload.loadSeasons();
    assert.equal(reload.selectedLeagueId.value, current.league_id);
  } finally { globalThis.window = previous; }
});

test("no saved default uses newest full catalog even with zero artifacts", async () => {
  const state = createPublicSeasonState(async () => [current, old], async () => ({ default_league_id: null }));
  await state.loadSeasons();
  assert.equal(state.selectedLeagueId.value, current.league_id);
});

test("server default is authoritative if catalog response briefly lags", async () => {
  const state = createPublicSeasonState(async () => [old], async () => ({ default_league_id: current.league_id }));
  await state.loadSeasons(); assert.equal(state.selectedLeagueId.value, current.league_id);
});

test("concurrent startup shares requests and preserves a manual visit choice", async () => {
  const first = deferred(); let catalogs = 0, defaults = 0;
  const state = createPublicSeasonState(() => { catalogs++; return first.promise; }, async () => { defaults++; return { default_league_id: current.league_id }; });
  const early = state.loadSeasons(), later = state.loadSeasons();
  assert.equal(early, later);
  state.selectLeague(old.league_id);
  first.resolve([old, current]); await Promise.all([early, later]);
  assert.equal(catalogs, 1); assert.equal(defaults, 1);
  assert.equal(state.selectedLeagueId.value, old.league_id);
  await state.loadSeasons();
  assert.equal(catalogs, 2); assert.equal(defaults, 1);
});

test("failed shared initialization can retry, and a deliberate save wins an in-flight default", async () => {
  let calls = 0;
  const preference = deferred();
  const state = createPublicSeasonState(async () => { if (++calls === 1) throw Error('temporary'); return [old, current]; }, () => preference.promise);
  await assert.rejects(state.loadSeasons(), /temporary/);
  const retry = state.loadSeasons();
  state.applySavedDefault(old.league_id);
  preference.resolve({ default_league_id: current.league_id }); await retry;
  assert.equal(state.selectedLeagueId.value, old.league_id);
  assert.equal(state.savedDefaultLeagueId.value, old.league_id);
  assert.equal(calls, 2);
});
