import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.agent.service import CoachLoopLimitError, KimiConfigurationError
from app.main import app
from app.services.coach_rate_limit import CoachRateLimiter


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

    def test_rate_limit_returns_retry_after_before_provider_call(self) -> None:
        limiter = CoachRateLimiter(
            per_ip_per_minute=1,
            per_ip_per_day=10,
            server_per_minute=10,
            server_per_day=100,
            max_active_per_ip=1,
            max_active_server=2,
        )
        service = Mock()
        service.ask.return_value = {
            "request_id": "request-from-service",
            "model": "kimi-k2.6",
            "answer": "Answer.",
            "tool_calls": [],
            "usage": {},
        }
        with patch("app.api.coach.rate_limiter", limiter), patch(
            "app.api.coach.KimiCoachService", return_value=service
        ):
            first = self.client.post(
                "/api/coach", json={"message": "One", "league_id": "20260002"}
            )
            response = self.client.post(
                "/api/coach", json={"message": "Two", "league_id": "20260002"}
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["detail"]["code"], "coach_rate_limited")
        self.assertGreaterEqual(int(response.headers["Retry-After"]), 1)
        self.assertEqual(service.ask.call_count, 1)

    def test_usage_endpoint_exposes_no_client_identifiers(self) -> None:
        response = self.client.get("/api/coach/usage")

        self.assertEqual(response.status_code, 200)
        usage = response.json()["data"]
        self.assertIn("server", usage)
        self.assertIn("per_ip", usage)
        self.assertNotIn("ips", usage)

    def test_management_can_update_runtime_limits(self) -> None:
        limiter = CoachRateLimiter(
            per_ip_per_minute=5, per_ip_per_day=50, server_per_minute=30,
            server_per_day=500, max_active_per_ip=1, max_active_server=4,
        )
        with patch("app.api.coach.rate_limiter", limiter):
            response = self.client.put("/api/coach/limits", json={
                "ip_requests_per_minute": 3,
                "ip_requests_per_day": 25,
                "server_requests_per_minute": 12,
                "server_requests_per_day": 200,
                "ip_max_active_requests": 1,
                "server_max_active_requests": 2,
            })

        self.assertEqual(response.status_code, 200)
        usage = response.json()["data"]
        self.assertEqual(usage["per_ip"]["per_minute_limit"], 3)
        self.assertEqual(usage["server"]["per_24_hours_limit"], 200)


if __name__ == "__main__":
    unittest.main()
