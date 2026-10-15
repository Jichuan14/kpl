"""Upgrade the original one-code-per-browser board without losing data."""
import unittest
from unittest.mock import patch

from sqlalchemy import Connection, create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import ensure_schema_compatibility
from app.models import EventBoardCode


class EventBoardMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE event_board_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visitor_hash VARCHAR(64) NOT NULL,
                    code TEXT NOT NULL,
                    active_digest VARCHAR(64),
                    hidden BOOLEAN NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    UNIQUE (visitor_hash),
                    UNIQUE (active_digest)
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO event_board_codes VALUES
                (7, 'shared-browser', 'ORIGINAL', 'original-digest', 0, '2026-10-15 00:00:00', '2026-10-15 00:00:00'),
                (9, 'legacy-browser', 'HIDDEN', NULL, 1, '2026-10-15 01:00:00', '2026-10-15 02:00:00'),
                (30, 'temporary', 'TEMP', 'temporary-digest', 0, '2026-10-15 00:00:00', '2026-10-15 00:00:00')
            """)
            connection.exec_driver_sql("DELETE FROM event_board_codes WHERE id = 30")

    def records(self):
        with self.engine.connect() as connection:
            return connection.exec_driver_sql("SELECT * FROM event_board_codes ORDER BY id").all()

    def test_upgrade_preserves_posts_ids_sequence_and_token_uniqueness(self):
        original = self.records()
        ensure_schema_compatibility(self.engine)
        self.assertEqual(self.records(), original)
        constraints = inspect(self.engine).get_unique_constraints("event_board_codes")
        self.assertNotIn(["visitor_hash"], [item["column_names"] for item in constraints])
        self.assertIn(["active_digest"], [item["column_names"] for item in constraints])
        with Session(self.engine) as db:
            row = EventBoardCode(visitor_hash="shared-browser", code="SECOND", active_digest="second-digest")
            db.add(row)
            db.commit()
            self.assertEqual(row.id, 31)
            db.add(EventBoardCode(visitor_hash="shared-browser", code="DUPLICATE", active_digest="original-digest"))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            self.assertEqual(len(db.scalars(select(EventBoardCode)).all()), 3)
        # API restarts can run the same compatibility check repeatedly.
        snapshot = self.records()
        self.assertEqual(ensure_schema_compatibility(self.engine), [])
        self.assertEqual(self.records(), snapshot)

    def test_empty_legacy_board_retains_its_id_high_water_mark(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM event_board_codes")
        ensure_schema_compatibility(self.engine)
        with Session(self.engine) as db:
            row = EventBoardCode(visitor_hash="shared-browser", code="FIRST", active_digest="first-digest")
            db.add(row)
            db.commit()
            self.assertEqual(row.id, 31)

    def test_failed_upgrade_rolls_back_the_complete_original_table(self):
        original = self.records()
        execute = Connection.exec_driver_sql
        def fail_rename(connection, statement, *args, **kwargs):
            if statement.startswith("ALTER TABLE event_board_codes_multiple"):
                raise RuntimeError("simulated interrupted migration")
            return execute(connection, statement, *args, **kwargs)
        with patch.object(Connection, "exec_driver_sql", fail_rename):
            with self.assertRaises(RuntimeError):
                ensure_schema_compatibility(self.engine)
        self.assertEqual(self.records(), original)
        self.assertFalse(inspect(self.engine).has_table("event_board_codes_multiple"))
        self.assertIn(["visitor_hash"], [item["column_names"] for item in inspect(self.engine).get_unique_constraints("event_board_codes")])
