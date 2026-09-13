export function copyVersionAction(entry) {
  return {
    field: String(entry.field),
    heroId: Number(entry.heroId),
    bpOrder: Number(entry.bpOrder),
  };
}

export function snapshotVersionState(state) {
  return {
    board: {
      blue_picks: [...state.board.blue_picks].map(Number),
      red_picks: [...state.board.red_picks].map(Number),
      blue_bans: [...state.board.blue_bans].map(Number),
      red_bans: [...state.board.red_bans].map(Number),
    },
    bpOrder: Number(state.bpOrder),
    history: [...state.history].map(copyVersionAction),
    blueUsed: [...state.blueUsed].map(Number),
    redUsed: [...state.redUsed].map(Number),
  };
}

export function cloneVersionNode(node) {
  if (node.type === "segment") {
    return {
      id: node.id,
      type: "segment",
      actions: [...node.actions].map(copyVersionAction),
      state: node.state ? snapshotVersionState(node.state) : null,
    };
  }
  return {
    id: node.id,
    type: "fork",
    selectedBranchId: node.selectedBranchId ?? null,
    baseState: node.baseState ? snapshotVersionState(node.baseState) : null,
    branches: [...(node.branches || [])].map((branch) => ({
      id: branch.id,
      actions: [...branch.actions].map(copyVersionAction),
      state: branch.state ? snapshotVersionState(branch.state) : null,
    })),
  };
}

export function createVersionSegment(id, actions, state) {
  return {
    id,
    type: "segment",
    actions: [...actions].map(copyVersionAction),
    state: snapshotVersionState(state),
  };
}

export function createVersionFork(id, baseState) {
  return {
    id,
    type: "fork",
    branches: [],
    selectedBranchId: null,
    baseState: snapshotVersionState(baseState),
  };
}

export function updateVersionFork(nodes, payload) {
  return nodes.map((node) => {
    if (node.id !== payload.sessionId || node.type !== "fork") return cloneVersionNode(node);
    return {
      ...cloneVersionNode(node),
      selectedBranchId: payload.selectedBranchId ?? node.selectedBranchId,
      branches: payload.branches
        .map((branch) => ({
          id: branch.id,
          actions: [...branch.actions].map(copyVersionAction),
          state: branch.state ? snapshotVersionState(branch.state) : null,
        }))
        .filter((branch) => branch.actions.length),
    };
  });
}

export function pinVersionForkBranch(nodes, sessionId, branch, selectedBranchId = null) {
  const pinned = {
    id: branch.id,
    actions: [...(branch.actions || [])].map(copyVersionAction),
    state: branch.state ? snapshotVersionState(branch.state) : null,
  };
  return nodes.map((node) => {
    if (node.id !== sessionId || node.type !== "fork") return cloneVersionNode(node);
    const next = cloneVersionNode(node);
    const index = next.branches.findIndex((item) => sameVersionId(item.id, pinned.id));
    if (!pinned.actions.length) {
      if (index >= 0) next.branches.splice(index, 1);
    } else if (index >= 0) {
      next.branches[index] = pinned;
    } else {
      next.branches.push(pinned);
    }
    if (selectedBranchId != null) next.selectedBranchId = selectedBranchId;
    return next;
  });
}

function sameVersionId(left, right) {
  return left != null && right != null && String(left) === String(right);
}

export function sameVersionHistory(left = [], right = []) {
  if (left.length !== right.length) return false;
  return left.every((entry, index) => {
    const other = right[index];
    return Number(entry.heroId) === Number(other.heroId)
      && String(entry.field) === String(other.field)
      && Number(entry.bpOrder) === Number(other.bpOrder);
  });
}

export function findForkAtHistory(nodes, history) {
  return nodes.find((node) => (
    node.type === "fork" && sameVersionHistory(node.baseState?.history || [], history)
  )) || null;
}

export function upsertMainPath(nodes, actions, state, id = "main-path") {
  const copied = [...actions].map(copyVersionAction);
  const forks = nodes.filter((node) => node.type === "fork").map(cloneVersionNode);
  if (!copied.length) return forks;
  const existing = nodes.find((node) => node.type === "segment");
  return [createVersionSegment(existing?.id || id, copied, state), ...forks];
}

function checkpointState(node, branchId) {
  if (branchId == null) {
    return node.type === "segment" ? node.state : node.baseState;
  }
  if (node.type !== "fork") return null;
  const branch = node.branches.find((item) => sameVersionId(item.id, branchId));
  return branch?.state || null;
}

export function restoreVersionTree(nodes, checkpoint) {
  const index = nodes.findIndex((node) => node.id === checkpoint.nodeId);
  if (index < 0) return null;
  const next = nodes.map(cloneVersionNode);
  const node = next[index];
  const state = checkpointState(node, checkpoint.branchId);
  if (!state) return null;
  if (checkpoint.branchId != null) {
    if (node.type !== "fork") return null;
    node.selectedBranchId = node.branches.find((item) => sameVersionId(item.id, checkpoint.branchId))?.id ?? null;
  }
  return {
    nodes: next,
    state: snapshotVersionState(state),
    cursor: state.history.length,
    activeCheckpoint: checkpoint.branchId == null ? node.id : `${node.id}:${checkpoint.branchId}`,
  };
}
