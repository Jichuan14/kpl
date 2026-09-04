from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.static_publisher import _historical_battle_lineups


class HistoricalBattleLineupTests(unittest.TestCase):
    def test_publishes_complete_lineups_in_lane_order(self) -> None:
        players = []
        for camp, offset in ((1, 100), (2, 200)):
            for position in (4, 7, 5, 2, 6):
                players.append(
                    {
                        "camp": camp,
                        "hero_id": offset + position,
                        "hero_name": f"Hero {offset + position}",
                        "position": position,
                    }
                )
        match = {
            "match_id": "match-1",
            "start_time": "2026-08-01 18:00:00",
            "match_stage": "final",
            "battles": [
                {
                    "battle_id": "battle-1",
                    "battle_seq": 1,
                    "win_camp": 1,
                    "camp_teams": {
                        "1": {"team_id": "blue-1", "team_name": "Blue Team"},
                        "2": {"team_id": "red-1", "team_name": "Red Team"},
                    },
                    "players": players,
                    "bp_actions": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matches.jsonl"
            path.write_text(json.dumps(match) + "\n", encoding="utf-8")
            result = _historical_battle_lineups(path)

        self.assertEqual(len(result["battles"]), 1)
        battle = result["battles"][0]
        self.assertEqual(battle["blue_team_name"], "Blue Team")
        self.assertEqual(battle["blue_team_id"], "blue-1")
        self.assertEqual(
            [hero["position"] for hero in battle["blue"]],
            [6, 2, 5, 7, 4],
        )
        self.assertEqual(len(battle["red"]), 5)


if __name__ == "__main__":
    unittest.main()
