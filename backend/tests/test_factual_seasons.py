import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Battle, League, Match
from app.api.leagues import list_leagues
from app.services.factual_seasons import factual_seasons, season_rankings_ready
from app.services import static_publisher
from app.services.sync import SyncService


class FactualSeasonTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db.add_all([
            League(league_id="20260003", league_name="S3", year=2026, season=3),
            League(league_id="20260004", league_name="S4", year=2026, season=4),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_empty_newest_season_visible_without_models_or_observations(self):
        with patch.object(static_publisher, "DATA_ROOT", self.root):
            response = list_leagues(db=self.db, factual=True).data
            static_publisher.publish_factual_catalog(self.db)
            self.assertEqual(json.loads((self.root / "factual-seasons.json").read_text()), response)
        self.assertEqual(response[0]["league_id"], "20260004")
        self.assertEqual(response[0]["fixture_teams"], [])
        self.assertEqual(response[0]["completed_match_count"], 0)
        self.assertFalse(response[0]["statistics_ready"])
        self.assertFalse((self.root / "seasons.json").exists())

    def test_default_league_api_keeps_tool_catalog_schema_and_order(self):
        from app.schemas import LeagueOut
        from sqlalchemy import select
        expected = [LeagueOut.model_validate(row).model_dump() for row in self.db.scalars(
            select(League).order_by(League.year.desc(), League.season.desc(), League.id.desc())
        ).all()]
        self.assertEqual(list_leagues(db=self.db, factual=False).data, expected)
        self.assertEqual(list_leagues(db=self.db).data, expected)

    def test_actual_fixture_roster_does_not_inherit_historical_results(self):
        self.db.add_all([
            Match(match_id="fixture", league_id="20260004", camp1_team_id="a", camp1_team_name="A", camp2_team_id="b", camp2_team_name="B"),
            Match(match_id="old", league_id="20260003", camp1_team_id="c", camp2_team_id="d", win_camp=1),
            Battle(battle_id="oldgame", match_id="old", league_id="20260003", win_camp=1),
        ])
        self.db.commit()
        rows = factual_seasons(self.db, self.root)
        self.assertEqual([team["team_id"] for team in rows[0]["fixture_teams"]], ["a", "b"])
        self.assertEqual(rows[0]["completed_match_count"], 0)
        self.assertEqual(rows[0]["completed_battle_count"], 0)
        self.assertEqual(rows[1]["completed_battle_count"], 1)

    def test_only_versioned_season_only_rankings_are_available(self):
        directory = self.root / "20260004"
        directory.mkdir()
        artifact = {"schema_version": 3, "evidence_scope": "season_only", "league": {"league_id": "20260004"}, "history_league_ids": ["20260004"]}
        path = directory / "rankings.json"
        for value in ({**artifact, "schema_version": 2}, {**artifact, "history_league_ids": ["20260003", "20260004"]}, [], "invalid"):
            path.write_text(json.dumps(value))
            self.assertFalse(season_rankings_ready(path, "20260004"))
        path.write_text(json.dumps(artifact))
        self.assertTrue(factual_seasons(self.db, self.root)[0]["rankings_ready"])
        self.assertFalse(season_rankings_ready(path, "20260003"))

    def test_publishing_legacy_rankings_does_not_mark_them_season_only(self):
        outputs = self.root / "outputs"
        directory = outputs / "20260004"
        directory.mkdir(parents=True)
        (directory / "power_rankings.json").write_text(json.dumps({"schema_version": 2, "league": {"league_id": "20260004"}}))
        with patch.object(static_publisher, "OUTPUT_ROOT", outputs), patch.object(static_publisher, "EXPORT_ROOT", self.root / "exports"), patch.object(static_publisher, "DATA_ROOT", self.root / "published"):
            static_publisher.publish_league(self.db, "20260004")
        self.assertFalse((self.root / "published" / "20260004" / "rankings.json").exists())

    def test_match_and_battle_sync_refresh_catalog_without_analysis_artifacts(self):
        service = SyncService(self.db)
        match_payload = {"code": 200, "results": [{
            "match_id": "new-s4", "status": 2, "win_camp": 0,
            "camp1": {"team_id": "a", "team_name": "A"},
            "camp2": {"team_id": "b", "team_name": "B"},
        }]}
        snapshots = []

        def details(match):
            snapshots.append(json.loads((self.root / "factual-seasons.json").read_text())[0])
            self.db.add(Battle(battle_id="new-game", match_id=match.match_id, league_id=match.league_id, win_camp=1))
            self.db.commit()
            return {"battles": 1, "bp_rows": 0, "battle_player_rows": 0,
                    "performance_rows": 0, "team_ids": set(), "player_keys": set(), "detail_errors": 0}

        try:
            with patch.object(static_publisher, "DATA_ROOT", self.root), patch.object(service.api, "get_matches", return_value=match_payload), patch.object(service, "_sync_match_battles_and_bp", side_effect=details), patch.object(service, "_refresh_heroes_for_league", return_value=0), patch.object(service, "_sleep"):
                static_publisher.publish_factual_catalog(self.db)
                initial = json.loads((self.root / "factual-seasons.json").read_text())[0]
                self.assertEqual(initial["completed_match_count"], 0)
                service.sync_league_bp("20260004", recompute_stats=False)
            # Already current before details; finished status alone prevents a
            # fabricated initial neutral ranking when winner/detail is missing.
            self.assertEqual(snapshots[0]["completed_match_count"], 1)
            self.assertEqual(snapshots[0]["completed_battle_count"], 0)
            final = json.loads((self.root / "factual-seasons.json").read_text())[0]
            self.assertEqual(final["completed_match_count"], 1)
            self.assertEqual(final["completed_battle_count"], 1)
            for flag in ("statistics_ready", "team_synergy_ready", "rankings_ready"):
                self.assertFalse(final[flag])
            self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["factual-seasons.json"])
        finally:
            service.close()

    def test_catalog_remains_current_when_detail_sync_is_interrupted(self):
        service = SyncService(self.db)
        try:
            with patch.object(static_publisher, "DATA_ROOT", self.root), patch.object(service.api, "get_matches", return_value={"code": 200, "results": [{"match_id": "new-s4", "status": 2, "win_camp": 0}]}), patch.object(service, "_sync_match_battles_and_bp", side_effect=RuntimeError("detail interrupted")), patch.object(service, "_sleep"):
                static_publisher.publish_factual_catalog(self.db)
                with self.assertRaisesRegex(RuntimeError, "detail interrupted"):
                    service.sync_league_bp("20260004", recompute_stats=False)
            final = json.loads((self.root / "factual-seasons.json").read_text())[0]
            self.assertEqual(final["completed_match_count"], 1)
            self.assertEqual(final["completed_battle_count"], 0)
            self.assertFalse(final["rankings_ready"])
        finally:
            service.close()

    def test_successful_catalog_sync_publishes_only_factual_catalog(self):
        service = SyncService(self.db)
        try:
            with patch.object(service.api, "get_leagues", return_value={"code": 200, "results": [{"league_id": "20270001", "league_name": "Next", "year": 2027, "season": 1}]}), patch.object(static_publisher, "DATA_ROOT", self.root):
                service.sync_leagues()
            value = json.loads((self.root / "factual-seasons.json").read_text())
            self.assertEqual(value[0]["league_id"], "20270001")
            self.assertFalse((self.root / "seasons.json").exists())
        finally:
            service.close()
