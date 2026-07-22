from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ApiResponse

router = APIRouter(prefix="/api/data", tags=["data"])

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPORT_DIR = REPO_ROOT / "analysis" / "exports"
OUTPUT_DIR = REPO_ROOT / "analysis" / "outputs"

STAT_ARTIFACTS = (
    ("ban_response", "ban_response_stats.jsonl"),
    ("pick_synergy", "pick_synergy_stats.jsonl"),
    ("counter_pick", "counter_pick_stats.jsonl"),
    ("counter_ban", "counter_ban_stats.jsonl"),
)


def count_rows(
    db: Session,
    table: str,
    league_id: str | None = None,
    extra_where: str = "",
) -> int:
    where_parts: list[str] = []
    params: dict[str, Any] = {}
    if league_id is not None:
        where_parts.append("league_id = :league_id")
        params["league_id"] = league_id
    if extra_where:
        where_parts.append(extra_where)
    where = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
    value = db.execute(text(f"SELECT COUNT(*) FROM {table}{where}"), params).scalar()
    return int(value or 0)


def latest_value(db: Session, table: str, column: str, league_id: str) -> str | None:
    value = db.execute(
        text(
            f"SELECT MAX({column}) FROM {table} "
            "WHERE league_id = :league_id"
        ),
        {"league_id": league_id},
    ).scalar()
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def artifact(path: Path, key: str, label: str) -> dict[str, Any]:
    exists = path.is_file()
    records = 0
    if exists and path.suffix == ".jsonl":
        with path.open("rb") as source:
            records = sum(1 for line in source if line.strip())
    stat = path.stat() if exists else None
    return {
        "key": key,
        "label": label,
        "path": str(path.relative_to(REPO_ROOT)),
        "exists": exists,
        "ready": exists,
        "records": records,
        "bytes": stat.st_size if stat else 0,
        "updated_at": (
            datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat()
            if stat
            else None
        ),
    }


