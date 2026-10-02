import sys
from pathlib import Path
import unittest
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from export_production_hero_feature_space import build_feature_space, production_vectors


def fixture(count=3):
    return {'model_type': 'sequence_familiarity_residual_choice', 'model_fingerprint': 'exact-model',
            'hero_feature_matrix': [[float(i), 1] for i in range(count)],
            'base_artifact': {'target_season': 'season', 'hero_ids': list(range(count)), 'feature_names': ['lane__mid', 'legacy_feature_known'],
                              'parameters': {'bag': {'feature_projection.weight': [[2, 0], [0, 3]], 'hero_residual.weight': [[1, -1]] * (count + 1)}}}}


class ProductionFeatureSpaceTests(unittest.TestCase):
    def test_runtime_formula_and_observed_counts(self):
        model = fixture()
        np.testing.assert_array_equal(production_vectors(model), [[1, 2], [3, 2], [5, 2]])
        result = build_feature_space(model, 'season', [{'action': 'pick', 'selected_hero_id': 1}, {'action': 'ban', 'selected_hero_id': 1}])
        self.assertEqual(result['source_model_fingerprint'], 'exact-model')
        self.assertEqual(result['source_dimension'], 2)
        self.assertEqual(result['rows'][0]['nearest_hero_ids'], [1, 2])
        self.assertEqual(result['rows'][1]['weighted_bp_action_count'], 2)
        self.assertTrue(result['rows'][1]['feature_known'])
        self.assertEqual(result['rows'][1]['primary_lane'], 'mid')
        self.assertTrue(np.isfinite([[row['x'], row['y']] for row in result['rows']]).all())

    def test_single_and_degenerate_maps_are_finite_without_self_neighbors(self):
        for count in (1, 3):
            model = fixture(count)
            model['hero_feature_matrix'] = [[0, 1]] * count
            result = build_feature_space(model, 'season', [])
            self.assertEqual(result['explained_variance_ratio'], [0, 0])
            for row in result['rows']:
                self.assertEqual((row['x'], row['y']), (0, 0))
                self.assertNotIn(row['hero_id'], row['nearest_hero_ids'])

    def test_wrong_season_missing_source_and_invalid_vectors_fail(self):
        with self.assertRaises(ValueError): build_feature_space(fixture(), 'other', [])
        model = fixture(); model['model_fingerprint'] = ''
        with self.assertRaises(ValueError): production_vectors(model)
        model = fixture(); model['hero_feature_matrix'][0][0] = float('nan')
        with self.assertRaises(ValueError): production_vectors(model)
