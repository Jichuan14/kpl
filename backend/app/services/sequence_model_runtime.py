"""NumPy runtime for the exported frozen-bag plus GRU draft model."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray


FloatArray = NDArray[np.float32]
IntArray = NDArray[np.int64]

MAX_ACTIONS = 20
ACTION_INDEX = {"pick": 1, "ban": 2}
SIDE_INDEX = {"blue": 1, "red": 2}

_SHARED_KEYS = {
    "hero_bias",
    "feature_projection.weight",
    "hero_residual.weight",
    "action_embedding.weight",
    "side_embedding.weight",
    "relation_embedding.weight",
    "position_embedding.weight",
    "team_slot_embedding.weight",
    "acting_team_embedding.weight",
    "opponent_team_embedding.weight",
    "query_projection.weight",
    "query_projection.bias",
}
_BAG_KEYS = _SHARED_KEYS | {"source_projection.weight"}
_GRU_KEYS = _SHARED_KEYS | {
    "gru.weight_ih_l0",
    "gru.weight_hh_l0",
    "gru.bias_ih_l0",
    "gru.bias_hh_l0",
}


def _array(value: Any, shape: tuple[int, ...], name: str) -> FloatArray:
    result = np.asarray(value, dtype=np.float32)
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f"Sequence model parameter {name} has an invalid shape or value")
    result.setflags(write=False)
    return result


def _prepare_branch(
    raw: dict[str, Any],
    *,
    branch_name: str,
    hero_count: int,
    team_count: int,
    feature_width: int,
    hidden_dim: int,
) -> dict[str, FloatArray]:
    required = _BAG_KEYS if branch_name == "bag" else _GRU_KEYS
    if set(raw) != required:
        raise ValueError(f"Sequence model {branch_name} parameters are incomplete")
    d = hidden_dim
    h = hero_count
    t = team_count
    shapes = {
        "hero_bias": (h,),
        "feature_projection.weight": (d, feature_width),
        "hero_residual.weight": (h + 1, d),
        "action_embedding.weight": (3, d),
        "side_embedding.weight": (3, d),
        "relation_embedding.weight": (5, d),
        "position_embedding.weight": (MAX_ACTIONS + 1, d),
        "team_slot_embedding.weight": (6, d),
        "acting_team_embedding.weight": (t + 1, d),
        "opponent_team_embedding.weight": (t + 1, d),
        "query_projection.weight": (d, d),
        "query_projection.bias": (d,),
    }
    if branch_name == "bag":
        shapes["source_projection.weight"] = (d, d)
    else:
        shapes.update(
            {
                "gru.weight_ih_l0": (3 * d, d),
                "gru.weight_hh_l0": (3 * d, d),
                "gru.bias_ih_l0": (3 * d,),
                "gru.bias_hh_l0": (3 * d,),
            }
        )
    return {
        name: _array(raw[name], shape, f"{branch_name}.{name}")
        for name, shape in shapes.items()
    }


def prepare_sequence_parameters(
    artifact: dict[str, Any], feature_matrix: FloatArray
) -> dict[str, Any]:
    """Validate exported arrays and precompute constant candidate vectors."""
    config = artifact.get("config", {})
    hero_count = len(artifact.get("hero_ids", []))
    team_count = len(artifact.get("team_ids", []))
    feature_width = len(artifact.get("feature_names", []))
    hidden_dim = int(config.get("hidden_dim") or 0)
    if (
        hero_count < 1
        or team_count < 1
        or feature_width < 1
        or hidden_dim < 1
        or int(config.get("hero_count") or 0) != hero_count
        or int(config.get("team_count") or 0) != team_count
        or int(config.get("feature_width") or 0) != feature_width
        or feature_matrix.shape != (hero_count, feature_width)
    ):
        raise ValueError("Sequence model config does not match its vocabularies")
    if not np.isfinite(feature_matrix).all():
        raise ValueError("Sequence model feature matrix contains non-finite values")

    raw_parameters = artifact.get("parameters", {})
    if set(raw_parameters) != {"residual_scale_logit", "bag", "gru"}:
        raise ValueError("Sequence model parameters are incomplete")
    residual_scale_logit = float(raw_parameters["residual_scale_logit"])
    if not math.isfinite(residual_scale_logit):
        raise ValueError("Sequence model residual scale is invalid")
    bag = _prepare_branch(
        raw_parameters["bag"],
        branch_name="bag",
        hero_count=hero_count,
        team_count=team_count,
        feature_width=feature_width,
        hidden_dim=hidden_dim,
    )
    gru = _prepare_branch(
        raw_parameters["gru"],
        branch_name="gru",
        hero_count=hero_count,
        team_count=team_count,
        feature_width=feature_width,
        hidden_dim=hidden_dim,
    )
    features = np.asarray(feature_matrix, dtype=np.float32)
    bag_candidates = (
        features @ bag["feature_projection.weight"].T
        + bag["hero_residual.weight"][:hero_count]
    )
    gru_candidates = (
        features @ gru["feature_projection.weight"].T
        + gru["hero_residual.weight"][:hero_count]
    )
    bag_candidates.setflags(write=False)
    gru_candidates.setflags(write=False)
    if residual_scale_logit >= 0:
        residual_scale = float(1.0 / (1.0 + math.exp(-residual_scale_logit)))
    else:
        exponential = math.exp(residual_scale_logit)
        residual_scale = float(exponential / (1.0 + exponential))
    return {
        "bag": bag,
        "gru": gru,
        "bag_candidates": bag_candidates,
        "gru_candidates": gru_candidates,
        "residual_scale": residual_scale,
        "hidden_dim": hidden_dim,
        "hero_count": hero_count,
    }


def _context_query(
    branch: dict[str, FloatArray],
    *,
    next_action: int,
    next_side: int,
    next_position: int,
    next_team_slot: int,
    acting_team: int,
    opponent_team: int,
) -> FloatArray:
    return (
        branch["action_embedding.weight"][next_action]
        + branch["side_embedding.weight"][next_side]
        + branch["position_embedding.weight"][next_position]
        + branch["team_slot_embedding.weight"][next_team_slot]
        + branch["acting_team_embedding.weight"][acting_team]
        + branch["opponent_team_embedding.weight"][opponent_team]
    )


def _bag_logits(
    prepared: dict[str, Any],
    history_heroes: IntArray,
    history_actions: IntArray,
    history_relations: IntArray,
    context: FloatArray,
) -> FloatArray:
    branch = prepared["bag"]
    candidates = prepared["bag_candidates"]
    if history_heroes.size:
        source = (
            candidates[history_heroes] @ branch["source_projection.weight"].T
            + branch["action_embedding.weight"][history_actions]
            + branch["relation_embedding.weight"][history_relations]
        )
        pooled = np.tanh(source).sum(axis=0) / np.float32(
            math.sqrt(history_heroes.size)
        )
    else:
        pooled = np.zeros(prepared["hidden_dim"], dtype=np.float32)
    query = np.tanh(
        pooled @ branch["query_projection.weight"].T
        + branch["query_projection.bias"]
        + context
    )
    return (
        query @ candidates.T / np.float32(math.sqrt(prepared["hidden_dim"]))
        + branch["hero_bias"]
    )


def _gru_logits(
    prepared: dict[str, Any],
    history_heroes: IntArray,
    history_actions: IntArray,
    history_sides: IntArray,
    history_relations: IntArray,
    history_positions: IntArray,
    context: FloatArray,
) -> FloatArray:
    branch = prepared["gru"]
    candidates = prepared["gru_candidates"]
    hidden = np.zeros(prepared["hidden_dim"], dtype=np.float32)
    if history_heroes.size:
        tokens = (
            candidates[history_heroes]
            + branch["action_embedding.weight"][history_actions]
            + branch["side_embedding.weight"][history_sides]
            + branch["relation_embedding.weight"][history_relations]
            + branch["position_embedding.weight"][history_positions]
        )
        weight_ih = branch["gru.weight_ih_l0"]
        weight_hh = branch["gru.weight_hh_l0"]
        bias_ih = branch["gru.bias_ih_l0"]
        bias_hh = branch["gru.bias_hh_l0"]
        d = prepared["hidden_dim"]
        for token in tokens:
            input_gates = weight_ih @ token + bias_ih
            hidden_gates = weight_hh @ hidden + bias_hh
            reset = 1.0 / (
                1.0 + np.exp(-(input_gates[:d] + hidden_gates[:d]))
            )
            update = 1.0 / (
                1.0 + np.exp(-(input_gates[d : 2 * d] + hidden_gates[d : 2 * d]))
            )
            candidate = np.tanh(
                input_gates[2 * d :] + reset * hidden_gates[2 * d :]
            )
            hidden = (1.0 - update) * candidate + update * hidden
    query = np.tanh(
        hidden @ branch["query_projection.weight"].T
        + branch["query_projection.bias"]
        + context
    )
    return (
        query @ candidates.T / np.float32(math.sqrt(prepared["hidden_dim"]))
        + branch["hero_bias"]
    )


def sequence_logits(
    prepared: dict[str, Any],
    *,
    history_heroes: IntArray,
    history_actions: IntArray,
    history_sides: IntArray,
    history_relations: IntArray,
    history_positions: IntArray,
    next_action: int,
    next_side: int,
    next_position: int,
    next_team_slot: int,
    acting_team: int,
    opponent_team: int,
    legal_mask: NDArray[np.bool_],
) -> FloatArray:
    """Return masked logits using the production PyTorch training equations."""
    if legal_mask.shape != (prepared["hero_count"],) or not legal_mask.any():
        raise ValueError("Sequence model legal mask is empty or malformed")
    bag_context = _context_query(
        prepared["bag"],
        next_action=next_action,
        next_side=next_side,
        next_position=next_position,
        next_team_slot=next_team_slot,
        acting_team=acting_team,
        opponent_team=opponent_team,
    )
    gru_context = _context_query(
        prepared["gru"],
        next_action=next_action,
        next_side=next_side,
        next_position=next_position,
        next_team_slot=next_team_slot,
        acting_team=acting_team,
        opponent_team=opponent_team,
    )
    bag_logits = _bag_logits(
        prepared,
        history_heroes,
        history_actions,
        history_relations,
        bag_context,
    )
    gru_logits = _gru_logits(
        prepared,
        history_heroes,
        history_actions,
        history_sides,
        history_relations,
        history_positions,
        gru_context,
    )
    centered_gru = gru_logits - gru_logits[legal_mask].mean(dtype=np.float32)
    combined = bag_logits + np.float32(prepared["residual_scale"]) * centered_gru
    return np.where(legal_mask, combined, np.float32(-1e9)).astype(
        np.float32, copy=False
    )
