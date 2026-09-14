import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.coach_rate_limit import CoachRateLimiter


class SimulationApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @staticmethod
    def payload() -> dict:
        return {
            "league_id": "20260002",
            "blue_team_id": "blue-1",
            "blue_team_name": "Blue Club",
            "red_team_id": "red-1",
            "red_team_name": "Red Club",
            "bp_order": 1,
        }

    def test_simulator_rate_limit_rejects_second_request_before_simulation(self) -> None:
        limiter = CoachRateLimiter(
            per_ip_per_minute=1, per_ip_per_day=10, server_per_minute=10,
            server_per_day=100, max_active_per_ip=1, max_active_server=2,
        )
        with (
            patch("app.api.simulation.simulation_rate_limiter", limiter),
            patch("app.api.simulation.validate_season_team_pair", return_value={"blue": {"team_name": "Blue Club"}, "red": {"team_name": "Red Club"}}),
            patch("app.api.simulation.simulate", return_value={"ok": True}) as simulate,
        ):
            first = self.client.post("/api/simulations/draft", json=self.payload())
            second = self.client.post("/api/simulations/draft", json=self.payload())

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        simulate.assert_called_once()

    def test_simulator_rejects_client_rollout_override(self) -> None:
        response = self.client.post("/api/simulations/draft", json={**self.payload(), "rollouts": 5000})
        self.assertEqual(response.status_code, 422)

    def test_scenario_uses_fixed_completion_cap(self) -> None:
        completions = [{"completed": True, "path": [], "state": {"blue_picks": [1, 2, 3, 4, 5], "red_picks": [6, 7, 8, 9, 10]}}] * 50
        scorer = type("Scorer", (), {"score": lambda *_: {"blue_advantage": 0.62}})()
        with (
            patch("app.api.simulation.validate_season_team_pair", return_value={"blue": {"team_name": "Blue Club"}, "red": {"team_name": "Red Club"}}),
            patch("app.api.simulation.load_lineup_value_model", return_value=scorer),
            patch("app.api.simulation.predict_next_action", return_value={"next_action_probabilities": []}),
            patch("app.api.simulation.sample_forced_draft_completions", return_value={"next_step": {"bp_order": 1, "side": "blue", "action": "ban"}, "forced_hero_id": 1, "forced_policy_probability": 0.2, "completions": completions}),
        ):
            response = self.client.post("/api/simulations/draft-scenario", json={**self.payload(), "forced_hero_id": 1})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["rollouts"], 50)

    def test_lineup_score_allows_opposing_mirror_heroes(self) -> None:
        scorer = Mock()
        scorer.payload = {
            "version": "lineup-value-model-v1",
            "generated_at": "2026-09-14T00:00:00Z",
            "source": {},
        }
        scorer.score.return_value = {
            "blue_advantage": 0.5,
            "red_advantage": 0.5,
        }
        payload = {
            "league_id": "20260003",
            "blue_team_id": "10001",
            "red_team_id": "10017",
            "blue_hero_ids": [140, 521, 107, 519, 194],
            "red_hero_ids": [140, 106, 107, 519, 175],
        }
        limiter = CoachRateLimiter(
            per_ip_per_minute=10,
            per_ip_per_day=100,
            server_per_minute=10,
            server_per_day=100,
            max_active_per_ip=1,
            max_active_server=2,
        )

        with (
            patch("app.api.simulation.simulation_rate_limiter", limiter),
            patch(
                "app.api.simulation.validate_season_team_pair",
                return_value={
                    "blue": {"team_name": "Blue Club"},
                    "red": {"team_name": "Red Club"},
                },
            ),
            patch("app.api.simulation.load_lineup_value_model", return_value=scorer),
        ):
            response = self.client.post("/api/simulations/score-lineup", json=payload)

        self.assertEqual(response.status_code, 200)
        scorer.score.assert_called_once_with(
            payload["blue_team_id"],
            payload["blue_hero_ids"],
            payload["red_team_id"],
            payload["red_hero_ids"],
            allow_mirror_heroes=True,
        )


if __name__ == "__main__":
    unittest.main()
