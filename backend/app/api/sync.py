from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ApiResponse, SyncLeagueRequest
from app.services.pipeline_jobs import enqueue_job

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/leagues", status_code=202)
def sync_leagues(db: Session = Depends(get_db)) -> ApiResponse:
    return ApiResponse(message="league catalog queued", data=enqueue_job(db, "sync_leagues"))


@router.post("/league-bp", status_code=202)
def sync_league_bp(body: SyncLeagueRequest, db: Session = Depends(get_db)) -> ApiResponse:
    """Refresh a league; normal calls download detail only for new matches."""
    return ApiResponse(message="league BP sync queued", data=enqueue_job(
        db, "sync_bp", body.league_id, body.model_dump()))
