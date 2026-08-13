import unittest

from app.services.hero_matchup import rank_matchup_recommendations


class HeroMatchupRecommendationTest(unittest.TestCase):
    def test_blends_multi_opponent_evidence_and_favorite_similarity(self) -> None:
        features = [
            {
                "hero_id": 1,
                "hero_name": "Favorite",
                "primary_lane": "mid",
                "x": 0.0,
                "y": 0.0,
                "weighted_bp_action_count": 20,
            },
            {
                "hero_id": 2,
                "hero_name": "Strong response",
                "primary_lane": "mid",
                "x": 0.1,
                "y": 0.0,
                "weighted_bp_action_count": 20,
            },
            {
                "hero_id": 3,
                "hero_name": "Other lane",
                "primary_lane": "jungle",
                "x": 0.0,
                "y": 0.0,
                "weighted_bp_action_count": 100,
            },
            {"hero_id": 10, "hero_name": "Enemy A", "primary_lane": "clash"},
            {"hero_id": 11, "hero_name": "Enemy B", "primary_lane": "farm"},
        ]
        counters = [
            {
                "context_level": "overall",
                "is_peak_battle": False,
                "opponent_hero_id": enemy,
                "candidate_hero_id": candidate,
                "selection_count": 12,
                "smoothed_lift": lift,
                "battle_win_count_when_selected": wins,
                "battle_win_rate_when_selected": wins / 12,
            }
            for enemy in (10, 11)
            for candidate, lift, wins in ((1, 0.7, 5), (2, 2.0, 8), (3, 3.0, 10))
        ]

        result = rank_matchup_recommendations(features, counters, [1], [10, 11])

        self.assertEqual(result["recommendations"][0]["hero_id"], 2)
        self.assertNotIn(3, [row["hero_id"] for row in result["recommendations"]])
        self.assertEqual(result["favorites"][0]["rank"], 2)
        self.assertEqual(result["recommendations"][0]["supported_opponents"], 2)

        multi_favorite_result = rank_matchup_recommendations(
            features, counters, [1, 3], [10, 11]
        )
        self.assertEqual(multi_favorite_result["recommendations"][0]["hero_id"], 3)
        self.assertEqual(len(multi_favorite_result["favorites"]), 2)
        self.assertEqual(
            multi_favorite_result["methodology"]["lane_constraints"],
            ["jungle", "mid"],
        )

        lane_result = rank_matchup_recommendations(
            features, counters, [1, 3], [10, 11], "jungle"
        )
        self.assertEqual(
            [row["hero_id"] for row in lane_result["recommendations"]], [3]
        )
        self.assertEqual(lane_result["methodology"]["selected_lane"], "jungle")

    def test_rejects_invalid_enemy_selection(self) -> None:
        features = [{"hero_id": 1, "hero_name": "Favorite", "primary_lane": "mid"}]
        with self.assertRaisesRegex(ValueError, "at least one"):
            rank_matchup_recommendations(features, [], [1], [])
        with self.assertRaisesRegex(ValueError, "cannot also"):
            rank_matchup_recommendations(features, [], [1], [1])

    def test_empty_favorite_pool_ranks_all_heroes(self) -> None:
        features = [
            {"hero_id": 1, "hero_name": "Candidate A", "primary_lane": "mid"},
            {"hero_id": 2, "hero_name": "Candidate B", "primary_lane": "jungle"},
            {"hero_id": 3, "hero_name": "Enemy", "primary_lane": "farm"},
        ]
        result = rank_matchup_recommendations(features, [], [], [3])

        self.assertEqual(len(result["recommendations"]), 2)
        self.assertEqual(result["favorites"], [])
        self.assertFalse(result["methodology"]["uses_favorite_pool"])


if __name__ == "__main__":
    unittest.main()
