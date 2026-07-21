from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import League
from app.schemas import ApiResponse, LeagueOut
from app.services.sync import SyncService

router = APIRouter(prefix="/api/leagues", tags=["leagues"])


@router.get("")
def list_leagues(db: Session = Depends(get_db)) -> ApiResponse:
    rows = db.scalars(
        select(League).order_by(League.year.desc(), League.season.desc(), League.id.desc())
    ).all()
    return ApiResponse(data=[LeagueOut.model_validate(r).model_dump() for r in rows])


@router.get("/latest")
def latest_league(db: Session = Depends(get_db)) -> ApiResponse:
    row = db.scalar(
        select(League).order_by(League.year.desc(), League.season.desc(), League.id.desc())
    )
    if not row:
        # Pull once from official API if DB empty
        sync = SyncService(db)
        try:
            sync.sync_leagues()
        finally:
            sync.close()
        row = db.scalar(
            select(League).order_by(League.year.desc(), League.season.desc(), League.id.desc())
        )
    if not row:
        return ApiResponse(success=False, message="No leagues found", data=None)
    return ApiResponse(data=LeagueOut.model_validate(row).model_dump())
