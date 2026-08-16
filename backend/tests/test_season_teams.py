import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import BattlePlayer, Match, Team
from app.services.season_teams import (
    list_season_teams,
    next_scheduled_match,
    validate_season_team_pair,
)


class SeasonTeamsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Team.__table__.create(self.engine)
        BattlePlayer.__table__.create(self.engine)
        Match.__table__.create(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        with self.factory() as db:
            db.add_all(
                [
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


if __name__ == "__main__":
    unittest.main()
