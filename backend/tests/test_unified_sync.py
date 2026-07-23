import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Battle, BattleBp, BattlePlayer, Hero, Match, Player, Team
from app.services.sync import SyncService


class UnifiedBattleSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.match = Match(
            match_id="match-1",
            league_id="league-1",
            camp1_team_id="team-a",
            camp1_team_name="Team A",
            camp2_team_id="team-b",
            camp2_team_name="Team B",
            status=2,
        )
        self.battle = Battle(
            battle_id="battle-1",
            match_id="match-1",
            league_id="league-1",
            battle_seq=1,
        )
        self.db.add_all([self.match, self.battle])
        self.db.commit()
        self.service = SyncService(self.db)

    def tearDown(self) -> None:
        self.service.close()
        self.db.close()
        self.engine.dispose()

    def detail(self) -> dict:
        return {
            "battle_id": "battle-1",
            "battle_seq": 1,
            "status": 2,
            "win_camp": 1,
            "camp1": {
                "team_id": "team-b",
                "team_name": "Team B",
                "team_icon": "b.png",
            },
            "camp2": {
                "team_id": "team-a",
                "team_name": "Team A",
                "team_icon": "a.png",
            },
            "bp_list": [
                {
                    "is_ban_or_pick": 0,
                    "camp": 1,
                    "hero_id": 101,
                    "hero_name": "Banned Hero",
                    "hero_icon": "101.png",
                },
                {
                    "is_ban_or_pick": 1,
                    "camp": 2,
                    "hero_id": 102,
                    "hero_name": "Picked Hero",
                    "hero_icon": "102.png",
                },
            ],
            "battle_player_list": [
                {
                    "team_id": "team-a",
                    "team_name": "Team A",
                    "actual_player_name": "Player A",
                    "hero_id": 102,
                    "hero_name": "Picked Hero",
                    "camp": 2,
                    "position": 1,
                },
                {
                    "team_id": "team-b",
                    "team_name": "Team B",
                    "actual_player_name": "Player B",
                    "hero_id": 103,
                    "hero_name": "Lineup-only Hero",
                    "camp": 1,
                    "position": 2,
                },
            ],
        }

    def test_one_detail_populates_all_tables_idempotently(self) -> None:
        result = self.service._persist_battle_detail(
            battle=self.battle,
            match=self.match,
            data=self.detail(),
        )
        self.db.commit()
        self.service._refresh_heroes_for_league("league-1")

        self.assertEqual(result["bp_rows"], 2)
        self.assertEqual(result["battle_player_rows"], 2)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Team)), 2)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Player)), 2)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(BattleBp)), 2
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(BattlePlayer)), 2
        )
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Hero)), 3)

        player_a = self.db.scalar(
            select(BattlePlayer).where(BattlePlayer.player_name == "Player A")
        )
        self.assertEqual(player_a.match_camp, 1)

        self.service._persist_battle_detail(
            battle=self.battle,
            match=self.match,
            data=self.detail(),
        )
        self.db.commit()
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(BattleBp)), 2
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(BattlePlayer)), 2
        )

    def test_incremental_sync_downloads_only_finished_matches_without_battles(self) -> None:
        calls: list[str] = []
        # This existing match is fully stored, so it must not be downloaded.
        self.db.add(
            BattleBp(
                battle_id="battle-1",
                league_id="league-1",
                action_type=0,
                hero_id=1,
            )
        )
        self.db.commit()
        self.service._sleep = lambda: None
        self.service.api.get_matches = lambda league_id: {
            "code": 200,
            "results": [
                {
                    "match_id": "match-1",
                    "status": 2,
                    "camp1": {"team_id": "team-a", "team_name": "Team A"},
                    "camp2": {"team_id": "team-b", "team_name": "Team B"},
                },
                {
                    "match_id": "match-new",
                    "status": 2,
                    "camp1": {"team_id": "team-a", "team_name": "Team A"},
                    "camp2": {"team_id": "team-b", "team_name": "Team B"},
                },
            ],
        }

        def battles(match_id: str) -> dict:
            calls.append(f"battles:{match_id}")
            return {"code": 200, "results": [{"battle_id": "battle-new"}]}

        def detail(battle_id: str) -> dict:
            calls.append(f"detail:{battle_id}")
            data = self.detail()
            data["battle_id"] = battle_id
            return {"code": 200, "data": data}

        self.service.api.get_match_battles = battles
        self.service.api.get_battle_detail = detail

        first = self.service.sync_league_bp(
            league_id="league-1", recompute_stats=False
        )
        self.assertTrue(first["data_changed"])
        self.assertEqual(first["finished_matches_found"], 2)
        self.assertEqual(first["finished_matches_processed"], 1)
        self.assertEqual(first["finished_matches_skipped"], 1)
        self.assertEqual(calls, ["battles:match-new", "detail:battle-new"])

        calls.clear()
        second = self.service.sync_league_bp(
            league_id="league-1", recompute_stats=False
        )
        self.assertFalse(second["data_changed"])
        self.assertEqual(second["finished_matches_processed"], 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
