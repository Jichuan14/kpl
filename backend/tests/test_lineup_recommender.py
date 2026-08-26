import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import lineup_recommender
from app.services import lineup_value
from app.services.lineup_value import load_lineup_value_model


class LineupValueIntegrationTest(unittest.TestCase):
    def test_management_artifact_is_preferred_for_selected_season(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            generated = root / "20260003" / "lineup_value_model.json"
            generated.parent.mkdir(parents=True)
            generated.write_text("{}", encoding="utf-8")
            with patch.object(lineup_value, "GENERATED_MODEL_ROOT", root):
                selected = lineup_value.lineup_value_model_path("20260003")

        self.assertEqual(selected, generated)

    def test_bundled_artifact_remains_fallback_before_management_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(
                lineup_value,
                "GENERATED_MODEL_ROOT",
                Path(temporary_directory),
            ):
                selected = lineup_value.lineup_value_model_path("20260003")

        self.assertEqual(selected, lineup_value.DEFAULT_MODEL_PATH)

    def test_real_artifact_scores_and_explains_a_complete_lineup(self) -> None:
        model = load_lineup_value_model()
        team_ids = list(model.ratings)
        hero_ids = list(model.hero_names)[:10]

        result = model.score(
            team_ids[0], hero_ids[:5], team_ids[1], hero_ids[5:10]
        )

        self.assertGreater(result["blue_advantage"], 0.0)
        self.assertLess(result["blue_advantage"], 1.0)
        self.assertAlmostEqual(
            result["blue_advantage"] + result["red_advantage"], 1.0
        )
        self.assertEqual(result["blue_composition"]["hero_count"], 5.0)
        self.assertIn("hero_synergy", result["grouped_contributions"])
        self.assertIn("not literal win probability", result["interpretation"])

    def test_value_model_rejects_partial_lineups(self) -> None:
        model = load_lineup_value_model()
        team_ids = list(model.ratings)
        hero_ids = list(model.hero_names)[:9]

        with self.assertRaisesRegex(ValueError, "five distinct heroes"):
            model.score(team_ids[0], hero_ids[:4], team_ids[1], hero_ids[4:9])


class FakeValueModel:
    payload = {"version": "fake-v1", "warning": "ranking only"}
    hero_names = {101: "A", 102: "B"}

    @staticmethod
    def composition_profile(heroes):
        return {
            "hero_count": float(len(heroes)),
            "frontline_count": float(101 in heroes),
            "primary_engage_count": 0.0,
            "hard_cc_count": 0.0,
            "mage_count": 0.0,
        }

    @staticmethod
    def team_hero_signal(team_id, hero_id):
        return {"effect": 0.1, "evidence": 0.5, "effective_games": 5.0}

    def score(self, blue_team_id, blue_heroes, red_team_id, red_heroes):
        blue = 0.7 if 101 in blue_heroes else 0.6
        composition = self.composition_profile(blue_heroes)
        return {
            "blue_advantage": blue,
            "red_advantage": 1.0 - blue,
            "grouped_contributions": {
                "team_strength": 0.2,
                "selected_hero_familiarity": 0.1,
                "hero_synergy": 0.05,
                "hero_counters": 0.02,
            },
            "evidence": {
                "hero_familiarity": 0.5,
                "team_pair_synergy": 0.4,
                "historical_counter": 0.3,
            },
            "blue_composition": composition,
            "red_composition": self.composition_profile(red_heroes),
        }


class LineupRecommendationTest(unittest.TestCase):
    def test_behavior_supported_candidates_are_value_ranked(self) -> None:
        policy = {
            "model_label": "Policy",
            "model_generated_at": "2026-08-26T00:00:00Z",
            "candidate_count": 2,
            "next_step": {"bp_order": 17, "side": "blue", "action": "pick"},
            "next_action_probabilities": [
                {"hero_id": 102, "hero_name": "B", "probability": 0.6},
                {"hero_id": 101, "hero_name": "A", "probability": 0.4},
            ],
        }

        def completions(*args, forced_first_hero_id, **kwargs):
            terminal = {
                "blue_picks": [forced_first_hero_id, 2, 3, 4, 5],
                "red_picks": [6, 7, 8, 9, 10],
            }
            path = [
                {"hero_id": forced_first_hero_id, "hero_name": "candidate", "side": "blue", "action": "pick"},
                {"hero_id": 6, "hero_name": "response", "side": "red", "action": "pick"},
            ]
            return {
                "completions": [
                    {"completed": True, "state": terminal, "path": path}
                    for _ in range(lineup_recommender.ROLLOUTS_PER_CANDIDATE)
                ]
            }

        state = {
            "blue_team_id": "blue",
            "blue_team_name": "Blue",
            "red_team_id": "red",
            "red_team_name": "Red",
            "bp_order": 17,
            "blue_picks": [],
            "red_picks": [],
            "blue_bans": [],
            "red_bans": [],
        }
        with (
            patch.object(lineup_recommender, "predict_next_action", return_value=policy),
            patch.object(
                lineup_recommender,
                "sample_forced_draft_completions",
                side_effect=completions,
            ),
            patch.object(
                lineup_recommender,
                "load_lineup_value_model",
                return_value=FakeValueModel(),
            ) as load_value_model,
        ):
            result = lineup_recommender.recommend_lineup(
                "league", state, top_k=2, seed=7
            )

        self.assertEqual(result["recommendations"][0]["hero_id"], 101)
        self.assertEqual(result["recommendations"][0]["rank"], 1)
        self.assertGreater(
            result["recommendations"][0]["advantage_delta_vs_policy_baseline"],
            0.0,
        )
        self.assertEqual(
            result["recommendations"][0]["likely_opponent_responses"][0]["hero_id"],
            6,
        )
        self.assertEqual(result["candidate_gate"]["evaluated_candidate_count"], 2)
        load_value_model.assert_called_once_with("league")


if __name__ == "__main__":
    unittest.main()
