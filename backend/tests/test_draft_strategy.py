import unittest

from app.services.draft_strategy import (
    hero_lane_masks,
    second_ban_farm_conflicts,
)


class SecondBanFarmConstraintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.feature_names = [
            "lane__clash",
            "lane__mid",
            "lane__farm",
            "damage__physical",
        ]
        self.hero_ids = [101, 102, 103, 104]
        self.lane_masks = hero_lane_masks(
            self.hero_ids,
            self.feature_names,
            [
                [0.0, 0.0, 1.0, 1.0],
                [0.0, 0.0, 1.0, 1.0],
                [0.0, 1.0, 1.0, 0.0],
                [1.0, 0.0, 0.0, 1.0],
            ],
        )

    def conflicts(
        self, *, order: int = 11, action: str = "ban", opponent=(101,)
    ) -> set[int]:
        return second_ban_farm_conflicts(
            action=action,
            bp_order=order,
            opponent_pick_ids=opponent,
            candidate_ids=[102, 103, 104],
            lane_masks=self.lane_masks,
            feature_names=self.feature_names,
        )

    def test_blocks_only_farm_only_candidates_after_opponent_farm_pick(self) -> None:
        self.assertEqual(self.conflicts(), {102})

    def test_preserves_farm_flex_candidates(self) -> None:
        self.assertNotIn(103, self.conflicts())

    def test_does_not_apply_outside_second_bans_or_without_opponent_farm(self) -> None:
        self.assertEqual(self.conflicts(order=10), set())
        self.assertEqual(self.conflicts(action="pick"), set())
        self.assertEqual(self.conflicts(opponent=(104,)), set())
        self.assertEqual(self.conflicts(opponent=(103,)), set())


if __name__ == "__main__":
    unittest.main()
