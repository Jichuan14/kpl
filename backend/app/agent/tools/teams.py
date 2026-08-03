"""Team-specific pair preference tools backed by cached artifacts."""

from typing import Any, Literal

from pydantic import Field

from app.agent.artifact_cache import artifact_cache
from app.agent.tools.common import (
    LeagueArguments,
    entity_rows,
    index_rows_by_entity,
    normalize_name,
)

TEAM_SYNERGY_FILE = "team_synergy_stats.jsonl"


class GetTeamSynergiesArguments(LeagueArguments):
    model_config = {"extra": "forbid"}

    team_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        description=(
            "Optional exact team ID. Use only when it comes from authoritative "
            "application context."
        ),
    )
    team_name: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Required exact team name. Do not call this tool for a league-wide "
            "question with no named team."
        ),
    )
    hero_id: int | None = Field(default=None, gt=0)
    hero_name: str | None = Field(default=None, min_length=1, max_length=100)
    min_selections: int = Field(default=2, ge=1, le=1000)
    sort_by: Literal["lift", "selections", "win_rate"] = "lift"
    limit: int = Field(default=5, ge=1, le=20)

def _team_sort_key(row: dict[str, Any], sort_by: str) -> tuple[float, float, int]:
    lift = float(row.get("smoothed_lift") or 0.0)
    win_rate = float(row.get("battle_win_rate_when_paired") or 0.0)
    selections = int(row.get("selection_count") or 0)
    if sort_by == "selections":
        return float(selections), lift, selections
    if sort_by == "win_rate":
        return win_rate, lift, selections
    return lift, float(selections), selections


def _matches_hero(row: dict[str, Any], hero_id: int | None, hero_name: str | None) -> bool:
    if hero_id is not None:
        return hero_id in {
            int(row.get("hero_a_id") or 0),
            int(row.get("hero_b_id") or 0),
        }
    if hero_name:
        wanted = normalize_name(hero_name)
        return wanted in {
            normalize_name(str(row.get("hero_a_name") or "")),
            normalize_name(str(row.get("hero_b_name") or "")),
        }
    return True


def get_team_synergies(arguments: GetTeamSynergiesArguments) -> dict[str, Any]:
    index, snapshot = artifact_cache.get_index(
        arguments.league_id,
        TEAM_SYNERGY_FILE,
        "team-synergies-by-team",
        lambda rows: index_rows_by_entity(
            rows,
            id_field="team_id",
            name_field="team_name",
        ),
    )
    candidates = entity_rows(
        index,
        entity_kind="team",
        entity_id=arguments.team_id,
        entity_name=arguments.team_name,
    )
    candidates = [
        row
        for row in candidates
        if int(row.get("selection_count") or 0) >= arguments.min_selections
        and _matches_hero(row, arguments.hero_id, arguments.hero_name)
    ]
    candidates.sort(
        key=lambda row: _team_sort_key(row, arguments.sort_by),
        reverse=True,
    )
    rows = [
        {
            "team_id": str(row.get("team_id") or ""),
            "team_name": row.get("team_name") or "",
            "team_battle_count": int(row.get("team_battle_count") or 0),
            "hero_a_id": int(row.get("hero_a_id") or 0),
            "hero_a_name": row.get("hero_a_name") or "",
            "hero_b_id": int(row.get("hero_b_id") or 0),
            "hero_b_name": row.get("hero_b_name") or "",
            "legal_completion_opportunities": int(
                row.get("legal_completion_opportunity_count") or 0
            ),
            "selections": int(row.get("selection_count") or 0),
            "raw_completion_probability": row.get("raw_completion_probability"),
            "smoothed_completion_probability": row.get(
                "smoothed_completion_probability"
            ),
            "team_baseline_probability": row.get(
                "team_baseline_completion_probability"
            ),
            "smoothed_lift": row.get("smoothed_lift"),
            "ci95_low": row.get("probability_ci95_low"),
            "ci95_high": row.get("probability_ci95_high"),
            "battle_wins_when_paired": int(
                row.get("battle_win_count_when_paired") or 0
            ),
            "descriptive_battle_win_rate": row.get(
                "battle_win_rate_when_paired"
            ),
            "quality_flagged_selections": int(
                row.get("quality_flagged_selection_count") or 0
            ),
        }
        for row in candidates[: arguments.limit]
    ]
    return {
        "league_id": arguments.league_id,
        "artifact": TEAM_SYNERGY_FILE,
        "artifact_version": snapshot.version.token,
        "result_count": len(rows),
        "rows": rows,
        "warning": (
            "Pair completion and win rates are descriptive, season-wide associations."
        ),
    }
