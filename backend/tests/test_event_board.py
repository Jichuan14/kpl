"""Public board lifecycle, browser ownership and stable newest-first paging."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import event_board
from app.config import Settings
from app.database import Base, get_db
from app.models import EventBoardCode, EventBoardReport
from app.services.event_board import BoardConfig, InvalidBoardCode, load_board_config, sanitize_board_code
from app.services import public_requests


MOZI_SHARE = "王者荣耀【墨子顶级立牌2V470T】价值910分，复制口令获取立牌，来小王生日会免费换周年庆限定传说皮肤！"
YAO_SHARE = "王者荣耀【瑶顶级立牌LFV0QM】价值853分，复制口令获取立牌，来小王生日会免费换周年庆限定传说皮肤！"


class EventBoardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config_path = Path(self.temporary.name) / "board.json"
        self.write_config()
        settings = Settings(_env_file=None, event_board_config_path=str(self.config_path),
                            public_session_secret="a" * 64)
        for target in ("app.services.event_board.get_settings", "app.services.public_requests.get_settings"):
            mock = patch(target, return_value=settings)
            mock.start()
            self.addCleanup(mock.stop)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.app = FastAPI()
        self.app.include_router(event_board.router)
        def database():
            with Session(self.engine) as db:
                yield db
        self.app.dependency_overrides[get_db] = database
        # Independent rate-limit behavior is checked explicitly below.
        self.app.dependency_overrides[event_board.limit_public_writes] = lambda: None
        self.client = TestClient(self.app)

    def write_config(self, **overrides):
        self.config_path.write_text(json.dumps({"enabled": True, **overrides}))

    def seed(self, count):
        with Session(self.engine) as db:
            db.add_all([EventBoardCode(visitor_hash=f"visitor-{index}", code=f"CODE-{index}",
                                      active_digest=f"digest-{index}") for index in range(count)])
            db.commit()

    def test_initial_page_has_ten_newest_and_cursor_survives_new_posts(self):
        self.seed(32)
        first = self.client.get("/api/event-board")
        self.assertEqual(first.headers["cache-control"], "no-store")
        page = first.json()["data"]
        self.assertEqual([row["id"] for row in page["codes"]], list(range(32, 22, -1)))
        self.assertEqual(page["next_cursor"], 23)
        for row in page["codes"]:
            self.assertEqual(set(row), {"id", "code", "created_at"})
        posted = self.client.post("/api/event-board/codes", json={"code": "NEWEST"})
        self.assertEqual(posted.status_code, 200)
        second = self.client.get("/api/event-board?before=23").json()["data"]
        self.assertEqual([row["id"] for row in second["codes"]], list(range(22, 7, -1)))
        self.assertEqual(second["next_cursor"], 8)
        last = self.client.get("/api/event-board?before=8").json()["data"]
        self.assertEqual([row["id"] for row in last["codes"]], list(range(7, 0, -1)))
        self.assertIsNone(last["next_cursor"])
        self.assertEqual(self.client.get("/api/event-board").json()["data"]["codes"][0]["code"], "NEWEST")

    def test_multiple_submissions_are_immutable_idempotent_and_newest_first(self):
        own = self.client.post("/api/event-board/codes", json={"code": "  ABC\n123  "}).json()["data"]
        self.assertEqual(own["code"], "ABC 123")
        other = TestClient(self.app)
        duplicate = other.post("/api/event-board/codes", json={"code": "ABC\t123"})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["detail"]["code"], "duplicate_code")
        theirs = other.post("/api/event-board/codes", json={"code": "SECOND"}).json()["data"]
        another = self.client.post("/api/event-board/codes", json={"code": "ANOTHER"}).json()["data"]
        retry = self.client.post("/api/event-board/codes", json={"code": "ABC 123"})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["data"], own)
        page = self.client.get("/api/event-board").json()["data"]
        self.assertEqual(page["codes"], [another, theirs, own])
        self.assertNotIn("has_submitted", page)
        self.assertNotIn("mine", page)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(EventBoardCode)), 3)
            self.assertEqual(len(db.scalars(select(EventBoardCode).where(EventBoardCode.visitor_hash == db.get(EventBoardCode, own["id"]).visitor_hash)).all()), 2)

    def test_two_tabs_can_submit_distinct_codes_without_overwriting_and_duplicates_stay_unique(self):
        first = self.client.post("/api/event-board/codes", json={"code": "FIRST"}).json()["data"]
        other_tab = TestClient(self.app)
        other_tab.cookies.update(self.client.cookies)
        second = other_tab.post("/api/event-board/codes", json={"code": "SECOND"}).json()["data"]
        for _ in range(2):
            self.assertEqual(other_tab.post("/api/event-board/codes", json={"code": "FIRST"}).json()["data"], first)
        page = self.client.get("/api/event-board").json()["data"]
        self.assertEqual(page["codes"], [second, first])

    def test_only_submit_view_and_invalid_report_routes_exist(self):
        self.client.post("/api/event-board/codes", json={"code": "PERMANENT"})
        self.assertEqual(self.client.put("/api/event-board/mine", json={"code": "EDIT"}).status_code, 404)
        self.assertEqual(self.client.delete("/api/event-board/mine").status_code, 404)
        self.assertEqual(self.client.put("/api/event-board/codes", json={"code": "EDIT"}).status_code, 405)
        self.assertEqual(self.client.delete("/api/event-board/codes").status_code, 405)
        routes = [route for route in self.app.routes if route.path.startswith("/api/event-board")]
        self.assertEqual([(route.path, route.methods) for route in routes],
                         [("/api/event-board", {"GET"}), ("/api/event-board/codes", {"POST"}),
                          ("/api/event-board/codes/{code_id}/report", {"POST"})])
        self.assertEqual(self.client.get("/api/event-board").json()["data"]["codes"][0]["code"], "PERMANENT")

    def test_closed_future_expired_and_invalid_config_reject_writes(self):
        self.client.post("/api/event-board/codes", json={"code": "TEST"})
        for overrides in ({"enabled": False}, {"starts_at": "2099-01-01T00:00:00+08:00"},
                          {"ends_at": "2020-01-01T00:00:00Z"}):
            self.write_config(**overrides)
            self.assertEqual(self.client.get("/api/event-board").json()["data"], {"active": False})
            self.assertEqual(self.client.post("/api/event-board/codes", json={"code": "NEW"}).status_code, 409)
            self.assertEqual(self.client.post("/api/event-board/codes/1/report").status_code, 409)
        self.config_path.write_text("{")
        self.assertFalse(load_board_config().is_active())
        self.assertEqual(self.client.post("/api/event-board/codes", json={"code": "NEW"}).status_code, 409)
        self.write_config()
        page = self.client.get("/api/event-board").json()["data"]
        self.assertEqual(page["codes"][0]["code"], "TEST")

    def test_hidden_code_cannot_be_reposted_and_invalid_input_is_rejected(self):
        for value in ("  ", "\u200bHIDDEN", "a" * 501):
            self.assertEqual(self.client.post("/api/event-board/codes", json={"code": value}).status_code, 422)
        self.write_config(max_code_length=5)
        self.assertEqual(self.client.post("/api/event-board/codes", json={"code": "TOO-LONG"}).status_code, 422)
        # Plain text markup is stored as text, never returned as HTML.
        self.write_config()
        code = self.client.post("/api/event-board/codes", json={"code": "<script>test</script>"}).json()["data"]
        with Session(self.engine) as db:
            db.get(EventBoardCode, code["id"]).hidden = True
            db.commit()
        page = self.client.get("/api/event-board").json()["data"]
        self.assertEqual(page["codes"], [])
        self.assertEqual(self.client.post("/api/event-board/codes", json={"code": "<script>test</script>"}).status_code, 409)
        self.assertEqual(self.client.post("/api/event-board/codes", json={"code": "DIFFERENT"}).status_code, 200)

    def test_configuration_requires_valid_timezone_and_window(self):
        for value in ({"ends_at": "2026-10-31T00:00:00"},
                      {"starts_at": "2026-11-01T00:00:00Z", "ends_at": "2026-10-31T00:00:00Z"}):
            with self.assertRaises(ValueError):
                BoardConfig(**value)
        boundary = datetime(2026, 10, 31, tzinfo=timezone.utc)
        config = BoardConfig(enabled=True, ends_at=boundary)
        self.assertFalse(config.is_active(boundary))
        self.assertTrue(config.is_active(boundary - timedelta(seconds=1)))

    def test_shared_public_write_rate_limit_is_enforced(self):
        del self.app.dependency_overrides[event_board.limit_public_writes]
        with patch.object(public_requests, "write_rate_limiter", public_requests._limiter(1, 10, 100, 100)):
            self.assertEqual(self.client.post("/api/event-board/codes", json={"code": "TEST"}).status_code, 200)
            limited = self.client.post("/api/event-board/codes", json={"code": "TEST"})
            self.assertEqual(limited.status_code, 429)
            self.assertIn("retry-after", limited.headers)

    def test_standee_uploads_are_sanitized_and_deduplicated_by_token(self):
        self.write_config(code_format="birthday_standee")
        formatted = " \ufeff\n" + MOZI_SHARE.replace("2V470T", "２ｖ４７０ｔ").replace("，", ", \n").replace("顶级", "顶\u200b级") + "\t "
        response = self.client.post("/api/event-board/codes", json={"code": formatted})
        self.assertEqual(response.status_code, 200)
        first = response.json()["data"]
        self.assertEqual(first["code"], MOZI_SHARE)
        other = TestClient(self.app)
        # Claimed name and points cannot be changed to bypass token uniqueness.
        modified = MOZI_SHARE.replace("墨子", "瑶").replace("910", "853")
        duplicate = other.post("/api/event-board/codes", json={"code": modified})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["detail"]["code"], "duplicate_code")
        self.assertEqual(other.post("/api/event-board/codes", json={"code": YAO_SHARE}).status_code, 200)
        # Every request still goes through validation, even from an existing submitter.
        rejected = self.client.post("/api/event-board/codes", json={"code": "unrelated replacement"})
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(rejected.json()["detail"]["code"], "invalid_share_message")
        self.assertEqual(self.client.get("/api/event-board").json()["data"]["codes"][-1], first)

    def test_same_browser_can_submit_both_valid_standee_messages(self):
        self.write_config(code_format="birthday_standee")
        for code in (MOZI_SHARE, YAO_SHARE):
            self.assertEqual(self.client.post("/api/event-board/codes", json={"code": code}).status_code, 200)
        self.assertEqual([row["code"] for row in self.client.get("/api/event-board").json()["data"]["codes"]],
                         [YAO_SHARE, MOZI_SHARE])

    def test_invalid_standee_posts_do_not_create_entries(self):
        self.write_config(code_format="birthday_standee")
        invalid = ["2V470T", "TEST-ABC123", MOZI_SHARE[:-1], "prefix " + MOZI_SHARE,
                   MOZI_SHARE + "https://spam.example", MOZI_SHARE.replace("2V470T", "2V470"),
                   MOZI_SHARE.replace("910", "0"), MOZI_SHARE.replace("2V470T", "2V47\u202e0T"),
                   MOZI_SHARE.replace("墨子", "<script>alert(1)</script>"), MOZI_SHARE + YAO_SHARE]
        for value in invalid:
            with self.subTest(value=value):
                response = self.client.post("/api/event-board/codes", json={"code": value})
                self.assertEqual(response.status_code, 422)
        multiple = self.client.post("/api/event-board/codes", json={"code": MOZI_SHARE + YAO_SHARE})
        self.assertEqual(multiple.json()["detail"]["code"], "multiple_share_messages")
        self.assertEqual(self.client.get("/api/event-board").json()["data"]["codes"], [])
        self.write_config(code_format="misspelled-format")
        self.assertFalse(self.client.get("/api/event-board").json()["data"]["active"])
        self.assertEqual(self.client.post("/api/event-board/codes", json={"code": MOZI_SHARE}).status_code, 409)

    def test_third_distinct_report_hard_deletes_code_and_reports(self):
        code = self.client.post("/api/event-board/codes", json={"code": "REPORT-ME"}).json()["data"]
        other = self.client.post("/api/event-board/codes", json={"code": "KEEP-ME"}).json()["data"]
        path = f'/api/event-board/codes/{code["id"]}/report'
        reporters = [TestClient(self.app) for _ in range(3)]
        for index, client in enumerate(reporters):
            response = client.post(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"], {"reported": True, "deleted": index == 2})
            with Session(self.engine) as db:
                if index < 2:
                    self.assertIsNotNone(db.get(EventBoardCode, code["id"]))
                    self.assertEqual(db.scalar(select(func.count()).select_from(EventBoardReport)), index + 1)
                    # One or two reports do not change anyone else's public list.
                    listed = self.client.get("/api/event-board").json()["data"]["codes"]
                    self.assertEqual(listed, [other, code])
                    self.assertEqual(set(listed[-1]), {"id", "code", "created_at"})
                else:
                    self.assertIsNone(db.get(EventBoardCode, code["id"]))
                    self.assertEqual(db.scalar(select(func.count()).select_from(EventBoardReport)), 0)
                    self.assertEqual(self.client.get("/api/event-board").json()["data"]["codes"], [other])
        # Stale clicks and retries after deletion leave no orphan reports.
        self.assertEqual(reporters[0].post(path).json()["data"]["reported"], False)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(EventBoardReport)), 0)

    def test_repeat_reports_and_shared_cookie_tabs_count_once(self):
        code = self.client.post("/api/event-board/codes", json={"code": "REPEAT"}).json()["data"]
        path = f'/api/event-board/codes/{code["id"]}/report'
        # A submitter may report their code, but still contributes only one vote.
        self.client.post(path)
        same_browser = TestClient(self.app)
        same_browser.cookies.update(self.client.cookies)
        for _ in range(4):
            self.assertFalse(same_browser.post(path).json()["data"]["deleted"])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(EventBoardReport)), 1)
            self.assertIsNotNone(db.get(EventBoardCode, code["id"]))
        self.assertFalse(TestClient(self.app).post(path).json()["data"]["deleted"])
        self.assertTrue(TestClient(self.app).post(path).json()["data"]["deleted"])

    def test_reporting_missing_or_hidden_codes_does_not_create_reports(self):
        self.assertEqual(self.client.post("/api/event-board/codes/999/report").json()["data"]["reported"], False)
        self.assertEqual(self.client.post("/api/event-board/codes/0/report").status_code, 422)
        code = self.client.post("/api/event-board/codes", json={"code": "HIDDEN"}).json()["data"]
        with Session(self.engine) as db:
            db.get(EventBoardCode, code["id"]).hidden = True
            db.commit()
        self.assertFalse(self.client.post(f'/api/event-board/codes/{code["id"]}/report').json()["data"]["reported"])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(EventBoardReport)), 0)

    def test_reports_use_the_shared_public_write_limit(self):
        code = self.client.post("/api/event-board/codes", json={"code": "RATE"}).json()["data"]
        del self.app.dependency_overrides[event_board.limit_public_writes]
        with patch.object(public_requests, "write_rate_limiter", public_requests._limiter(1, 10, 100, 100)):
            path = f'/api/event-board/codes/{code["id"]}/report'
            self.assertEqual(self.client.post(path).status_code, 200)
            self.assertEqual(self.client.post(path).status_code, 429)

    def test_concurrent_reports_delete_once_without_orphans(self):
        from concurrent.futures import ThreadPoolExecutor

        engine = create_engine(f'sqlite:///{Path(self.temporary.name) / "reports.db"}',
                               connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        def database():
            with Session(engine) as db:
                yield db
        self.app.dependency_overrides[get_db] = database
        code = self.client.post("/api/event-board/codes", json={"code": "CONCURRENT"}).json()["data"]
        clients = [TestClient(self.app) for _ in range(3)]
        path = f'/api/event-board/codes/{code["id"]}/report'
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(lambda client: client.post(path), clients))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(sum(response.json()["data"]["deleted"] for response in responses), 1)
        with Session(engine) as db:
            self.assertIsNone(db.get(EventBoardCode, code["id"]))
            self.assertEqual(db.scalar(select(func.count()).select_from(EventBoardReport)), 0)

    def test_failed_threshold_deletion_rolls_back_code_and_third_report(self):
        from sqlalchemy.sql.dml import Delete

        code = self.client.post("/api/event-board/codes", json={"code": "ATOMIC"}).json()["data"]
        path = f'/api/event-board/codes/{code["id"]}/report'
        for _ in range(2):
            TestClient(self.app).post(path)
        execute = Session.execute
        def fail_cleanup(db, statement, *args, **kwargs):
            if isinstance(statement, Delete) and statement.table.name == "event_board_reports":
                raise RuntimeError("simulated deletion failure")
            return execute(db, statement, *args, **kwargs)
        with patch.object(Session, "execute", fail_cleanup):
            with self.assertRaises(RuntimeError):
                TestClient(self.app).post(path)
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(EventBoardCode, code["id"]))
            self.assertEqual(db.scalar(select(func.count()).select_from(EventBoardReport)), 2)
        self.assertTrue(TestClient(self.app).post(path).json()["data"]["deleted"])


class StandeeShareParserTests(unittest.TestCase):
    def test_both_reference_messages_are_recognized_without_seeding_them(self):
        config = BoardConfig(code_format="birthday_standee")
        for sample, token in ((MOZI_SHARE, "2V470T"), (YAO_SHARE, "LFV0QM")):
            with self.subTest(token=token):
                code, identity = sanitize_board_code(sample, config)
                self.assertEqual(code, sample)
                self.assertEqual(identity, f"birthday_standee:{token}")

    def test_sanitized_text_and_identity_are_stable(self):
        config = BoardConfig(code_format="birthday_standee")
        code, identity = sanitize_board_code(MOZI_SHARE.replace("2V470T", "2v 470t").replace("！", "!"), config)
        self.assertEqual(code, MOZI_SHARE)
        self.assertEqual(sanitize_board_code(code, config), (code, identity))

    def test_different_names_and_values_still_follow_the_message_structure(self):
        config = BoardConfig(code_format="birthday_standee")
        value = MOZI_SHARE.replace("墨子顶级", "鲁班七号普通").replace("910", "1234").replace("2V470T", "ABCDEF")
        code, identity = sanitize_board_code(value, config)
        self.assertEqual(code, value)
        self.assertEqual(identity, "birthday_standee:ABCDEF")
        for token in ("ABCDE", "ABCDEFG", "ABC-12", "ＡＢＣ中文１"):
            with self.subTest(token=token), self.assertRaises(InvalidBoardCode):
                sanitize_board_code(MOZI_SHARE.replace("2V470T", token), config)

    def test_checked_in_configuration_enables_the_pattern_check(self):
        config = BoardConfig.model_validate_json((Path(__file__).resolve().parents[1] / "event-board.json").read_text())
        self.assertEqual(config.code_format, "birthday_standee")
        with self.assertRaises(InvalidBoardCode):
            sanitize_board_code("TEST-ABC123", config)
