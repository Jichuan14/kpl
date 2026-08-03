"""Season meta and aggregate hero BP tools."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select

from app.agent.artifact_cache import artifact_cache
from app.agent.tools.common import LeagueArguments, normalize_name
from app.database import SessionLocal
from app.models import HeroBpStats

META_FILE = "meta_hero_stats.jsonl"


class GetMetaHeroesArguments(LeagueArguments):
    hero_id: int | None = Field(default=None, gt=0)
    hero_name: str | None = Field(default=None, min_length=1, max_length=100)
    min_battles: int = Field(default=1, ge=1, le=10000)
    sort_by: Literal["priority", "opening_ban", "blue_first_pick"] = "priority"
    limit: int = Field(default=10, ge=1, le=50)


class GetHeroBpStatsArguments(LeagueArguments):
    hero_id: int | None = Field(default=None, gt=0)
    hero_name: str | None = Field(default=None, min_length=1, max_length=100)
    min_battles: int = Field(default=1, ge=1, le=10000)
    sort_by: Literal["presence", "ban", "pick", "win"] = "presence"
    limit: int = Field(default=10, ge=1, le=50)


def _matches_requested_hero(
    row: dict[str, Any],
    hero_id: int | None,
    hero_name: str | None,
) -> bool:
    if hero_id is not None:
        return int(row.get("hero_id") or 0) == hero_id
    if hero_name:
        return normalize_name(str(row.get("hero_name") or "")) == normalize_name(
            hero_name
        )
    return True


def get_meta_heroes(arguments: GetMetaHeroesArguments) -> dict[str, Any]:
    snapshot = artifact_cache.load(arguments.league_id, META_FILE)
    candidates = [
        row
        for row in snapshot.rows
        if int(row.get("eligible_battle_count") or 0) >= arguments.min_battles
        and _matches_requested_hero(row, arguments.hero_id, arguments.hero_name)
    ]
    sort_fields = {
        "priority": ("early_priority_rate", "early_priority_count"),
        "opening_ban": ("opening_ban_rate", "opening_ban_count"),
        "blue_first_pick": (
            "blue_first_pick_rate_given_legal",
            "blue_first_pick_count",
        ),
    }[arguments.sort_by]
    candidates.sort(
        key=lambda row: (
            float(row.get(sort_fields[0]) or 0.0),
            int(row.get(sort_fields[1]) or 0),
        ),
        reverse=True,
    )
    rows = [dict(row) for row in candidates[: arguments.limit]]
    return {
        "league_id": arguments.league_id,
        "artifact": META_FILE,
        "artifact_version": snapshot.version.token,
        "result_count": len(rows),
        "rows": rows,
        "warning": "Meta priority combines opening bans and Blue first picks.",
    }


def _hero_bp_sort_value(row: HeroBpStats, sort_by: str) -> float:
    return float(
        {
            "presence": row.presence_rate,
            "ban": row.ban_rate,
            "pick": row.pick_rate,
            "win": row.win_rate,
        }[sort_by]
    )


def get_hero_bp_stats(arguments: GetHeroBpStatsArguments) -> dict[str, Any]:
    with SessionLocal() as db:
        records = db.scalars(
            select(HeroBpStats).where(HeroBpStats.league_id == arguments.league_id)
        ).all()
    records = [
        row
        for row in records
        if int(row.battle_count or 0) >= arguments.min_battles
        and (
            arguments.hero_id is None
            or int(row.hero_id) == arguments.hero_id
        )
        and (
            not arguments.hero_name
            or normalize_name(row.hero_name) == normalize_name(arguments.hero_name)
        )
    ]
    if (arguments.hero_id is not None or arguments.hero_name) and not records:
        reference = arguments.hero_id or arguments.hero_name
        raise LookupError(f"Unknown hero statistics: {reference}")
    records.sort(
        key=lambda row: _hero_bp_sort_value(row, arguments.sort_by),
        reverse=True,
    )
    selected = records[: arguments.limit]
    rows = [
        {
            "hero_id": int(row.hero_id),
            "hero_name": row.hero_name,
            "battle_count": int(row.battle_count),
            "ban_count": int(row.ban_count),
            "pick_count": int(row.pick_count),
            "win_count": int(row.win_count),
            "ban_rate": float(row.ban_rate),
            "pick_rate": float(row.pick_rate),
            "presence_rate": float(row.presence_rate),
            "descriptive_win_rate": float(row.win_rate),
        }
        for row in selected
    ]
    updated_values: list[datetime] = [
        row.updated_at for row in selected if row.updated_at is not None
    ]
    return {
        "league_id": arguments.league_id,
        "source": "sqlite:hero_bp_stats",
        "source_updated_at": (
            max(updated_values).isoformat() if updated_values else None
        ),
        "result_count": len(rows),
        "rows": rows,
        "warning": "Hero win rates are descriptive and do not establish causality.",
    }

