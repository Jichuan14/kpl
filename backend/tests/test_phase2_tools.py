import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent.artifact_cache import JsonlArtifactCache
from app.agent.tools import team_profiles
from app.agent.tools.team_profiles import (
    GetPlayerHeroPoolArguments,
    GetRecentTeamTrendsArguments,
    GetTeamComboPerformanceArguments,
    GetTeamDraftTendenciesArguments,
    GetTeamOpeningSequencesArguments,
    get_player_hero_pool,
    get_recent_team_trends,
    get_team_combo_performance,
    get_team_draft_tendencies,
    get_team_opening_sequences,
)


class Phase2TeamToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.league_id = "season-1"
        self.output = self.root / self.league_id
        self.output.mkdir()
        self.cache = JsonlArtifactCache(self.root)
        self.patcher = patch.object(team_profiles, "artifact_cache", self.cache)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.temporary.cleanup()

    def write(self, filename: str, rows: list[dict]) -> None:
        (self.output / filename).write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    def base(self, **values) -> dict:
        return {
            "league_id": self.league_id,
            "team_id": "wolves",
            "team_name": "Wolves",
            **values,
        }

    def test_team_tendency_filters_side_slot_and_action(self) -> None:
        self.write(
            "team_action_tendencies.jsonl",
            [
                self.base(
                    context_level="slot",
                    side="blue",
                    action="pick",
                    team_action_type_number=1,
                    hero_id=101,
                    hero_name="Hero A",
                    selection_count=4,
                    legal_opportunity_count=10,
                    smoothed_probability_given_legal=0.38,
                    smoothed_lift=1.8,
                ),
                self.base(
                    context_level="slot",
                    side="red",
                    action="pick",
                    team_action_type_number=1,
                    hero_id=102,
                    hero_name="Hero B",
                    selection_count=8,
                    smoothed_probability_given_legal=0.8,
                    smoothed_lift=3.0,
                ),
            ],
        )
        result = get_team_draft_tendencies(
            GetTeamDraftTendenciesArguments(
                league_id=self.league_id,
                team_name="wolves",
                side="blue",
                action="pick",
                team_action_type_number=1,
            )
        )
        self.assertEqual([row["hero_id"] for row in result["rows"]], [101])
        self.assertEqual(result["context_level"], "slot")

    def test_opening_combo_player_and_recent_artifacts(self) -> None:
        self.write(
            "team_opening_sequences.jsonl",
            [
                self.base(
                    context_level="side",
                    side="blue",
                    sequence=[{"action": "ban", "hero_id": 101}],
                    occurrence_count=3,
                    sequence_rate=0.3,
                )
            ],
        )
        self.write(
            "team_combo_performance.jsonl",
            [
                self.base(
                    context_level="side",
                    side="blue",
                    hero_a_id=101,
                    hero_a_name="Hero A",
                    hero_b_id=102,
                    hero_b_name="Hero B",
                    pair_battle_count=4,
                    pair_battle_rate=0.2,
                    descriptive_battle_win_rate=0.75,
                )
            ],
        )
        self.write(
            "player_hero_pools.jsonl",
            [
                self.base(
                    player_name="Wolves.Player",
                    hero_id=101,
                    hero_name="Hero A",
                    pick_count=6,
                    pick_share=0.3,
                    descriptive_battle_win_rate=0.5,
                )
            ],
        )
        self.write(
            "team_recent_trends.jsonl",
            [
                self.base(
                    context_level="overall",
                    side=None,
                    action="pick",
                    hero_id=101,
                    hero_name="Hero A",
                    selection_count=3,
                    smoothed_probability_given_legal=0.3,
                    probability_change_vs_season=0.1,
                    recent_match_window=5,
                )
            ],
        )

        opening = get_team_opening_sequences(
            GetTeamOpeningSequencesArguments(
                league_id=self.league_id,
                team_id="wolves",
                side="blue",
            )
        )
        combo = get_team_combo_performance(
            GetTeamComboPerformanceArguments(
                league_id=self.league_id,
                team_id="wolves",
                side="blue",
                hero_a_name="Hero B",
                hero_b_name="Hero A",
            )
        )
        player = get_player_hero_pool(
            GetPlayerHeroPoolArguments(
                league_id=self.league_id,
                team_id="wolves",
                player_name="wolves.player",
            )
        )
        recent = get_recent_team_trends(
            GetRecentTeamTrendsArguments(
                league_id=self.league_id,
                team_id="wolves",
                action="pick",
            )
        )

        self.assertEqual(opening["rows"][0]["occurrence_count"], 3)
        self.assertEqual(combo["rows"][0]["pair_battle_count"], 4)
        self.assertEqual(player["rows"][0]["pick_count"], 6)
        self.assertEqual(recent["recent_match_window"], 5)


if __name__ == "__main__":
    unittest.main()
