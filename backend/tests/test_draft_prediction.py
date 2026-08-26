import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import draft_simulator


def model_fixture() -> dict:
    return {
        "generated_at": "2026-08-02T00:00:00Z",
        "draft_sequence": [
            {
                "bp_order": 1,
                "side": "blue",
                "action": "ban",
                "team_action_type_number": 1,
            },
            {
                "bp_order": 2,
                "side": "red",
                "action": "pick",
                "team_action_type_number": 1,
            },
        ],
        "hero_names": {"101": "A", "102": "B", "103": "C"},
        "_hero_role_masks": {101: 1, 102: 2, 103: 1},
    }


class PredictNextActionTest(unittest.TestCase):
    def test_ban_candidates_exclude_roles_already_filled_by_opponent(self) -> None:
        model = {
            "hero_ids": [101, 102, 103, 104],
            # 101 and 103 are mid; 102 is farm; 104 can play either lane.
            "_hero_role_masks": {101: 1, 102: 2, 103: 1, 104: 3},
        }
        state = {
            "blue_picks": [],
            "red_picks": [101],
            "blue_bans": [],
            "red_bans": [],
        }
        blue_ban = {"side": "blue", "action": "ban"}

        self.assertEqual(
            draft_simulator._legal_heroes(model, state, blue_ban),
            [102, 104],
        )

    def test_sequence_loader_rejects_a_bad_parameter_checksum(self) -> None:
        artifact = {
            "schema_version": 3,
            "model_type": "frozen_bag_gru_residual_choice",
            "target_season": "league-1",
            "parameters_sha256": "not-the-real-checksum",
            "parameters": {},
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "sequence.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")
            draft_simulator._SEQUENCE_CACHE.clear()
            with patch.object(
                draft_simulator, "sequence_model_path", return_value=path
            ):
                with self.assertRaisesRegex(ValueError, "checksum"):
                    draft_simulator.load_sequence_model("league-1")

    def test_sequence_history_reconstructs_the_canonical_prefix(self) -> None:
        base_model = {
            "draft_sequence": [
                {"bp_order": 1, "side": "blue", "action": "ban"},
                {"bp_order": 2, "side": "red", "action": "ban"},
                {"bp_order": 3, "side": "blue", "action": "pick"},
                {"bp_order": 4, "side": "red", "action": "pick"},
            ]
        }
        sequence_model = {"_hero_to_index": {101: 0, 102: 1, 103: 2}}
        state = {
            "blue_bans": [101],
            "red_bans": [102],
            "blue_picks": [103],
            "red_picks": [],
        }

        heroes, actions, sides, relations, positions = (
            draft_simulator._sequence_history(
                base_model,
                sequence_model,
                state,
                base_model["draft_sequence"][3],
            )
        )

        self.assertEqual(heroes.tolist(), [0, 1, 2])
        self.assertEqual(actions.tolist(), [2, 2, 1])
        self.assertEqual(sides.tolist(), [1, 2, 1])
        self.assertEqual(relations.tolist(), [4, 3, 2])
        self.assertEqual(positions.tolist(), [1, 2, 3])

    def test_sequence_history_rejects_missing_or_future_selections(self) -> None:
        base_model = {
            "draft_sequence": [
                {"bp_order": 1, "side": "blue", "action": "ban"},
                {"bp_order": 2, "side": "red", "action": "ban"},
            ]
        }
        sequence_model = {"_hero_to_index": {101: 0, 102: 1}}
        with self.assertRaisesRegex(ValueError, "missing the hero"):
            draft_simulator._sequence_history(
                base_model,
                sequence_model,
                {"blue_bans": [], "red_bans": []},
                base_model["draft_sequence"][1],
            )
        with self.assertRaisesRegex(ValueError, "after its bp_order"):
            draft_simulator._sequence_history(
                base_model,
                sequence_model,
                {"blue_bans": [101, 102], "red_bans": []},
                base_model["draft_sequence"][1],
            )

    def test_learnable_model_uses_acting_team_embedding(self) -> None:
        base_model = {
            "hero_ids": [101, 102],
            "hero_names": {"101": "A", "102": "B"},
            "_hero_role_masks": {101: 1, 102: 1},
        }
        learnable_model = {
            "_parameters": {
                "hero_residual": [[0.0], [0.0]],
                "context_embedding": [[0.0]],
                "state_projection": [[0.0] for _ in range(12)],
                "source_embedding": [
                    [[0.0], [0.0]],
                    [[0.0], [0.0]],
                    [[0.0], [0.0]],
                    [[0.0], [0.0]],
                ],
                "acting_team_embedding": [[1.0], [-1.0]],
                "opponent_team_embedding": [[0.0], [0.0]],
                "hero_bias": [0.0, 0.0],
            },
            "_feature_matrix": [[0.0], [0.0]],
            "_hero_to_index": {101: 0, 102: 1},
            "_team_to_index": {"wolves": 0, "ag": 1},
            "_context_to_index": {("ban", "blue", 1): 0},
            "_candidate_representations": [[1.0], [-1.0]],
            "team_training_decisions": {"wolves": 20, "ag": 15},
        }
        step = {
            "action": "ban",
            "side": "blue",
            "team_action_type_number": 1,
        }
        state = {
            "blue_team_id": "wolves",
            "red_team_id": "ag",
            "blue_picks": [],
            "red_picks": [],
            "blue_bans": [],
            "red_bans": [],
        }

        wolves_rows = draft_simulator._predict_learnable(
            base_model, learnable_model, state, step
        )
        ag_rows = draft_simulator._predict_learnable(
            base_model,
            learnable_model,
            {**state, "blue_team_id": "ag", "red_team_id": "wolves"},
            step,
        )

        self.assertEqual(wolves_rows[0]["hero_id"], 101)
        self.assertEqual(ag_rows[0]["hero_id"], 102)
        self.assertEqual(wolves_rows[0]["team_context_level"], "learned_embeddings")
        self.assertEqual(wolves_rows[0]["team_context_decisions"], 20)

    def test_learnable_model_does_not_apply_legacy_team_tendency(self) -> None:
        with (
            patch.object(
                draft_simulator,
                "_predict_learnable",
                return_value=[{"hero_id": 101, "probability": 1.0}],
            ),
            patch.object(
                draft_simulator,
                "_apply_team_tendency",
                side_effect=AssertionError("legacy adjustment must not run"),
            ),
        ):
            rows = draft_simulator._predict(
                model_fixture(), {}, model_fixture()["draft_sequence"][0], {}
            )

        self.assertEqual(rows, [{"hero_id": 101, "probability": 1.0}])

    def test_team_tendency_blend_reweights_and_reports_context(self) -> None:
        base = [
            {"hero_id": 101, "hero_name": "A", "probability": 0.5},
            {"hero_id": 102, "hero_name": "B", "probability": 0.5},
        ]
        tendency_index = {
            ("wolves", "slot", "blue", "ban", 1, ""): {
                101: {
                    "hero_id": 101,
                    "smoothed_lift": 2.5,
                    "context_decision_count": 20,
                }
            }
        }
        state = {
            "blue_team_id": "wolves",
            "blue_team_name": "Wolves",
            "red_team_id": "ag",
            "red_team_name": "AG",
        }
        step = model_fixture()["draft_sequence"][0]

        with patch.object(
            draft_simulator,
            "_load_team_tendency_index",
            return_value=(tendency_index, "version-1"),
        ):
            rows = draft_simulator._apply_team_tendency(
                {"_league_id": "season-1"}, state, step, base
            )

        self.assertGreater(rows[0]["probability"], 0.5)
        self.assertEqual(rows[0]["team_context_level"], "slot")
        self.assertAlmostEqual(sum(row["probability"] for row in rows), 1.0)

    def test_returns_limited_distribution_without_rollouts(self) -> None:
        probabilities = [
            {"hero_id": 101, "hero_name": "A", "probability": 0.5},
            {"hero_id": 102, "hero_name": "B", "probability": 0.3},
            {"hero_id": 103, "hero_name": "C", "probability": 0.2},
        ]
        state = {
            "bp_order": 1,
            "blue_picks": [],
            "red_picks": [],
            "blue_bans": [],
            "red_bans": [],
        }

        with (
            patch.object(draft_simulator, "load_model", return_value=model_fixture()),
            patch.object(draft_simulator, "_predict", return_value=probabilities) as predict,
            patch.object(
                draft_simulator.random,
                "Random",
                side_effect=AssertionError("rollouts must not run"),
            ),
        ):
            result = draft_simulator.predict_next_action(
                "league-1",
                state,
                limit=2,
            )

        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(result["next_action_probabilities"], probabilities[:2])
        self.assertEqual(result["next_step"]["bp_order"], 1)
        self.assertEqual(result["model_type"], "stats")
        predict.assert_called_once()

    def test_rejects_global_bp_overlap_before_prediction(self) -> None:
        state = {
            "bp_order": 2,
            "blue_picks": [],
            "red_picks": [101],
            "blue_bans": [],
            "red_bans": [],
            "red_used_previous_battles": [101],
        }

        with (
            patch.object(draft_simulator, "load_model", return_value=model_fixture()),
            patch.object(draft_simulator, "_predict") as predict,
        ):
            with self.assertRaisesRegex(ValueError, "used in an earlier battle"):
                draft_simulator.predict_next_action("league-1", state)

        predict.assert_not_called()

    def test_requires_a_model_sequence_bp_order(self) -> None:
        state = {
            "bp_order": 99,
            "blue_picks": [],
            "red_picks": [],
            "blue_bans": [],
            "red_bans": [],
        }

        with patch.object(
            draft_simulator,
            "load_model",
            return_value=model_fixture(),
        ):
            with self.assertRaisesRegex(ValueError, "not in the model sequence"):
                draft_simulator.predict_next_action("league-1", state)

    def test_rejects_non_positive_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "limit must be at least 1"):
            draft_simulator.predict_next_action(
                "league-1",
                {"bp_order": 1},
                limit=0,
            )

    def test_simulation_stops_at_requested_action_horizon(self) -> None:
        probabilities = [
            {"hero_id": 101, "hero_name": "A", "probability": 0.6},
            {"hero_id": 102, "hero_name": "B", "probability": 0.4},
        ]
        state = {
            "bp_order": 1,
            "blue_picks": [],
            "red_picks": [],
            "blue_bans": [],
            "red_bans": [],
        }

        with (
            patch.object(draft_simulator, "load_model", return_value=model_fixture()),
            patch.object(
                draft_simulator,
                "_predict",
                return_value=probabilities,
            ) as predict,
        ):
            result = draft_simulator.simulate(
                "league-1",
                state,
                rollouts=5,
                seed=1,
                max_actions=1,
            )

        self.assertEqual(result["simulation"]["actions_simulated"], 1)
        self.assertEqual(set(result["simulation"]["next_actions"]), {"1"})
        predict.assert_called_once()

    def test_simulation_rejects_non_positive_action_horizon(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_actions must be at least 1"):
            draft_simulator.simulate(
                "league-1",
                {"bp_order": 1},
                rollouts=5,
                seed=1,
                max_actions=0,
            )

    def test_forced_completion_keeps_candidate_and_samples_remaining_actions(self) -> None:
        model = model_fixture()
        sequence = model["draft_sequence"]
        state = {
            "bp_order": 1,
            "blue_picks": [],
            "red_picks": [],
            "blue_bans": [],
            "red_bans": [],
        }
        prepared = (
            model,
            None,
            None,
            sequence,
            0,
            sequence[0],
            [
                {"hero_id": 101, "hero_name": "A", "probability": 0.7},
                {"hero_id": 102, "hero_name": "B", "probability": 0.3},
            ],
        )
        with (
            patch.object(draft_simulator, "_prepare_prediction", return_value=prepared),
            patch.object(
                draft_simulator,
                "_predict",
                return_value=[
                    {"hero_id": 102, "hero_name": "B", "probability": 1.0}
                ],
            ),
        ):
            result = draft_simulator.sample_forced_draft_completions(
                "league-1",
                state,
                forced_first_hero_id=101,
                rollouts=2,
                seed=1,
            )

        self.assertEqual(result["forced_policy_probability"], 0.7)
        self.assertEqual(len(result["completions"]), 2)
        for completion in result["completions"]:
            self.assertTrue(completion["completed"])
            self.assertEqual(completion["state"]["blue_bans"], [101])
            self.assertEqual(completion["state"]["red_picks"], [102])


if __name__ == "__main__":
    unittest.main()
