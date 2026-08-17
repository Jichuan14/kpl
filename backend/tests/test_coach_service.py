import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.service import (
    CoachInput,
    CoachLoopLimitError,
    KimiCoachService,
    KimiConfigurationError,
    build_kimi_client,
)
from app.config import Settings


class FakeMessage:
    def __init__(self, *, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none=True):
        value = {"role": "assistant"}
        if self.content is not None:
            value["content"] = self.content
        if self.tool_calls is not None:
            value["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in self.tool_calls
            ]
        return value


def tool_call(name: str, arguments: str, call_id: str = "call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def response(message: FakeMessage, input_tokens=10, output_tokens=5):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )


class FakeCompletions:
    def __init__(self, responses, scope_responses=None):
        self.responses = list(responses)
        self.scope_responses = list(scope_responses or [])
        self.calls = []
        self.scope_calls = []

    def create(self, **kwargs):
        if "KPL Draft Coach scope gate" in kwargs["messages"][0]["content"]:
            self.scope_calls.append(deepcopy(kwargs))
            if self.scope_responses:
                return self.scope_responses.pop(0)
            return response(
                FakeMessage(
                    content=(
                        '{"decision":"allow","intent":"draft_prediction",'
                        '"query_scope":"current_draft",'
                        '"reason_code":"supported_kpl_question"}'
                    )
                ),
                input_tokens=0,
                output_tokens=0,
            )
        self.calls.append(deepcopy(kwargs))
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses, scope_responses=None):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(responses, scope_responses)
        )


