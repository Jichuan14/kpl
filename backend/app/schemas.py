from typing import Literal
from uuid import UUID

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


class VisitorTrackRequest(BaseModel):
    """A browser-local anonymous ID and the public route it viewed."""

    visitor_id: UUID
    page_path: str = Field(min_length=1, max_length=120, pattern=r"^/[^?#]*$")


class LiveWinnerPredictionRequest(BaseModel):
    """A browser's final prediction for one currently followed game."""

    model_config = {"extra": "forbid"}

    visitor_id: UUID
    match_id: str = Field(min_length=1, max_length=32)
    # Zero denotes a pre-match series-winner prediction; 1–7 are individual games.
    game_number: int = Field(ge=0, le=7)
    team_a_id: str = Field(min_length=1, max_length=32)
    team_b_id: str = Field(min_length=1, max_length=32)
    winner_team_id: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_winner_is_in_match(self) -> "LiveWinnerPredictionRequest":
        if self.team_a_id == self.team_b_id:
            raise ValueError("A prediction requires two different teams")
        if self.winner_team_id not in {self.team_a_id, self.team_b_id}:
            raise ValueError("The predicted winner must be one of the match teams")
        return self


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
        "power_rankings",
        "draft_model",
        "learnable_draft_model",
        "sequence_draft_model",
        "ban_value_model",
        "lineup_value_model",
        "display",
        "all",
    ] = "all"


class DraftSimulationRequest(BaseModel):
    # Reject, rather than silently ignore, client attempts to supply a
    # compute-affecting field such as ``rollouts``.
    model_config = {"extra": "forbid"}

    league_id: str = Field(min_length=1, max_length=32)
    model_type: Literal["stats", "learnable", "sequence"] = "stats"
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
    seed: int | None = None

    @model_validator(mode="after")
    def validate_distinct_teams(self) -> "DraftSimulationRequest":
        if self.blue_team_id == self.red_team_id:
            raise ValueError("Blue and Red must be different teams")
        return self


class LineupRecommendationRequest(DraftSimulationRequest):
    """A live draft state plus presentation preferences for ranked actions."""

    top_k: int = Field(default=3, ge=1, le=5)
    risk_mode: Literal["safe", "balanced", "upside"] = "balanced"


class HeroMatchupRecommendationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    league_id: str = Field(min_length=1, max_length=32)
    favorite_hero_ids: list[int] = Field(default_factory=list, max_length=12)
    opponent_hero_ids: list[int] = Field(min_length=1, max_length=5)
    preferred_lane: Literal["clash", "mid", "jungle", "farm", "roam"] | None = None
    limit: int = Field(default=6, ge=1, le=24)

    @model_validator(mode="after")
    def validate_heroes(self) -> "HeroMatchupRecommendationRequest":
        if len(self.favorite_hero_ids) != len(set(self.favorite_hero_ids)):
            raise ValueError("Favorite heroes must be unique")
        if len(self.opponent_hero_ids) != len(set(self.opponent_hero_ids)):
            raise ValueError("Opponent heroes must be unique")
        if set(self.favorite_hero_ids).intersection(self.opponent_hero_ids):
            raise ValueError("A favorite hero cannot also be an opponent pick")
        return self


class DraftSelectionCommentaryRequest(DraftSimulationRequest):
    """The board immediately before a selected pick or ban."""

    action: Literal["pick", "ban"]
    side: Literal["blue", "red"]
    selected_hero_id: int = Field(gt=0)
