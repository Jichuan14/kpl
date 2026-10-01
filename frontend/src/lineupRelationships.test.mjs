import test from 'node:test';
import assert from 'node:assert/strict';
import { indexRelationships, relationshipWeight } from './lineupRelationships.js';

test('indexed evidence matches stable ranking across duplicates, numeric IDs, directions and absent rows', () => {
  const rows = [
    { relation: 'pick_synergy', source_hero_id: '1', target_hero_id: 2, selections: 2, smoothed_lift: 2 },
    { relation: 'pick_synergy', source_hero_id: 1, target_hero_id: '2', selections: 6, smoothed_lift: 2 },
    { relation: 'pick_synergy', source_hero_id: 1, target_hero_id: 2, selections: 12, smoothed_lift: 2 },
    { relation: 'pick_synergy', source_hero_id: 2, target_hero_id: 1, selections: 6, smoothed_lift: 3 },
    { relation: 'counter_pick', source_hero_id: 1, target_hero_id: 2, selections: 3, smoothed_lift: 4 },
  ];
  const lookup = indexRelationships(rows);
  for (const relation of ['pick_synergy', 'counter_pick']) {
    for (const source of [1, 2, 3]) for (const target of [1, 2, 3]) {
      const expected = rows.filter(row => row.relation === relation && Number(row.source_hero_id) === source && Number(row.target_hero_id) === target)
        .sort((a, b) => relationshipWeight(b) - relationshipWeight(a))[0] || null;
      assert.equal(lookup(relation, String(source), target), expected);
    }
  }
  assert.equal(lookup('pick_synergy', 1, 2), rows[1]);
  assert.equal(relationshipWeight(lookup('pick_synergy', 1, 3)), 0);
});
