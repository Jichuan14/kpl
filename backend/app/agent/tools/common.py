"""Shared validation and entity helpers for agent tools."""

from typing import Any

from pydantic import BaseModel, Field


class LeagueArguments(BaseModel):
    model_config = {"extra": "forbid"}

    league_id: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


def normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def index_rows_by_entity(
    rows: tuple[dict[str, Any], ...],
    *,
    id_field: str,
    name_field: str,
) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        entity_id = str(row.get(id_field) or "")
        entity_name = str(row.get(name_field) or "")
        keys = []
        if entity_id:
            keys.append(f"id:{entity_id}")
        if entity_name:
            keys.append(f"name:{normalize_name(entity_name)}")
        for key in keys:
            index.setdefault(key, []).append(row)
    return index


def entity_rows(
    index: dict[str, list[dict[str, Any]]],
    *,
    entity_kind: str,
    entity_id: int | str | None,
    entity_name: str | None,
) -> list[dict[str, Any]]:
    key = (
        f"id:{entity_id}"
        if entity_id is not None
        else f"name:{normalize_name(entity_name or '')}"
    )
    rows = index.get(key, [])
    if not rows:
        reference = entity_id if entity_id is not None else entity_name
        raise LookupError(f"Unknown {entity_kind}: {reference}")
    return rows
