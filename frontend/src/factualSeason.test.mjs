import test from "node:test";
import assert from "node:assert/strict";
import { selectedFactualLeagueId } from "./selectedFactualLeague.js";
import { createFactualLoader, neutralSeasonRankings, isSeasonOnlyRanking } from "./factualResource.js";

const s3 = { league_id: "20260003", year: 2026, season: 3 };
const s4 = { league_id: "20260004", year: 2026, season: 4, fixture_teams: [] };

test("factual pages alias the same public visit selection as tools", async () => {
  const { selectedLeagueId } = await import("./selectedLeague.js");
  assert.equal(selectedFactualLeagueId, selectedLeagueId);
});

test("empty factual roster is never invented, fixture Elo has no observed scores", () => {
  assert.deepEqual(neutralSeasonRankings(s4).team_rankings, []);
  const result = neutralSeasonRankings({ ...s4, fixture_teams: [{ team_id: "a", team_name: "A" }] });
  const team = result.team_rankings[0];
  assert.equal(team.elo, 1500);
  for (const field of ["rank", "hybrid_score", "decayed_win_rate", "recent_10_wins"]) assert.equal(team[field], null);
  assert.deepEqual(result.position_rankings, []);
  assert.ok(isSeasonOnlyRanking(result, s4.league_id));
  assert.equal(isSeasonOnlyRanking({ ...result, schema_version: 2 }, s4.league_id), false);
  assert.equal(isSeasonOnlyRanking(result, s3.league_id), false);
});

test("late success and failure cannot restore data after unavailable season selection", async () => {
  for (const rejectOld of [false, true]) {
    let resolveOld, rejectOldRequest;
    let selected = "old";
    const state = { payload: null, loading: false, missing: false, error: null };
    const run = createFactualLoader({
      start() { Object.assign(state, { payload: null, loading: true, missing: false, error: null }); },
      load() { return selected === "old" ? new Promise((resolve, reject) => { resolveOld = resolve; rejectOldRequest = reject; }) : null; },
      value(value) { state.payload = value; },
      missing() { state.missing = true; },
      error(error) { state.error = error; },
      finish() { state.loading = false; },
    });
    const old = run();
    selected = "new";
    await run();
    if (rejectOld) rejectOldRequest(new Error("late server failure")); else resolveOld({ old: true });
    await old;
    assert.deepEqual(state, { payload: null, loading: false, missing: true, error: null });
  }
});

test("factual loader distinguishes 404, valid empty rows, and genuine errors", async () => {
  let mode = "missing";
  const events = [];
  const run = createFactualLoader({ start() {}, finish() {},
    load() {
      if (mode === "empty") return { rows: [] };
      const error = new Error("failed"); error.status = mode === "missing" ? 404 : 500; throw error;
    }, value(value) { events.push(value); }, missing() { events.push("missing"); }, error() { events.push("error"); },
  });
  await run(); mode = "empty"; await run(); mode = "failure"; await run();
  assert.deepEqual(events, ["missing", { rows: [] }, "error"]);
});