def settings(**overrides) -> Settings:
    values = {
        "moonshot_api_key": "test-secret",
        "kimi_model": "kimi-k2.6",
        "kimi_max_tool_rounds": 3,
        "kimi_max_tool_calls": 8,
        "kimi_max_output_tokens": 600,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class KimiCoachServiceTest(unittest.TestCase):
    def test_missing_key_is_rejected_before_importing_provider(self) -> None:
        configuration = settings(moonshot_api_key=None)

        with self.assertRaisesRegex(KimiConfigurationError, "MOONSHOT_API_KEY"):
            build_kimi_client(configuration)

    def test_settings_mask_the_key(self) -> None:
        configuration = settings(moonshot_api_key="do-not-print-this")

        self.assertNotIn("do-not-print-this", repr(configuration))

    def test_returns_direct_answer_without_tools(self) -> None:
        client = FakeClient([response(FakeMessage(content="Choose Hero A."))])
        service = KimiCoachService(client=client, settings=settings())

        result = service.ask(
            CoachInput(message="What is next?", league_id="20260002"),
            request_id="request-1",
        )

        self.assertEqual(result["answer"], "Choose Hero A.")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["usage"]["total_tokens"], 15)
        provider_call = client.chat.completions.calls[0]
        self.assertEqual(provider_call["model"], "kimi-k2.6")
        self.assertNotIn("api_key", provider_call)
        self.assertEqual(
            provider_call["extra_body"],
            {"thinking": {"type": "disabled"}},
        )
        self.assertIn(
            "Do not use Markdown tables",
            provider_call["messages"][0]["content"],
        )
        self.assertIn(
            "confidence-weighted acting- team tendencies",
            " ".join(provider_call["messages"][0]["content"].lower().split()),
        )
        self.assertIn(
            "answer only questions directly related to honor of kings",
            " ".join(provider_call["messages"][0]["content"].lower().split()),
        )
        self.assertIn(
            "never state a factual game or kpl claim solely from model memory",
            " ".join(provider_call["messages"][0]["content"].lower().split()),
        )
        self.assertIn(
            "call only get_hero_relationships with relation=pick_synergy",
            " ".join(provider_call["messages"][0]["content"].lower().split()),
        )
        user_payload = json.loads(provider_call["messages"][1]["content"])
        self.assertEqual(
            user_payload["response_style"],
            {
                "language": "match the question",
                "format": "concise plain language",
                "normal_answer_max_sentences": 3,
                "markdown_tables": False,
            },
        )
        self.assertEqual(user_payload["analysis_scope"], "current_draft")

    def test_rewrites_provider_planning_text_before_returning_it(self) -> None:
        client = FakeClient(
            [
                response(
                    FakeMessage(
                        content="让我先查看可用工具。根据工具列表，我无法回答。"
                    )
                ),
                response(FakeMessage(content="抱歉，我目前无法查询这项数据。")),
            ]
        )
        service = KimiCoachService(client=client, settings=settings())

        result = service.ask(
            CoachInput(message="谁在狼队？", league_id="20260002"),
            request_id="request-rewrite",
        )

        self.assertEqual(result["answer"], "抱歉，我目前无法查询这项数据。")
        self.assertEqual(result["usage"]["total_tokens"], 30)
        self.assertEqual(len(client.chat.completions.calls), 2)
        rewrite_call = client.chat.completions.calls[1]
        self.assertNotIn("tools", rewrite_call)
        self.assertIn("Never mention reasoning", rewrite_call["messages"][0]["content"])

    def test_executes_tool_and_returns_followup_answer(self) -> None:
        call = tool_call(
            "predict_next_draft_action",
            json.dumps({"league_id": "20260002", "bp_order": 1}),
        )
        client = FakeClient(
            [
                response(FakeMessage(tool_calls=[call])),
                response(FakeMessage(content="Hero A is most likely.")),
            ]
        )
        service = KimiCoachService(client=client, settings=settings())
        tool_result = {
            "candidate_count": 1,
            "next_action_probabilities": [
                {"hero_id": 101, "hero_name": "Hero A", "probability": 0.7}
            ],
        }

        with patch("app.agent.service.invoke_tool", return_value=tool_result) as invoke:
            result = service.ask(
                CoachInput(
                    message="What is next?",
                    league_id="20260002",
                    draft_state={
                        "bp_order": 1,
                        "blue_team_id": "blue-1",
                        "blue_team_name": "Blue Club",
                        "red_team_id": "red-1",
                        "red_team_name": "Red Club",
                    },
                ),
                request_id="request-2",
            )

        self.assertEqual(result["answer"], "Hero A is most likely.")
        self.assertTrue(result["tool_calls"][0]["success"])
        self.assertEqual(result["usage"]["total_tokens"], 30)
        invoke.assert_called_once()
        second_messages = client.chat.completions.calls[1]["messages"]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertIn("Hero A", second_messages[-1]["content"])

    def test_league_wide_scope_hides_board_and_draft_tools(self) -> None:
        client = FakeClient(
            [response(FakeMessage(content="Lu Bu historically counters Hero A."))],
            scope_responses=[
                response(
                    FakeMessage(
                        content=(
                            '{"decision":"allow","intent":"hero_relationships",'
                            '"query_scope":"league_wide",'
                            '"reason_code":"general_counter_pick"}'
                        )
                    )
                )
            ],
        )
        service = KimiCoachService(client=client, settings=settings())

        service.ask(
            CoachInput(
                message="吕布通常反制哪些英雄？",
                league_id="20260002",
                draft_state={
                    "bp_order": 1,
                    "blue_team_id": "blue-1",
                    "blue_team_name": "Blue Club",
                    "red_team_id": "red-1",
                    "red_team_name": "Red Club",
                },
            )
        )

        provider_call = client.chat.completions.calls[0]
        payload = json.loads(provider_call["messages"][-1]["content"])
        self.assertEqual(payload["analysis_scope"], "league_wide")
        self.assertIsNone(payload["draft_state"])
        names = [tool["function"]["name"] for tool in provider_call["tools"]]
        self.assertIn("get_hero_relationships", names)
        self.assertNotIn("predict_next_draft_action", names)
        self.assertNotIn("get_team_draft_tendencies", names)

    def test_relays_only_server_filtered_history_as_untrusted_context(self) -> None:
        client = FakeClient([response(FakeMessage(content="It refers to Wolves."))])
        service = KimiCoachService(client=client, settings=settings())

        service.ask(
            CoachInput(
                message="What about from Blue?",
                league_id="20260003",
                history=[
                    {
                        "user": "What does Wolves pick most?",
                        "assistant": "Wolves most often picks Hero A.",
                    }
                ],
            )
        )

        provider_messages = client.chat.completions.calls[0]["messages"]
        self.assertEqual(
            [message["role"] for message in provider_messages],
            ["system", "user", "user"],
        )
        self.assertIn("untrusted_conversation_context", provider_messages[1]["content"])
        self.assertIn("What does Wolves pick most?", provider_messages[1]["content"])
        self.assertIn("What about from Blue?", provider_messages[2]["content"])

    def test_website_context_overrides_model_draft_arguments(self) -> None:
        call = tool_call(
            "predict_next_draft_action",
            json.dumps(
                {
                    "league_id": "wrong-league",
                    "model_type": "stats",
                    "bp_order": 19,
                    "blue_picks": [999],
                    "limit": 3,
                }
            ),
        )
        client = FakeClient(
            [
                response(FakeMessage(tool_calls=[call])),
                response(FakeMessage(content="Hero A is most likely.")),
            ]
        )
        service = KimiCoachService(client=client, settings=settings())

        with patch(
            "app.agent.service.invoke_tool",
            return_value={"candidate_count": 0, "next_action_probabilities": []},
        ) as invoke:
            service.ask(
                CoachInput(
                    message="What is next?",
                    league_id="20260003",
                    draft_state={
                        "model_type": "learnable",
                        "blue_team_id": "blue-1",
                        "blue_team_name": "Blue Club",
                        "red_team_id": "red-1",
                        "red_team_name": "Red Club",
                        "bp_order": 4,
                        "blue_picks": [101],
                        "red_bans": [202],
                    },
                ),
                request_id="request-authoritative-context",
            )

        effective_arguments = invoke.call_args.args[1]
        self.assertEqual(effective_arguments["league_id"], "20260003")
        self.assertEqual(effective_arguments["model_type"], "learnable")
        self.assertEqual(effective_arguments["blue_team_id"], "blue-1")
        self.assertEqual(effective_arguments["red_team_id"], "red-1")
        self.assertEqual(effective_arguments["bp_order"], 4)
        self.assertEqual(effective_arguments["blue_picks"], [101])
        self.assertEqual(effective_arguments["red_bans"], [202])
        self.assertEqual(effective_arguments["limit"], 3)
        self.assertNotIn(999, effective_arguments["blue_picks"])

    def test_malformed_tool_arguments_are_returned_as_safe_error(self) -> None:
        call = tool_call("predict_next_draft_action", "not-json")
        client = FakeClient(
            [
                response(FakeMessage(tool_calls=[call])),
                response(FakeMessage(content="I need the current board.")),
            ]
        )
        service = KimiCoachService(client=client, settings=settings())

        result = service.ask(
            CoachInput(message="What is next?", league_id="20260002")
        )

        self.assertFalse(result["tool_calls"][0]["success"])
        self.assertNotIn("not-json", result["tool_calls"][0]["error"])

    def test_tool_round_limit_stops_repeated_calls(self) -> None:
        repeated = tool_call("get_meta_heroes", "{}")
        client = FakeClient(
            [
                response(FakeMessage(tool_calls=[repeated])),
                response(FakeMessage(tool_calls=[repeated])),
            ]
        )
        service = KimiCoachService(
            client=client,
            settings=settings(kimi_max_tool_rounds=1),
        )

        with patch("app.agent.service.invoke_tool", return_value={"rows": []}):
            with self.assertRaisesRegex(CoachLoopLimitError, "tool-round limit"):
                service.ask(
                    CoachInput(message="Keep searching", league_id="20260002")
                )


if __name__ == "__main__":
    unittest.main()
