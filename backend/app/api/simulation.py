from fastapi import APIRouter, HTTPException, Query

from app.schemas import ApiResponse, DraftSimulationRequest
from app.services.draft_simulator import learned_feature_space, metadata, simulate

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
def draft_simulation(body: DraftSimulationRequest) -> ApiResponse:
    state = body.model_dump(exclude={"league_id", "model_type", "rollouts", "seed"})
    try:
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
