import test from "node:test";
import assert from "node:assert/strict";
import { nextScenarioNode } from "./composables/draftScenario.js";

test("nested scenarios retain root → child → grandchild lineage without mutating ancestors", () => {
  if (!globalThis.crypto) globalThis.crypto = { randomUUID: () => "scenario-id" };
  const rootPrefix = { bp_order: 1, blue_picks: [], red_picks: [], blue_bans: [], red_bans: [] };
  const root = nextScenarioNode([], null, { hero_id: 101 }, rootPrefix);
  const afterRoot = { ...rootPrefix, bp_order: 2, blue_bans: [101] };
  const child = nextScenarioNode([root], root.id, { hero_id: 202 }, afterRoot);
  const afterChild = { ...afterRoot, bp_order: 3, red_bans: [202] };
  const grandchild = nextScenarioNode([root, child], child.id, { hero_id: 303 }, afterChild);

  grandchild.state.blue_bans.push(999);

  assert.equal(child.parentId, root.id);
  assert.equal(grandchild.parentId, child.id);
  assert.deepEqual(root.state.blue_bans, []);
  assert.deepEqual(child.state.blue_bans, [101]);
  assert.deepEqual(grandchild.state.blue_bans, [101, 999]);
  assert.equal(child.state.bp_order, 2);
  assert.equal(grandchild.state.bp_order, 3);
});
