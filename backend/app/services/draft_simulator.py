"""Load season draft models and run probability-backed BP simulations."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

from app.services.analysis_pipeline import OUTPUT_ROOT

_CACHE: dict[Path, tuple[int, dict[str, Any]]] = {}


def model_path(league_id: str) -> Path:
    if not league_id or not all(character.isalnum() or character in "-_" for character in league_id):
        raise ValueError("Invalid league id")
    return OUTPUT_ROOT / league_id / "draft_model.json"


def load_model(league_id: str) -> dict[str, Any]:
    path = model_path(league_id)
    if not path.is_file():
        raise FileNotFoundError(f"No draft model has been generated for {league_id}")
    modified = path.stat().st_mtime_ns
    cached = _CACHE.get(path)
    if cached and cached[0] == modified:
        return cached[1]
    with path.open(encoding="utf-8") as source:
        model = json.load(source)
    if model.get("schema_version") != 1:
        raise ValueError("Unsupported draft model version")
    model["_base_index"] = {
        (row["context"], int(row["hero_id"])): row for row in model.get("base", [])
    }
    model["_action_index"] = {
        (row["action"], int(row["hero_id"])): row for row in model.get("action", [])
    }
    model["_relation_index"] = {row["key"]: row for row in model.get("relations", [])}
    role_bits = {
        int(role_id): 1 << index
        for index, role_id in enumerate(model.get("role_ids", []))
    }
    model["_hero_role_masks"] = {
        int(hero_id): sum(
            role_bits.get(int(role_id), 0)
            for role_id in positions
        )
        for hero_id, positions in model.get("hero_positions", {}).items()
    }
    _CACHE[path] = (modified, model)
    return model


def metadata(league_id: str) -> dict[str, Any]:
    model = load_model(league_id)
    return {
        "league_id": league_id,
        "generated_at": model["generated_at"],
        "training_inputs": model["training_inputs"],
        "training_decisions": model["training_decisions"],
        "heroes": [
            {
                "hero_id": hero_id,
                "hero_name": model["hero_names"].get(str(hero_id), str(hero_id)),
                "hero_icon": model.get("hero_icons", {}).get(str(hero_id), ""),
                "positions": model.get("hero_positions", {}).get(str(hero_id), []),
            }
            for hero_id in model["hero_ids"]
        ],
        "draft_sequence": model["draft_sequence"],
    }


def _relation_key(context: str, role: str, source_id: int, target_id: int) -> str:
    return f"{context}|{role}|{source_id}|{target_id}"


def _used_heroes(state: dict[str, Any]) -> set[int]:
    return {
        int(hero_id)
        for key in ("blue_picks", "red_picks", "blue_bans", "red_bans")
        for hero_id in state.get(key, [])
    }


def _role_assignment_masks(model: dict[str, Any], hero_ids: list[int]) -> set[int]:
    """Return every distinct-role assignment available to the supplied heroes."""
    assignments = {0}
    role_masks = model["_hero_role_masks"]
    for hero_id in hero_ids:
        hero_mask = role_masks.get(int(hero_id), 0)
        assignments = {
            assigned_mask | role_bit
            for assigned_mask in assignments
            for role_bit in (1 << index for index in range(hero_mask.bit_length()))
            if hero_mask & role_bit and not assigned_mask & role_bit
        }
        if not assignments:
            break
    return assignments


def _roles_are_feasible(model: dict[str, Any], hero_ids: list[int]) -> bool:
    """Check if the picked heroes have a one-to-one assignment to roles."""
    return bool(_role_assignment_masks(model, hero_ids))


def _legal_heroes(
    model: dict[str, Any], state: dict[str, Any], step: dict[str, Any]
) -> list[int]:
    used = _used_heroes(state)
    if state.get("legal_hero_ids") is not None:
        candidates = sorted(
            {
                int(hero_id)
                for hero_id in state["legal_hero_ids"]
                if int(hero_id) > 0 and int(hero_id) not in used
            }
        )
    else:
        candidates = [hero_id for hero_id in model["hero_ids"] if hero_id not in used]
    if step["action"] != "pick":
        return candidates
    previous_match_used = {
        int(hero_id)
        for hero_id in state.get(f"{step['side']}_used_previous_battles", [])
    }
    candidates = [hero_id for hero_id in candidates if hero_id not in previous_match_used]
    team_picks = [int(hero_id) for hero_id in state.get(f"{step['side']}_picks", [])]
    assignments = _role_assignment_masks(model, team_picks)
    role_masks = model["_hero_role_masks"]
    return [
        hero_id
        for hero_id in candidates
        if any(role_masks.get(hero_id, 0) & ~assignment for assignment in assignments)
    ]


def _visible_sources(state: dict[str, Any], side: str) -> list[tuple[str, int]]:
    own = "blue" if side == "blue" else "red"
    opponent = "red" if own == "blue" else "blue"
    fields = (
        ("own_pick", state.get(f"{own}_picks", [])),
        ("opponent_pick", state.get(f"{opponent}_picks", [])),
        ("own_ban", state.get(f"{own}_bans", [])),
        ("opponent_ban", state.get(f"{opponent}_bans", [])),
    )
    return [(role, int(hero_id)) for role, hero_ids in fields for hero_id in hero_ids if int(hero_id) > 0]


def _predict(model: dict[str, Any], state: dict[str, Any], step: dict[str, Any]) -> list[dict[str, Any]]:
    config = model["config"]
    action = step["action"]
    context = f"{action}|{step['side']}|{int(step['team_action_type_number'])}"
    alpha = float(config["alpha"])
    max_log_lift = math.log(float(config["max_lift"]))
    sources = _visible_sources(state, step["side"])
    scores: list[tuple[int, float]] = []

    for hero_id in _legal_heroes(model, state, step):
        action_row = model["_action_index"].get((action, hero_id))
        action_probability = (
            action_row["selections"] / action_row["opportunities"]
            if action_row and action_row["opportunities"]
            else 1e-9
        )
        base_row = model["_base_index"].get((context, hero_id))
        base_probability = (
            (base_row["selections"] + alpha * action_probability)
            / (base_row["opportunities"] + alpha)
            if base_row
            else action_probability
        )
        score = math.log(max(base_probability, 1e-12))
        for role, source_id in sources:
            relation = model["_relation_index"].get(
                _relation_key(context, role, source_id, hero_id)
            )
            if not relation:
                continue
            relation_probability = (
                relation["selections"] + alpha * base_probability
            ) / (relation["opportunities"] + alpha)
            lift = relation_probability / max(base_probability, 1e-12)
            weight = relation["opportunities"] / (
                relation["opportunities"] + float(config["shrinkage"])
            )
            score += weight * max(-max_log_lift, min(max_log_lift, math.log(lift)))
        scores.append((hero_id, score))

    maximum = max((score for _, score in scores), default=0.0)
    weights = [(hero_id, math.exp(score - maximum)) for hero_id, score in scores]
    total = sum(weight for _, weight in weights) or 1.0
    return [
        {
            "hero_id": hero_id,
            "hero_name": model["hero_names"].get(str(hero_id), str(hero_id)),
            "probability": weight / total,
        }
        for hero_id, weight in sorted(weights, key=lambda item: item[1], reverse=True)
    ]


def _apply(state: dict[str, Any], step: dict[str, Any], hero_id: int) -> None:
    field = f"{step['side']}_{'picks' if step['action'] == 'pick' else 'bans'}"
    state.setdefault(field, []).append(hero_id)
    if state.get("legal_hero_ids") is not None:
        state["legal_hero_ids"] = [
            candidate for candidate in state["legal_hero_ids"] if int(candidate) != hero_id
        ]


def simulate(league_id: str, state: dict[str, Any], rollouts: int, seed: int | None) -> dict[str, Any]:
    model = load_model(league_id)
    for side in ("blue", "red"):
        picks = [int(hero_id) for hero_id in state.get(f"{side}_picks", [])]
        if not _roles_are_feasible(model, picks):
            raise ValueError(
                f"{side.title()} picks cannot be assigned to distinct eligible roles"
            )
        overlap = set(picks) & {
            int(hero_id)
            for hero_id in state.get(f"{side}_used_previous_battles", [])
        }
        if overlap:
            raise ValueError(
                f"{side.title()} cannot pick heroes used in an earlier battle: {sorted(overlap)}"
            )
    sequence = model["draft_sequence"]
    start_order = int(state["bp_order"])
    start_index = next(
        (index for index, step in enumerate(sequence) if int(step["bp_order"]) == start_order),
        None,
    )
    if start_index is None:
        raise ValueError(f"bp_order={start_order} is not in the model sequence")
    next_step = sequence[start_index]
    next_probabilities = _predict(model, state, next_step)
    if not next_probabilities:
        raise ValueError("No legal heroes remain")

    randomizer = random.Random(seed)
    event_counts: dict[int, dict[int, int]] = {}
    ban_counts: dict[int, int] = {}
    for _ in range(rollouts):
        current = json.loads(json.dumps(state))
        for index, step in enumerate(sequence[start_index:]):
            probabilities = next_probabilities if index == 0 else _predict(model, current, step)
            if not probabilities:
                break
            hero_ids = [row["hero_id"] for row in probabilities]
            weights = [row["probability"] for row in probabilities]
            selected = randomizer.choices(hero_ids, weights=weights, k=1)[0]
            order_counts = event_counts.setdefault(int(step["bp_order"]), {})
            order_counts[selected] = order_counts.get(selected, 0) + 1
            if step["action"] == "ban":
                ban_counts[selected] = ban_counts.get(selected, 0) + 1
            _apply(current, step, selected)

    def rows(counts: dict[int, int], limit: int) -> list[dict[str, Any]]:
        return [
            {
                "hero_id": hero_id,
                "hero_name": model["hero_names"].get(str(hero_id), str(hero_id)),
                "probability": count / rollouts,
            }
            for hero_id, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
        ]

    return {
        "model_generated_at": model["generated_at"],
        "next_step": next_step,
        "next_action_probabilities": next_probabilities,
        "simulation": {
            "rollouts": rollouts,
            "next_actions": {str(order): rows(counts, 8) for order, counts in event_counts.items()},
            "banned_by_end": rows(ban_counts, 20),
        },
    }
