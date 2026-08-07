"""Safe registration and dispatch for language-model-requested tools."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from app.agent.tools.battles import (
    GetBattleDraftArguments,
    get_battle_draft,
)
from app.agent.tools.draft import (
    PredictNextDraftActionArguments,
    SimulateFutureDraftArguments,
    predict_next_draft_action,
    simulate_future_draft,
)
from app.agent.tools.meta import (
    GetHeroBpStatsArguments,
    GetMetaHeroesArguments,
    get_hero_bp_stats,
    get_meta_heroes,
)
from app.agent.tools.relationships import (
    GetHeroRelationshipsArguments,
    get_hero_relationships,
)
from app.agent.tools.teams import (
    GetTeamSynergiesArguments,
    get_team_synergies,
)
from app.agent.tools.team_profiles import (
    GetPlayerHeroPoolArguments,
    GetRecentTeamTrendsArguments,
    GetTeamComboPerformanceArguments,
    GetTeamDraftTendenciesArguments,
    GetTeamOpeningSequencesArguments,
    get_player_hero_pool,
    get_recent_team_trends,
    get_team_combo_performance,
    get_team_draft_tendencies,
    get_team_opening_sequences,
)

logger = logging.getLogger(__name__)

ToolHandler = Callable[[BaseModel], dict[str, Any]]


class UnknownAgentToolError(ValueError):
    """Raised when a model requests a tool outside the approved registry."""


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: ToolHandler

    def model_definition(self) -> dict[str, Any]:
        """Return an OpenAI-compatible function-tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments_model.model_json_schema(),
            },
        }


TOOLS: dict[str, RegisteredTool] = {
    "get_team_draft_tendencies": RegisteredTool(
        name="get_team_draft_tendencies",
        description=(
            "Retrieve a team's season selection tendencies, optionally by Blue/Red "
            "side, pick/ban slot, or opponent. Probabilities are conditioned on a "
            "hero being legal and are not win probabilities."
        ),
        arguments_model=GetTeamDraftTendenciesArguments,
        handler=get_team_draft_tendencies,
    ),
    "get_team_opening_sequences": RegisteredTool(
        name="get_team_opening_sequences",
        description=(
            "Retrieve a team's most frequent first three BP actions, optionally "
            "filtered by Blue/Red side or opponent."
        ),
        arguments_model=GetTeamOpeningSequencesArguments,
        handler=get_team_opening_sequences,
    ),
    "get_team_combo_performance": RegisteredTool(
        name="get_team_combo_performance",
        description=(
            "Retrieve team-specific hero-pair frequency and descriptive battle-win "
            "rate, optionally by side or opponent. Use for a named pair or top pairs."
        ),
        arguments_model=GetTeamComboPerformanceArguments,
        handler=get_team_combo_performance,
    ),
    "get_player_hero_pool": RegisteredTool(
        name="get_player_hero_pool",
        description=(
            "Retrieve one named player's recorded season hero pool, optionally "
            "filtered to a team or hero. If the player name matches multiple teams, "
            "use the returned candidate teams to ask for clarification."
        ),
        arguments_model=GetPlayerHeroPoolArguments,
        handler=get_player_hero_pool,
    ),
    "get_recent_team_trends": RegisteredTool(
        name="get_recent_team_trends",
        description=(
            "Compare a team's last five recorded matches with its full-season pick "
            "or ban tendencies, optionally by side or action slot."
        ),
        arguments_model=GetRecentTeamTrendsArguments,
        handler=get_recent_team_trends,
    ),
    "predict_next_draft_action": RegisteredTool(
        name="predict_next_draft_action",
        description=(
            "Predict the most historically likely legal hero choices for the "
            "current BP action. When the supplied board includes selected Blue "
            "and Red teams, the result applies confidence-weighted tendencies "
            "for the acting team. Use for next pick, next ban, or top choices. "
            "Probabilities are selections, not win probabilities."
        ),
        arguments_model=PredictNextDraftActionArguments,
        handler=predict_next_draft_action,
    ),
    "simulate_future_draft": RegisteredTool(
        name="simulate_future_draft",
        description=(
            "Run bounded historical rollouts for the next one to twenty BP "
            "actions with the supplied Blue/Red team context. Use for questions "
            "about several upcoming picks or bans. "
            "Results are marginal action frequencies, not a guaranteed "
            "sequence, optimal strategy, or battle-win probability."
        ),
        arguments_model=SimulateFutureDraftArguments,
        handler=simulate_future_draft,
    ),
    "get_hero_relationships": RegisteredTool(
        name="get_hero_relationships",
        description=(
            "Retrieve league-wide historical pick synergy, counter-pick, "
            "counter-ban, or ban-response evidence for one source hero. Use "
            "this as the only tool for league-wide questions asking what is "
            "commonly paired with a hero. These associations do not prove "
            "causal gameplay counters."
        ),
        arguments_model=GetHeroRelationshipsArguments,
        handler=get_hero_relationships,
    ),
    "get_team_synergies": RegisteredTool(
        name="get_team_synergies",
        description=(
            "Retrieve one explicitly named team's season-wide historical "
            "hero-pair completion statistics. Requires team_name. "
            "Use only when the user asks about a specific team. Never use for "
            "a league-wide 'what pairs with this hero' question; use "
            "get_hero_relationships with pick_synergy for that instead."
        ),
        arguments_model=GetTeamSynergiesArguments,
        handler=get_team_synergies,
    ),
    "get_meta_heroes": RegisteredTool(
        name="get_meta_heroes",
        description=(
            "Retrieve season opening-priority hero statistics based on opening "
            "bans and Blue first picks."
        ),
        arguments_model=GetMetaHeroesArguments,
        handler=get_meta_heroes,
    ),
    "get_hero_bp_stats": RegisteredTool(
        name="get_hero_bp_stats",
        description=(
            "Retrieve season-wide hero pick, ban, presence, and descriptive "
            "battle-win aggregates from SQLite."
        ),
        arguments_model=GetHeroBpStatsArguments,
        handler=get_hero_bp_stats,
    ),
    "get_battle_draft": RegisteredTool(
        name="get_battle_draft",
        description=(
            "Retrieve the complete recorded pick/ban sequence for one exact "
            "battle ID in the selected season. Use for questions about BP "
            "order, a specific step, prior actions, or which side handled a "
            "hero. A resolvable battle ID is required."
        ),
        arguments_model=GetBattleDraftArguments,
        handler=get_battle_draft,
    ),
}


