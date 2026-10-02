export function relationshipWeight(row) {
  const lift = Math.max(1, Number(row?.smoothed_lift || 1));
  const support = Math.min(1, Number(row?.selections || 0) / 6);
  return Math.log2(lift) * support;
}

const key = (relation, source, target) => `${relation}:${Number(source)}:${Number(target)}`;

// Keep the first strongest row, matching the former stable descending sort.
export function indexRelationships(rows) {
  const index = new Map();
  for (const row of rows) {
    const id = key(row.relation, row.source_hero_id, row.target_hero_id);
    const previous = index.get(id);
    if (!previous || relationshipWeight(row) > relationshipWeight(previous)) index.set(id, row);
  }
  return (relation, source, target) => index.get(key(relation, source, target)) || null;
}
