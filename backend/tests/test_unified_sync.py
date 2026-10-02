import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.orm import Session

from app.database import Base, ensure_schema_compatibility
from app.models import Battle, BattleBp, BattlePlayer, Hero, Match, Player, Team
from app.services.sync import SyncService


class UnifiedBattleSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        # Synthetic season rows must never overwrite the real public catalog.
        publisher = patch("app.services.static_publisher.publish_factual_catalog")
        publisher.start()
        self.addCleanup(publisher.stop)
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

    def complete_game(self, battle):
        battle.win_camp = 1
        for camp in (1, 2):
            for slot in range(5):
                hero_id = camp * 100 + slot
                self.db.add(BattleBp(battle_id=battle.battle_id, league_id=battle.league_id,
                                    action_type=1, camp=camp, hero_id=hero_id, bp_order=slot + 1))
                self.db.add(BattlePlayer(battle_id=battle.battle_id, match_id=battle.match_id, league_id=battle.league_id,
                                        camp=camp, hero_id=hero_id, player_name=f"{camp}-{slot}"))
        self.db.commit()

    def test_batch_completeness_requires_expected_games_and_full_detail(self):
        self.match.camp1_score = 1
        self.complete_game(self.battle)
        statements = []
        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            result = self.service._complete_match_ids(["match-1", "no-battles"])
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(result, {"match-1"})
        self.assertEqual(len(statements), 1)
        self.assertEqual(self.service._complete_match_ids([]), set())
        self.match.camp1_score = 3
        self.match.camp2_score = 1
        self.db.commit()
        self.assertFalse(self.service._match_has_complete_battle_data("match-1"))

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
                    "kill_num": 4,
                    "death_num": 1,
                    "assist_num": 7,
                    "gold": 9708,
                    "hurt_total": 100000,
                    "hurt_to_hero_total": 63068,
                    "be_hurt_total": 42000,
                    "be_hurt_by_hero_total": 31000,
                    "kda": 11,
                    "mvp_score": 11,
                    "is_mvp": 1,
                    "participation_rate": 78.5714,
                    "hurt_total_rate": 22.5,
                    "be_hurt_total_rate": 12.5,
                    "hurt_to_hero_total_rate": 25.5,
                    "be_hurt_by_hero_total_rate": 13.5,
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

    def test_future_hero_first_league_storage_and_updated_resync(self):
        detail=self.detail();new_id=909090
        detail['bp_list'][1].update(hero_id=new_id,hero_name='Future hero',hero_icon='future.png')
        detail['battle_player_list'][0].update(hero_id=new_id,hero_name='Future hero')
        self.service._persist_battle_detail(battle=self.battle,match=self.match,data=detail);self.db.commit()
        self.assertEqual(self.db.get(Hero,new_id).hero_name,'Future hero')
        detail['bp_list'][1].update(hero_name='Corrected future hero',hero_icon='corrected.png')
        detail['battle_player_list'][0].update(hero_name='Corrected future hero')
        self.service._persist_battle_detail(battle=self.battle,match=self.match,data=detail);self.db.commit()
        hero=self.db.get(Hero,new_id)
        self.assertEqual(hero.hero_name,'Corrected future hero');self.assertEqual(hero.hero_icon,'corrected.png')
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Hero).where(Hero.hero_id==new_id)),1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(BattleBp)),2)

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

        placeholder = self.detail()
        placeholder["battle_player_list"][0] = {
            key: value
            for key, value in placeholder["battle_player_list"][0].items()
            if key
            not in {
                "kill_num",
                "death_num",
                "assist_num",
                "gold",
                "hurt_total",
                "hurt_to_hero_total",
                "be_hurt_total",
                "be_hurt_by_hero_total",
                "kda",
                "mvp_score",
                "is_mvp",
                "participation_rate",
                "hurt_total_rate",
                "be_hurt_total_rate",
                "hurt_to_hero_total_rate",
                "be_hurt_by_hero_total_rate",
            }
        }
        self.service._persist_battle_detail(
            battle=self.battle,
            match=self.match,
            data=placeholder,
        )
        self.db.commit()
        preserved = self.db.scalar(
            select(BattlePlayer).where(BattlePlayer.player_name == "Player A")
        )
        self.assertEqual(preserved.kill_num, 4)
        self.assertEqual(preserved.kda, 11.0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Hero)), 3)

        player_a = self.db.scalar(
            select(BattlePlayer).where(BattlePlayer.player_name == "Player A")
        )
        self.assertEqual(player_a.match_camp, 1)
        self.assertEqual(player_a.performance_data_available, 1)
        self.assertEqual(player_a.kill_num, 4)
        self.assertEqual(player_a.death_num, 1)
        self.assertEqual(player_a.assist_num, 7)
        self.assertEqual(player_a.kda, 11.0)
        self.assertEqual(player_a.hurt_to_hero_total, 63068)
        self.assertAlmostEqual(player_a.participation_rate, 78.5714)

        player_b = self.db.scalar(
            select(BattlePlayer).where(BattlePlayer.player_name == "Player B")
        )
        self.assertEqual(player_b.performance_data_available, 0)
        self.assertEqual(result["performance_rows"], 1)

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
        # This existing match has one complete peak-duel game.
        self.match.camp1_score = 1
        self.complete_game(self.battle)
        self.service._sleep = lambda: None
        self.service.api.get_matches = lambda league_id: {
            "code": 200,
            "results": [
                {
                    "match_id": "match-1",
                    "status": 2,
                    "camp1": {"team_id": "team-a", "team_name": "Team A", "score": 1},
                    "camp2": {"team_id": "team-b", "team_name": "Team B"},
                },
                {
                    "match_id": "match-new",
                    "status": 2,
                    "camp1": {"team_id": "team-a", "team_name": "Team A", "score": 1},
                    "camp2": {"team_id": "team-b", "team_name": "Team B"},
                },
            ],
        }

        def battles(match_id: str) -> dict:
            calls.append(f"battles:{match_id}")
            return {"code": 200, "results": [{"battle_id": "battle-new", "battle_seq": 1, "win_camp": 1}]}

        def detail(battle_id: str) -> dict:
            calls.append(f"detail:{battle_id}")
            data = self.detail()
            data["battle_id"] = battle_id
            data["bp_list"] = [{"is_ban_or_pick": 1, "camp": camp, "hero_id": camp * 100 + slot,
                                "hero_name": f"Hero {camp}-{slot}"} for camp in (1, 2) for slot in range(5)]
            data["battle_player_list"] = [{"camp": camp, "hero_id": camp * 100 + slot,
                "actual_player_name": f"Player {camp}-{slot}", "team_id": "team-a" if camp == 1 else "team-b"}
                for camp in (1, 2) for slot in range(5)]
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
        self.assertEqual(first["performance_rows_written"], 0)
        self.assertEqual(calls, ["battles:match-new", "detail:battle-new"])

        calls.clear()
        second = self.service.sync_league_bp(
            league_id="league-1", recompute_stats=False
        )
        self.assertFalse(second["data_changed"])
        self.assertEqual(second["finished_matches_processed"], 0)
        self.assertEqual(calls, [])


class AdditiveSchemaMigrationTest(unittest.TestCase):
    def test_existing_battle_player_rows_receive_safe_defaults(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE battle_players (id INTEGER PRIMARY KEY)"
            )
            connection.exec_driver_sql("INSERT INTO battle_players (id) VALUES (1)")

        added = ensure_schema_compatibility(engine)
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("battle_players")
        }
        self.assertIn("kda", added)
        self.assertIn("performance_data_available", columns)
        with engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT performance_data_available, kda "
                "FROM battle_players WHERE id = 1"
            ).one()
        self.assertEqual(tuple(row), (0, 0.0))
        self.assertEqual(ensure_schema_compatibility(engine), [])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
