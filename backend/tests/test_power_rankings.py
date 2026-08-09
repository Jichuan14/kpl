import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ANALYSIS_DIR = Path(__file__).resolve().parents[2] / "analysis"
sys.path.insert(0, str(ANALYSIS_DIR))

from compute_power_rankings import (  # noqa: E402
    build_rankings,
    canonical_player_name,
    decay_weight,
)


def player(
    name: str,
    team_id: str,
    team_name: str,
    hero_id: int,
    *,
    kda: float,
    mvp: float,
    participation: float,
    damage_rate: float,
    gold: int,
) -> dict:
    return {
        "team_id": team_id,
        "team_name": team_name,
        "player_name": f"{team_name}.{name}",
        "hero_id": hero_id,
        "hero_name": f"Hero {hero_id}",
        "position": 1,
        "position_desc": "Clash",
        "performance_data_available": True,
        "kda": kda,
        "mvp_score": mvp,
        "participation_rate": participation,
        "gold": gold,
        "damage": {"to_heroes_rate": damage_rate},
    }


def match(
    match_id: str,
    league_id: str,
    played_at: datetime,
    winner_id: str,
    players: list[dict],
) -> dict:
    return {
        "match_id": match_id,
        "league_id": league_id,
        "start_time": played_at.isoformat(sep=" "),
        "teams": [
            {"team_id": "a", "team_name": "Team A"},
            {"team_id": "b", "team_name": "Team B"},
        ],
        "battles": [
            {
                "battle_id": f"battle-{match_id}",
                "game_duration_ms": 900_000,
                "winner_team_id": winner_id,
                "camp_teams": {
                    "1": {"team_id": "a", "team_name": "Team A"},
                    "2": {"team_id": "b", "team_name": "Team B"},
                },
                "players": players,
            }
        ],
    }


class PowerRankingsTest(unittest.TestCase):
    def test_decay_half_life_and_player_name_normalization(self) -> None:
        as_of = datetime(2026, 7, 1)
        self.assertAlmostEqual(
            decay_weight(as_of - timedelta(days=180), as_of, 180),
            0.5,
        )
        self.assertEqual(canonical_player_name("杭州LGD.NBW.九尾"), "九尾")

    def test_builds_team_and_per_hero_rankings_with_shrinkage(self) -> None:
        old_date = datetime(2025, 7, 1)
        current_date = datetime(2026, 7, 1)
        weak = player(
            "Weak",
            "b",
            "Team B",
            101,
            kda=1,
            mvp=2,
            participation=30,
            damage_rate=0.08,
            gold=6000,
        )
        strong = player(
            "Strong",
            "a",
            "Team A",
            101,
            kda=9,
            mvp=10,
            participation=90,
            damage_rate=0.3,
            gold=12000,
        )
        old_match = match("old", "old-league", old_date, "b", [strong, weak])
        current_match = match(
            "current",
            "target-league",
            current_date,
            "a",
            [strong, weak],
        )

        artifact = build_rankings(
            [current_match],
            [old_match, current_match],
            current_date,
            ["old-league", "target-league"],
            hero_catalog={101: "Hero 101", 999: "Unused Hero"},
        )

        self.assertEqual(artifact["schema_version"], 1)
        self.assertEqual(artifact["team_rankings"][0]["team_id"], "a")
        hero = next(row for row in artifact["hero_rankings"] if row["hero_id"] == 101)
        self.assertEqual(hero["hero_id"], 101)
        self.assertEqual(hero["players"][0]["player_name"], "Strong")
        self.assertGreater(
            hero["players"][0]["hybrid_score"],
            hero["players"][1]["hybrid_score"],
        )
        self.assertLess(hero["players"][0]["hybrid_score"], 100)
        self.assertEqual(hero["players"][0]["target_season_games"], 1)
        unused = next(row for row in artifact["hero_rankings"] if row["hero_id"] == 999)
        self.assertEqual(unused["players"], [])


if __name__ == "__main__":
    unittest.main()
