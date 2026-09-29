from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import League, PipelineJob
from app.schemas import ApiResponse
from app.services.pipeline_jobs import china_day_key, enqueue_job, job_data

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class LeagueJobRequest(BaseModel):
    league_id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")


class ScheduledJobRequest(BaseModel):
    league_id: str | None = Field(default=None, min_length=1, max_length=32,
                                  pattern=r"^[A-Za-z0-9_-]+$")


def require_league(db: Session, league_id: str) -> None:
    if not db.scalar(select(League).where(League.league_id == league_id)):
        raise HTTPException(status_code=404, detail="League not found")


@router.post("/scheduled", status_code=202)
def schedule_refresh(body: ScheduledJobRequest, db: Session = Depends(get_db)) -> ApiResponse:
    return ApiResponse(message="scheduled refresh queued", data=enqueue_job(
        db, "scheduled", body.league_id, {"auto": body.league_id is None},
        idempotency_key=china_day_key(body.league_id)))


@router.post("/full-update", status_code=202)
def full_update(body: LeagueJobRequest, db: Session = Depends(get_db)) -> ApiResponse:
    require_league(db, body.league_id)
    return ApiResponse(message="full update queued", data=enqueue_job(
        db, "full_update", body.league_id))


@router.get("")
def list_jobs(league_id: str | None = Query(default=None), limit: int = Query(default=20, ge=1, le=100),
              db: Session = Depends(get_db)) -> ApiResponse:
    statement = select(PipelineJob)
    if league_id:
        statement = statement.where(PipelineJob.league_id == league_id)
    jobs = db.scalars(statement.order_by(
        case((PipelineJob.status.in_(["pending", "running"]), 0), else_=1),
        PipelineJob.created_at.desc(),
    ).limit(limit)).all()
    return ApiResponse(data=[job_data(job, include_result=False) for job in jobs])


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)) -> ApiResponse:
    job = db.get(PipelineJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return ApiResponse(data=job_data(job))
