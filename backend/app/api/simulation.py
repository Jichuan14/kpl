from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ApiResponse, DraftSimulationRequest
from app.services.draft_simulator import learned_feature_space, metadata, simulate
from app.services.season_teams import validate_season_team_pair

router = APIRouter(prefix="/api/simulations", tags=["simulations"])


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


@router.post("/draft")
def draft_simulation(
    body: DraftSimulationRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    state = body.model_dump(exclude={"league_id", "model_type", "rollouts", "seed"})
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
                body.rollouts,
                body.seed,
                model_type=body.model_type,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
