import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
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

        state = service.get_match_state("season", "hero", "lgd", "live-1")

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

        service.get_match_state("season", "lgd", "hero", "live-1")
        service.get_match_state("season", "hero", "lgd", "live-1")

        self.assertEqual(client.matches_calls, 1)
        self.assertEqual(client.battles_calls, 1)
        self.assertEqual(client.detail_calls, 1)

    def test_manual_refresh_uses_the_cached_state_until_one_minute_has_passed(self) -> None:
        client = FakeKplClient()
        service = LiveMatchService(
            client=client, cache_seconds=180, manual_refresh_seconds=60
        )

        first = service.get_match_state("season", "lgd", "hero", "live-1")
        manual = service.refresh_match_state("season", "lgd", "hero", "live-1")

        self.assertTrue(first["official_refresh"]["performed"])
        self.assertFalse(manual["official_refresh"]["performed"])
        self.assertGreater(manual["official_refresh"]["manual_refresh_available_in_seconds"], 0)
        self.assertEqual(client.matches_calls, 1)

    def test_does_not_treat_an_old_same_team_series_as_the_scheduled_fixture(self) -> None:
        client = FakeKplClient()
        client.get_matches = lambda _league_id: {
            "results": [
                {
                    "match_id": "old-1",
                    "status": 2,
                    "bo": 5,
                    "camp1": {"team_id": "lgd", "team_name": "LGD", "score": 3},
                    "camp2": {"team_id": "hero", "team_name": "Hero", "score": 1},
                }
            ]
        }
        service = LiveMatchService(client=client, cache_seconds=180)

        state = service.get_match_state("season", "lgd", "hero", "scheduled-2")

        self.assertFalse(state["is_finished"])
        self.assertIsNone(state["match"])


