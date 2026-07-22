from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import League
from app.schemas import AnalysisRunRequest, ApiResponse
from app.services.analysis_pipeline import AnalysisPipeline

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.get("/report/{league_id}")
def open_report(league_id: str) -> FileResponse:
    try:
        report_path = AnalysisPipeline(league_id).report_path
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not report_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Generate this season's report first",
        )
    return FileResponse(
        report_path,
        media_type="text/html",
        filename=None,
    )


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
