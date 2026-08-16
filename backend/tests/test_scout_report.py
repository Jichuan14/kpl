import json
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.agent.scout_report import (
    ScoutReportInput,
    ScoutReportService,
    scout_report_system_prompt,
)
from app.main import app
from tests.test_coach_service import FakeClient, FakeMessage, response, settings


class ScoutReportServiceTest(unittest.TestCase):
    def test_chinese_report_prompt_uses_chinese_display_headings(self) -> None:
        prompt = scout_report_system_prompt("zh-CN")

        self.assertIn("1. 对阵概览", prompt)
        self.assertIn("2. [BLUE_TEAM_PROFILE] 蓝方队伍档案", prompt)
        self.assertIn("3. [RED_TEAM_PROFILE] 红方队伍档案", prompt)
        self.assertIn("6. 数据说明", prompt)

    def test_fixed_plan_collects_evidence_before_one_llm_report_call(self) -> None:
        client = FakeClient([
            response(
                FakeMessage(
                    content=(
                        "1. Matchup frame\n"
                        "2. [Blue] Blue team profile\n"
                        "3. [Red] Red team profile"
                    )
                )
            )
        ])
        service = ScoutReportService(client=client, settings=settings())

        def tool_result(name, arguments):
            if name == "get_team_roster":
                return {"rows": [{"player_name": f"{arguments['team_name']} Player"}]}
            if name == "get_team_draft_tendencies":
                hero = "Hero Blue" if arguments["team_name"] == "Blue" else "Hero Red"
                return {"rows": [{"hero_name": hero}]}
            if name == "get_team_opening_sequences":
                return {"rows": [{"sequence_rate": 0.5}]}
            if name == "get_recent_team_trends":
                return {"rows": [{"hero_name": "Hero Trend"}]}
            if name == "get_player_hero_pool":
                return {"rows": [{"hero_name": "Hero Pool"}]}
            if name == "get_hero_relationships":
                return {"rows": [{"source_hero_name": arguments["source_hero_name"]}]}
            self.fail(f"unexpected tool: {name}")

        with patch("app.agent.scout_report.invoke_tool", side_effect=tool_result) as invoke:
            result = service.generate(
                ScoutReportInput(
                    league_id="20260002",
                    blue_team_id="blue-id",
                    blue_team_name="Blue",
                    red_team_id="red-id",
                    red_team_name="Red",
                ),
                request_id="scout-request",
            )

        names = [call.args[0] for call in invoke.call_args_list]
        self.assertEqual(names.count("get_team_roster"), 2)
        self.assertEqual(names.count("get_team_draft_tendencies"), 4)
        self.assertEqual(names.count("get_team_opening_sequences"), 2)
        self.assertEqual(names.count("get_recent_team_trends"), 4)
        self.assertEqual(names.count("get_player_hero_pool"), 2)
        self.assertGreaterEqual(names.count("get_hero_relationships"), 2)
        self.assertEqual(result["request_id"], "scout-request")
        self.assertEqual(result["warnings"], [])
        self.assertIn("Hero Blue", result["priority_heroes"])
        self.assertIn("Hero Red", result["priority_heroes"])
        self.assertEqual(result["priority_heroes_by_team"]["blue"], ["Hero Blue", "Hero Trend"])
        self.assertEqual(result["priority_heroes_by_team"]["red"], ["Hero Red", "Hero Trend"])
        self.assertNotIn("Pre-match scout report", result["answer"])

        provider_call = client.chat.completions.calls[0]
        self.assertNotIn("tools", provider_call)
        packet = json.loads(provider_call["messages"][1]["content"])["evidence_packet"]
        self.assertEqual(packet["report_scope"]["blue_team"]["team_name"], "Blue")
        self.assertEqual(packet["report_scope"]["red_team"]["team_name"], "Red")

    def test_missing_source_becomes_a_warning_not_an_invented_fact(self) -> None:
        client = FakeClient([
            response(
                FakeMessage(
                    content=(
                        "1. Matchup frame\n"
                        "2. [BLUE_TEAM_PROFILE] No recorded opening data is available.\n"
                        "3. [RED_TEAM_PROFILE] No recorded opening data is available."
                    )
                )
            )
        ])
        service = ScoutReportService(client=client, settings=settings())

        def tool_result(name, arguments):
            if name == "get_team_roster":
                return {"rows": []}
            if name == "get_team_opening_sequences":
                raise LookupError("not found")
            return {"rows": []}

        with patch("app.agent.scout_report.invoke_tool", side_effect=tool_result):
            result = service.generate(
                ScoutReportInput(
                    league_id="20260002",
                    blue_team_id="blue-id",
                    blue_team_name="Blue",
                    red_team_id="red-id",
                    red_team_name="Red",
                )
            )

        self.assertTrue(any("opening sequences" in warning for warning in result["warnings"]))
        self.assertTrue(any(not call["success"] for call in result["tool_calls"]))

    def test_one_sided_first_draft_gets_verified_portraits_for_both_teams(self) -> None:
        client = FakeClient([
            response(FakeMessage(content="1. Matchup frame\n2. [BLUE_TEAM_PROFILE] Blue evidence only.")),
        ])
        service = ScoutReportService(client=client, settings=settings())

        with patch("app.agent.scout_report.invoke_tool", return_value={"rows": []}):
            result = service.generate(
                ScoutReportInput(
                    league_id="20260002",
                    blue_team_id="blue-id",
                    blue_team_name="Blue",
                    red_team_id="red-id",
                    red_team_name="Red",
                )
            )

        self.assertIn("Blue team profile: Blue", result["answer"])
        self.assertIn("Red team profile: Red", result["answer"])
        self.assertEqual(len(client.chat.completions.calls), 1)
        self.assertEqual(result["usage"]["total_tokens"], 15)


class ScoutReportApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_api_uses_season_verified_team_names_and_returns_evidence(self) -> None:
        service = Mock()
        service.generate.return_value = {
            "request_id": "scout-request",
            "model": "kimi-k2.6",
            "answer": "Report.",
            "tool_calls": [
                {
                    "name": "get_team_roster",
                    "subject": "blue roster",
                    "success": True,
                    "result": {"rows": []},
                }
            ],
            "warnings": [],
            "usage": {"total_tokens": 12},
            "priority_heroes": ["Hero A"],
        }
        teams = {
            "blue": {"team_name": "Verified Blue"},
            "red": {"team_name": "Verified Red"},
        }
        with (
            patch("app.api.coach.validate_season_team_pair", return_value=teams),
            patch("app.api.coach.ScoutReportService", return_value=service),
        ):
            response_value = self.client.post(
                "/api/coach/scout-report",
                json={
                    "league_id": "20260002",
                    "blue_team_id": "blue-id",
                    "blue_team_name": "Untrusted name",
                    "red_team_id": "red-id",
                    "red_team_name": "Untrusted name",
                },
            )

        self.assertEqual(response_value.status_code, 200)
        payload = response_value.json()["data"]
        self.assertEqual(payload["priority_heroes"], ["Hero A"])
        self.assertEqual(payload["evidence"][0]["subject"], "blue roster")
        trusted_input = service.generate.call_args.args[0]
        self.assertEqual(trusted_input.blue_team_name, "Verified Blue")
        self.assertEqual(trusted_input.red_team_name, "Verified Red")


if __name__ == "__main__":
    unittest.main()
