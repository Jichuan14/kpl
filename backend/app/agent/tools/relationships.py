"""League-wide hero relationship tools backed by cached JSONL artifacts."""

from typing import Any, Literal

from pydantic import Field, PositiveInt, model_validator

from app.agent.artifact_cache import artifact_cache
from app.agent.tools.common import (
    LeagueArguments,
    entity_rows,
    index_rows_by_entity,
)

RelationName = Literal[
    "pick_synergy",
    "counter_pick",
    "counter_ban",
    "ban_response",
]

RELATION_FILES = {
    "pick_synergy": "pick_synergy_stats.jsonl",
    "counter_pick": "counter_pick_stats.jsonl",
    "counter_ban": "counter_ban_stats.jsonl",
    "ban_response": "ban_response_stats.jsonl",
}

RELATION_SOURCE_FIELDS = {
    "pick_synergy": ("ally_hero_id", "ally_hero_name"),
    "counter_pick": ("opponent_hero_id", "opponent_hero_name"),
    "counter_ban": ("opponent_hero_id", "opponent_hero_name"),
    "ban_response": ("trigger_hero_id", "trigger_hero_name"),
}


class GetHeroRelationshipsArguments(LeagueArguments):
    relation: RelationName
    source_hero_id: PositiveInt | None = None
    source_hero_name: str | None = Field(default=None, min_length=1, max_length=100)
    context_level: Literal["overall", "slot_context"] = "overall"
    side: Literal["blue", "red"] | None = None
    slot: int | None = Field(default=None, ge=1, le=10)
    response_scope: Literal[
        "opponent_next_ban",
        "banning_team_later_pick",
        "opponent_later_pick",
    ] | None = None
    include_peak_battles: bool = False
    min_selections: int = Field(default=2, ge=1, le=1000)
    sort_by: Literal["lift", "probability", "selections"] = "lift"
    limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def validate_reference_and_scope(self):
        if self.source_hero_id is None and not self.source_hero_name:
            raise ValueError("source_hero_id or source_hero_name is required")
        if self.relation != "ban_response" and self.response_scope is not None:
            raise ValueError("response_scope applies only to ban_response")
        return self


def _relationship_index(
    rows: tuple[dict[str, Any], ...],
    relation: RelationName,
) -> dict[str, list[dict[str, Any]]]:
    id_field, name_field = RELATION_SOURCE_FIELDS[relation]
    return index_rows_by_entity(
        rows,
        id_field=id_field,
        name_field=name_field,
    )


def _sort_key(row: dict[str, Any], sort_by: str) -> tuple[float, float, int]:
    lift = float(row.get("smoothed_lift") or 0.0)
    probability = float(row.get("smoothed_probability_given_legal") or 0.0)
    selections = int(row.get("selection_count") or 0)
    if sort_by == "probability":
        return probability, lift, selections
    if sort_by == "selections":
        return float(selections), lift, selections
    return lift, probability, selections


def _normalize_relationship(
    relation: RelationName,
    row: dict[str, Any],
) -> dict[str, Any]:
    source_id_field, source_name_field = RELATION_SOURCE_FIELDS[relation]
    target_id_field = (
        "response_hero_id" if relation == "ban_response" else "candidate_hero_id"
    )
    target_name_field = (
        "response_hero_name"
        if relation == "ban_response"
        else "candidate_hero_name"
    )
    return {
        "relation": relation,
        "context_level": row.get("context_level"),
        "is_peak_battle": bool(row.get("is_peak_battle")),
        "source_hero_id": int(row.get(source_id_field) or 0),
        "source_hero_name": row.get(source_name_field) or "",
        "target_hero_id": int(row.get(target_id_field) or 0),
        "target_hero_name": row.get(target_name_field) or "",
        "response_action": row.get("response_action") or "",
        "response_scope": row.get("response_scope"),
        "side": row.get("response_side"),
        "slot": row.get("response_slot"),
        "context_count": int(
            row.get("context_decision_count")
            or row.get("trigger_event_count")
            or 0
        ),
        "legal_opportunities": int(row.get("legal_opportunity_count") or 0),
        "selections": int(row.get("selection_count") or 0),
        "availability_rate": row.get("availability_rate"),
        "raw_probability": row.get("raw_probability_given_legal"),
        "smoothed_probability": row.get("smoothed_probability_given_legal"),
        "baseline_probability": row.get("baseline_probability_given_legal"),
        "smoothed_lift": row.get("smoothed_lift"),
        "ci95_low": row.get("probability_ci95_low"),
        "ci95_high": row.get("probability_ci95_high"),
        "descriptive_win_rate": (
            row.get("response_team_battle_win_rate")
            if relation == "ban_response"
            else row.get("battle_win_rate_when_selected")
        ),
        "quality_flagged_selections": int(
            row.get("quality_flagged_selection_count") or 0
        ),
        "legal_overrides": int(row.get("legal_override_count") or 0),
    }


def get_hero_relationships(
    arguments: GetHeroRelationshipsArguments,
) -> dict[str, Any]:
    filename = RELATION_FILES[arguments.relation]
    index, snapshot = artifact_cache.get_index(
        arguments.league_id,
        filename,
        f"{arguments.relation}-by-source",
        lambda rows: _relationship_index(rows, arguments.relation),
    )
    candidates = entity_rows(
        index,
        entity_kind="source hero",
        entity_id=arguments.source_hero_id,
        entity_name=arguments.source_hero_name,
    )
    candidates = [
        row
        for row in candidates
        if row.get("context_level") == arguments.context_level
        and (
            arguments.include_peak_battles
            or not bool(row.get("is_peak_battle"))
        )
        and int(row.get("selection_count") or 0) >= arguments.min_selections
        and (arguments.side is None or row.get("response_side") == arguments.side)
        and (arguments.slot is None or int(row.get("response_slot") or 0) == arguments.slot)
        and (
            arguments.response_scope is None
            or row.get("response_scope") == arguments.response_scope
        )
    ]
    candidates.sort(
        key=lambda row: _sort_key(row, arguments.sort_by),
        reverse=True,
    )
    rows = [
        _normalize_relationship(arguments.relation, row)
        for row in candidates[: arguments.limit]
    ]
    return {
        "league_id": arguments.league_id,
        "artifact": filename,
        "artifact_version": snapshot.version.token,
        "relation": arguments.relation,
        "result_count": len(rows),
        "rows": rows,
        "warning": (
            "Historical associations do not establish causal gameplay counters."
        ),
    }

