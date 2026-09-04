from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.schemas import (
    ApiResponse,
    DraftSelectionCommentaryRequest,
    DraftSimulationRequest,
    HeroMatchupRecommendationRequest,
    LineupRecommendationRequest,
    LineupScoreRequest,
    NeutralLineupScoreRequest,
    UltimateCounterLineupRequest,
)
from app.services.draft_commentary import build_selection_commentary
from app.services.draft_simulator import (
    FIXED_ROLLOUTS,
    learned_feature_space,
    metadata,
    simulate,
)
from app.services.season_teams import validate_season_team_pair
from app.services.coach_rate_limit import CoachRateLimiter
from app.services.request_identity import client_key
from app.services.hero_matchup import recommend_heroes
from app.services.lineup_recommender import recommend_lineup
from app.services.lineup_value import load_lineup_value_model
from app.services.ultimate_lineup import optimize_counter_lineup, optimize_ultimate_lineups

router = APIRouter(prefix="/api/simulations", tags=["simulations"])


def _new_simulation_rate_limiter() -> CoachRateLimiter:
    settings = get_settings()
    return CoachRateLimiter(
        per_ip_per_minute=settings.simulation_ip_requests_per_minute,
        per_ip_per_day=settings.simulation_ip_requests_per_day,
        server_per_minute=settings.simulation_server_requests_per_minute,
        server_per_day=settings.simulation_server_requests_per_day,
        max_active_per_ip=settings.simulation_ip_max_active_requests,
        max_active_server=settings.simulation_server_max_active_requests,
    )


simulation_rate_limiter = _new_simulation_rate_limiter()


def _simulation_client_key(request: Request) -> str:
    return client_key(
        request,
        trust_proxy_headers=get_settings().simulation_trust_proxy_headers,
    )


