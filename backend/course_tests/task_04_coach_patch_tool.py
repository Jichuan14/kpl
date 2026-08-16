"""Opt-in Task 4 tests for the Coach-facing patch retrieval registration."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agent.service import CoachInput, KimiCoachService
from app.agent.tool_registry import TOOLS, invoke_tool
from app.agent.tools.patches import SearchPatchNotesArguments
from tests.test_coach_service import FakeClient, FakeMessage, response, settings, tool_call


PATCH_RESULT = {
    "source_type": "tencent_patch_notes",
    "index_version": "index-v1",
    "result_count": 1,
    "results": [
        {
            "announcement_id": "807018",
            "title": "英雄平衡性调整 | 刘备平衡",
            "published_at": "2026-08-12",
            "hero_names": ["刘备"],
            "heading_path": ["英雄调整", "刘备"],
            "excerpt": "被动：新增效果：弹丸可穿透非英雄单位。",
            "source_url": "https://example.test/807018",
            "source_hash": "a" * 64,
        }
    ],
    "warnings": [],
}


class CoachPatchToolTest(unittest.TestCase):
    def test_registry_exposes_a_read_only_patch_tool(self) -> None:
        tool = TOOLS["search_patch_notes"]

        self.assertIs(tool.arguments_model, SearchPatchNotesArguments)
        self.assertIn("official Tencent", tool.description)
        self.assertNotIn("league_id", tool.model_definition()["function"]["parameters"]["properties"])

    def test_registry_dispatches_the_retriever_response(self) -> None:
        with patch(
            "app.knowledge.patch_retrieval.PatchRetriever.search",
            return_value=type("Response", (), {"model_dump": lambda self, **_: PATCH_RESULT})(),
        ) as search:
            result = invoke_tool(
                "search_patch_notes",
                {"query": "刘备最近有什么改动？", "hero_name": "刘备"},
                request_id="patch-tool-request",
            )

        self.assertEqual(result, PATCH_RESULT)
        search.assert_called_once()

    def test_coach_does_not_add_league_id_to_patch_tool_arguments(self) -> None:
        call = tool_call(
            "search_patch_notes",
            '{"query":"刘备最近有什么改动？","hero_name":"刘备"}',
        )
        client = FakeClient(
            [
                response(FakeMessage(tool_calls=[call])),
                response(FakeMessage(content="刘备在官方公告中有改动。")),
            ],
            scope_responses=[
                response(
                    FakeMessage(
                        content=(
                            '{"decision":"allow","intent":"patch_notes",'
                            '"reason_code":"official_patch_question"}'
                        )
                    )
                )
            ],
        )
        service = KimiCoachService(client=client, settings=settings())

        with patch("app.agent.service.invoke_tool", return_value=PATCH_RESULT) as invoke:
            result = service.ask(
                CoachInput(message="刘备最近有什么改动？", league_id="20260002"),
                request_id="coach-patch-request",
            )

        self.assertTrue(result["tool_calls"][0]["success"])
        self.assertEqual(invoke.call_args.args[0], "search_patch_notes")
        self.assertEqual(
            invoke.call_args.args[1],
            {"query": "刘备最近有什么改动？", "hero_name": "刘备"},
        )


if __name__ == "__main__":
    unittest.main()
