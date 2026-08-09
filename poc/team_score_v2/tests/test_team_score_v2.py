from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "team_score_v2.py"
SPEC = importlib.util.spec_from_file_location("team_score_v2_test", MODULE_PATH)
assert SPEC and SPEC.loader
V2 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = V2
SPEC.loader.exec_module(V2)


class MechanicsTests(unittest.TestCase):
    def test_composition_metrics_have_expected_shape(self):
        mechanics, metadata = V2.load_mechanics(V2.MECHANICS_PATH)
        hero_ids = list(mechanics)[:5]
        values, coverage = V2.composition_mechanics(hero_ids, mechanics)
        self.assertEqual(len(values), 4)
        self.assertEqual(coverage, 1.0)
        self.assertEqual(metadata["hero_count"], len(mechanics))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in values))

    def test_missing_mechanics_falls_back_safely(self):
        values, coverage = V2.composition_mechanics([999001, 999002], {})
        self.assertEqual(values, [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(coverage, 0.0)


class HierarchyTests(unittest.TestCase):
    def battle(self):
        return V2.V1.Battle(
            league_id="s1",
            match_id="m1",
            battle_id="b1",
            start_time="2026-01-01",
            battle_seq=1,
            team_a_id="a",
            team_a_name="A",
            team_b_id="b",
            team_b_name="B",
            heroes_a=(1, 2, 3, 4, 5),
            heroes_b=(6, 7, 8, 9, 10),
            team_a_won=1,
        )

    def test_team_pair_updates_both_sides(self):
        state = V2.HistoricalStateV2()
        state.update(self.battle())
        self.assertEqual(state.team_pair[("a", 1, 2)].count, 1.0)
        self.assertEqual(state.team_pair[("b", 6, 7)].count, 1.0)
        self.assertGreater(state.team_pair[("a", 1, 2)].residual_sum, 0)
        self.assertLess(state.team_pair[("b", 6, 7)].residual_sum, 0)

    def test_v2_feature_width_and_state_round_trip(self):
        state = V2.HistoricalStateV2()
        state.update(self.battle())
        features, evidence = state.features(
            "a", (1, 2, 3, 4, 5), "b", (6, 7, 8, 9, 10)
        )
        self.assertEqual(len(features), len(V2.FEATURE_NAMES))
        self.assertIn("team_pair_synergy", evidence)
        restored = V2.HistoricalStateV2.from_dict(state.to_dict(), {})
        self.assertEqual(restored.team_pair[("a", 1, 2)].count, 1.0)


if __name__ == "__main__":
    unittest.main()
