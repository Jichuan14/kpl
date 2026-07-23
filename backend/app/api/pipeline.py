from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import League
from app.schemas import AnalysisRunRequest, ApiResponse
from app.services.analysis_pipeline import AnalysisPipeline

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/run")
def run_pipeline(
    body: AnalysisRunRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    league = db.scalar(
        select(League).where(League.league_id == body.league_id)
    )
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")
    try:
        result = AnalysisPipeline(body.league_id).run(body.step)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message=f"{body.step} completed", data=result)
