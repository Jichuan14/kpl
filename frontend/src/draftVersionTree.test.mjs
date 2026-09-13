import test from "node:test";
import assert from "node:assert/strict";
import {
  createVersionFork,
  createVersionSegment,
  findForkAtHistory,
  pinVersionForkBranch,
  restoreVersionTree,
  sameVersionHistory,
  updateVersionFork,
  upsertMainPath,
} from "./composables/draftVersionTree.js";

const action = (bpOrder, field, heroId) => ({ bpOrder, field, heroId });
const emptyBoard = () => ({ blue_picks: [], red_picks: [], blue_bans: [], red_bans: [] });
const stateAt = (history, bpOrder, board = emptyBoard()) => ({
  board,
  bpOrder,
  history,
  blueUsed: [],
  redUsed: [],
});
const branchWithState = (id, actions, state) => ({ id, actions, state });

test("consecutive main-board actions remain in one version node", () => {
  const node = createVersionSegment("main-1", [
    action(1, "blue_bans", 11),
    action(2, "red_bans", 12),
    action(3, "blue_picks", 13),
  ], stateAt([], 4));
  assert.equal(node.actions.length, 3);
  assert.equal(node.type, "segment");
  assert.ok(node.state);
});

test("a main-board node keeps an independent restore checkpoint", () => {
  const state = {
    board: { blue_picks: [11], red_picks: [], blue_bans: [], red_bans: [] },
    bpOrder: 3,
    history: [action(1, "blue_bans", 10), action(2, "blue_picks", 11)],
    blueUsed: [4],
    redUsed: [5],
  };
  const node = createVersionSegment("main-1", state.history, state);
  state.board.blue_picks.push(12);
  state.history.push(action(3, "red_picks", 13));
  assert.deepEqual(node.state.board.blue_picks, [11]);
  assert.equal(node.state.history.length, 2);
});

test("later main-board actions merge into the same node", () => {
  const first = stateAt([action(1, "blue_bans", 11)], 2, { ...emptyBoard(), blue_bans: [11] });
  const nodes = upsertMainPath([], first.history, first);
  const laterHistory = [...first.history, action(2, "red_bans", 12), action(3, "blue_picks", 13)];
  const merged = upsertMainPath(nodes, laterHistory, stateAt(laterHistory, 4));
  assert.equal(merged.filter((node) => node.type === "segment").length, 1);
  assert.equal(merged[0].actions.length, 3);
  assert.equal(merged[0].id, nodes[0].id);
});

test("a what-if fork records each explored branch", () => {
  const nodes = updateVersionFork([createVersionFork("what-if-1", stateAt([], 4))], {
    sessionId: "what-if-1",
    selectedBranchId: 2,
    branches: [
      branchWithState(1, [action(4, "red_picks", 21)], stateAt([action(4, "red_picks", 21)], 5, { ...emptyBoard(), red_picks: [21] })),
      branchWithState(2, [action(4, "red_picks", 22), action(5, "blue_bans", 23)], stateAt([action(4, "red_picks", 22), action(5, "blue_bans", 23)], 6)),
    ],
  });
  assert.deepEqual(nodes[0].branches.map((branch) => branch.actions.length), [1, 2]);
  assert.equal(nodes[0].selectedBranchId, 2);
  assert.deepEqual(nodes[0].branches[0].state.board.red_picks, [21]);
});

test("pinning a what-if snapshot adds that branch without dropping siblings", () => {
  const fork = createVersionFork("what-if-1", stateAt([], 4));
  const first = pinVersionForkBranch([fork], "what-if-1", branchWithState(
    1,
    [action(4, "red_picks", 21)],
    stateAt([action(4, "red_picks", 21)], 5, { ...emptyBoard(), red_picks: [21] }),
  ));
  const second = pinVersionForkBranch(first, "what-if-1", branchWithState(
    2,
    [action(4, "red_picks", 22)],
    stateAt([action(4, "red_picks", 22)], 5, { ...emptyBoard(), red_picks: [22] }),
  ));
  assert.equal(second[0].branches.length, 2);
  const updated = pinVersionForkBranch(second, "what-if-1", branchWithState(
    1,
    [action(4, "red_picks", 21), action(5, "blue_bans", 23)],
    stateAt([action(4, "red_picks", 21), action(5, "blue_bans", 23)], 6),
  ));
  assert.equal(updated[0].branches.length, 2);
  assert.equal(updated[0].branches[0].actions.length, 2);
  assert.equal(updated[0].branches[1].actions.length, 1);
});

