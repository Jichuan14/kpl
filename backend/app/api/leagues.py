from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
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
from app.services.public_requests import limit_live_requests, limit_public_writes, public_visitor
from sqlalchemy.dialects.sqlite import insert

router = APIRouter(prefix="/api/leagues", tags=["leagues"])
live_match_service = LiveMatchService()


def require_fixture(db: Session, league_id: str, match_id: str, team_a_id: str, team_b_id: str) -> Match:
    match = db.scalar(select(Match).where(Match.league_id == league_id, Match.match_id == match_id))
    if match is None:
        raise HTTPException(404, detail="Scheduled match not found")
    if {team_a_id, team_b_id} != {match.camp1_team_id, match.camp2_team_id} or team_a_id == team_b_id:
        raise HTTPException(422, detail="Selected teams do not match the scheduled fixture")
    return match


@router.get("/daily-matches")
def daily_matches(
    match_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> ApiResponse:
    """Return every locally scheduled KPL fixture for a China-calendar day."""
    match_date = match_date if isinstance(match_date, date) else None
    china_day = (
        match_date.isoformat()
        if match_date is not None
        else datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    )
    # Direct service calls do not receive FastAPI's resolved defaults.
    start_date = start_date if isinstance(start_date, date) else None
    end_date = end_date if isinstance(end_date, date) else None
    if (start_date is None) != (end_date is None):
        raise HTTPException(422, detail="Both calendar range dates are required")
    if start_date is not None:
        if match_date is not None or end_date == date.max or not 0 <= (end_date - start_date).days <= 16:
            raise HTTPException(422, detail="Calendar range must be ordered and at most 17 days")
        condition = (Match.start_time >= f"{start_date.isoformat()} 00:00:00") & (
            Match.start_time < f"{(end_date + timedelta(days=1)).isoformat()} 00:00:00")
    else:
        condition = Match.start_time.like(f"{china_day}%")
    rows = db.execute(
        select(Match, League.league_name)
        .outerjoin(League, League.league_id == Match.league_id)
        .where(condition)
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
def list_leagues(db: Session = Depends(get_db), factual: bool = Query(default=False)) -> ApiResponse:
    if factual is True:
        from app.services.factual_seasons import factual_seasons
        from app.services.static_publisher import DATA_ROOT
        return ApiResponse(data=factual_seasons(db, DATA_ROOT))
    rows = db.scalars(
        select(League).order_by(League.year.desc(), League.season.desc(), League.id.desc())
    ).all()
    return ApiResponse(data=[LeagueOut.model_validate(r).model_dump() for r in rows])


class SiteDefaultRequest(BaseModel):
    league_id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")


@router.put("/site-default")
def update_site_default(body: SiteDefaultRequest, db: Session = Depends(get_db)) -> ApiResponse:
    """Management write; production's private league catch-all requires auth."""
    from app.services.site_settings import save_default_league
    try:
        return ApiResponse(data=save_default_league(db, body.league_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    # Upcoming fixtures must include teams that have not played this season yet.
    # The observed-player roster remains separate from the schedule catalogue.
    fixture = (
        next_scheduled_match(
            db,
            league_id,
        )
        if next_only is True
        else current_or_next_scheduled_match(
            db,
            league_id,
        )
    )
    if fixture is not None:
        fixture["fixture_status"] = "scheduled"
        fixture["is_live"] = False
    return ApiResponse(data=fixture)


@router.get("/{league_id}/live-match", dependencies=[Depends(limit_live_requests)])
def live_match(
    league_id: str,
    team_a_id: str = Query(min_length=1, max_length=32),
    team_b_id: str = Query(min_length=1, max_length=32),
    match_id: str = Query(min_length=1, max_length=32),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Return disposable live BP context without writing to the database."""
    try:
        require_fixture(db, league_id, match_id, team_a_id, team_b_id)
        state = live_match_service.get_match_state(
            league_id, team_a_id, team_b_id, match_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=state)


@router.post("/{league_id}/live-match/refresh", dependencies=[Depends(limit_live_requests)])
def refresh_live_match(
    league_id: str,
    team_a_id: str = Query(min_length=1, max_length=32),
    team_b_id: str = Query(min_length=1, max_length=32),
    match_id: str = Query(min_length=1, max_length=32),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Request a read-only live refresh, rate-limited by the in-memory cache."""
    try:
        require_fixture(db, league_id, match_id, team_a_id, team_b_id)
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


def validate_prediction_window(match: Match, body: LiveWinnerPredictionRequest, league_id: str) -> None:
    if match.status == 2 or match.win_camp in (1, 2):
        raise HTTPException(409, detail="Predictions are closed for this match")
    if body.best_of is not None and body.best_of != match.bo:
        raise HTTPException(422, detail="Best-of does not match the scheduled fixture")
    if body.game_number == 0:
        from app.services.sync import _china_time
        kickoff = _china_time(match.start_time)
        if match.status == 1 or kickoff is None or datetime.now(ZoneInfo("Asia/Shanghai")) >= kickoff:
            raise HTTPException(409, detail="Pre-match predictions are closed")
    else:
        state = live_match_service.get_match_state(league_id, body.team_a_id, body.team_b_id, body.match_id)
        if not state.get("is_live") or state.get("current_game") != body.game_number:
            raise HTTPException(409, detail="Predictions are closed for this game")


@router.post("/{league_id}/live-match/predictions", dependencies=[Depends(limit_public_writes)])
def save_live_winner_prediction(
    league_id: str,
    body: LiveWinnerPredictionRequest,
    db: Session = Depends(get_db),
    visitor_hash: str = Depends(public_visitor),
) -> ApiResponse:
    """Save one anonymous prediction and return the public totals.

    A vote is deliberately immutable for the game so a visitor cannot revise
    their prediction after seeing new match information.
    """
    match = require_fixture(db, league_id, body.match_id, body.team_a_id, body.team_b_id)
    prediction = db.scalar(
        select(LiveMatchWinnerPrediction).where(
            LiveMatchWinnerPrediction.league_id == league_id,
            LiveMatchWinnerPrediction.match_id == body.match_id,
            LiveMatchWinnerPrediction.game_number == body.game_number,
            LiveMatchWinnerPrediction.visitor_hash == visitor_hash,
        )
    )
    created = prediction is None
    if prediction is None:
        validate_prediction_window(match, body, league_id)
        values = dict(
            league_id=league_id,
            match_id=body.match_id,
            game_number=body.game_number,
            visitor_hash=visitor_hash,
            winner_team_id=body.winner_team_id,
            best_of=body.best_of,
            team_a_score=body.team_a_score,
            team_b_score=body.team_b_score,
        )
        # Concurrent duplicate requests preserve the first committed vote.
        created = db.execute(insert(LiveMatchWinnerPrediction).values(**values).on_conflict_do_nothing(
            index_elements=["match_id", "game_number", "visitor_hash"])).rowcount == 1
        db.commit()
        prediction = db.scalar(select(LiveMatchWinnerPrediction).where(
            LiveMatchWinnerPrediction.match_id == body.match_id,
            LiveMatchWinnerPrediction.game_number == body.game_number,
            LiveMatchWinnerPrediction.visitor_hash == visitor_hash))
    elif (
        body.game_number == 0
        and prediction.winner_team_id == body.winner_team_id
        and prediction.team_a_score is None
        and prediction.team_b_score is None
    ):
        validate_prediction_window(match, body, league_id)
        # Let predictions made before exact scores were introduced be completed
        # once, without allowing the original winner pick to change.
        db.execute(update(LiveMatchWinnerPrediction).where(
            LiveMatchWinnerPrediction.id == prediction.id,
            LiveMatchWinnerPrediction.team_a_score.is_(None),
            LiveMatchWinnerPrediction.team_b_score.is_(None),
        ).values(best_of=body.best_of, team_a_score=body.team_a_score, team_b_score=body.team_b_score))
        db.commit()
        db.refresh(prediction)
    totals = _winner_prediction_totals(db, league_id, body.match_id, body.game_number)
    totals["your_winner_team_id"] = prediction.winner_team_id
    totals["your_best_of"] = prediction.best_of
    totals["your_team_a_score"] = prediction.team_a_score
    totals["your_team_b_score"] = prediction.team_b_score
    return ApiResponse(
        message="winner prediction saved" if created else "winner prediction already saved",
        data=totals,
    )
