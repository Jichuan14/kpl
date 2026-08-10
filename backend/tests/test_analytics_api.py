from datetime import datetime, timedelta, timezone
import unittest
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.analytics import track_visit, visitor_summary
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