test("restoring a checkpoint keeps the main path snapshot unchanged", () => {
  const firstState = stateAt([action(1, "blue_bans", 11)], 2, { ...emptyBoard(), blue_bans: [11] });
  const first = createVersionSegment("main-path", [action(1, "blue_bans", 11)], firstState);
  const leftState = stateAt(
    [action(1, "blue_bans", 11), action(2, "red_bans", 21)],
    3,
    { ...emptyBoard(), blue_bans: [11], red_bans: [21] },
  );
  const rightState = stateAt(
    [action(1, "blue_bans", 11), action(2, "red_bans", 22)],
    3,
    { ...emptyBoard(), blue_bans: [11], red_bans: [22] },
  );
  const fork = updateVersionFork([createVersionFork("what-if-1", first.state)], {
    sessionId: "what-if-1",
    selectedBranchId: 2,
    branches: [
      branchWithState(1, [action(2, "red_bans", 21)], leftState),
      branchWithState(2, [action(2, "red_bans", 22)], rightState),
    ],
  })[0];

  const restoredBranch = restoreVersionTree([first, fork], { nodeId: "what-if-1", branchId: 1 });
  assert.deepEqual(restoredBranch.nodes.map((node) => node.id), ["main-path", "what-if-1"]);
  assert.equal(restoredBranch.nodes[1].branches.length, 2);
  assert.deepEqual(restoredBranch.nodes[0].state.board.blue_bans, [11]);
  assert.deepEqual(restoredBranch.nodes[0].state.board.red_bans, []);
  assert.deepEqual(restoredBranch.state.board.red_bans, [21]);
  restoredBranch.state.board.red_bans.push(99);
  assert.deepEqual(fork.branches[0].state.board.red_bans, [21]);
});

test("restore reads snapshots through proxies instead of structuredClone", () => {
  const firstState = stateAt([action(1, "blue_bans", 11)], 2, { ...emptyBoard(), blue_bans: [11] });
  const first = createVersionSegment("main-1", [action(1, "blue_bans", 11)], firstState);
  const proxied = new Proxy(first, {});
  assert.throws(() => structuredClone(proxied));
  const restored = restoreVersionTree([proxied], { nodeId: "main-1" });
  assert.deepEqual(restored.state.board.blue_bans, [11]);
  assert.equal(restored.state.history.at(-1).heroId, 11);
});

test("pinning an empty live branch removes it", () => {
  const fork = createVersionFork("what-if-1", stateAt([], 4));
  const pinned = pinVersionForkBranch([fork], "what-if-1", branchWithState(
    "board-1",
    [action(4, "red_picks", 21)],
    stateAt([action(4, "red_picks", 21)], 5, { ...emptyBoard(), red_picks: [21] }),
  ));
  const cleared = pinVersionForkBranch(pinned, "what-if-1", branchWithState("board-1", [], stateAt([], 4)));
  assert.equal(cleared[0].branches.length, 0);
});

test("a later default looks up a fork by matching history, not only length", () => {
  const leftHistory = [action(1, "blue_bans", 11), action(2, "red_bans", 21)];
  const rightHistory = [action(1, "blue_bans", 11), action(2, "red_bans", 22)];
  const nodes = [
    createVersionFork("left", stateAt(leftHistory, 3)),
    createVersionFork("right", stateAt(rightHistory, 3)),
  ];
  assert.equal(findForkAtHistory(nodes, rightHistory).id, "right");
  assert.equal(sameVersionHistory(leftHistory, rightHistory), false);
});
