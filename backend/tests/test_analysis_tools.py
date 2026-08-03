import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.artifact_cache import JsonlArtifactCache
from app.agent.tools import battles, meta, relationships, teams
from app.agent.tools.battles import (
    GetBattleDraftArguments,
    get_battle_draft,
)
from app.agent.tools.meta import (
    GetHeroBpStatsArguments,
    GetMetaHeroesArguments,
    get_hero_bp_stats,
    get_meta_heroes,
)
from app.agent.tools.relationships import (
    GetHeroRelationshipsArguments,
    get_hero_relationships,
)
from app.agent.tools.teams import (
    GetTeamSynergiesArguments,
    get_team_synergies,
)
from app.models import Battle, BattleBp, HeroBpStats


class AnalysisToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.league_id = "league-1"
        self.output = self.root / self.league_id
        self.output.mkdir()
        self.cache = JsonlArtifactCache(self.root)
        self.patchers = [
            patch.object(relationships, "artifact_cache", self.cache),
            patch.object(teams, "artifact_cache", self.cache),
            patch.object(meta, "artifact_cache", self.cache),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary.cleanup()

    def write_jsonl(self, filename: str, rows: list[dict]) -> None:
        (self.output / filename).write_text(
            "".join(f"{json.dumps(row)}\n" for row in rows),
            encoding="utf-8",
        )

    def test_relationship_tool_filters_and_ranks_cached_rows(self) -> None:
        self.write_jsonl(
            "pick_synergy_stats.jsonl",
            [
                {
                    "relation": "pick_synergy",
                    "context_level": "overall",
                    "is_peak_battle": False,
                    "response_action": "pick",
                    "ally_hero_id": 101,
                    "ally_hero_name": "Hero A",
                    "candidate_hero_id": 102,
                    "candidate_hero_name": "Hero B",
                    "context_decision_count": 20,
                    "legal_opportunity_count": 10,
                    "selection_count": 4,
                    "smoothed_probability_given_legal": 0.35,
                    "baseline_probability_given_legal": 0.15,
                    "smoothed_lift": 2.33,
                    "probability_ci95_low": 0.17,
                    "probability_ci95_high": 0.69,
                    "battle_win_rate_when_selected": 0.75,
                },
                {
                    "relation": "pick_synergy",
                    "context_level": "overall",
                    "is_peak_battle": True,
                    "response_action": "pick",
                    "ally_hero_id": 101,
                    "ally_hero_name": "Hero A",
                    "candidate_hero_id": 103,
                    "candidate_hero_name": "Peak Hero",
                    "selection_count": 8,
                    "smoothed_lift": 9.0,
                },
            ],
        )

        result = get_hero_relationships(
            GetHeroRelationshipsArguments(
                league_id=self.league_id,
                relation="pick_synergy",
                source_hero_name="hero a",
            )
        )

        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["rows"][0]["target_hero_name"], "Hero B")
        self.assertEqual(result["rows"][0]["selections"], 4)
        self.assertIn("artifact_version", result)

    def test_team_synergy_tool_filters_by_team_and_hero(self) -> None:
        self.write_jsonl(
            "team_synergy_stats.jsonl",
            [
                {
                    "league_id": self.league_id,
                    "team_id": "wolves",
                    "team_name": "Wolves",
                    "team_battle_count": 30,
                    "hero_a_id": 101,
                    "hero_a_name": "Hero A",
                    "hero_b_id": 102,
                    "hero_b_name": "Hero B",
                    "legal_completion_opportunity_count": 12,
                    "selection_count": 5,
                    "raw_completion_probability": 0.416,
                    "smoothed_completion_probability": 0.35,
                    "team_baseline_completion_probability": 0.17,
                    "smoothed_lift": 2.05,
                    "probability_ci95_low": 0.19,
                    "probability_ci95_high": 0.68,
                    "battle_win_count_when_paired": 3,
                    "battle_win_rate_when_paired": 0.6,
                },
                {
                    "league_id": self.league_id,
                    "team_id": "ag",
                    "team_name": "AG",
                    "hero_a_id": 101,
                    "hero_a_name": "Hero A",
                    "hero_b_id": 103,
                    "hero_b_name": "Hero C",
                    "selection_count": 9,
                    "smoothed_lift": 3.0,
                },
            ],
        )

        result = get_team_synergies(
            GetTeamSynergiesArguments(
                league_id=self.league_id,
                team_name="wolves",
                hero_name="Hero A",
            )
        )

        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["rows"][0]["hero_b_name"], "Hero B")
        self.assertEqual(result["rows"][0]["descriptive_battle_win_rate"], 0.6)

    def test_meta_tool_sorts_existing_priority_rows(self) -> None:
        self.write_jsonl(
            "meta_hero_stats.jsonl",
            [
                {
                    "league_id": self.league_id,
                    "hero_id": 101,
                    "hero_name": "Hero A",
                    "eligible_battle_count": 20,
                    "opening_ban_count": 5,
                    "opening_ban_rate": 0.25,
                    "blue_first_pick_count": 2,
                    "blue_first_pick_rate_given_legal": 0.1,
                    "early_priority_count": 7,
                    "early_priority_rate": 0.35,
                },
                {
                    "league_id": self.league_id,
                    "hero_id": 102,
                    "hero_name": "Hero B",
                    "eligible_battle_count": 20,
                    "opening_ban_count": 9,
                    "opening_ban_rate": 0.45,
                    "blue_first_pick_count": 1,
                    "blue_first_pick_rate_given_legal": 0.05,
                    "early_priority_count": 10,
                    "early_priority_rate": 0.5,
                },
            ],
        )

        result = get_meta_heroes(
            GetMetaHeroesArguments(
                league_id=self.league_id,
                sort_by="priority",
                limit=1,
            )
        )

        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["rows"][0]["hero_name"], "Hero B")

    def test_hero_bp_tool_queries_sqlite_aggregates(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        HeroBpStats.__table__.create(engine)
        factory = sessionmaker(bind=engine)
        with factory() as db:
            db.add_all(
                [
                    HeroBpStats(
                        league_id=self.league_id,
                        hero_id=101,
                        hero_name="Hero A",
                        battle_count=20,
                        ban_count=4,
                        pick_count=10,
                        win_count=6,
                        ban_rate=0.2,
                        pick_rate=0.5,
                        presence_rate=0.7,
                        win_rate=0.6,
                    ),
                    HeroBpStats(
                        league_id=self.league_id,
                        hero_id=102,
                        hero_name="Hero B",
                        battle_count=20,
                        ban_count=10,
                        pick_count=4,
                        win_count=2,
                        ban_rate=0.5,
                        pick_rate=0.2,
                        presence_rate=0.7,
                        win_rate=0.5,
                    ),
                ]
            )
            db.commit()

        with patch.object(meta, "SessionLocal", factory):
            result = get_hero_bp_stats(
                GetHeroBpStatsArguments(
                    league_id=self.league_id,
                    sort_by="ban",
                    limit=1,
                )
            )

        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["rows"][0]["hero_name"], "Hero B")
        self.assertEqual(result["rows"][0]["ban_rate"], 0.5)
        engine.dispose()

    def test_unknown_entities_are_rejected(self) -> None:
        self.write_jsonl(
            "team_synergy_stats.jsonl",
            [
                {
                    "team_id": "wolves",
                    "team_name": "Wolves",
                    "hero_a_id": 101,
                    "hero_a_name": "Hero A",
                    "hero_b_id": 102,
                    "hero_b_name": "Hero B",
                    "selection_count": 3,
                }
            ],
        )

        with self.assertRaisesRegex(LookupError, "Unknown team"):
            get_team_synergies(
                GetTeamSynergiesArguments(
                    league_id=self.league_id,
                    team_name="Missing Team",
                )
            )

    def test_battle_draft_tool_returns_ordered_season_scoped_actions(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Battle.__table__.create(engine)
        BattleBp.__table__.create(engine)
        factory = sessionmaker(bind=engine)
        with factory() as db:
            db.add(
                Battle(
                    battle_id="battle-1",
                    match_id="match-1",
                    league_id=self.league_id,
                    battle_seq=2,
                    win_camp=1,
                )
            )
            db.add_all(
                [
                    BattleBp(
                        battle_id="battle-1",
                        league_id=self.league_id,
                        camp=2,
                        action_type=1,
                        hero_id=102,
                        hero_name="Hero B",
                        bp_order=2,
                    ),
                    BattleBp(
                        battle_id="battle-1",
                        league_id=self.league_id,
                        camp=1,
                        action_type=0,
                        hero_id=101,
                        hero_name="Hero A",
                        bp_order=1,
                    ),
                ]
            )
            db.commit()

        with patch.object(battles, "SessionLocal", factory):
            result = get_battle_draft(
                GetBattleDraftArguments(
                    league_id=self.league_id,
                    battle_id="battle-1",
                )
            )

        self.assertEqual(result["action_count"], 2)
        self.assertEqual(result["actions"][0]["hero_name"], "Hero A")
        self.assertEqual(result["actions"][0]["side"], "blue")
        self.assertEqual(result["actions"][1]["action"], "pick")
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
