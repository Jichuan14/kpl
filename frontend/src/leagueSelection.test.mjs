import test from "node:test";
import assert from "node:assert/strict";
import { availableLeagueId, inferLeagueSelectionMode, newestPublishedLeague } from "./leagueSelection.js";

const old = { league_id: "20260003", year: 2026, season: 3, start_time: "2026-06-17 00:00:00" };
const current = { league_id: "20260004", year: 2026, season: 4, start_time: "2026-09-28 00:00:00" };

test("fresh and legacy-default visitors follow a newly published season", () => {
  assert.equal(inferLeagueSelectionMode(null, null, old.league_id), "auto");
  assert.equal(inferLeagueSelectionMode(old.league_id, null, old.league_id), "auto");
  assert.equal(availableLeagueId([old], "auto", null), old.league_id);
  assert.equal(availableLeagueId([old, current], "auto", null), current.league_id);
});

test("newest published season uses start date even when catalog order differs", () => {
  assert.equal(newestPublishedLeague([old, current]).league_id, current.league_id);
  assert.equal(newestPublishedLeague([current, old]).league_id, current.league_id);
});

test("intentional stored season remains selected while published", () => {
  assert.equal(inferLeagueSelectionMode(old.league_id, "explicit", old.league_id), "explicit");
  assert.equal(inferLeagueSelectionMode("20250004", null, old.league_id), "explicit");
  assert.equal(availableLeagueId([old, current], "explicit", old.league_id), old.league_id);
  assert.equal(availableLeagueId([current], "explicit", old.league_id), current.league_id);
  assert.equal(availableLeagueId([old, current], "explicit", old.league_id), old.league_id);
});

test("unpublished future catalog entries cannot become the public default", () => {
  const future = { league_id: "20270001", year: 2027, season: 1, start_time: "2027-01-01" };
  const published = [old, current]; // seasons.json excludes future without overview.json.
  assert.equal(availableLeagueId(published, "auto", null), current.league_id);
  assert.equal(availableLeagueId([], "auto", null), "");
  assert.notEqual(availableLeagueId(published, "auto", null), future.league_id);
});

test("catalog updates follow auto mode, then preserve a deliberate browser choice", async () => {
  const values = new Map([["kpl-lab:selected-league-id", old.league_id]]);
  const previousWindow = globalThis.window;
  globalThis.window = { localStorage: {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  } };
  try {
    const { selectedLeagueId, selectAvailableLeague } = await import("./selectedLeague.js?selection-test");
    selectAvailableLeague([old, current]);
    assert.equal(selectedLeagueId.value, current.league_id);
    assert.equal(values.get("kpl-lab:league-selection-mode"), "auto");
    selectedLeagueId.value = old.league_id;
    assert.equal(values.get("kpl-lab:league-selection-mode"), "explicit");
    selectAvailableLeague([old, current]);
    assert.equal(selectedLeagueId.value, old.league_id);
    selectAvailableLeague([current]);
    assert.equal(selectedLeagueId.value, current.league_id);
    selectAvailableLeague([old, current]);
    assert.equal(selectedLeagueId.value, old.league_id);
  } finally {
    globalThis.window = previousWindow;
  }
});
