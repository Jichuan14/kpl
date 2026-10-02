import test from "node:test";
import assert from "node:assert/strict";
import { loadSelectedStatus } from "./managementStatus.js";

test("a completed job for a prior season cannot replace the selected season status", async () => {
  let resolveOld;
  let selected = "old";
  let shown = null;
  const oldRequest = loadSelectedStatus(
    () => new Promise((resolve) => { resolveOld = resolve; }),
    "old",
    () => selected,
    (status) => { shown = status; },
  );
  selected = "new";
  await loadSelectedStatus(async () => ({ league_id: "new" }), "new", () => selected, (status) => { shown = status; });
  resolveOld({ league_id: "old" });
  await oldRequest;
  assert.deepEqual(shown, { league_id: "new" });
});
