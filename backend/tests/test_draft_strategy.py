import unittest

from app.services.draft_strategy import (
    hero_lane_profiles,
    second_ban_lane_conflicts,
)


class SecondBanLaneConstraintTest(unittest.TestCase):
    def setUp(self) -> None:
        artifact = {
            "schema_version": 1,
            "artifact_type": "hero_lane_profiles",
            "lanes": ["clash", "mid", "jungle", "farm", "roam"],
            "rows": [
                {"hero_id": 101, "lanes": ["farm"], "constraint_eligible": True},
                {"hero_id": 102, "lanes": ["farm"], "constraint_eligible": True},
                {"hero_id": 103, "lanes": ["farm", "mid"], "constraint_eligible": False},
                {"hero_id": 104, "lanes": ["mid"], "constraint_eligible": True},
                {"hero_id": 105, "lanes": ["mid"], "constraint_eligible": True},
                {"hero_id": 106, "lanes": ["roam"], "constraint_eligible": True},
                {"hero_id": 107, "lanes": ["roam"], "constraint_eligible": False},
            ],
        }
        self.lane_masks, self.eligible = hero_lane_profiles(artifact)

    def conflicts(
        self,
        *,
        order: int = 11,
        action: str = "ban",
        opponent=(101, 104, 106),
    ) -> set[int]:
        return second_ban_lane_conflicts(
            action=action,
            bp_order=order,
            opponent_pick_ids=opponent,
            candidate_ids=[102, 103, 105, 107],
            lane_masks=self.lane_masks,
            constraint_eligible_ids=self.eligible,
        )

    def test_blocks_confident_single_lane_candidates_in_every_locked_lane(self) -> None:
        self.assertEqual(self.conflicts(), {102, 105})

    def test_preserves_flexible_and_uncertain_candidates(self) -> None:
        self.assertNotIn(103, self.conflicts())
        self.assertNotIn(107, self.conflicts())

    def test_uncertain_opponent_pick_does_not_lock_a_lane(self) -> None:
        self.assertEqual(self.conflicts(opponent=(107,)), set())

    def test_does_not_apply_outside_second_bans(self) -> None:
        self.assertEqual(self.conflicts(order=10), set())
        self.assertEqual(self.conflicts(action="pick"), set())

    def test_rejects_eligible_flexible_profile(self) -> None:
        artifact = {
            "schema_version": 1,
            "artifact_type": "hero_lane_profiles",
            "lanes": ["clash", "mid", "jungle", "farm", "roam"],
            "rows": [{"hero_id": 1, "lanes": ["mid", "roam"], "constraint_eligible": True}],
        }
        with self.assertRaisesRegex(ValueError, "not single-lane"):
            hero_lane_profiles(artifact)


if __name__ == "__main__":
    unittest.main()
