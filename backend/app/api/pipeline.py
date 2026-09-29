from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import League
from app.schemas import AnalysisRunRequest, ApiResponse
from app.services.pipeline_jobs import enqueue_job

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/run", status_code=202)
def run_pipeline(
    body: AnalysisRunRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    league = db.scalar(
        select(League).where(League.league_id == body.league_id)
    )
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")
    return ApiResponse(message=f"{body.step} queued", data=enqueue_job(
        db, "analysis", body.league_id, {"step": body.step}))


@router.post("/publish", status_code=202)
def publish_frontend_assets(
    body: AnalysisRunRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Write the selected season's browser-ready files from local analysis."""
    league = db.scalar(select(League).where(League.league_id == body.league_id))
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")
    return ApiResponse(message="frontend asset publication queued", data=enqueue_job(
        db, "publish", body.league_id))
