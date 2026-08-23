from hashlib import sha256
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import League, LiveMatchWinnerPrediction, Match
from app.schemas import ApiResponse, LeagueOut, LiveWinnerPredictionRequest
from app.services.sync import SyncService
from app.services.season_teams import (
    current_or_next_scheduled_match,
    list_season_teams,
    next_scheduled_match,
)
from app.services.live_match import LiveMatchService

router = APIRouter(prefix="/api/leagues", tags=["leagues"])
live_match_service = LiveMatchService()


@router.get("/daily-matches")
def daily_matches(db: Session = Depends(get_db)) -> ApiResponse:
    """Return every locally scheduled KPL fixture for the current China day."""
    china_day = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    rows = db.execute(
        select(Match, League.league_name)
        .outerjoin(League, League.league_id == Match.league_id)
        .where(Match.start_time.like(f"{china_day}%"))
        .order_by(Match.start_time.asc(), Match.match_id.asc())
    ).all()
    return ApiResponse(
        data={
            "date": china_day,
            "timezone": "Asia/Shanghai",
            "matches": [
                {
                    "league_id": match.league_id,
                    "league_name": league_name or match.league_id,
                    "match_id": match.match_id,
                    "start_time": match.start_time,
                    "bo": int(match.bo or 0),
                    "teams": [
                        {"team_id": match.camp1_team_id, "team_name": match.camp1_team_name},
                        {"team_id": match.camp2_team_id, "team_name": match.camp2_team_name},
                    ],
                }
                for match, league_name in rows
            ],
        }
    )


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
    match_id: str = Query(min_length=1, max_length=32),
) -> ApiResponse:
    """Return disposable live BP context without writing to the database."""
    try:
        state = live_match_service.get_match_state(
            league_id, team_a_id, team_b_id, match_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=state)


@router.post("/{league_id}/live-match/refresh")
def refresh_live_match(
    league_id: str,
    team_a_id: str = Query(min_length=1, max_length=32),
    team_b_id: str = Query(min_length=1, max_length=32),
    match_id: str = Query(min_length=1, max_length=32),
) -> ApiResponse:
    """Request a read-only live refresh, rate-limited by the in-memory cache."""
    try:
        state = live_match_service.refresh_match_state(
            league_id, team_a_id, team_b_id, match_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=state)


def _winner_prediction_totals(
    db: Session, league_id: str, match_id: str, game_number: int
) -> dict[str, object]:
    rows = db.execute(
        select(
            LiveMatchWinnerPrediction.winner_team_id,
            func.count(LiveMatchWinnerPrediction.id),
        )
        .where(
            LiveMatchWinnerPrediction.league_id == league_id,
            LiveMatchWinnerPrediction.match_id == match_id,
            LiveMatchWinnerPrediction.game_number == game_number,
        )
        .group_by(LiveMatchWinnerPrediction.winner_team_id)
    ).all()
    votes_by_team = {str(team_id): int(count) for team_id, count in rows}
    return {
        "match_id": match_id,
        "game_number": game_number,
        "total_votes": sum(votes_by_team.values()),
        "votes_by_team": votes_by_team,
    }


@router.get("/{league_id}/live-match/predictions")
def live_winner_predictions(
    league_id: str,
    match_id: str = Query(min_length=1, max_length=32),
    game_number: int = Query(ge=0, le=7),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Return anonymous public winner-prediction totals for one live game."""
    return ApiResponse(data=_winner_prediction_totals(db, league_id, match_id, game_number))


@router.post("/{league_id}/live-match/predictions")
def save_live_winner_prediction(
    league_id: str,
    body: LiveWinnerPredictionRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Save one anonymous prediction and return the public totals.

    A vote is deliberately immutable for the game so a visitor cannot revise
    their prediction after seeing new match information.
    """
    visitor_hash = sha256(str(body.visitor_id).encode()).hexdigest()
    prediction = db.scalar(
        select(LiveMatchWinnerPrediction).where(
            LiveMatchWinnerPrediction.match_id == body.match_id,
            LiveMatchWinnerPrediction.game_number == body.game_number,
            LiveMatchWinnerPrediction.visitor_hash == visitor_hash,
        )
    )
    created = prediction is None
    if prediction is None:
        prediction = LiveMatchWinnerPrediction(
            league_id=league_id,
            match_id=body.match_id,
            game_number=body.game_number,
            visitor_hash=visitor_hash,
            winner_team_id=body.winner_team_id,
        )
        db.add(prediction)
        db.commit()
    totals = _winner_prediction_totals(db, league_id, body.match_id, body.game_number)
    totals["your_winner_team_id"] = prediction.winner_team_id
    return ApiResponse(
        message="winner prediction saved" if created else "winner prediction already saved",
        data=totals,
    )