@router.post("/commentary")
def selection_commentary(
    body: DraftSelectionCommentaryRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        state = body.model_dump(exclude={"league_id", "model_type", "seed", "selected_hero_id"})
        teams = validate_season_team_pair(
            db, body.league_id, body.blue_team_id, body.red_team_id
        )
        state.update(
            blue_team_name=str(teams["blue"]["team_name"]),
            red_team_name=str(teams["red"]["team_name"]),
        )
        return ApiResponse(data=build_selection_commentary(league_id=body.league_id, state=state, selected_hero_id=body.selected_hero_id, model_type=body.model_type))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/model")
def draft_model(league_id: str = Query(..., min_length=1, max_length=32)) -> ApiResponse:
    try:
        return ApiResponse(data=metadata(league_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/feature-space")
def feature_space(league_id: str = Query(..., min_length=1, max_length=32)) -> ApiResponse:
    try:
        return ApiResponse(data=learned_feature_space(league_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ultimate-lineups")
def ultimate_lineups(
    request: Request,
    league_id: str = Query(..., min_length=1, max_length=32),
) -> ApiResponse:
    """Compute team-neutral peak-duel lineup profiles from existing artifacts."""
    key = _simulation_client_key(request)
    decision = simulation_rate_limiter.acquire(key)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "simulation_rate_limited",
                "message": "The ultimate-lineup optimizer is busy. Try again shortly.",
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    try:
        return ApiResponse(data=optimize_ultimate_lineups(league_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        simulation_rate_limiter.release(key)


@router.post("/ultimate-lineups/counter")
def ultimate_counter_lineup(
    body: UltimateCounterLineupRequest,
    request: Request,
) -> ApiResponse:
    """Build a legal team-neutral counter after the Blue lineup is complete."""
    key = _simulation_client_key(request)
    decision = simulation_rate_limiter.acquire(key)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "simulation_rate_limited",
                "message": "The counter-lineup optimizer is busy. Try again shortly.",
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    try:
        return ApiResponse(
            data=optimize_counter_lineup(body.league_id, body.target_hero_ids)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        simulation_rate_limiter.release(key)


@router.post("/hero-matchup")
def hero_matchup(body: HeroMatchupRecommendationRequest) -> ApiResponse:
    """Recommend favorite-compatible heroes into one or more enemy picks."""
    try:
        space = learned_feature_space(body.league_id)
        return ApiResponse(
            data=recommend_heroes(
                body.league_id,
                space,
                body.favorite_hero_ids,
                body.opponent_hero_ids,
                body.preferred_lane,
                limit=body.limit,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft")
def draft_simulation(
    body: DraftSimulationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    key = _simulation_client_key(request)
    decision = simulation_rate_limiter.acquire(key)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "simulation_rate_limited",
                "message": "The simulator is busy. Try again shortly.",
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    state = body.model_dump(exclude={"league_id", "model_type", "seed"})
    try:
        teams = validate_season_team_pair(
            db,
            body.league_id,
            body.blue_team_id,
            body.red_team_id,
        )
        state.update(
            blue_team_name=teams["blue"]["team_name"],
            red_team_name=teams["red"]["team_name"],
        )
        return ApiResponse(
            data=simulate(
                body.league_id,
                state,
                FIXED_ROLLOUTS,
                body.seed,
                model_type=body.model_type,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        simulation_rate_limiter.release(key)


@router.post("/recommend-lineup")
def lineup_recommendation(
    body: LineupRecommendationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Route picks to lineup value and bans to opponent-denial value."""
    key = _simulation_client_key(request)
    decision = simulation_rate_limiter.acquire(key)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "simulation_rate_limited",
                "message": "The recommendation planner is busy. Try again shortly.",
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    state = body.model_dump(
        exclude={"league_id", "model_type", "seed", "top_k", "risk_mode"}
    )
    try:
        teams = validate_season_team_pair(
            db,
            body.league_id,
            body.blue_team_id,
            body.red_team_id,
        )
        state.update(
            blue_team_name=teams["blue"]["team_name"],
            red_team_name=teams["red"]["team_name"],
        )
        return ApiResponse(
            data=recommend_lineup(
                body.league_id,
                state,
                model_type=body.model_type,
                seed=body.seed,
                top_k=body.top_k,
                risk_mode=body.risk_mode,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        simulation_rate_limiter.release(key)


@router.post("/score-lineup")
def lineup_score(
    body: LineupScoreRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Score one completed 5v5 lineup comparison without BP rollouts."""
    key = _simulation_client_key(request)
    decision = simulation_rate_limiter.acquire(key)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "simulation_rate_limited",
                "message": "The lineup scorer is busy. Try again shortly.",
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    try:
        teams = validate_season_team_pair(
            db,
            body.league_id,
            body.blue_team_id,
            body.red_team_id,
        )
        model = load_lineup_value_model(body.league_id)
        result = model.score(
            body.blue_team_id,
            body.blue_hero_ids,
            body.red_team_id,
            body.red_hero_ids,
        )
        return ApiResponse(
            data={
                "league_id": body.league_id,
                "blue_team": {
                    "team_id": body.blue_team_id,
                    "team_name": str(teams["blue"]["team_name"]),
                    "hero_ids": body.blue_hero_ids,
                },
                "red_team": {
                    "team_id": body.red_team_id,
                    "team_name": str(teams["red"]["team_name"]),
                    "hero_ids": body.red_hero_ids,
                },
                **result,
                "value_model": {
                    "version": model.payload["version"],
                    "generated_at": model.payload.get("generated_at"),
                    "source": model.payload.get("source", {}),
                },
            }
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        simulation_rate_limiter.release(key)


@router.post("/score-neutral-lineup")
def neutral_lineup_score(
    body: NeutralLineupScoreRequest,
    request: Request,
) -> ApiResponse:
    """Score arbitrary 5v5 lineups with all team-specific effects neutralized."""
    key = _simulation_client_key(request)
    decision = simulation_rate_limiter.acquire(key)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "simulation_rate_limited",
                "message": "The neutral lineup scorer is busy. Try again shortly.",
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    try:
        model = load_lineup_value_model(body.league_id)
        result = model.score(
            "__neutral_blue__",
            body.blue_hero_ids,
            "__neutral_red__",
            body.red_hero_ids,
            team_neutral=True,
            allow_mirror_heroes=True,
        )
        return ApiResponse(
            data={
                "league_id": body.league_id,
                "blue_hero_ids": body.blue_hero_ids,
                "red_hero_ids": body.red_hero_ids,
                **result,
                "value_model": {
                    "version": model.payload["version"],
                    "generated_at": model.payload.get("generated_at"),
                    "source": model.payload.get("source", {}),
                },
            }
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        simulation_rate_limiter.release(key)
