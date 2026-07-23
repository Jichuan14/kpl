from typing import Literal

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    success: bool = True
    message: str = "ok"
    data: object | None = None


class LeagueOut(BaseModel):
    league_id: str
    league_name: str
    league_type: str = ""
    year: int | None = None
    season: int | None = None
    status: int = 0

    model_config = {"from_attributes": True}


class HeroBpStatOut(BaseModel):
    league_id: str
    hero_id: int
    hero_name: str
    hero_icon: str = ""
    battle_count: int
    ban_count: int
    pick_count: int
    win_count: int
    ban_rate: float
    pick_rate: float
    presence_rate: float
    win_rate: float

    model_config = {"from_attributes": True}


class SyncLeagueRequest(BaseModel):
    league_id: str | None = Field(
        default=None,
        description="If omitted, sync the latest league from the official API list.",
    )
    match_limit: int | None = Field(
        default=None,
        description="Optional cap on finished matches to deep-sync (BP detail). Useful for testing.",
    )
    recompute_stats: bool = True
    run_analysis: bool = True
    incremental: bool = Field(
        default=True,
        description=(
            "When true, refresh the match list but download battle/BP details "
            "only for finished matches that do not yet have complete battle data. "
            "Set false for an explicit full repair/backfill."
        ),
    )


class AnalysisRunRequest(BaseModel):
    league_id: str = Field(min_length=1, max_length=32)
    step: Literal[
        "export",
        "decisions",
        "statistics",
        "meta",
        "team_synergy",
        "draft_model",
        "all",
    ] = "all"


class DraftSimulationRequest(BaseModel):
    league_id: str = Field(min_length=1, max_length=32)
    bp_order: int = Field(ge=1, le=20)
    blue_picks: list[int] = Field(default_factory=list)
    red_picks: list[int] = Field(default_factory=list)
    blue_bans: list[int] = Field(default_factory=list)
    red_bans: list[int] = Field(default_factory=list)
    blue_used_previous_battles: list[int] = Field(default_factory=list)
    red_used_previous_battles: list[int] = Field(default_factory=list)
    legal_hero_ids: list[int] | None = None
    rollouts: int = Field(default=100, ge=100, le=5000)
    seed: int | None = None
