from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import League
from app.schemas import ApiResponse, LeagueOut
from app.services.sync import SyncService
from app.services.season_teams import (
    current_or_next_scheduled_match,
    list_season_teams,
    next_scheduled_match,
)
from app.services.live_match import LiveMatchService

router = APIRouter(prefix="/api/leagues", tags=["leagues"])
live_match_service = LiveMatchService()


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
    next_only: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Return local catalogue timing for the current or next fixture.

    This intentionally does not call the official KPL API. The browser uses
    this database timestamp to defer its first live check until five minutes
    after the scheduled start.
    """
    if not db.scalar(select(League.id).where(League.league_id == league_id)):
        raise HTTPException(status_code=404, detail="League not found")
    teams = list_season_teams(db, league_id)
    selectable_team_ids = {str(team["team_id"]) for team in teams}
    fixture = (
        next_scheduled_match(
            db,
            league_id,
            selectable_team_ids=selectable_team_ids,
        )
        if next_only
        else current_or_next_scheduled_match(
            db,
            league_id,
            selectable_team_ids=selectable_team_ids,
        )
    )
    if fixture is not None:
        fixture["fixture_status"] = "scheduled"
        fixture["is_live"] = False
    return ApiResponse(data=fixture)


@router.get("/{league_id}/live-match")
def live_match(
    league_id: str,
    team_a_id: str = Query(min_length=1, max_length=32),
    team_b_id: str = Query(min_length=1, max_length=32),
) -> ApiResponse:
    """Return disposable live BP context without writing to the database."""
    try:
        state = live_match_service.get_match_state(league_id, team_a_id, team_b_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=state)


@router.post("/{league_id}/live-match/refresh")
def refresh_live_match(
    league_id: str,
    team_a_id: str = Query(min_length=1, max_length=32),
    team_b_id: str = Query(min_length=1, max_length=32),
) -> ApiResponse:
    """Request a read-only live refresh, rate-limited by the in-memory cache."""
    try:
        state = live_match_service.refresh_match_state(league_id, team_a_id, team_b_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=state)