@router.get("/status")
def data_status(
    league_id: str = Query(..., min_length=1, max_length=32),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Summarize downloaded DB rows and local JSONL processing artifacts."""
    if not all(character.isalnum() or character in "-_" for character in league_id):
        raise HTTPException(status_code=400, detail="Invalid league_id")

    tables = set(inspect(db.get_bind()).get_table_names())
    league = db.execute(
        text(
            "SELECT league_name, year, season, status FROM leagues "
            "WHERE league_id = :league_id"
        ),
        {"league_id": league_id},
    ).mappings().first()
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")

    hero_sources = (
        "SELECT hero_id FROM battle_bps "
        "WHERE league_id = :league_id AND hero_id > 0"
    )
    if "battle_players" in tables:
        hero_sources += (
            " UNION SELECT hero_id FROM battle_players "
            "WHERE league_id = :league_id AND hero_id > 0"
        )
    heroes_used = int(
        db.execute(
            text(f"SELECT COUNT(*) FROM ({hero_sources}) AS league_heroes"),
            {"league_id": league_id},
        ).scalar()
        or 0
    )
    heroes_missing = (
        int(
            db.execute(
                text(
                    "SELECT COUNT(*) FROM "
                    f"({hero_sources}) AS league_heroes "
                    "LEFT JOIN heroes USING (hero_id) "
                    "WHERE heroes.hero_id IS NULL"
                ),
                {"league_id": league_id},
            ).scalar()
            or 0
        )
        if "heroes" in tables
        else heroes_used
    )

    counts = {
        "matches": count_rows(db, "matches", league_id),
        "finished_matches": count_rows(
            db, "matches", league_id, "status = 2"
        ),
        "battles": count_rows(db, "battles", league_id),
        "bp_actions": count_rows(db, "battle_bps", league_id),
        "hero_stats": count_rows(db, "hero_bp_stats", league_id),
        "battle_players": (
            count_rows(db, "battle_players", league_id)
            if "battle_players" in tables
            else 0
        ),
        "teams": (
            int(
                db.execute(
                    text(
                        "SELECT COUNT(DISTINCT team_id) FROM battle_players "
                        "WHERE league_id = :league_id AND team_id != ''"
                    ),
                    {"league_id": league_id},
                ).scalar()
                or 0
            )
            if "battle_players" in tables
            else 0
        ),
        "players": (
            int(
                db.execute(
                    text(
                        "SELECT COUNT(DISTINCT player_name || ':' || team_id) "
                        "FROM battle_players "
                        "WHERE league_id = :league_id AND player_name != ''"
                    ),
                    {"league_id": league_id},
                ).scalar()
                or 0
            )
            if "battle_players" in tables
            else 0
        ),
        "heroes": count_rows(db, "heroes") if "heroes" in tables else 0,
        "heroes_used": heroes_used,
        "heroes_missing": heroes_missing,
    }

    league_export_dir = EXPORT_DIR / league_id
    league_output_dir = OUTPUT_DIR / league_id
    exports = [
        artifact(
            league_export_dir / "matches.jsonl",
            "match_export",
            "Match export",
        ),
        artifact(
            league_export_dir / "bp_decisions.jsonl",
            "bp_decisions",
            "BP decision states",
        ),
    ]
    match_export = exports[0]
    decisions = exports[1]
    match_export_path = league_export_dir / "matches.jsonl"
    decisions_path = league_export_dir / "bp_decisions.jsonl"
    decisions["ready"] = bool(
        match_export_path.is_file()
        and decisions_path.is_file()
        and decisions_path.stat().st_mtime >= match_export_path.stat().st_mtime
    )
    statistics = [
        artifact(
            league_output_dir / filename,
            key,
            label.replace("_", " ").title(),
        )
        for key, filename in STAT_ARTIFACTS
        for label in (key,)
    ]
    decision_mtime = (
        decisions_path.stat().st_mtime
        if decisions["ready"]
        else None
    )
    for item, (_, filename) in zip(statistics, STAT_ARTIFACTS, strict=True):
        path = league_output_dir / filename
        item["ready"] = bool(
            decision_mtime is not None
            and path.is_file()
            and path.stat().st_mtime >= decision_mtime
        )
    statistics_ready = all(item["ready"] for item in statistics)
    meta = artifact(
        league_output_dir / "meta_hero_stats.jsonl",
        "meta_heroes",
        "Opening meta heroes",
    )
    meta_path = league_output_dir / "meta_hero_stats.jsonl"
    meta["ready"] = bool(
        decision_mtime is not None
        and meta_path.is_file()
        and meta_path.stat().st_mtime >= decision_mtime
    )
    team_synergy = artifact(
        league_output_dir / "team_synergy_stats.jsonl",
        "team_synergy",
        "Team hero synergies",
    )
    team_synergy_path = league_output_dir / "team_synergy_stats.jsonl"
    team_synergy["ready"] = bool(
        decision_mtime is not None
        and team_synergy_path.is_file()
        and team_synergy_path.stat().st_mtime >= decision_mtime
    )
    pipeline = [
        {
            "key": "download",
            "label": "KPL API download",
            "ready": counts["bp_actions"] > 0,
            "detail": f'{counts["battles"]:,} battles · {counts["bp_actions"]:,} BP actions',
        },
        {
            "key": "players",
            "label": "Team and player mapping",
            "ready": counts["battle_players"] > 0,
            "detail": (
                f'{counts["teams"]:,} teams · {counts["players"]:,} players · '
                f'{counts["battle_players"]:,} battle-player rows'
            ),
        },
        {
            "key": "matches_jsonl",
            "label": "Match JSONL export",
            "ready": match_export["exists"],
            "detail": f'{match_export["records"]:,} match records',
        },
        {
            "key": "decisions_jsonl",
            "label": "Pre-action decision JSONL",
            "ready": decisions["ready"],
            "detail": f'{decisions["records"]:,} decision records',
        },
        {
            "key": "statistics",
            "label": "Relationship statistics",
            "ready": statistics_ready,
            "detail": (
                f'{sum(item["records"] for item in statistics):,} statistical rows'
            ),
        },
        {
            "key": "meta",
            "label": "Opening meta heroes",
            "ready": meta["ready"],
            "detail": f'{meta["records"]:,} ranked heroes',
        },
        {
            "key": "team_synergy",
            "label": "Team hero synergies",
            "ready": team_synergy["ready"],
            "detail": f'{team_synergy["records"]:,} team-specific pairs',
        },
    ]

    return ApiResponse(
        data={
            "league": {
                "league_id": league_id,
                "league_name": league["league_name"],
                "year": league["year"],
                "season": league["season"],
                "status": league["status"],
            },
            "counts": counts,
            "freshness": {
                "matches": latest_value(db, "matches", "updated_at", league_id),
                "battles": latest_value(db, "battles", "updated_at", league_id),
                "hero_stats": latest_value(
                    db, "hero_bp_stats", "updated_at", league_id
                ),
            },
            "pipeline": pipeline,
            "artifacts": {
                "exports": exports,
                "statistics": statistics,
                "meta": meta,
                "team_synergy": team_synergy,
            },
            "processing_note": (
                "JSONL processing preserves questionable source rows and marks "
                "them with quality flags; it does not silently delete them."
            ),
        }
    )
