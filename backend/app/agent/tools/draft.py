"""Validated agent tools backed by the season draft models."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, PositiveInt

from app.services.draft_simulator import (
    FIXED_ROLLOUTS,
    load_model,
    predict_next_action,
    simulate,
)

HeroId = Annotated[int, Field(gt=0)]


class DraftStateArguments(BaseModel):
    """Shared validated board state for draft inference tools."""

    model_config = {"extra": "forbid"}

    league_id: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    model_type: Literal["stats", "learnable"] = "stats"
    blue_team_id: str = Field(min_length=1, max_length=32)
    blue_team_name: str = Field(min_length=1, max_length=64)
    red_team_id: str = Field(min_length=1, max_length=32)
    red_team_name: str = Field(min_length=1, max_length=64)
    bp_order: int = Field(ge=1, le=20)
    blue_picks: list[HeroId] = Field(default_factory=list)
    red_picks: list[HeroId] = Field(default_factory=list)
    blue_bans: list[HeroId] = Field(default_factory=list)
    red_bans: list[HeroId] = Field(default_factory=list)
    blue_used_previous_battles: list[HeroId] = Field(default_factory=list)
    red_used_previous_battles: list[HeroId] = Field(default_factory=list)
    legal_hero_ids: list[HeroId] | None = None

    def draft_state(self) -> dict[str, Any]:
        return {
            "bp_order": self.bp_order,
            "blue_team_id": self.blue_team_id,
            "blue_team_name": self.blue_team_name,
            "red_team_id": self.red_team_id,
            "red_team_name": self.red_team_name,
            "blue_picks": list(self.blue_picks),
            "red_picks": list(self.red_picks),
            "blue_bans": list(self.blue_bans),
            "red_bans": list(self.red_bans),
            "blue_used_previous_battles": list(
                self.blue_used_previous_battles
            ),
            "red_used_previous_battles": list(self.red_used_previous_battles),
            "legal_hero_ids": (
                list(self.legal_hero_ids)
                if self.legal_hero_ids is not None
                else None
            ),
        }


class PredictNextDraftActionArguments(DraftStateArguments):
    """Inputs Kimi may supply for one next-action prediction."""

    limit: PositiveInt = Field(default=5, le=20)


class SimulateFutureDraftArguments(DraftStateArguments):
    """Inputs for bounded marginal rollouts over upcoming BP actions."""

    horizon: int = Field(default=3, ge=1, le=20)
    choices_per_action: int = Field(default=5, ge=1, le=8)
    seed: int | None = None


def predict_next_draft_action(
    arguments: PredictNextDraftActionArguments,
) -> dict[str, Any]:
    """Return ranked legal candidates for the current BP action."""
    return predict_next_action(
        arguments.league_id,
        arguments.draft_state(),
        model_type=arguments.model_type,
        limit=int(arguments.limit),
    )


def simulate_future_draft(
    arguments: SimulateFutureDraftArguments,
) -> dict[str, Any]:
    """Return bounded marginal distributions for upcoming BP actions."""
    result = simulate(
        arguments.league_id,
        arguments.draft_state(),
        FIXED_ROLLOUTS,
        arguments.seed,
        model_type=arguments.model_type,
        max_actions=arguments.horizon,
    )
    model = load_model(arguments.league_id)
    steps_by_order = {
        int(step["bp_order"]): step for step in model["draft_sequence"]
    }
    future_actions = []
    for order_text, candidates in result["simulation"]["next_actions"].items():
        order = int(order_text)
        step = steps_by_order[order]
        future_actions.append(
            {
                "bp_order": order,
                "side": step["side"],
                "action": step["action"],
                "team_action_type_number": int(
                    step["team_action_type_number"]
                ),
                "candidates": candidates[: arguments.choices_per_action],
            }
        )
    future_actions.sort(key=lambda action: action["bp_order"])
    return {
        "league_id": arguments.league_id,
        "model_generated_at": result["model_generated_at"],
        "model_type": result["model_type"],
        "model_label": result["model_label"],
        "rollouts": FIXED_ROLLOUTS,
        "requested_horizon": arguments.horizon,
        "actions_simulated": len(future_actions),
        "next_step": result["next_step"],
        "next_action_probabilities": result["next_action_probabilities"][:
            arguments.choices_per_action
        ],
        "future_actions": future_actions,
        "banned_in_horizon": result["simulation"]["banned_by_end"][:
            arguments.choices_per_action
        ],
        "result_count": len(future_actions),
        "warning": (
            "Future-action probabilities are marginal historical rollout "
            "frequencies, not one guaranteed sequence or battle-win probabilities."
        ),
    }
