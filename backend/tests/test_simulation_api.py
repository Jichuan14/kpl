import unittest
from unittest.mock import patch

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
            per_ip_per_minute=1,
            per_ip_per_day=10,
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
            patch("app.api.simulation.simulate", return_value={"ok": True}) as simulate,
        ):
            first = self.client.post("/api/simulations/draft", json=self.payload())
            second = self.client.post("/api/simulations/draft", json=self.payload())

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["detail"]["code"], "simulation_rate_limited")
        self.assertGreaterEqual(int(second.headers["Retry-After"]), 1)
        simulate.assert_called_once()

    def test_simulator_rejects_client_rollout_override(self) -> None:
        payload = {**self.payload(), "rollouts": 5000}

        response = self.client.post("/api/simulations/draft", json=payload)

        self.assertEqual(response.status_code, 422)

    def test_simulator_accepts_sequence_model(self) -> None:
        limiter = CoachRateLimiter(
            per_ip_per_minute=10,
            per_ip_per_day=10,
            server_per_minute=10,
            server_per_day=100,
            max_active_per_ip=1,
            max_active_server=2,
        )
        payload = {**self.payload(), "model_type": "sequence"}
        with (
            patch("app.api.simulation.simulation_rate_limiter", limiter),
            patch(
                "app.api.simulation.validate_season_team_pair",
                return_value={
                    "blue": {"team_name": "Blue Club"},
                    "red": {"team_name": "Red Club"},
                },
            ),
            patch(
                "app.api.simulation.simulate", return_value={"model_type": "sequence"}
            ) as simulate,
        ):
            response = self.client.post("/api/simulations/draft", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["model_type"], "sequence")
        self.assertEqual(simulate.call_args.kwargs["model_type"], "sequence")

    def test_hero_matchup_endpoint_uses_feature_space_and_counter_evidence(self) -> None:
        payload = {
            "league_id": "20260003",
            "favorite_hero_ids": [101, 102],
            "opponent_hero_ids": [201, 202],
            "preferred_lane": "mid",
        }
        with (
            patch(
                "app.api.simulation.learned_feature_space",
                return_value={"rows": [{"hero_id": 101}]},
            ),
            patch(
                "app.api.simulation.recommend_heroes",
                return_value={"recommendations": [{"hero_id": 102}]},
            ) as recommend,
        ):
            response = self.client.post("/api/simulations/hero-matchup", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["recommendations"][0]["hero_id"], 102)
        recommend.assert_called_once_with(
            "20260003", {"rows": [{"hero_id": 101}]}, [101, 102], [201, 202], "mid", limit=6
        )


if __name__ == "__main__":
    unittest.main()
