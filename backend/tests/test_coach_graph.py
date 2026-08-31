import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.agent.graph import GRAPH_NODE_NAMES, build_coach_graph, initial_coach_state
from app.agent.service import CoachInput, KimiCoachService
from tests.test_coach_service import FakeClient, FakeMessage, response, settings


class CoachGraphTest(unittest.TestCase):
    def test_settings_accept_both_orchestration_modes(self) -> None:
        self.assertEqual(settings().coach_orchestration, "langgraph")
        self.assertEqual(
            settings(coach_orchestration="legacy").coach_orchestration,
            "legacy",
        )

    def test_default_orchestration_uses_the_graph(self) -> None:
        dummy = {
            "request_id": "request-default",
            "model": "kimi-k2.6",
            "answer": "Graph answer.",
            "tool_calls": [],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }
        service = KimiCoachService(
            client=FakeClient([]),
            settings=settings(),
        )
        with patch.object(service, "_ask_langgraph", return_value=dummy) as graph_ask:
            result = service.ask(
                CoachInput(message="What is next?", league_id="20260002"),
                request_id="request-default",
            )

        graph_ask.assert_called_once()
        self.assertEqual(result, dummy)

    def test_invalid_orchestration_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            settings(coach_orchestration="prebuilt-agent")

    def test_legacy_flag_does_not_compile_the_graph(self) -> None:
        service = KimiCoachService(
            client=FakeClient([response(FakeMessage(content="Choose Hero A."))]),
            settings=settings(coach_orchestration="legacy"),
        )
        with patch.object(service, "_ask_langgraph") as graph_ask:
            result = service.ask(
                CoachInput(message="What is next?", league_id="20260002")
            )

        graph_ask.assert_not_called()
        self.assertEqual(result["answer"], "Choose Hero A.")
        self.assertIsNone(service._compiled_coach_graph)

    def test_langgraph_flag_uses_the_graph_orchestrator(self) -> None:
        dummy = {
            "request_id": "request-graph",
            "model": "kimi-k2.6",
            "answer": "Graph answer.",
            "tool_calls": [],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }
        service = KimiCoachService(
            client=FakeClient([]),
            settings=settings(coach_orchestration="langgraph"),
        )
        with patch.object(service, "_ask_langgraph", return_value=dummy) as graph_ask:
            result = service.ask(
                CoachInput(message="What is next?", league_id="20260002"),
                request_id="request-graph",
            )

        graph_ask.assert_called_once()
        self.assertEqual(result, dummy)

    def test_compiled_graph_has_expected_nodes_and_no_checkpointer(self) -> None:
        service = KimiCoachService(
            client=FakeClient([]),
            settings=settings(coach_orchestration="langgraph"),
        )
        graph = service.compiled_coach_graph()

        self.assertIsNone(getattr(graph, "checkpointer", None))
        self.assertEqual(set(GRAPH_NODE_NAMES), set(graph.nodes) - {"__start__"})
        self.assertIs(service.compiled_coach_graph(), graph)

    def test_initial_state_is_serializable_and_excludes_runtime_objects(self) -> None:
        request = CoachInput(message="What is next?", league_id="20260002")
        state = initial_coach_state(
            request,
            request_id="request-state",
            normalized_message="What is next?",
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )

        self.assertEqual(state["request"]["league_id"], "20260002")
        self.assertNotIn("client", state)
        self.assertNotIn("settings", state)
        self.assertIsInstance(state["allowed_tools"], list)
        self.assertIsInstance(state["messages"], list)


class CoachGraphBuilderTest(unittest.TestCase):
    def test_builder_does_not_register_langchain_tool_nodes(self) -> None:
        service = KimiCoachService(
            client=FakeClient([]),
            settings=settings(coach_orchestration="langgraph"),
        )
        graph = build_coach_graph(service)
        node_names = set(graph.nodes)

        self.assertIn("execute_tools", node_names)
        self.assertNotIn("tools", node_names)
        self.assertNotIn("ToolNode", node_names)


if __name__ == "__main__":
    unittest.main()
