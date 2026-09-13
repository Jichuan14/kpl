import test from "node:test";
import assert from "node:assert/strict";
import { actionsSinceCheckpoint, appendVersionCheckpoint, buildVersionTree, createVersionCheckpoint, findDeepestVersionPrefix, restoreVersionTree } from "./composables/draftVersionTree.js";

const action = (bpOrder, field, heroId) => ({ bpOrder, field, heroId });
const state = (history) => ({ board: { blue_picks: [], red_picks: [], blue_bans: [], red_bans: [] }, bpOrder: history.length + 1, history, blueUsed: [], redUsed: [] });

test("H1 → three choices → H2 and a rewind produce immutable H2 siblings", () => {
  const h1History = [action(1, "blue_bans", 1)];
  const h1 = createVersionCheckpoint({ id: "h1", actions: h1History, state: state(h1History), createdOrder: 1 });
  const original = [...h1History, action(2, "red_bans", 2), action(3, "blue_picks", 3), action(4, "red_picks", 4)];
  const h2a = createVersionCheckpoint({ id: "h2a", parentId: "h1", actions: actionsSinceCheckpoint(original, h1), state: state(original), createdOrder: 2 });
  const rewound = restoreVersionTree([h1, h2a], { nodeId: "h1" });
  const alternate = [...rewound.state.history, action(2, "red_bans", 22), action(3, "blue_picks", 33), action(4, "red_picks", 44)];
  const h2b = createVersionCheckpoint({ id: "h2b", parentId: "h1", actions: actionsSinceCheckpoint(alternate, h1), state: state(alternate), createdOrder: 3 });
  const nodes = appendVersionCheckpoint(rewound.nodes, h2b);
  assert.deepEqual(buildVersionTree(nodes).get("h1").map((node) => node.id), ["h2a", "h2b"]);
  assert.deepEqual(nodes.find((node) => node.id === "h2a").actions.map((entry) => entry.heroId), [2, 3, 4]);
});

test("a repeated pin with no new action is a no-op", () => {
  const history = [action(1, "blue_bans", 1)];
  const h1 = createVersionCheckpoint({ id: "h1", actions: history, state: state(history), createdOrder: 1 });
  assert.equal(appendVersionCheckpoint([h1], h1).length, 1);
  assert.equal(actionsSinceCheckpoint(history, h1).length, 0);
});

test("repeated pins in one what-if path form an immutable checkpoint chain", () => {
  const h1History = [action(1, "blue_bans", 1)];
  const h1 = createVersionCheckpoint({ id: "h1", actions: h1History, state: state(h1History), createdOrder: 1 });
  const afterA = [...h1History, action(2, "red_bans", 2)];
  const h2 = createVersionCheckpoint({ id: "h2", parentId: "h1", actions: actionsSinceCheckpoint(afterA, h1), state: state(afterA), createdOrder: 2 });
  const afterB = [...afterA, action(3, "blue_picks", 3)];
  const parent = findDeepestVersionPrefix([h1, h2], afterB);
  const h3 = createVersionCheckpoint({ id: "h3", parentId: parent.id, actions: actionsSinceCheckpoint(afterB, parent), state: state(afterB), createdOrder: 3 });
  const nodes = appendVersionCheckpoint([h1, h2], h3);
  assert.equal(h3.parentId, "h2");
  assert.deepEqual(h3.actions.map((entry) => entry.heroId), [3]);
  assert.deepEqual(nodes.find((node) => node.id === "h2").actions.map((entry) => entry.heroId), [2]);
  assert.deepEqual(buildVersionTree(nodes).get("h2").map((node) => node.id), ["h3"]);
});

test("restoring a child keeps exact snapshot and makes it the next recording anchor", () => {
  const base = [action(1, "blue_bans", 1)];
  const h1 = createVersionCheckpoint({ id: "h1", actions: base, state: state(base), createdOrder: 1 });
  const h2History = [...base, action(2, "red_bans", 2)];
  const h2 = createVersionCheckpoint({ id: "h2", parentId: "h1", actions: actionsSinceCheckpoint(h2History, h1), state: state(h2History), createdOrder: 2 });
  const restored = restoreVersionTree([h1, h2], { nodeId: "h2" });
  assert.equal(restored.activeCheckpoint, "h2");
  assert.deepEqual(restored.state.history, h2History);
});

test("a 100-checkpoint topology is built once from parent links in stable order", () => {
  const nodes = [];
  let history = [];
  for (let index = 1; index <= 100; index += 1) {
    history = [...history, action(index, index % 2 ? "blue_bans" : "red_bans", index)];
    nodes.push(createVersionCheckpoint({ id: `n-${index}`, parentId: index === 1 ? null : `n-${index - 1}`, actions: [history.at(-1)], state: state(history), createdOrder: index }));
  }
  const tree = buildVersionTree(nodes);
  assert.equal(tree.get(null)[0].id, "n-1");
  assert.equal(tree.get("n-99")[0].id, "n-100");
});
