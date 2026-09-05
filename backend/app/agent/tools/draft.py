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
    model_type: Literal["stats", "learnable", "sequence"] = "stats"
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
    unavailable_hero_names: list[str] = Field(default_factory=list, max_length=10)
    start_at_next_pick: bool = False
    target_side: Literal["blue", "red"] | None = None
    combination_size: int = Field(default=0, ge=0, le=5)


def hero_names_in_message(league_id: str, message: str) -> list[str]:
    """Return exact known hero names mentioned in one hypothetical request."""
    normalized = "".join(message.casefold().split())
    model = load_model(league_id)
    matches = {
        str(name)
        for name in model.get("hero_names", {}).values()
        if len(str(name).strip()) >= 2
        and "".join(str(name).casefold().split()) in normalized
    }
    return sorted(matches, key=lambda name: (-len(name), name))


def _resolve_hero_ids(model: dict[str, Any], names: list[str]) -> list[int]:
    by_name = {
        "".join(str(name).casefold().split()): int(hero_id)
        for hero_id, name in model.get("hero_names", {}).items()
    }
    resolved: list[int] = []
    for name in names:
        key = "".join(name.casefold().split())
        if key not in by_name:
            raise LookupError(f"Unknown hero in hypothetical draft: {name}")
        if by_name[key] not in resolved:
            resolved.append(by_name[key])
    return resolved


def _next_pick_order(
    model: dict[str, Any],
    current_order: int,
    target_side: str | None,
) -> int:
    candidates = [
        step
        for step in model["draft_sequence"]
        if int(step["bp_order"]) >= current_order
        and step["action"] == "pick"
        and (target_side is None or step["side"] == target_side)
    ]
    if not candidates:
        raise ValueError("No remaining pick is available for this hypothetical draft")
    return int(candidates[0]["bp_order"])


def _actions_through_combination(
    model: dict[str, Any],
    start_order: int,
    target_side: str,
    combination_size: int,
) -> int:
    seen = 0
    actions = 0
    for step in model["draft_sequence"]:
        if int(step["bp_order"]) < start_order:
            continue
        actions += 1
        if step["action"] == "pick" and step["side"] == target_side:
            seen += 1
            if seen >= combination_size:
                return actions
    return actions


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
    model = load_model(arguments.league_id)
    state = arguments.draft_state()
    unavailable_ids = _resolve_hero_ids(model, arguments.unavailable_hero_names)
    effective_model_type = arguments.model_type
    model_adjustment = ""
    if (
        effective_model_type == "sequence"
        and arguments.start_at_next_pick
        and int(state["bp_order"])
        < _next_pick_order(model, int(state["bp_order"]), arguments.target_side)
    ):
        effective_model_type = "stats"
        model_adjustment = (
            "The hypothetical omits earlier ban ownership and order, so the "
            "simulation uses the historical statistical model instead of "
            "inventing missing sequence actions."
        )
    if unavailable_ids:
        legal_ids = state.get("legal_hero_ids")
        if legal_ids is None:
            legal_ids = [int(hero_id) for hero_id in model.get("hero_names", {})]
        state["legal_hero_ids"] = [
            int(hero_id)
            for hero_id in legal_ids
            if int(hero_id) not in unavailable_ids
        ]
    if arguments.start_at_next_pick:
        state["bp_order"] = _next_pick_order(
            model,
            int(state["bp_order"]),
            arguments.target_side,
        )
    max_actions = arguments.horizon
    if arguments.target_side and arguments.combination_size:
        max_actions = max(
            max_actions,
            _actions_through_combination(
                model,
                int(state["bp_order"]),
                arguments.target_side,
                arguments.combination_size,
            ),
        )
    simulation_options: dict[str, Any] = {
        "model_type": effective_model_type,
        "max_actions": max_actions,
    }
    if arguments.target_side and arguments.combination_size:
        simulation_options.update(
            {
                "combination_side": arguments.target_side,
                "combination_size": arguments.combination_size,
            }
        )
    result = simulate(
        arguments.league_id,
        state,
        FIXED_ROLLOUTS,
        arguments.seed,
        **simulation_options,
    )
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
        "model_adjustment": model_adjustment,
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
        "hypothetical_unavailable_heroes": [
            {
                "hero_id": hero_id,
                "hero_name": model["hero_names"].get(str(hero_id), str(hero_id)),
            }
            for hero_id in unavailable_ids
        ],
        "pick_combinations": result["simulation"].get("pick_combinations", [])[:
            arguments.choices_per_action
        ],
        "hypothetical_assumption": (
            "The named heroes are treated as unavailable. Because the user did not "
            "specify which side banned each hero, ban ownership and ban order are "
            "not inferred."
            if unavailable_ids
            else ""
        ),
        "result_count": len(future_actions),
        "warning": (
            "Future-action probabilities are marginal historical rollout "
            "frequencies, not one guaranteed sequence or battle-win probabilities."
        ),
    }
