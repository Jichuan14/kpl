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
    position: int = 1,
    position_desc: str = "Clash",
) -> dict:
    return {
        "team_id": team_id,
        "team_name": team_name,
        "player_name": f"{team_name}.{name}",
        "hero_id": hero_id,
        "hero_name": f"Hero {hero_id}",
        "position": position,
        "position_desc": position_desc,
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

        self.assertEqual(artifact["schema_version"], 2)
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
        position = artifact["position_rankings"][0]
        self.assertEqual(position["position"], 1)
        self.assertEqual(position["players"][0]["player_name"], "Strong")
        self.assertEqual(artifact["summary"]["player_position_rows"], 2)

    def test_position_ranking_aggregates_heroes_without_mixing_roles(self) -> None:
        played_at = datetime(2026, 7, 1)
        mid_a = player(
            "Flexible",
            "a",
            "Team A",
            101,
            kda=8,
            mvp=9,
            participation=80,
            damage_rate=0.25,
            gold=11000,
            position=2,
            position_desc="Mid",
        )
        mid_b = {**mid_a, "hero_id": 102, "hero_name": "Hero 102"}
        jungle = player(
            "Jungler",
            "b",
            "Team B",
            103,
            kda=4,
            mvp=6,
            participation=70,
            damage_rate=0.2,
            gold=10000,
            position=5,
            position_desc="Jungle",
        )
        current = match(
            "current",
            "target-league",
            played_at,
            "a",
            [mid_a, mid_b, jungle],
        )

        artifact = build_rankings(
            [current],
            [current],
            played_at,
            ["target-league"],
        )

        mid_board = next(
            row for row in artifact["position_rankings"] if row["position"] == 2
        )
        jungle_board = next(
            row for row in artifact["position_rankings"] if row["position"] == 5
        )
        self.assertEqual(mid_board["players"][0]["games"], 2)
        self.assertEqual(mid_board["players"][0]["hero_count"], 2)
        self.assertEqual(mid_board["players"][0]["target_season_games"], 2)
        self.assertEqual(jungle_board["players"][0]["games"], 1)


if __name__ == "__main__":
    unittest.main()
