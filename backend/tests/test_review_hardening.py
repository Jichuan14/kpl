"""Regression coverage for anonymous work admission and source validation."""
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import analytics, leagues, simulation
from app.config import Settings
from app.database import Base, get_db
from app.models import League, Match, LiveMatchWinnerPrediction, VisitorDailyVisitor, VisitorDailyPage
from app.services import public_requests, provider_budget
from app.services.coach_rate_limit import CoachRateLimiter


def limiter(limit=100):
    return CoachRateLimiter(per_ip_per_minute=limit, per_ip_per_day=limit,
                            server_per_minute=limit, server_per_day=limit,
                            max_active_per_ip=2, max_active_server=4)


class PublicApiHardeningTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.app = FastAPI()
        for router in (analytics.router, leagues.router, simulation.router):
            self.app.include_router(router)
        def database():
            with Session(self.engine) as db:
                yield db
        self.app.dependency_overrides[get_db] = database
        self.client = TestClient(self.app)
        for name in ("live_rate_limiter", "write_rate_limiter"):
            context = patch.object(public_requests, name, limiter())
            context.start(); self.addCleanup(context.stop)
        settings = patch.object(public_requests, "get_settings", return_value=Settings(public_session_secret="x" * 32))
        settings.start(); self.addCleanup(settings.stop)
        self.fixture()

    def fixture(self):
        with Session(self.engine) as db:
            db.add_all([League(league_id="20260004"), Match(league_id="20260004", match_id="fixture",
                camp1_team_id="a", camp2_team_id="b", bo=5, start_time="2099-01-01 12:00:00")])
            db.commit()

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def vote(self, **changes):
        body = dict(visitor_id=str(uuid4()), match_id="fixture", game_number=0,
                    team_a_id="a", team_b_id="b", winner_team_id="a", best_of=5,
                    team_a_score=3, team_b_score=1)
        return {**body, **changes}

    def post_vote(self, **changes):
        return self.client.post("/api/leagues/20260004/live-match/predictions", json=self.vote(**changes))

    def test_uuid_rotation_cannot_create_more_votes_in_same_signed_session(self):
        self.assertEqual(self.post_vote().status_code, 200)
        response = self.post_vote(winner_team_id="b", team_a_score=1, team_b_score=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["total_votes"], 1)
        self.assertEqual(response.json()["data"]["your_winner_team_id"], "a")
        self.assertIn(public_requests.COOKIE, self.client.cookies)

    def test_fabricated_match_teams_and_series_length_are_rejected(self):
        self.assertEqual(self.post_vote(match_id="fabricated").status_code, 404)
        self.assertEqual(self.post_vote(team_b_id="invented").status_code, 422)
        self.assertEqual(self.post_vote(best_of=7, team_a_score=4).status_code, 422)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(LiveMatchWinnerPrediction)), 0)

    def test_closed_series_and_closed_games_reject_new_votes(self):
        with Session(self.engine) as db:
            row = db.scalar(select(Match)); row.status = 2; db.commit()
        self.assertEqual(self.post_vote().status_code, 409)
        with Session(self.engine) as db:
            row = db.scalar(select(Match)); row.status = 1; db.commit()
        self.assertEqual(self.post_vote().status_code, 409)
        with patch.object(leagues.live_match_service, "get_match_state", return_value={"is_live": True, "current_game": 2}):
            self.assertEqual(self.post_vote(game_number=1).status_code, 409)
            self.assertEqual(self.post_vote(game_number=2).status_code, 200)

    def test_analytics_only_tracks_known_routes_and_ignores_uuid_rotation(self):
        for page in ("/", "/teams/"):
            response = self.client.post("/api/analytics/visits", json={"visitor_id": str(uuid4()), "page_path": page})
            self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/analytics/visits", json={"visitor_id": str(uuid4()), "page_path": "/invented"})
        self.assertEqual(response.status_code, 400)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(VisitorDailyVisitor)), 1)
            self.assertEqual(db.scalar(select(func.sum(VisitorDailyPage.page_views))), 2)

    def test_tampered_cookie_is_reissued_and_cannot_impersonate_visitor(self):
        self.post_vote()
        original = self.client.cookies.get(public_requests.COOKIE)
        self.client.cookies.clear()
        self.client.cookies.set(public_requests.COOKIE, original[:-1] + ("0" if original[-1] != "0" else "1"), domain="testserver.local", path="/")
        self.post_vote()
        self.assertNotEqual(self.client.cookies.get(public_requests.COOKIE), original)

    def test_public_write_limit_prevents_further_db_writes(self):
        with patch.object(public_requests, "write_rate_limiter", limiter(1)):
            self.assertEqual(self.post_vote().status_code, 200)
            response = self.post_vote()
        self.assertEqual(response.status_code, 429)
        self.assertIn("retry-after", response.headers)

    def test_generated_cookie_key_survives_process_cache_reset(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "public_session.key"
            settings = Settings(public_session_secret=None, public_session_key_path=str(path))
            with patch.object(public_requests, "get_settings", return_value=settings):
                self.assertEqual(self.post_vote().status_code, 200)
                public_requests._persistent_secret.cache_clear()
                self.assertEqual(self.post_vote().json()["data"]["total_votes"], 1)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_coach_and_commentary_consume_the_same_provider_budget(self):
        from app.api import coach
        budget = limiter(1)
        body = dict(league_id="20260004", model_type="stats", blue_team_id="a", red_team_id="b",
                    blue_team_name="A", red_team_name="B", bp_order=1, action="ban", side="blue", selected_hero_id=1)
        with patch.object(provider_budget, "rate_limiter", budget), patch.object(coach, "rate_limiter", budget), patch.object(simulation, "validate_season_team_pair", return_value={"blue": {"team_name": "A"}, "red": {"team_name": "B"}}), patch.object(simulation, "build_selection_commentary", return_value={"commentary": "grounded"}):
            self.assertEqual(self.client.post("/api/simulations/commentary", json=body).status_code, 200)
            self.assertEqual(coach.coach_usage().data["server"]["last_24_hours"], 1)
            self.assertFalse(coach.rate_limiter.acquire("another-client").allowed)

    def test_invalid_live_fixture_never_calls_upstream_and_reads_are_limited(self):
        with patch.object(leagues.live_match_service, "get_match_state", return_value={"is_live": True}) as fetch:
            response = self.client.get("/api/leagues/20260004/live-match", params={"team_a_id": "a", "team_b_id": "wrong", "match_id": "fixture"})
            self.assertEqual(response.status_code, 422)
            fetch.assert_not_called()
            with patch.object(public_requests, "live_rate_limiter", limiter(1)):
                args = {"team_a_id": "a", "team_b_id": "b", "match_id": "fixture"}
                self.assertEqual(self.client.get("/api/leagues/20260004/live-match", params=args).status_code, 200)
                self.assertEqual(self.client.get("/api/leagues/20260004/live-match", params=args).status_code, 429)
            self.assertEqual(fetch.call_count, 1)

    def test_commentary_budget_blocks_generation_and_releases_on_error(self):
        body = dict(league_id="20260004", model_type="stats", blue_team_id="a", red_team_id="b",
                    blue_team_name="A", red_team_name="B", bp_order=1, action="ban", side="blue", selected_hero_id=1)
        budget = limiter(1)
        with patch.object(provider_budget, "rate_limiter", budget), patch.object(simulation, "validate_season_team_pair", return_value={"blue": {"team_name": "A"}, "red": {"team_name": "B"}}), patch.object(simulation, "build_selection_commentary", side_effect=ValueError("invalid state")) as generate:
            self.assertEqual(self.client.post("/api/simulations/commentary", json=body).status_code, 400)
            self.assertEqual(budget.usage()["active_requests"], 0)
            self.assertEqual(self.client.post("/api/simulations/commentary", json={**body, "bp_order": 2}).status_code, 429)
            self.assertEqual(generate.call_count, 1)

    def test_calendar_range_is_bounded_and_includes_both_edges(self):
        with Session(self.engine) as db:
            for i, time in enumerate(["2026-09-30 23:59:59", "2026-10-01 00:00:00", "2026-10-17 23:59:59", "2026-10-18 00:00:00"]):
                db.add(Match(league_id="20260004", match_id=f"range-{i}", start_time=time))
            db.commit()
        response = self.client.get("/api/leagues/daily-matches?start_date=2026-10-01&end_date=2026-10-17")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["match_id"] for row in response.json()["data"]["matches"]], ["range-1", "range-2"])
        for query in ("start_date=2026-10-01", "start_date=2026-10-18&end_date=2026-10-01", "start_date=2026-10-01&end_date=2026-10-18", "start_date=9999-12-31&end_date=9999-12-31"):
            self.assertEqual(self.client.get(f"/api/leagues/daily-matches?{query}").status_code, 422)


