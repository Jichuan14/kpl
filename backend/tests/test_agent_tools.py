import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from pydantic import ValidationError

from app.agent import available_tool_definitions, invoke_tool
from app.agent.tool_registry import TOOLS, UnknownAgentToolError
from app.agent.tools.draft import (
    FIXED_ROLLOUTS,
    SimulateFutureDraftArguments,
    simulate_future_draft,
)


class AgentToolRegistryTest(unittest.TestCase):
    def arguments(self) -> dict:
        return {
            "league_id": "20260002",
            "model_type": "stats",
            "blue_team_id": "blue-1",
            "blue_team_name": "Blue Club",
            "red_team_id": "red-1",
            "red_team_name": "Red Club",
            "bp_order": 3,
            "blue_picks": [101],
            "red_picks": [],
            "blue_bans": [102],
            "red_bans": [103],
            "blue_used_previous_battles": [104],
            "red_used_previous_battles": [],
            "limit": 3,
        }

    def test_definitions_expose_only_registered_tools(self) -> None:
        definitions = available_tool_definitions()

        self.assertEqual(len(definitions), 14)
        functions = {
            definition["function"]["name"]: definition["function"]
            for definition in definitions
        }
        self.assertEqual(
            set(functions),
            {
                "get_team_roster",
                "predict_next_draft_action",
                "simulate_future_draft",
                "get_hero_relationships",
                "get_team_synergies",
                "get_meta_heroes",
                "get_hero_bp_stats",
                "get_battle_draft",
                "get_team_draft_tendencies",
                "get_team_opening_sequences",
                "get_team_combo_performance",
                "get_player_hero_pool",
                "get_recent_team_trends",
                "search_patch_notes",
            },
        )
        function = functions["predict_next_draft_action"]
        self.assertEqual(function["name"], "predict_next_draft_action")
        self.assertIn("bp_order", function["parameters"]["properties"])
        self.assertIn("league_id", function["parameters"]["required"])
        json.dumps(definitions)
        team_schema = functions["get_team_synergies"]["parameters"]
        self.assertIn("team_name", team_schema["required"])
        self.assertNotIn("anyOf", team_schema)
        player_schema = functions["get_player_hero_pool"]["parameters"]
        self.assertIn("player_name", player_schema["required"])
        self.assertNotIn("team_name", player_schema["required"])
        simulation_schema = functions["simulate_future_draft"]["parameters"]
        self.assertNotIn("rollouts", simulation_schema["properties"])
        patch_schema = functions["search_patch_notes"]["parameters"]
        self.assertIn("query", patch_schema["required"])
        self.assertNotIn("league_id", patch_schema["properties"])

    def test_dispatch_validates_and_calls_fast_prediction(self) -> None:
        expected = {
            "candidate_count": 2,
            "next_action_probabilities": [
                {"hero_id": 105, "probability": 0.6},
                {"hero_id": 106, "probability": 0.4},
            ],
        }

        with patch(
            "app.agent.tools.draft.predict_next_action",
            return_value=expected,
        ) as prediction:
            with self.assertLogs("app.agent.tool_registry", level="INFO") as logs:
                result = invoke_tool(
                    "predict_next_draft_action",
                    self.arguments(),
                    request_id="request-1",
                )

        self.assertEqual(result, expected)
        prediction.assert_called_once_with(
            "20260002",
            {
                "bp_order": 3,
                "blue_team_id": "blue-1",
                "blue_team_name": "Blue Club",
                "red_team_id": "red-1",
                "red_team_name": "Red Club",
                "blue_picks": [101],
                "red_picks": [],
                "blue_bans": [102],
                "red_bans": [103],
                "blue_used_previous_battles": [104],
                "red_used_previous_battles": [],
                "legal_hero_ids": None,
            },
            model_type="stats",
            limit=3,
        )
        self.assertIn("agent_tool_completed", logs.output[0])

    def test_future_draft_tool_returns_bounded_action_metadata(self) -> None:
        simulation_result = {
            "model_generated_at": "2026-08-02T00:00:00Z",
            "model_type": "stats",
            "model_label": "Statistical model",
            "next_step": {
                "bp_order": 3,
                "side": "blue",
                "action": "pick",
                "team_action_type_number": 1,
            },
            "next_action_probabilities": [
                {"hero_id": 105, "hero_name": "Hero E", "probability": 0.6}
            ],
            "simulation": {
                "rollouts": FIXED_ROLLOUTS,
                "actions_simulated": 2,
                "next_actions": {
                    "3": [
                        {
                            "hero_id": 105,
                            "hero_name": "Hero E",
                            "probability": 0.61,
                        }
                    ],
                    "4": [
                        {
                            "hero_id": 106,
                            "hero_name": "Hero F",
                            "probability": 0.42,
                        }
                    ],
                },
                "banned_by_end": [],
            },
        }
        model = {
            "draft_sequence": [
                {
                    "bp_order": 3,
                    "side": "blue",
                    "action": "pick",
                    "team_action_type_number": 1,
                },
                {
                    "bp_order": 4,
                    "side": "red",
                    "action": "ban",
                    "team_action_type_number": 2,
                },
            ]
        }
        raw_arguments = self.arguments()
        raw_arguments.pop("limit")
        arguments = SimulateFutureDraftArguments(
            **raw_arguments,
            horizon=2,
        )

        with (
            patch(
                "app.agent.tools.draft.simulate",
                return_value=simulation_result,
            ) as simulation,
            patch("app.agent.tools.draft.load_model", return_value=model),
        ):
            result = simulate_future_draft(arguments)

        self.assertEqual(result["result_count"], 2)
        self.assertEqual(result["future_actions"][1]["action"], "ban")
        self.assertIn("not one guaranteed sequence", result["warning"])
        simulation.assert_called_once_with(
            "20260002",
            arguments.draft_state(),
            FIXED_ROLLOUTS,
            None,
            model_type="stats",
            max_actions=2,
        )

    def test_future_draft_tool_rejects_a_model_supplied_rollout_count(self) -> None:
        arguments = self.arguments()
        arguments.pop("limit")
        arguments["rollouts"] = 1000

        with self.assertRaises(ValidationError):
            SimulateFutureDraftArguments(**arguments)

    def test_dispatch_rejects_invalid_arguments(self) -> None:
        arguments = self.arguments()
        arguments["bp_order"] = 0

        with self.assertLogs("app.agent.tool_registry", level="WARNING") as logs:
            with self.assertRaises(ValidationError):
                invoke_tool("predict_next_draft_action", arguments)

        self.assertIn("agent_tool_invalid_arguments", logs.output[0])

    def test_draft_tool_accepts_sequence_model(self) -> None:
        arguments = self.arguments()
        arguments["model_type"] = "sequence"
        with patch(
            "app.agent.tools.draft.predict_next_action",
            return_value={"model_type": "sequence"},
        ) as prediction:
            result = invoke_tool("predict_next_draft_action", arguments)

        self.assertEqual(result["model_type"], "sequence")
        self.assertEqual(prediction.call_args.kwargs["model_type"], "sequence")

    def test_dispatch_rejects_unknown_tools(self) -> None:
        with self.assertLogs("app.agent.tool_registry", level="WARNING") as logs:
            with self.assertRaisesRegex(UnknownAgentToolError, "Unknown agent tool"):
                invoke_tool("run_sql", {})

        self.assertIn("agent_tool_rejected", logs.output[0])

    def test_dispatch_logs_no_data_without_a_failure_trace(self) -> None:
        tool = TOOLS["get_team_synergies"]

        def missing_team(_arguments):
            raise LookupError("Unknown team")

        with patch.dict(
            TOOLS,
            {"get_team_synergies": replace(tool, handler=missing_team)},
        ):
            with self.assertLogs("app.agent.tool_registry", level="WARNING") as logs:
                with self.assertRaisesRegex(LookupError, "Unknown team"):
                    invoke_tool(
                        "get_team_synergies",
                        {
                            "league_id": "20260002",
                            "team_name": "Missing Team",
                        },
                    )

        self.assertIn("agent_tool_no_data", logs.output[0])


if __name__ == "__main__":
    unittest.main()
