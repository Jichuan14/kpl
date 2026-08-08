from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ApiResponse(BaseModel):
    success: bool = True
    message: str = "ok"
    data: object | None = None


class CoachLimitsUpdate(BaseModel):
    """Runtime limits for the process-local Draft Coach limiter."""

    ip_requests_per_minute: int = Field(ge=1, le=10_000)
    ip_requests_per_day: int = Field(ge=1, le=1_000_000)
    server_requests_per_minute: int = Field(ge=1, le=100_000)
    server_requests_per_day: int = Field(ge=1, le=10_000_000)
    ip_max_active_requests: int = Field(ge=1, le=1_000)
    server_max_active_requests: int = Field(ge=1, le=10_000)


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
        "team_profiles",
        "draft_model",
        "learnable_draft_model",
        "all",
    ] = "all"


class DraftSimulationRequest(BaseModel):
    league_id: str = Field(min_length=1, max_length=32)
    model_type: Literal["stats", "learnable"] = "stats"
    blue_team_id: str = Field(min_length=1, max_length=32)
    blue_team_name: str = Field(min_length=1, max_length=64)
    red_team_id: str = Field(min_length=1, max_length=32)
    red_team_name: str = Field(min_length=1, max_length=64)
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

    @model_validator(mode="after")
    def validate_distinct_teams(self) -> "DraftSimulationRequest":
        if self.blue_team_id == self.red_team_id:
            raise ValueError("Blue and Red must be different teams")
        return self


class DraftSelectionCommentaryRequest(DraftSimulationRequest):
    """The board immediately before a selected pick or ban."""

    action: Literal["pick", "ban"]
    side: Literal["blue", "red"]
    selected_hero_id: int = Field(gt=0)
