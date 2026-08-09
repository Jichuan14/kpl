from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np


MODULE_PATH = Path(__file__).resolve().parents[1] / "team_score_poc.py"
SPEC = importlib.util.spec_from_file_location("team_score_poc", MODULE_PATH)
assert SPEC and SPEC.loader
POC = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = POC
SPEC.loader.exec_module(POC)


class HistoricalStateTests(unittest.TestCase):
    def battle(self, winner: int = 1):
        return POC.Battle(
            league_id="season-1",
            match_id="match-1",
            battle_id="battle-1",
            start_time="2026-01-01",
            battle_seq=1,
            team_a_id="a",
            team_a_name="A",
            team_b_id="b",
            team_b_name="B",
            heroes_a=(1, 2, 3, 4, 5),
            heroes_b=(6, 7, 8, 9, 10),
            team_a_won=winner,
        )

    def test_unseen_matchup_has_neutral_components(self):
        features, evidence = POC.HistoricalState().features(
            "a", (1, 2, 3, 4, 5), "b", (6, 7, 8, 9, 10)
        )
        self.assertEqual(features, [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(evidence["ally_synergy"], 0.0)

    def test_update_is_antisymmetric_for_counters(self):
        state = POC.HistoricalState()
        state.update(self.battle(winner=1))
        forward = state.counters[(1, 6)]
        reverse = state.counters[(6, 1)]
        self.assertAlmostEqual(forward.residual_sum, -reverse.residual_sum)
        self.assertEqual(forward.count, reverse.count)

    def test_feature_generation_precedes_outcome_update(self):
        battle = self.battle(winner=1)
        features, outcomes, _leagues, state = POC.build_prequential_features([battle])
        np.testing.assert_allclose(features, np.zeros((1, 4)))
        self.assertEqual(outcomes.tolist(), [1.0])
        self.assertEqual(state.team_games["a"], 1)

    def test_state_round_trip(self):
        state = POC.HistoricalState()
        state.update(self.battle(winner=1))
        restored = POC.HistoricalState.from_dict(state.to_dict())
        self.assertAlmostEqual(restored.ratings["a"], state.ratings["a"])
        self.assertEqual(restored.ally_pair[(1, 2)].count, 1)


class ModelTests(unittest.TestCase):
    def test_logistic_model_learns_direction(self):
        features = np.asarray([[-2.0], [-1.0], [1.0], [2.0]])
        outcomes = np.asarray([0.0, 0.0, 1.0, 1.0])
        model = POC.fit_logistic(features, outcomes, l2=0.1)
        probabilities = POC.predict_probabilities(model, features)
        self.assertLess(probabilities[0], probabilities[-1])
        self.assertLess(probabilities[1], 0.5)
        self.assertGreater(probabilities[2], 0.5)

    def test_metrics_are_finite(self):
        result = POC.metrics(
            np.asarray([0.0, 1.0]), np.asarray([0.25, 0.75])
        )
        self.assertGreater(result["accuracy"], 0.5)
        self.assertGreater(result["auc"], 0.5)


if __name__ == "__main__":
    unittest.main()
