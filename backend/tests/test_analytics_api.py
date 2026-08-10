from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.analytics import (
    require_widget_token,
    track_visit,
    visitor_summary,
    widget_visitor_summary,
)
from app.config import Settings
from app.main import app
from app.database import Base
from app.models import VisitorDailyPage, VisitorDailyVisitor
from app.schemas import VisitorTrackRequest


class AnalyticsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_tracks_daily_unique_visitors_and_page_views(self) -> None:
        first = VisitorTrackRequest(
            visitor_id=UUID("00000000-0000-4000-8000-000000000001"), page_path="/"
        )
        second_view = VisitorTrackRequest(
            visitor_id=first.visitor_id, page_path="/teams"
        )
        another_visitor = VisitorTrackRequest(
            visitor_id=UUID("00000000-0000-4000-8000-000000000002"), page_path="/"
        )

        track_visit(first, self.session)
        track_visit(second_view, self.session)
        track_visit(another_visitor, self.session)

        data = visitor_summary(self.session).data
        self.assertEqual(data["today"], {"unique_visitors": 2, "page_views": 3})
        self.assertEqual(
            data["last_7_days"], {"unique_visitors": 2, "page_views": 3}
        )

    def test_summary_keeps_windows_separate(self) -> None:
        old_day = datetime.now(timezone.utc).date() - timedelta(days=8)
        visitor = VisitorDailyVisitor(day=old_day, visitor_hash="old-visitor")
        page = VisitorDailyPage(
            day=old_day, page_path="/", page_views=4
        )
        self.session.add_all([visitor, page])
        self.session.commit()

        data = visitor_summary(self.session).data
        self.assertEqual(
            data["last_7_days"], {"unique_visitors": 0, "page_views": 0}
        )
        self.assertEqual(data["all_time"], {"unique_visitors": 1, "page_views": 4})

    def test_widget_requires_a_configured_bearer_token(self) -> None:
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="x" * 32
        )
        with patch("app.api.analytics.get_settings", return_value=Settings()):
            with self.assertRaises(HTTPException) as error:
                require_widget_token(credentials)

        self.assertEqual(error.exception.status_code, 503)

    def test_blank_widget_token_disables_the_optional_endpoint(self) -> None:
        self.assertIsNone(Settings(analytics_widget_token="").analytics_widget_token)

    def test_widget_rejects_invalid_bearer_token(self) -> None:
        configured = Settings(analytics_widget_token="a" * 32)
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="b" * 32
        )
        with patch("app.api.analytics.get_settings", return_value=configured):
            with self.assertRaises(HTTPException) as error:
                require_widget_token(credentials)

        self.assertEqual(error.exception.status_code, 401)
        self.assertEqual(error.exception.headers["WWW-Authenticate"], "Bearer")

    def test_widget_route_rejects_unauthenticated_http_requests(self) -> None:
        configured = Settings(analytics_widget_token="a" * 32)
        with patch("app.api.analytics.get_settings", return_value=configured):
            response = TestClient(app).get("/api/analytics/widget")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_widget_returns_only_today_metrics_for_valid_token(self) -> None:
        visit = VisitorTrackRequest(
            visitor_id=UUID("00000000-0000-4000-8000-000000000003"), page_path="/"
        )
        track_visit(visit, self.session)
        configured = Settings(analytics_widget_token="a" * 32)
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="a" * 32
        )
        response = Response()

        with patch("app.api.analytics.get_settings", return_value=configured):
            require_widget_token(credentials)
            data = widget_visitor_summary(response, None, self.session).data

        self.assertEqual(data, {"today": {"unique_visitors": 1, "page_views": 1}})
        self.assertEqual(response.headers["cache-control"], "no-store")
