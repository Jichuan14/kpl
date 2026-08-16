from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import League
from app.schemas import ApiResponse, LeagueOut
from app.services.sync import SyncService
from app.services.season_teams import list_season_teams, next_scheduled_match

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


@router.get("/{league_id}/teams")
def season_teams(
    league_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """List valid selectable teams for exactly one competition season."""
    if not db.scalar(select(League.id).where(League.league_id == league_id)):
        raise HTTPException(status_code=404, detail="League not found")
    rows = list_season_teams(db, league_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No teams found for this season")
    return ApiResponse(data=rows)


@router.get("/{league_id}/upcoming-match")
def upcoming_match(
    league_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Return the nearest scheduled, selectable fixture in China Standard Time."""
    if not db.scalar(select(League.id).where(League.league_id == league_id)):
        raise HTTPException(status_code=404, detail="League not found")
    teams = list_season_teams(db, league_id)
    fixture = next_scheduled_match(
        db,
        league_id,
        selectable_team_ids={str(team["team_id"]) for team in teams},
    )
    return ApiResponse(data=fixture)
