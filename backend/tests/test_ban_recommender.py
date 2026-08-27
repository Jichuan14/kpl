from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import ban_recommender


def payload() -> dict:
    return {
        "version": "ban-value-model-v1",
        "generated_at": "2026-08-26T00:00:00Z",
        "source": {"ban_decisions": 100},
        "config": {"uncertainty_scale": 0.08},
        "global": [100.0, 200.0],
        "team": [["acting", 50.0, 100.0], ["opponent", 50.0, 100.0]],
        "global_pick": [[101, 18.0, 20.0], [102, 10.0, 20.0]],
        "team_pick": [
            ["opponent", 101, 10.0, 10.0],
            ["opponent", 102, 5.0, 10.0],
        ],
        "global_ban": [[101, 15.0, 20.0], [102, 10.0, 20.0]],
        "opponent_ban": [["opponent", 101, 8.0, 10.0]],
        "ally_pair": [],
        "counter_pair": [],
        "behavior": [["blue|1", 101, 2.0], ["blue|1", 102, 8.0]],
        "behavior_total": {"blue|1": 10.0},
        "opponent_behavior": [],
        "opponent_behavior_total": [],
        "hero_names": {"101": "Threat", "102": "Popular"},
        "interpretation": "Relative denial value.",
    }


def policy() -> dict:
    return {
        "model_type": "stats",
        "model_label": "Policy",
        "model_generated_at": "2026-08-26T00:00:00Z",
        "candidate_count": 2,
        "next_step": {
            "bp_order": 1,
            "action": "ban",
            "side": "blue",
            "team_action_type_number": 1,
        },
        "next_action_probabilities": [
            {"hero_id": 102, "hero_name": "Popular", "probability": 0.8},
            {"hero_id": 101, "hero_name": "Threat", "probability": 0.2},
        ],
    }


STATE = {
    "blue_team_id": "acting",
    "blue_team_name": "Acting",
    "red_team_id": "opponent",
    "red_team_name": "Opponent",
    "blue_picks": [],
    "red_picks": [],
}


class BanRecommendationTests(unittest.TestCase):
    def test_global_bp_removes_threat_value_when_opponent_already_used_hero(self) -> None:
        model = ban_recommender.BanValueModel(payload())
        step = policy()["next_step"]
        normal = model.score(
            state=STATE,
            next_step=step,
            hero_id=101,
            policy_probability=0.2,
            maximum_policy_probability=0.8,
        )
        used_state = {**STATE, "red_used_previous_battles": [101]}
        unavailable = model.score(
            state=used_state,
            next_step=step,
            hero_id=101,
            policy_probability=0.2,
            maximum_policy_probability=0.8,
        )

        self.assertGreater(normal["ban_value"], unavailable["ban_value"])
        self.assertFalse(unavailable["signals"]["opponent_can_pick"])

    def test_denial_value_can_outrank_behavior_probability(self) -> None:
        model = ban_recommender.BanValueModel(payload())
        with patch.object(
            ban_recommender, "load_ban_value_model", return_value=model
        ):
            result = ban_recommender.recommend_ban(
                "league",
                STATE,
                policy=policy(),
                top_k=2,
                risk_mode="balanced",
            )

        self.assertEqual(result["recommender"], "ban_value")
        self.assertEqual(result["recommendations"][0]["hero_id"], 101)
        self.assertEqual(result["recommendations"][0]["action"], "ban")
        self.assertFalse(result["methodology"]["pick_model_used"])
        self.assertNotIn(
            "mechanics_role_coverage",
            result["recommendations"][0]["ban_value_components"],
        )

    def test_missing_artifact_falls_back_to_behavior_ranking(self) -> None:
        with patch.object(
            ban_recommender,
            "load_ban_value_model",
            side_effect=FileNotFoundError,
        ):
            result = ban_recommender.recommend_ban(
                "league",
                STATE,
                policy=policy(),
                top_k=1,
                risk_mode="balanced",
            )

        self.assertEqual(result["recommender"], "ban_behavior_fallback")
        self.assertEqual(result["recommendations"][0]["hero_id"], 102)
        self.assertTrue(result["methodology"]["fallback"])


if __name__ == "__main__":
    unittest.main()
