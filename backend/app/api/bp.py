from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BattleBp, HeroBpStats, League
from app.schemas import ApiResponse, HeroBpStatOut
from app.services.bp_stats import recompute_hero_bp_stats

router = APIRouter(prefix="/api/bp", tags=["bp"])


def _resolve_league_id(db: Session, league_id: str | None) -> str | None:
    if league_id:
        return league_id
    latest = db.scalar(
        select(League).order_by(League.year.desc(), League.season.desc(), League.id.desc())
    )
    return latest.league_id if latest else None


@router.get("/heroes")
def hero_bp_stats(
    league_id: str | None = None,
    sort: str = Query(default="presence", pattern="^(presence|ban|pick|win)$"),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> ApiResponse:
    lid = _resolve_league_id(db, league_id)
    if not lid:
        return ApiResponse(success=False, message="No league available. Sync first.", data=[])

    sort_col = {
        "presence": HeroBpStats.presence_rate,
        "ban": HeroBpStats.ban_rate,
        "pick": HeroBpStats.pick_rate,
        "win": HeroBpStats.win_rate,
    }[sort]

    rows = db.scalars(
        select(HeroBpStats)
        .where(HeroBpStats.league_id == lid)
        .order_by(sort_col.desc())
        .limit(limit)
    ).all()

    return ApiResponse(
        data={
            "league_id": lid,
            "sort": sort,
            "heroes": [HeroBpStatOut.model_validate(r).model_dump() for r in rows],
        }
    )


@router.get("/battles/{battle_id}")
def battle_bp(battle_id: str, db: Session = Depends(get_db)) -> ApiResponse:
    rows = db.scalars(
        select(BattleBp)
        .where(BattleBp.battle_id == battle_id)
        .order_by(BattleBp.bp_order.asc())
    ).all()
    return ApiResponse(
        data=[
            {
                "battle_id": r.battle_id,
                "league_id": r.league_id,
                "camp": r.camp,
                "action_type": r.action_type,
                "action": "ban" if r.action_type == 0 else "pick",
                "hero_id": r.hero_id,
                "hero_name": r.hero_name,
                "hero_icon": r.hero_icon,
                "position": r.position,
                "bp_order": r.bp_order,
            }
            for r in rows
        ]
    )


@router.post("/recompute")
def recompute(league_id: str | None = None, db: Session = Depends(get_db)) -> ApiResponse:
    lid = _resolve_league_id(db, league_id)
    if not lid:
        return ApiResponse(success=False, message="No league available", data=None)
    result = recompute_hero_bp_stats(db, lid)
    return ApiResponse(data=result)
