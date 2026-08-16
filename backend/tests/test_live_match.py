import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.live_match import LiveMatchService


class FakeKplClient:
    def __init__(self) -> None:
        self.matches_calls = 0
        self.battles_calls = 0
        self.detail_calls = 0

    def get_matches(self, league_id: str) -> dict:
        self.matches_calls += 1
        return {
            "results": [
                {
                    "match_id": "live-1",
                    "status": 1,
                    "bo": 5,
                    "camp1": {"team_id": "lgd", "team_name": "LGD", "score": 1},
                    "camp2": {"team_id": "hero", "team_name": "Hero", "score": 0},
                }
            ]
        }

    def get_match_battles(self, match_id: str) -> dict:
        self.battles_calls += 1
        return {
            "results": [
                {"battle_id": "game-1", "battle_seq": 1, "status": 2, "win_camp": 1},
                {"battle_id": "game-2", "battle_seq": 2, "status": 1, "win_camp": 0},
            ]
        }

    def get_battle_detail(self, battle_id: str) -> dict:
        self.detail_calls += 1
        if battle_id != "game-1":
            raise AssertionError(f"unexpected battle {battle_id!r}")
        return {
            "data": {
                "camp1": {"team_id": "lgd"},
                "camp2": {"team_id": "hero"},
                "bp_list": [
                    {"camp": 1, "is_ban_or_pick": 1, "hero_id": 101},
                    {"camp": 2, "is_ban_or_pick": 1, "hero_id": 202},
                    {"camp": 1, "is_ban_or_pick": 0, "hero_id": 303},
                ],
            }
        }

class LiveMatchServiceTest(unittest.TestCase):
    def test_current_fixture_prefers_an_official_live_match_and_caches_it(self) -> None:
        client = FakeKplClient()
        service = LiveMatchService(client=client, cache_seconds=180)

        fixture = service.get_current_fixture(
            "season", selectable_team_ids={"lgd", "hero", "other"}
        )
        cached_fixture = service.get_current_fixture(
            "season", selectable_team_ids={"lgd", "hero", "other"}
        )

        self.assertEqual(fixture["match_id"], "live-1")
        self.assertTrue(fixture["is_live"])
        self.assertEqual([team["team_id"] for team in fixture["teams"]], ["lgd", "hero"])
        self.assertEqual(cached_fixture, fixture)
        self.assertEqual(client.matches_calls, 1)

    def test_live_state_uses_completed_game_picks_without_locking_local_bp(self) -> None:
        client = FakeKplClient()
        service = LiveMatchService(client=client, cache_seconds=180)

        state = service.get_match_state("season", "hero", "lgd")

        self.assertTrue(state["is_live"])
        self.assertFalse(state["is_finished"])
        self.assertEqual(state["current_game"], 2)
        self.assertEqual(state["current_game_status"], "in_progress")
        self.assertFalse(state["hero_selection_locked"])
        self.assertEqual(state["used_hero_ids_by_team"], {"hero": [202], "lgd": [101]})
        self.assertEqual(state["completed_games"][0]["game"], 1)
        self.assertEqual(client.detail_calls, 1)

    def test_reuses_the_process_memory_cache_without_another_official_request(self) -> None:
        client = FakeKplClient()
        service = LiveMatchService(client=client, cache_seconds=180)

        service.get_match_state("season", "lgd", "hero")
        service.get_match_state("season", "hero", "lgd")

        self.assertEqual(client.matches_calls, 1)
        self.assertEqual(client.battles_calls, 1)
        self.assertEqual(client.detail_calls, 1)

    def test_manual_refresh_uses_the_cached_state_until_one_minute_has_passed(self) -> None:
        client = FakeKplClient()
        service = LiveMatchService(
            client=client, cache_seconds=180, manual_refresh_seconds=60
        )

        first = service.get_match_state("season", "lgd", "hero")
        manual = service.refresh_match_state("season", "lgd", "hero")

        self.assertTrue(first["official_refresh"]["performed"])
        self.assertFalse(manual["official_refresh"]["performed"])
        self.assertGreater(manual["official_refresh"]["manual_refresh_available_in_seconds"], 0)
        self.assertEqual(client.matches_calls, 1)


class LiveMatchApiTest(unittest.TestCase):
    def test_endpoint_returns_read_only_live_state(self) -> None:
        client = TestClient(app)
        state = {"is_live": True, "hero_selection_locked": True, "match": {"match_id": "live-1"}}
        with patch("app.api.leagues.live_match_service.get_match_state", return_value=state) as get_state:
            response = client.get(
                "/api/leagues/season/live-match?team_a_id=lgd&team_b_id=hero"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], state)
        get_state.assert_called_once_with("season", "lgd", "hero")

    def test_refresh_endpoint_delegates_to_the_minute_limited_refresh(self) -> None:
        client = TestClient(app)
        state = {"is_live": True, "official_refresh": {"performed": False}}
        with patch(
            "app.api.leagues.live_match_service.refresh_match_state", return_value=state
        ) as refresh:
            response = client.post(
                "/api/leagues/season/live-match/refresh?team_a_id=lgd&team_b_id=hero"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], state)
        refresh.assert_called_once_with("season", "lgd", "hero")

if __name__ == "__main__":
    unittest.main()
