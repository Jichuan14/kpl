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
