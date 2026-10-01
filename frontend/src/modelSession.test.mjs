import assert from "node:assert/strict";
import test from "node:test";
import { createModelSession } from "./modelSession.js";
import { buildWhatIfForecastPayload } from "./whatIfForecast.js";

test("one mounted tool retains its immutable version after activation changes", async () => {
  let active = "v1";
  let requests = 0;
  const session = createModelSession(async () => { requests += 1; return { model_version: active }; });
  assert.deepEqual(await Promise.all([session.version(), session.version()]), ["v1", "v1"]);
  active = "v2";
  assert.equal(await session.version(), "v1");
  assert.equal(requests, 1);
  assert.equal(await createModelSession(async () => ({ model_version: active })).version(), "v2");
});

test("failed model resolution cannot silently replace a tool's pinned version", async () => {
  let requests = 0;
  const session = createModelSession(async () => { requests += 1; throw new Error("Pinned version unavailable"); });
  await assert.rejects(session.version(), /Pinned version unavailable/);
  await assert.rejects(session.version(), /Pinned version unavailable/);
  assert.equal(requests, 1);
  await assert.rejects(createModelSession(async () => ({})).version(), /No active production model/);
});

test("nested what-if forecasts carry the version into request and cache identity", () => {
  const context = { leagueId: "20260004", modelVersion: "v1", modelType: "personalized", blueTeam: { team_id: 1, team_name: "Blue" }, redTeam: { team_id: 2, team_name: "Red" } };
  const scenario = { bpOrder: 8, board: { blue_picks: [105], red_picks: [106], blue_bans: [107], red_bans: [108] }, blueUsed: [109], redUsed: [110] };
  const payload = buildWhatIfForecastPayload(context, scenario);
  assert.equal(payload.model_version, "v1");
  assert.equal(payload.league_id, "20260004");
  assert.notEqual(JSON.stringify(payload), JSON.stringify(buildWhatIfForecastPayload({ ...context, modelVersion: "v2" }, scenario)));
  payload.blue_picks.push(111);
  assert.deepEqual(scenario.board.blue_picks, [105]);
});
