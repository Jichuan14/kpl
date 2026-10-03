import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import BattlePlayer, League, Match, Team
from app.api.leagues import upcoming_match
from app.services.season_teams import (
    list_season_teams,
    current_or_next_scheduled_match,
    next_scheduled_match,
    validate_season_team_pair,
)


class SeasonTeamsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Team.__table__.create(self.engine)
        BattlePlayer.__table__.create(self.engine)
        Match.__table__.create(self.engine)
        League.__table__.create(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        with self.factory() as db:
            db.add_all(
                [
                    League(league_id="season-1", league_name="Season 1"),
                    League(league_id="20260004", league_name="Season 4"),
                    Team(team_id="wolves", team_name="Wolves", team_icon="wolf.png"),
                    Team(team_id="ag", team_name="AG", team_icon="ag.png"),
                    Team(team_id="old", team_name="Old Team", team_icon="old.png"),
                    BattlePlayer(
                        battle_id="b1",
                        match_id="m1",
                        league_id="season-1",
                        team_id="wolves",
                        team_name="Wolves",
                        player_name="P1",
                        hero_id=101,
                        camp=1,
                    ),
                    BattlePlayer(
                        battle_id="b1",
                        match_id="m1",
                        league_id="season-1",
                        team_id="ag",
                        team_name="AG",
                        player_name="P2",
                        hero_id=102,
                        camp=2,
                    ),
                    BattlePlayer(
                        battle_id="old-battle",
                        match_id="old-match",
                        league_id="old-season",
                        team_id="old",
                        team_name="Old Team",
                        player_name="Old.P1",
                        hero_id=103,
                        camp=1,
                    ),
                ]
            )
            db.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_lists_only_requested_season(self) -> None:
        with self.factory() as db:
            rows = list_season_teams(db, "season-1")
        self.assertEqual({row["team_id"] for row in rows}, {"wolves", "ag"})
        self.assertEqual(next(row for row in rows if row["team_id"] == "wolves")["team_icon"], "wolf.png")

    def test_validates_distinct_season_pair(self) -> None:
        with self.factory() as db:
            resolved = validate_season_team_pair(db, "season-1", "wolves", "ag")
            self.assertEqual(resolved["blue"]["team_name"], "Wolves")
            with self.assertRaisesRegex(ValueError, "different teams"):
                validate_season_team_pair(db, "season-1", "wolves", "wolves")
            with self.assertRaisesRegex(ValueError, "not part of this season"):
                validate_season_team_pair(db, "season-1", "wolves", "old")

    def test_uses_china_time_to_find_the_next_selectable_fixture(self) -> None:
        with self.factory() as db:
            db.add_all(
                [
                    Match(
                        match_id="past",
                        league_id="season-1",
                        camp1_team_id="wolves",
                        camp1_team_name="Wolves",
                        camp2_team_id="ag",
                        camp2_team_name="AG",
                        start_time="2026-08-16 13:00:00",
                    ),
                    Match(
                        match_id="next",
                        league_id="season-1",
                        camp1_team_id="wolves",
                        camp1_team_name="Wolves",
                        camp2_team_id="ag",
                        camp2_team_name="AG",
                        start_time="2026-08-16 20:00:00",
                    ),
                ]
            )
            db.commit()
            fixture = next_scheduled_match(
                db,
                "season-1",
                selectable_team_ids={"wolves", "ag"},
                as_of_china=datetime(2026, 8, 16, 14, 0, 0),
            )

        self.assertEqual(fixture["match_id"], "next")
        self.assertEqual(fixture["start_time"], "2026-08-16 20:00:00")
        self.assertEqual(fixture["timezone"], "Asia/Shanghai")

    def test_upcoming_api_does_not_skip_teams_without_recorded_games(self) -> None:
        class BeijingNow(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 3, 12, 0, tzinfo=tz)

        with self.factory() as db:
            db.add_all([
                Match(match_id="unplayed", league_id="season-1", camp1_team_id="wb",
                      camp1_team_name="WB", camp2_team_id="lgd", camp2_team_name="LGD",
                      start_time="2026-10-03 14:00:00", bo=5),
                Match(match_id="observed", league_id="season-1", camp1_team_id="wolves",
                      camp2_team_id="ag", start_time="2026-10-04 14:00:00"),
                Match(match_id="other-season", league_id="old-season", camp1_team_id="wolves",
                      camp2_team_id="ag", start_time="2026-10-03 13:00:00"),
            ])
            db.commit()
            with patch("app.services.season_teams.datetime", BeijingNow):
                for next_only in (False, True):
                    fixture = upcoming_match("season-1", next_only=next_only, db=db).data
                    self.assertEqual(fixture["match_id"], "unplayed")
                    self.assertEqual([team["team_id"] for team in fixture["teams"]], ["wb", "lgd"])
            # Fixture participation does not create observed-player statistics.
            self.assertEqual({team["team_id"] for team in list_season_teams(db, "season-1")}, {"wolves", "ag"})

    def test_fixture_teams_are_valid_before_any_current_season_observations(self) -> None:
        with self.factory() as db:
            db.add(Match(match_id="s4", league_id="20260004", camp1_team_id="wb",
                         camp1_team_name="WB", camp2_team_id="lgd", camp2_team_name="LGD",
                         start_time="2026-10-03 14:00:00"))
            db.commit()
            self.assertEqual(list_season_teams(db, "20260004"), [])
            with patch("app.services.model_registry.current_bundle", return_value=None):
                teams = validate_season_team_pair(db, "20260004", "wb", "lgd")
                self.assertEqual(teams["blue"]["evidence_scope"], "season_fixture")
                self.assertNotIn("battle_count", teams["blue"])
                with self.assertRaisesRegex(ValueError, "not part of this season"):
                    validate_season_team_pair(db, "old-season", "wb", "lgd")

    def test_completed_and_placeholder_fixtures_are_not_current_or_next(self) -> None:
        with self.factory() as db:
            for match_id, start, fields in [
                ("finished", "2026-10-03 11:00:00", {"status": 2}),
                ("winner", "2026-10-03 11:30:00", {"win_camp": 1}),
                ("completed-future", "2026-10-03 12:30:00", {"status": 2}),
                ("winner-future", "2026-10-03 12:45:00", {"win_camp": 2}),
                ("placeholder", "2026-10-03 13:00:00", {"camp2_team_id": "0"}),
                ("duplicate", "2026-10-03 13:30:00", {"camp2_team_id": "wolves"}),
                ("next", "2026-10-03 14:00:00", {}),
            ]:
                values = {"match_id": match_id, "league_id": "season-1", "start_time": start,
                          "camp1_team_id": "wolves", "camp2_team_id": "ag", **fields}
                db.add(Match(**values))
            db.commit()
            for select_fixture in (next_scheduled_match, current_or_next_scheduled_match):
                fixture = select_fixture(db, "season-1", as_of_china=datetime(2026, 10, 3, 12))
                self.assertEqual(fixture["match_id"], "next")

    def test_recent_unfinished_fixture_remains_available_for_live_following(self) -> None:
        with self.factory() as db:
            db.add_all([
                Match(match_id="current", league_id="season-1", camp1_team_id="wolves",
                      camp2_team_id="ag", start_time="2026-10-03 14:00:00", status=1),
                Match(match_id="next", league_id="season-1", camp1_team_id="wolves",
                      camp2_team_id="ag", start_time="2026-10-03 17:00:00"),
            ])
            db.commit()
            now = datetime(2026, 10, 3, 14, 30)
            self.assertEqual(current_or_next_scheduled_match(db, "season-1", as_of_china=now)["match_id"], "current")
            self.assertEqual(next_scheduled_match(db, "season-1", as_of_china=now)["match_id"], "next")


if __name__ == "__main__":
    unittest.main()
