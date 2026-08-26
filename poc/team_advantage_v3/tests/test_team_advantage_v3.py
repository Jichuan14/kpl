from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np


MODULE_PATH = Path(__file__).resolve().parents[1] / "team_advantage_v3.py"
SPEC = importlib.util.spec_from_file_location("team_advantage_v3_test", MODULE_PATH)
assert SPEC and SPEC.loader
V3 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = V3
SPEC.loader.exec_module(V3)


class MechanicsRuleTests(unittest.TestCase):
    def test_ally_rule_fires(self):
        mechanics = {
            1: {name: 0.0 for name in V3.REQUIRED_FEATURES},
            2: {name: 0.0 for name in V3.REQUIRED_FEATURES},
        }
        mechanics[1]["mechanic__debuff_armor"] = 1.0
        mechanics[2]["damage__physical"] = 1.0
        score, coverage = V3.ally_compatibility([1, 2], mechanics)
        self.assertGreater(score, 0.0)
        self.assertEqual(coverage, 1.0)

    def test_counter_rule_is_directional(self):
        mechanics = {
            1: {name: 0.0 for name in V3.REQUIRED_FEATURES},
            2: {name: 0.0 for name in V3.REQUIRED_FEATURES},
        }
        mechanics[1]["mechanic__debuff_healing_reduction"] = 1.0
        mechanics[2]["mechanic__sustain_heal"] = 1.0
        score, coverage = V3.mechanics_counter([1], [2], mechanics)
        self.assertGreater(score, 0.0)
        self.assertEqual(coverage, 1.0)

    def test_unknown_mechanics_are_neutral(self):
        score, coverage = V3.mechanics_counter([1], [2], {})
        self.assertEqual(score, 0.0)
        self.assertEqual(coverage, 0.0)


class SearchTests(unittest.TestCase):
    def test_target_league_filter_prevents_future_season_leakage(self):
        battles = [
            type("Battle", (), {"league_id": league_id})()
            for league_id in ("s1", "s1", "s2", "s3")
        ]

        included, leagues = V3.battles_through_league(battles, "s2")

        self.assertEqual(leagues, ["s1", "s2"])
        self.assertEqual([battle.league_id for battle in included], ["s1", "s1", "s2"])

    def test_target_league_filter_rejects_unknown_season(self):
        battle = type("Battle", (), {"league_id": "s1"})()
        with self.assertRaisesRegex(ValueError, "no completed battles"):
            V3.battles_through_league([battle], "missing")

    def test_parameter_search_is_reproducible(self):
        first = V3.parameter_candidates(8, 17)
        second = V3.parameter_candidates(8, 17)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 8)

    def test_advantage_coefficients_are_monotonic(self):
        features = np.asarray([[-2.0, 2.0], [-1.0, 1.0], [1.0, -1.0], [2.0, -2.0]])
        outcomes = np.asarray([0.0, 0.0, 1.0, 1.0])
        model = V3.fit_advantage_model(features, outcomes, l2=1.0)
        self.assertTrue(all(value >= 0.0 for value in model["coefficients"]))
        self.assertEqual(
            model["constraint"], "all advantage coefficients are nonnegative"
        )


if __name__ == "__main__":
    unittest.main()
