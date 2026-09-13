export const MAX_SCENARIO_NODES = 200;

export function snapshotDraft(state) {
  return structuredClone(state);
}

export function nextScenarioNode(nodes, parentId, hero, state) {
  if (nodes.length >= MAX_SCENARIO_NODES) return null;
  return {
    id: crypto.randomUUID(), parentId, hero, state: snapshotDraft(state),
    expanded: false, result: null,
  };
}

export function scenarioDifference(left, right) {
  if (!left || !right) return null;
  const value = (node) => node.result?.average_blue_relative_lineup_advantage;
  return {
    left: value(left), right: value(right),
    delta: value(left) == null || value(right) == null ? null : value(left) - value(right),
  };
}