def available_tool_definitions() -> list[dict[str, Any]]:
    """Return stable model-facing definitions for every approved tool."""
    return [tool.model_definition() for tool in TOOLS.values()]


def invoke_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    request_id: str = "",
) -> dict[str, Any]:
    """Validate and execute one approved tool call with structured logging."""
    tool = TOOLS.get(name)
    if tool is None:
        logger.warning(
            "agent_tool_rejected",
            extra={"request_id": request_id, "tool_name": name},
        )
        raise UnknownAgentToolError(f"Unknown agent tool: {name}")

    started = perf_counter()
    try:
        validated = tool.arguments_model.model_validate(arguments)
        result = tool.handler(validated)
    except ValidationError:
        logger.warning(
            "agent_tool_invalid_arguments",
            extra={
                "request_id": request_id,
                "tool_name": name,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            },
        )
        raise
    except LookupError:
        logger.warning(
            "agent_tool_no_data",
            extra={
                "request_id": request_id,
                "tool_name": name,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            },
        )
        raise
    except Exception:
        logger.exception(
            "agent_tool_failed",
            extra={
                "request_id": request_id,
                "tool_name": name,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            },
        )
        raise

    logger.info(
        "agent_tool_completed",
        extra={
            "request_id": request_id,
            "tool_name": name,
            "league_id": getattr(validated, "league_id", ""),
            "model_type": getattr(validated, "model_type", ""),
            "bp_order": getattr(validated, "bp_order", None),
            "duration_ms": round((perf_counter() - started) * 1000, 3),
            "result_count": int(
                result.get("result_count")
                or result.get("candidate_count")
                or 0
            ),
        },
    )
    return result
