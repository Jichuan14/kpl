from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ApiResponse, SyncLeagueRequest
from app.services.analysis_pipeline import AnalysisPipeline
from app.services.sync import SyncService

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/leagues")
def sync_leagues(db: Session = Depends(get_db)) -> ApiResponse:
    sync = SyncService(db)
    try:
        result = sync.sync_leagues()
        return ApiResponse(message="leagues synced", data=result)
    finally:
        sync.close()


@router.post("/league-bp")
def sync_league_bp(body: SyncLeagueRequest, db: Session = Depends(get_db)) -> ApiResponse:
    """Pull complete match, battle, BP, team, player, and hero data."""
    sync = SyncService(db)
    try:
        result = sync.sync_league_bp(
            league_id=body.league_id,
            match_limit=body.match_limit,
            recompute_stats=body.recompute_stats,
        )
        if body.run_analysis:
            try:
                result["analysis"] = AnalysisPipeline(
                    result["league_id"]
                ).run("all")
            except (ValueError, RuntimeError) as exc:
                result["analysis_error"] = str(exc)
        return ApiResponse(message="league BP synced", data=result)
    finally:
        sync.close()