class StorageHardeningTests(unittest.TestCase):
    def test_concurrent_coach_initialization_reuses_one_service(self):
        from app.api import coach
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from unittest.mock import Mock
        entered, release = Event(), Event()
        instance = Mock()
        def build(**kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test release")
            return instance
        settings = Settings(coach_enable_conversations=False)
        with patch.object(coach, "_coach_service", None), patch.object(coach, "get_settings", return_value=settings), patch.object(coach, "KimiCoachService", side_effect=build) as factory, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(coach.get_coach_service)
            try:
                self.assertTrue(entered.wait(1))
                second = pool.submit(coach.get_coach_service)
            finally:
                release.set()
            self.assertIs(first.result(), instance)
            self.assertIs(second.result(), instance)
            self.assertEqual(factory.call_count, 1)

    def test_rate_limiter_does_not_retain_blocked_identities_and_prunes_expired_ones(self):
        budget = limiter(1)
        with patch("app.services.coach_rate_limit.monotonic", return_value=100):
            self.assertTrue(budget.acquire("accepted").allowed)
            budget.release("accepted")
            for index in range(100):
                self.assertFalse(budget.acquire(f"blocked-{index}").allowed)
        self.assertEqual(len(budget._ip_requests), 1)
        with patch("app.services.coach_rate_limit.monotonic", return_value=90_000):
            self.assertTrue(budget.acquire("new").allowed)
            budget.release("new")
        self.assertEqual(set(budget._ip_requests), {"new"})

    def test_network_fetches_do_not_hold_sqlite_write_lock_and_failures_rollback(self):
        from app.models import SiteSettings, Battle
        from app.services.sync import SyncService
        with TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{directory}/test.db", connect_args={"timeout": 0.05})
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                match = Match(match_id="fixture", league_id="20260004")
                db.add_all([match, SiteSettings(id=1, default_league_id="20260004")]); db.commit()
                service = SyncService(db)
                updates = []
                def network_fetch(battle_id):
                    with Session(engine) as other:
                        settings = other.get(SiteSettings, 1)
                        settings.default_league_id = battle_id
                        other.commit()
                    updates.append(battle_id)
                    return {"code": 404}
                try:
                    with patch.object(service, "_sleep"), patch.object(service.api, "get_match_battles", return_value={"code": 200, "results": [{"battle_id": "first"}, {"battle_id": "second"}]}), patch.object(service.api, "get_battle_detail", side_effect=network_fetch):
                        result = service._sync_match_battles_and_bp(match)
                    self.assertEqual(updates, ["first", "second"])
                    self.assertEqual(result["detail_errors"], 2)
                    with patch.object(service, "_sleep"), patch.object(service.api, "get_match_battles", return_value={"code": 200, "results": [{"battle_id": "failed"}]}), patch.object(service.api, "get_battle_detail", return_value={"code": 200, "data": {}}), patch.object(service, "_persist_battle_detail", side_effect=RuntimeError("broken payload")):
                        with self.assertRaises(RuntimeError):
                            service._sync_match_battles_and_bp(match)
                    with Session(engine) as other:
                        self.assertIsNone(other.scalar(select(Battle).where(Battle.battle_id == "failed")))
                finally:
                    service.close()
            engine.dispose()

    def test_live_cache_reuses_fixtures_and_evicts_expired_and_oldest_keys(self):
        from app.services.live_match import LiveMatchService
        from unittest.mock import Mock
        upstream = Mock(); upstream.get_matches.return_value = {"results": []}
        service = LiveMatchService(client=upstream, max_cache_entries=2)
        with patch("app.services.live_match.monotonic", return_value=100):
            for index in range(3):
                service.get_match_state("20260004", "a", "b", f"match-{index}")
        self.assertEqual(upstream.get_matches.call_count, 1)
        self.assertEqual(len(service._cache), 2)
        with patch("app.services.live_match.monotonic", return_value=1000):
            service.get_match_state("20260004", "a", "b", "new")
        self.assertEqual(len(service._cache), 1)

    def test_file_summaries_cache_reads_and_invalidate_atomic_replacement(self):
        from app.services.file_summary_cache import file_summary
        from app.services.factual_seasons import season_rankings_ready
        from app.api.data import artifact
        with TemporaryDirectory() as directory:
            root = Path(directory)
            rankings = root / "rankings.json"
            data = {"schema_version": 3, "evidence_scope": "season_only", "league": {"league_id": "20260004"}, "history_league_ids": ["20260004"]}
            rankings.write_text(json.dumps(data))
            from unittest.mock import Mock
            loader = Mock(return_value=10)
            self.assertEqual(file_summary(rankings, loader), 10)
            self.assertEqual(file_summary(rankings, loader), 10)
            self.assertEqual(loader.call_count, 1)
            self.assertTrue(season_rankings_ready(rankings, "20260004"))
            replacement = root / "replacement"; replacement.write_text(json.dumps({**data, "schema_version": 2})); replacement.replace(rankings)
            self.assertFalse(season_rankings_ready(rankings, "20260004"))
            from app.api import data as data_api
            path = root / "data.jsonl"; path.write_text('{}\n\n{}\n')
            with patch.object(data_api, "REPO_ROOT", root):
                self.assertEqual(artifact(path, "key", "label")["records"], 2)
                path.write_text('{}\n')
                self.assertEqual(artifact(path, "key", "label")["records"], 1)

    def test_publishing_empty_relationships_replaces_previous_shards(self):
        from app.services import static_publisher
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with TemporaryDirectory() as directory, Session(engine) as db:
            root = Path(directory)
            db.add(League(league_id="20260004")); db.commit()
            from app.api.visualization import normalize_row
            row = normalize_row("ban_response", {"trigger_hero_id": 1, "response_hero_id": 2}, {})
            payload = dict(league={"league_id": "20260004"}, rows=[row], meta_heroes=[], source_counts={}, generated_at="2026-10-01")
            with patch.object(static_publisher, "DATA_ROOT", root / "published"), patch.object(static_publisher, "OUTPUT_ROOT", root / "outputs"), patch.object(static_publisher, "EXPORT_ROOT", root / "exports"), patch("app.api.visualization.statistics_ready", return_value=True), patch("app.api.visualization.visualization_patterns") as patterns:
                patterns.return_value.data = payload
                static_publisher.publish_league(db, "20260004")
                path = root / "published/20260004/patterns/ban_response/overall.json"
                self.assertEqual(len(json.loads(path.read_text())["rows"]), 1)
                patterns.return_value.data = {**payload, "rows": []}
                static_publisher.publish_league(db, "20260004")
                self.assertEqual(json.loads(path.read_text())["rows"], [])
        engine.dispose()

    def test_expiry_and_restart_remove_checkpoints_and_previous_orphans(self):
        from app.agent.conversation import SqliteConversationStore, build_checkpointer, CONVERSATION_TTL_SECONDS
        from app.agent.service import KimiCoachService
        from langgraph.graph import StateGraph, START, END
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = SqliteConversationStore(root / "conversations.sqlite")
            checkpoints = build_checkpointer(True, root / "checkpoints.sqlite")
            service = KimiCoachService(client=object(), conversation_store=store, checkpointer=checkpoints)
            record = store.create(store.ensure_session(None))
            graph = StateGraph(dict); graph.add_node("echo", lambda state: state); graph.add_edge(START, "echo"); graph.add_edge("echo", END)
            compiled = graph.compile(checkpointer=checkpoints)
            config = {"configurable": {"thread_id": record.conversation_id}}
            compiled.invoke({"text": "synthetic private text"}, config)
            compiled.invoke({"text": "old orphan"}, {"configurable": {"thread_id": "previous-orphan"}})
            record.updated_at -= CONVERSATION_TTL_SECONDS + 1
            store._persist(record)
            service.close()
            store = SqliteConversationStore(root / "conversations.sqlite")
            checkpoints = build_checkpointer(True, root / "checkpoints.sqlite")
            service = KimiCoachService(client=object(), conversation_store=store, checkpointer=checkpoints)
            self.assertEqual(checkpoints.thread_ids(), [])
            self.assertEqual(store._db.execute("SELECT count(*) FROM conversations").fetchone()[0], 0)
            service.clear_conversation_session(record.session_id)
            self.assertIsNone(checkpoints.get_tuple(config))
            service.close()
