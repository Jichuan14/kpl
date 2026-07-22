import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Hero, League
from app.schemas import ApiResponse
from app.services.analysis_pipeline import OUTPUT_ROOT

router = APIRouter(prefix="/api/visualization", tags=["visualization"])

STAT_FILES = {
    "ban_response": "ban_response_stats.jsonl",
    "pick_synergy": "pick_synergy_stats.jsonl",
    "counter_pick": "counter_pick_stats.jsonl",
    "counter_ban": "counter_ban_stats.jsonl",
}

SCOPE_LABELS = {
    "opponent_next_ban": "Opponent's next ban",
    "banning_team_later_pick": "Banning team's later pick",
    "opponent_later_pick": "Opponent's later pick",
}


def statistics_ready(league_id: str) -> bool:
    directory = OUTPUT_ROOT / league_id
    return all((directory / filename).is_file() for filename in STAT_FILES.values())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def normalize_row(
    relation: str,
    row: dict[str, Any],
    hero_icons: dict[int, str],
) -> dict[str, Any]:
    if relation == "ban_response":
        source_id = int(row.get("trigger_hero_id") or 0)
        source_name = row.get("trigger_hero_name") or str(source_id)
        target_id = int(row.get("response_hero_id") or 0)
        target_name = row.get("response_hero_name") or str(target_id)
        response_action = row.get("response_action") or ""
        side = row.get("response_side")
        slot = row.get("response_slot")
        trigger_side = row.get("trigger_side")
        trigger_slot = row.get("trigger_slot")
        win_rate = row.get("response_team_battle_win_rate")
        scope = row.get("response_scope") or ""
        scope_label = SCOPE_LABELS.get(scope, scope)
        context = (
            scope_label
            if row.get("context_level") == "overall"
            else (
                f"{scope_label} · after {trigger_side or 'unknown'} ban "
                f"{trigger_slot or '?'} · {side or 'unknown'} "
                f"{response_action} {slot or '?'}"
            )
        )
    else:
        source_id = int(
            row.get("ally_hero_id") or row.get("opponent_hero_id") or 0
        )
        source_name = (
            row.get("ally_hero_name")
            or row.get("opponent_hero_name")
            or str(source_id)
        )
        target_id = int(row.get("candidate_hero_id") or 0)
        target_name = row.get("candidate_hero_name") or str(target_id)
        response_action = row.get("response_action") or ""
        side = row.get("response_side")
        slot = row.get("response_slot")
        win_rate = row.get("battle_win_rate_when_selected")
        scope = ""
        context = (
            "All sides and slots"
            if row.get("context_level") == "overall"
            else f"{side or 'unknown'} {response_action} {slot or '?'}"
        )

    if relation == "pick_synergy":
        relationship = f"{source_name} + {target_name}"
    elif relation == "counter_pick":
        relationship = f"vs {source_name} → pick {target_name}"
    elif relation == "counter_ban":
        relationship = f"vs {source_name} → ban {target_name}"
    else:
        relationship = f"{source_name} → {response_action} {target_name}"

    return {
        "relation": relation,
        "context_level": row.get("context_level") or "overall",
        "is_peak_battle": bool(row.get("is_peak_battle")),
        "source_hero_id": source_id,
        "source_hero_name": source_name,
        "source_hero_icon": hero_icons.get(source_id, ""),
        "target_hero_id": target_id,
        "target_hero_name": target_name,
        "target_hero_icon": hero_icons.get(target_id, ""),
        "relationship": relationship,
        "response_action": response_action,
        "response_scope": scope,
        "side": side,
        "slot": slot,
        "context_description": context,
        "context_count": int(
            row.get("context_decision_count")
            or row.get("trigger_event_count")
            or 0
        ),
        "opportunities": int(row.get("legal_opportunity_count") or 0),
        "selections": int(row.get("selection_count") or 0),
        "availability_rate": as_float(row.get("availability_rate")),
        "raw_probability": as_float(
            row.get("raw_probability_given_legal")
        ),
        "smoothed_probability": as_float(
            row.get("smoothed_probability_given_legal")
        ),
        "baseline_probability": as_float(
            row.get("baseline_probability_given_legal")
        ),
        "smoothed_lift": as_float(row.get("smoothed_lift")),
        "ci_low": as_float(row.get("probability_ci95_low")),
        "ci_high": as_float(row.get("probability_ci95_high")),
        "win_rate": as_float(win_rate),
        "legal_overrides": int(row.get("legal_override_count") or 0),
        "flagged_selections": int(
            row.get("quality_flagged_selection_count") or 0
        ),
    }


@router.get("/seasons")
def available_seasons(db: Session = Depends(get_db)) -> ApiResponse:
    leagues = db.scalars(
        select(League).order_by(
            League.year.desc(),
            League.season.desc(),
            League.id.desc(),
        )
    ).all()
    rows = [
        {
            "league_id": league.league_id,
            "league_name": league.league_name,
            "year": league.year,
            "season": league.season,
            "status": league.status,
        }
        for league in leagues
        if statistics_ready(league.league_id)
    ]
    return ApiResponse(data=rows)


@router.get("/patterns")
def visualization_patterns(
    league_id: str = Query(..., min_length=1, max_length=32),
    min_selections: int = Query(2, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> ApiResponse:
    if not statistics_ready(league_id):
        raise HTTPException(
            status_code=404,
            detail="Statistics have not been generated for this season",
        )
    league = db.scalar(select(League).where(League.league_id == league_id))
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")

    hero_icons = {
        int(hero_id): icon or ""
        for hero_id, icon in db.execute(select(Hero.hero_id, Hero.hero_icon))
    }
    rows: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    output_dir = OUTPUT_ROOT / league_id
    latest_mtime = 0.0
    for relation, filename in STAT_FILES.items():
        path = output_dir / filename
        source_rows = read_jsonl(path)
        source_counts[relation] = len(source_rows)
        latest_mtime = max(latest_mtime, path.stat().st_mtime)
        rows.extend(
            normalize_row(relation, row, hero_icons)
            for row in source_rows
            if int(row.get("selection_count") or 0) >= min_selections
        )

    return ApiResponse(
        data={
            "league": {
                "league_id": league.league_id,
                "league_name": league.league_name,
                "year": league.year,
                "season": league.season,
            },
            "rows": rows,
            "source_counts": source_counts,
            "generated_at": (
                datetime.fromtimestamp(latest_mtime).astimezone().isoformat()
            ),
        }
    )
