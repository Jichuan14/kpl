import unittest

import numpy as np

from app.services.sequence_model_runtime import _context_query


class SeriesContextRuntimeTest(unittest.TestCase):
    def test_context_adds_distinct_own_and_opponent_previous_hero_vectors(self) -> None:
        branch = {
            "action_embedding.weight": np.zeros((3, 2), dtype=np.float32),
            "side_embedding.weight": np.zeros((3, 2), dtype=np.float32),
            "position_embedding.weight": np.zeros((21, 2), dtype=np.float32),
            "team_slot_embedding.weight": np.zeros((6, 2), dtype=np.float32),
            "acting_team_embedding.weight": np.zeros((2, 2), dtype=np.float32),
            "opponent_team_embedding.weight": np.zeros((2, 2), dtype=np.float32),
            "hero_bias": np.zeros(3, dtype=np.float32),
            "own_previous_hero_embedding.weight": np.asarray(
                [[2.0, 0.0], [0.0, 3.0], [1.0, 1.0]], dtype=np.float32
            ),
            "opponent_previous_hero_embedding.weight": np.asarray(
                [[0.0, 5.0], [7.0, 0.0], [1.0, 1.0]], dtype=np.float32
            ),
        }

        context = _context_query(
            branch,
            next_action=1,
            next_side=1,
            next_position=1,
            next_team_slot=1,
            acting_team=0,
            opponent_team=0,
            own_previous_hero_mask=np.asarray([True, False, True]),
            opponent_previous_hero_mask=np.asarray([False, True, False]),
        )

        np.testing.assert_allclose(
            context,
            np.asarray([3.0 / np.sqrt(2.0) + 7.0, 1.0 / np.sqrt(2.0)], dtype=np.float32),
        )


if __name__ == "__main__":
    unittest.main()
