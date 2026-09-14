export function copyVersionAction(entry) {
  return { field: String(entry.field), heroId: Number(entry.heroId), bpOrder: Number(entry.bpOrder) };
}

export function snapshotVersionState(state) {
  return {
    board: {
      blue_picks: [...state.board.blue_picks].map(Number), red_picks: [...state.board.red_picks].map(Number),
      blue_bans: [...state.board.blue_bans].map(Number), red_bans: [...state.board.red_bans].map(Number),
    },
    bpOrder: Number(state.bpOrder), history: [...state.history].map(copyVersionAction),
    blueUsed: [...state.blueUsed].map(Number), redUsed: [...state.redUsed].map(Number),
  };
}

export function sameVersionHistory(left = [], right = []) {
  return left.length === right.length && left.every((entry, index) => {
    const other = right[index];
    return Number(entry.heroId) === Number(other?.heroId)
      && String(entry.field) === String(other?.field)
      && Number(entry.bpOrder) === Number(other?.bpOrder);
  });
}

export function cloneVersionNode(node) {
  return {
    id: String(node.id), parentId: node.parentId == null ? null : String(node.parentId),
    actions: [...(node.actions || [])].map(copyVersionAction),
    state: snapshotVersionState(node.state), createdOrder: Number(node.createdOrder || 0),
  };
}

export function createVersionCheckpoint({ id, parentId = null, actions, state, createdOrder = 0 }) {
  const copiedActions = [...(actions || [])].map(copyVersionAction);
  if (!copiedActions.length) return null;
  return {
    id: String(id), parentId: parentId == null ? null : String(parentId), actions: copiedActions,
    state: snapshotVersionState(state), createdOrder: Number(createdOrder),
  };
}

export function appendVersionCheckpoint(nodes, checkpoint) {
  if (!checkpoint?.actions?.length) return [...nodes].map(cloneVersionNode);
  const next = [...nodes].map(cloneVersionNode);
  // Repeated saves at the same board do not create duplicate checkpoints.
  if (next.some((node) => sameVersionHistory(node.state.history, checkpoint.state.history))) return next;
  return [...next, cloneVersionNode(checkpoint)];
}

export function findVersionCheckpoint(nodes, id) {
  return nodes.find((node) => String(node.id) === String(id)) || null;
}

export function findDeepestVersionPrefix(nodes, history) {
  return nodes
    .filter((node) => {
      const candidate = node.state?.history || [];
      return candidate.length < history.length
        && sameVersionHistory(history.slice(0, candidate.length), candidate);
    })
    .sort((left, right) => (
      (right.state.history.length - left.state.history.length)
      || (right.createdOrder - left.createdOrder)
    ))[0] || null;
}

export function restoreVersionTree(nodes, checkpoint) {
  const node = findVersionCheckpoint(nodes, checkpoint?.nodeId);
  if (!node?.state) return null;
  return {
    nodes: nodes.map(cloneVersionNode), state: snapshotVersionState(node.state),
    cursor: node.state.history.length, activeCheckpoint: node.id,
  };
}

export function actionsSinceCheckpoint(history, checkpoint) {
  const base = checkpoint?.state?.history || [];
  if (!sameVersionHistory(history.slice(0, base.length), base)) return [...history].map(copyVersionAction);
  return history.slice(base.length).map(copyVersionAction);
}

export function buildVersionTree(nodes) {
  const byParent = new Map();
  const ids = new Set(nodes.map((node) => String(node.id)));
  [...nodes].map(cloneVersionNode)
    .sort((a, b) => a.createdOrder - b.createdOrder || a.id.localeCompare(b.id))
    .forEach((node) => {
      const parentId = node.parentId && ids.has(node.parentId) ? node.parentId : null;
      const list = byParent.get(parentId) || [];
      list.push(node);
      byParent.set(parentId, list);
    });
  return byParent;
}