class LiveMatchConcurrencyTest(unittest.TestCase):
    def test_same_key_waiters_share_result_and_unrelated_reads_continue(self):
        service = LiveMatchService(client=FakeKplClient())
        entered, release, joined = Event(), Event(), Event()
        class ObservedFuture(Future):
            def result(self, *args, **kwargs):
                joined.set()
                return super().result(*args, **kwargs)
        def fetch(league, teams, match, ttl):
            if match == "blocked":
                entered.set()
                if not release.wait(3): raise TimeoutError("release")
            return {"match": match, "teams": sorted(teams)}
        with patch.object(service, "_fetch_state", side_effect=fetch), patch("app.services.live_match.Future", ObservedFuture), ThreadPoolExecutor(max_workers=3) as pool:
            leader = pool.submit(service.get_match_state, "s4", "a", "b", "blocked")
            try:
                self.assertTrue(entered.wait(2))
                waiter = pool.submit(service.refresh_match_state, "s4", "b", "a", "blocked")
                self.assertTrue(joined.wait(2))
                unrelated = pool.submit(service.get_match_state, "s4", "a", "b", "other")
                self.assertEqual(unrelated.result(2)["match"], "other")
                cached = pool.submit(service.get_match_state, "s4", "b", "a", "other")
                self.assertFalse(cached.result(2)["official_refresh"]["performed"])
            finally:
                release.set()
            self.assertTrue(leader.result(2)["official_refresh"]["performed"])
            self.assertFalse(waiter.result(2)["official_refresh"]["performed"])
        self.assertEqual(service._state_flights, {})

    def test_ordinary_cache_read_during_failed_manual_refresh_and_retry(self):
        service = LiveMatchService(client=FakeKplClient())
        entered, release, joined = Event(), Event(), Event()
        class ObservedFuture(Future):
            def result(self, *args, **kwargs):
                joined.set()
                return super().result(*args, **kwargs)
        def fail(*args):
            entered.set()
            if not release.wait(3): raise TimeoutError("release")
            raise RuntimeError("upstream")
        with patch("app.services.live_match.monotonic", return_value=0):
            service.get_match_state("s4", "lgd", "hero", "live-1")
        with patch("app.services.live_match.monotonic", return_value=60), patch("app.services.live_match.Future", ObservedFuture), patch.object(service, "_fetch_state", side_effect=fail), ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(service.refresh_match_state, "s4", "hero", "lgd", "live-1")
            try:
                self.assertTrue(entered.wait(2))
                waiter = pool.submit(service.refresh_match_state, "s4", "lgd", "hero", "live-1")
                self.assertTrue(joined.wait(2))
                cached = service.get_match_state("s4", "lgd", "hero", "live-1")
                self.assertEqual(cached["official_refresh"]["cache_age_seconds"], 60)
                self.assertFalse(cached["official_refresh"]["performed"])
            finally:
                release.set()
            for result in (leader, waiter):
                with self.assertRaisesRegex(RuntimeError, "upstream"): result.result(2)
        self.assertEqual(service._state_flights, {})
        with patch("app.services.live_match.monotonic", return_value=61):
            self.assertTrue(service.refresh_match_state("s4", "lgd", "hero", "live-1")["official_refresh"]["performed"])

    def test_fixture_cache_is_raw_and_filters_independently_per_caller(self):
        client = FakeKplClient()
        service = LiveMatchService(client=client)
        self.assertIsNone(service.get_current_fixture("s4", selectable_team_ids={"other"}))
        self.assertEqual(service.get_current_fixture("s4", selectable_team_ids={"lgd", "hero"})["match_id"], "live-1")
        self.assertEqual(client.matches_calls, 1)
        # Same match with a different team pair cannot reuse the earlier state.
        service.get_match_state("s4", "lgd", "hero", "live-1")
        other = service.get_match_state("s4", "lgd", "other", "live-1")
        self.assertIsNone(other["match"])
        self.assertEqual(client.matches_calls, 1)

    def test_slow_fetch_ttl_starts_at_publication_and_boundary_expires(self):
        service = LiveMatchService(client=FakeKplClient(), cache_seconds=180)
        with patch("app.services.live_match.monotonic", side_effect=[0, 0, 200, 200]):
            first = service.get_match_state("s4", "lgd", "hero", "live-1")
        with patch("app.services.live_match.monotonic", return_value=379):
            self.assertFalse(service.get_match_state("s4", "lgd", "hero", "live-1")["official_refresh"]["performed"])
        with patch("app.services.live_match.monotonic", return_value=380):
            self.assertTrue(service.get_match_state("s4", "lgd", "hero", "live-1")["official_refresh"]["performed"])
        self.assertTrue(first["official_refresh"]["performed"])


class LiveMatchApiTest(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from sqlalchemy.pool import StaticPool
        from app.database import Base, get_db
        from app.models import Match
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db:
            db.add(Match(league_id="season", match_id="live-1", camp1_team_id="lgd", camp2_team_id="hero"))
            db.commit()
        def database():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_db] = database
    def tearDown(self):
        from app.database import get_db
        app.dependency_overrides.pop(get_db, None)
        self.engine.dispose()

    def test_endpoint_returns_read_only_live_state(self) -> None:
        client = TestClient(app)
        state = {"is_live": True, "hero_selection_locked": True, "match": {"match_id": "live-1"}}
        with patch("app.api.leagues.live_match_service.get_match_state", return_value=state) as get_state:
            response = client.get(
                "/api/leagues/season/live-match?team_a_id=lgd&team_b_id=hero&match_id=live-1"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], state)
        get_state.assert_called_once_with("season", "lgd", "hero", "live-1")

    def test_refresh_endpoint_delegates_to_the_minute_limited_refresh(self) -> None:
        client = TestClient(app)
        state = {"is_live": True, "official_refresh": {"performed": False}}
        with patch(
            "app.api.leagues.live_match_service.refresh_match_state", return_value=state
        ) as refresh:
            response = client.post(
                "/api/leagues/season/live-match/refresh?team_a_id=lgd&team_b_id=hero&match_id=live-1"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], state)
        refresh.assert_called_once_with("season", "lgd", "hero", "live-1")

if __name__ == "__main__":
    unittest.main()
