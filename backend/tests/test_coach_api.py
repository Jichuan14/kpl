import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.agent.service import CoachLoopLimitError, KimiConfigurationError
from app.main import app


class CoachApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_returns_answer_evidence_warnings_usage_and_request_id(self) -> None:
        service = Mock()
        service.ask.return_value = {
            "request_id": "request-from-service",
            "model": "kimi-k2.6",
            "answer": "Hero A has the highest opening priority.",
            "tool_calls": [
                {
                    "name": "get_meta_heroes",
                    "success": True,
                    "result": {"rows": [{"hero_name": "Hero A"}]},
                },
                {
                    "name": "get_hero_bp_stats",
                    "success": False,
                    "error": "Hero B was not found.",
                },
            ],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
        }

        with patch("app.api.coach.KimiCoachService", return_value=service):
            response = self.client.post(
                "/api/coach",
                json={
                    "message": "What are the top meta heroes?",
                    "league_id": "20260002",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["request_id"], "request-from-service")
        self.assertEqual(payload["data"]["evidence"][0]["tool"], "get_meta_heroes")
        self.assertEqual(
            payload["data"]["warnings"],
            ["get_hero_bp_stats: Hero B was not found."],
        )
        self.assertEqual(payload["data"]["usage"]["total_tokens"], 120)
        coach_input = service.ask.call_args.args[0]
        self.assertEqual(coach_input.league_id, "20260002")
        self.assertEqual(len(service.ask.call_args.kwargs["request_id"]), 32)

    def test_validates_active_draft_state_before_calling_kimi(self) -> None:
        service = Mock()

        with patch("app.api.coach.KimiCoachService", return_value=service):
            response = self.client.post(
                "/api/coach",
                json={
                    "message": "What is the next pick?",
                    "league_id": "20260002",
                    "draft_state": {
                        "bp_order": 2,
                        "blue_picks": [-1],
                        "unexpected": True,
                    },
                },
            )

        self.assertEqual(response.status_code, 422)
        service.ask.assert_not_called()

    def test_configuration_failure_returns_safe_503(self) -> None:
        with patch(
            "app.api.coach.KimiCoachService",
            side_effect=KimiConfigurationError("secret configuration detail"),
        ):
            response = self.client.post(
                "/api/coach",
                json={"message": "Hello", "league_id": "20260002"},
            )

        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "coach_unavailable")
        self.assertEqual(len(detail["request_id"]), 32)
        self.assertNotIn("secret configuration detail", response.text)

    def test_tool_loop_limit_returns_safe_502(self) -> None:
        service = Mock()
        service.ask.side_effect = CoachLoopLimitError("internal loop detail")

        with patch("app.api.coach.KimiCoachService", return_value=service):
            response = self.client.post(
                "/api/coach",
                json={"message": "Keep searching", "league_id": "20260002"},
            )

        self.assertEqual(response.status_code, 502)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "coach_incomplete")
        self.assertNotIn("internal loop detail", response.text)


if __name__ == "__main__":
    unittest.main()
