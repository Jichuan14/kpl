import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.tools.roster import GetTeamRosterArguments, get_team_roster


class TeamRosterToolTest(unittest.TestCase):
    def test_lists_unique_recorded_players_for_season_and_team(self) -> None:
        records = [
            SimpleNamespace(
                league_id="season-1",
                team_id="wolves",
                team_name="Wolves",
                player_name="Player A",
                player_icon="a.png",
                battle_id="battle-1",
                position_desc="Mid",
            ),
            SimpleNamespace(
                league_id="season-1",
                team_id="wolves",
                team_name="Wolves",
                player_name="Player A",
                player_icon="a.png",
                battle_id="battle-2",
                position_desc="Mid",
            ),
            SimpleNamespace(
                league_id="season-1",
                team_id="wolves",
                team_name="Wolves",
                player_name="Player B",
                player_icon="b.png",
                battle_id="battle-1",
                position_desc="Jungle",
            ),
        ]
        db = SimpleNamespace(
            scalars=lambda _query: SimpleNamespace(all=lambda: records),
        )
        class Session:
            def __enter__(self):
                return db

            def __exit__(self, *_):
                return None

        with patch("app.agent.tools.roster.SessionLocal", return_value=Session()):
            result = get_team_roster(
                GetTeamRosterArguments(league_id="season-1", team_name="wolves")
            )

        self.assertEqual(result["source"], "sqlite:battle_players")
        self.assertEqual(result["result_count"], 2)
        self.assertEqual(result["rows"][0]["player_name"], "Player A")
        self.assertEqual(result["rows"][0]["recorded_battle_count"], 2)
        self.assertEqual(result["rows"][0]["recorded_positions"], ["Mid"])


if __name__ == "__main__":
    unittest.main()
