"""Phase 2 team-aware tools backed by precomputed season artifacts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, PositiveInt, model_validator

from app.agent.artifact_cache import artifact_cache
from app.agent.tools.common import (
    LeagueArguments,
    entity_rows,
    index_rows_by_entity,
    normalize_name,
)


class TeamReferenceArguments(LeagueArguments):
    team_id: str | None = Field(default=None, max_length=32)
    team_name: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def require_team(self) -> "TeamReferenceArguments":
        if not self.team_id and not self.team_name:
            raise ValueError("Provide team_id or team_name")
        return self


class OpponentReferenceArguments(TeamReferenceArguments):
    opponent_team_id: str | None = Field(default=None, max_length=32)
    opponent_team_name: str | None = Field(default=None, max_length=64)


class GetTeamDraftTendenciesArguments(OpponentReferenceArguments):
    action: Literal["pick", "ban"] | None = None
    side: Literal["blue", "red"] | None = None
    team_action_type_number: int | None = Field(default=None, ge=1, le=10)
    sort_by: Literal["probability", "lift", "selections"] = "probability"
    limit: PositiveInt = Field(default=5, le=20)


class GetTeamOpeningSequencesArguments(OpponentReferenceArguments):
    side: Literal["blue", "red"] | None = None
    limit: PositiveInt = Field(default=5, le=15)


class GetTeamComboPerformanceArguments(OpponentReferenceArguments):
    hero_a_id: int | None = Field(default=None, gt=0)
    hero_a_name: str | None = Field(default=None, max_length=100)
    hero_b_id: int | None = Field(default=None, gt=0)
    hero_b_name: str | None = Field(default=None, max_length=100)
    side: Literal["blue", "red"] | None = None
    limit: PositiveInt = Field(default=10, le=30)

    @model_validator(mode="after")
    def validate_hero_pair(self) -> "GetTeamComboPerformanceArguments":
        first = self.hero_a_id is not None or bool(self.hero_a_name)
        second = self.hero_b_id is not None or bool(self.hero_b_name)
        if first != second:
            raise ValueError("Provide both heroes or neither hero")
        return self


class GetPlayerHeroPoolArguments(TeamReferenceArguments):
    player_name: str | None = Field(default=None, max_length=64)
    hero_id: int | None = Field(default=None, gt=0)
    hero_name: str | None = Field(default=None, max_length=100)
    sort_by: Literal["picks", "share", "win_rate"] = "picks"
    limit: PositiveInt = Field(default=10, le=30)


class GetRecentTeamTrendsArguments(TeamReferenceArguments):
    action: Literal["pick", "ban"] | None = None
    side: Literal["blue", "red"] | None = None
    team_action_type_number: int | None = Field(default=None, ge=1, le=10)
    sort_by: Literal["change", "probability", "selections"] = "change"
    limit: PositiveInt = Field(default=5, le=20)


def _rows_for_team(
    league_id: str,
    filename: str,
    team_id: str | None,
    team_name: str | None,
) -> tuple[list[dict[str, Any]], object]:
    index, snapshot = artifact_cache.get_index(
        league_id,
        filename,
        "team_id_name",
        lambda rows: index_rows_by_entity(
            rows,
            id_field="team_id",
            name_field="team_name",
        ),
    )
    return (
        entity_rows(
            index,
            entity_kind="team",
            entity_id=team_id,
            entity_name=team_name,
        ),
        snapshot,
    )


def _filter_opponent(
    rows: list[dict[str, Any]],
    opponent_id: str | None,
    opponent_name: str | None,
) -> list[dict[str, Any]]:
    if opponent_id is None and not opponent_name:
        return rows
    expected_name = normalize_name(opponent_name or "")
    filtered = [
        row
        for row in rows
        if (
            opponent_id is not None
            and str(row.get("opponent_team_id") or "") == opponent_id
        )
        or (
            opponent_name
            and normalize_name(str(row.get("opponent_team_name") or ""))
            == expected_name
        )
    ]
    if not filtered:
        raise LookupError(f"Unknown opponent: {opponent_id or opponent_name}")
    return filtered


def _context_level(
    *,
    side: str | None,
    slot: int | None = None,
    opponent: bool = False,
    combo: bool = False,
) -> str:
    if combo and opponent and side:
        return "side_opponent"
    if opponent:
        return "opponent_slot" if slot else "opponent"
    if slot:
        return "slot"
    if side:
        return "side"
    return "overall"


def _artifact_meta(snapshot: Any) -> dict[str, object]:
    return {
        "artifact": snapshot.filename,
        "artifact_version": snapshot.version.token,
        "cache_hit": snapshot.cache_hit,
    }


def get_team_draft_tendencies(
    arguments: GetTeamDraftTendenciesArguments,
) -> dict[str, Any]:
    rows, snapshot = _rows_for_team(
        arguments.league_id,
        "team_action_tendencies.jsonl",
        arguments.team_id,
        arguments.team_name,
    )
    has_opponent = bool(arguments.opponent_team_id or arguments.opponent_team_name)
    level = _context_level(
        side=arguments.side,
        slot=arguments.team_action_type_number,
        opponent=has_opponent,
    )
    rows = [row for row in rows if row.get("context_level") == level]
    rows = _filter_opponent(
        rows,
        arguments.opponent_team_id,
        arguments.opponent_team_name,
    )
    if arguments.side:
        rows = [row for row in rows if row.get("side") == arguments.side]
    if arguments.team_action_type_number:
        rows = [
            row
            for row in rows
            if int(row.get("team_action_type_number") or 0)
            == arguments.team_action_type_number
        ]
    if arguments.action:
        rows = [row for row in rows if row.get("action") == arguments.action]
    sort_field = {
        "probability": "smoothed_probability_given_legal",
        "lift": "smoothed_lift",
        "selections": "selection_count",
    }[arguments.sort_by]
    rows.sort(
        key=lambda row: (
            float(row.get(sort_field) or 0),
            int(row.get("selection_count") or 0),
        ),
        reverse=True,
    )
    selected = rows[: arguments.limit]
    if not selected:
        raise LookupError("No team tendency data matches this context")
    return {
        "league_id": arguments.league_id,
        "team_id": selected[0]["team_id"],
        "team_name": selected[0]["team_name"],
        "context_level": level,
        "rows": selected,
        "result_count": len(selected),
        **_artifact_meta(snapshot),
        "warning": (
            "These are smoothed historical selection tendencies conditional on "
            "legal availability, not win probabilities or guaranteed choices."
        ),
    }


def get_team_opening_sequences(
    arguments: GetTeamOpeningSequencesArguments,
) -> dict[str, Any]:
    rows, snapshot = _rows_for_team(
        arguments.league_id,
        "team_opening_sequences.jsonl",
        arguments.team_id,
        arguments.team_name,
    )
    has_opponent = bool(arguments.opponent_team_id or arguments.opponent_team_name)
    level = _context_level(side=arguments.side, opponent=has_opponent)
    rows = [row for row in rows if row.get("context_level") == level]
    rows = _filter_opponent(
        rows,
        arguments.opponent_team_id,
        arguments.opponent_team_name,
    )
    if arguments.side:
        rows = [row for row in rows if row.get("side") == arguments.side]
    rows.sort(
        key=lambda row: (
            int(row.get("occurrence_count") or 0),
            float(row.get("sequence_rate") or 0),
        ),
        reverse=True,
    )
    selected = rows[: arguments.limit]
    if not selected:
        raise LookupError("No opening sequence data matches this context")
    return {
        "league_id": arguments.league_id,
        "team_id": selected[0]["team_id"],
        "team_name": selected[0]["team_name"],
        "context_level": level,
        "rows": selected,
        "result_count": len(selected),
        **_artifact_meta(snapshot),
        "warning": "Opening sequences are descriptive historical frequencies.",
    }


def _hero_matches(row: dict[str, Any], hero_id: int | None, hero_name: str | None) -> bool:
    if hero_id is not None:
        return int(row.get("hero_id") or 0) == hero_id
    return normalize_name(str(row.get("hero_name") or "")) == normalize_name(hero_name or "")


def get_team_combo_performance(
    arguments: GetTeamComboPerformanceArguments,
) -> dict[str, Any]:
    rows, snapshot = _rows_for_team(
        arguments.league_id,
        "team_combo_performance.jsonl",
        arguments.team_id,
        arguments.team_name,
    )
    has_opponent = bool(arguments.opponent_team_id or arguments.opponent_team_name)
    level = _context_level(
        side=arguments.side,
        opponent=has_opponent,
        combo=True,
    )
    rows = [row for row in rows if row.get("context_level") == level]
    rows = _filter_opponent(
        rows,
        arguments.opponent_team_id,
        arguments.opponent_team_name,
    )
    if arguments.side:
        rows = [row for row in rows if row.get("side") == arguments.side]
    if arguments.hero_a_id is not None or arguments.hero_a_name:
        filtered = []
        for row in rows:
            first = {
                "hero_id": row.get("hero_a_id"),
                "hero_name": row.get("hero_a_name"),
            }
            second = {
                "hero_id": row.get("hero_b_id"),
                "hero_name": row.get("hero_b_name"),
            }
            direct = _hero_matches(first, arguments.hero_a_id, arguments.hero_a_name) and _hero_matches(
                second, arguments.hero_b_id, arguments.hero_b_name
            )
            reverse = _hero_matches(second, arguments.hero_a_id, arguments.hero_a_name) and _hero_matches(
                first, arguments.hero_b_id, arguments.hero_b_name
            )
            if direct or reverse:
                filtered.append(row)
        rows = filtered
    rows.sort(
        key=lambda row: (
            int(row.get("pair_battle_count") or 0),
            float(row.get("pair_battle_rate") or 0),
        ),
        reverse=True,
    )
    selected = rows[: arguments.limit]
    if not selected:
        raise LookupError("No team combination data matches this context")
    return {
        "league_id": arguments.league_id,
        "team_id": selected[0]["team_id"],
        "team_name": selected[0]["team_name"],
        "context_level": level,
        "rows": selected,
        "result_count": len(selected),
        **_artifact_meta(snapshot),
        "warning": (
            "Pair win rates are descriptive and may be unstable for small samples; "
            "they do not isolate the draft's causal effect."
        ),
    }


def get_player_hero_pool(arguments: GetPlayerHeroPoolArguments) -> dict[str, Any]:
    rows, snapshot = _rows_for_team(
        arguments.league_id,
        "player_hero_pools.jsonl",
        arguments.team_id,
        arguments.team_name,
    )
    if arguments.player_name:
        expected = normalize_name(arguments.player_name)
        rows = [
            row
            for row in rows
            if normalize_name(str(row.get("player_name") or "")) == expected
        ]
    if arguments.hero_id is not None or arguments.hero_name:
        rows = [
            row
            for row in rows
            if _hero_matches(row, arguments.hero_id, arguments.hero_name)
        ]
    sort_field = {
        "picks": "pick_count",
        "share": "pick_share",
        "win_rate": "descriptive_battle_win_rate",
    }[arguments.sort_by]
    rows.sort(key=lambda row: float(row.get(sort_field) or 0), reverse=True)
    selected = rows[: arguments.limit]
    if not selected:
        raise LookupError("No player hero-pool data matches this query")
    return {
        "league_id": arguments.league_id,
        "team_id": selected[0]["team_id"],
        "team_name": selected[0]["team_name"],
        "rows": selected,
        "result_count": len(selected),
        **_artifact_meta(snapshot),
        "warning": "Player hero pools describe recorded picks in this season only.",
    }


def get_recent_team_trends(arguments: GetRecentTeamTrendsArguments) -> dict[str, Any]:
    rows, snapshot = _rows_for_team(
        arguments.league_id,
        "team_recent_trends.jsonl",
        arguments.team_id,
        arguments.team_name,
    )
    level = _context_level(
        side=arguments.side,
        slot=arguments.team_action_type_number,
    )
    rows = [row for row in rows if row.get("context_level") == level]
    if arguments.side:
        rows = [row for row in rows if row.get("side") == arguments.side]
    if arguments.team_action_type_number:
        rows = [
            row
            for row in rows
            if int(row.get("team_action_type_number") or 0)
            == arguments.team_action_type_number
        ]
    if arguments.action:
        rows = [row for row in rows if row.get("action") == arguments.action]
    sort_field = {
        "change": "probability_change_vs_season",
        "probability": "smoothed_probability_given_legal",
        "selections": "selection_count",
    }[arguments.sort_by]
    rows.sort(key=lambda row: float(row.get(sort_field) or 0), reverse=True)
    selected = rows[: arguments.limit]
    if not selected:
        raise LookupError("No recent team trend data matches this context")
    return {
        "league_id": arguments.league_id,
        "team_id": selected[0]["team_id"],
        "team_name": selected[0]["team_name"],
        "recent_match_window": selected[0].get("recent_match_window"),
        "context_level": level,
        "rows": selected,
        "result_count": len(selected),
        **_artifact_meta(snapshot),
        "warning": (
            "Recent changes compare the last recorded matches with the full season "
            "and may reflect small samples."
        ),
    }
