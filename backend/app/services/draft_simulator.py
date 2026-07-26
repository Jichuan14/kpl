"""Load season draft models and run probability-backed BP simulations."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

from app.services.analysis_pipeline import ANALYSIS_DIR, OUTPUT_ROOT

_CACHE: dict[Path, tuple[int, dict[str, Any]]] = {}
_LEARNABLE_CACHE: dict[Path, tuple[int, dict[str, Any]]] = {}
SPECIALTY_FEATURES_PATH = ANALYSIS_DIR / "hero_specialty_vectors_thermometer.json"


def model_path(league_id: str) -> Path:
    if not league_id or not all(character.isalnum() or character in "-_" for character in league_id):
        raise ValueError("Invalid league id")
    return OUTPUT_ROOT / league_id / "draft_model.json"


def learnable_model_path(league_id: str) -> Path:
    if not league_id or not all(character.isalnum() or character in "-_" for character in league_id):
        raise ValueError("Invalid league id")
    return OUTPUT_ROOT / league_id / "learnable_draft_choice_model.json"


def feature_space_path(league_id: str) -> Path:
    if not league_id or not all(character.isalnum() or character in "-_" for character in league_id):
        raise ValueError("Invalid league id")
    return OUTPUT_ROOT / league_id / "learned_hero_feature_space.json"


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


def load_learnable_model(league_id: str) -> dict[str, Any]:
    """Load the JSON conditional-choice artifact for a season."""
    path = learnable_model_path(league_id)
    if not path.is_file():
        raise FileNotFoundError(f"No learnable draft model has been generated for {league_id}")
    modified = path.stat().st_mtime_ns
    cached = _LEARNABLE_CACHE.get(path)
    if cached and cached[0] == modified:
        return cached[1]
    if not SPECIALTY_FEATURES_PATH.is_file():
        raise FileNotFoundError(
            f"Hero specialty vectors are missing: {SPECIALTY_FEATURES_PATH}"
        )
    with path.open(encoding="utf-8") as source:
        model = json.load(source)
    if model.get("schema_version") != 1 or model.get("model_type") != "recency_weighted_hybrid_bilinear_choice":
        raise ValueError("Unsupported learnable draft model version")
    if str(model.get("target_season")) != league_id:
        raise ValueError("Learnable draft model target season does not match the requested league")

    with SPECIALTY_FEATURES_PATH.open(encoding="utf-8") as source:
        specialty_artifact = json.load(source)
    expected_feature_names = [*specialty_artifact.get("feature_names", []), "feature_known"]
    if model.get("feature_names") != expected_feature_names:
        raise ValueError("Learnable draft model specialty schema does not match the current vectors")
    role_fields = (
        "current_team_picks",
        "current_opponent_picks",
        "current_team_bans",
        "current_opponent_bans",
    )
    if model.get("role_fields") != list(role_fields):
        raise ValueError("Learnable draft model role schema is unsupported")

    hero_ids = [int(hero_id) for hero_id in model.get("hero_ids", [])]
    if not hero_ids:
        raise ValueError("Learnable draft model has no hero vocabulary")
    hero_to_index = {hero_id: index for index, hero_id in enumerate(hero_ids)}
    specialty_by_id = {
        int(row["hero_id"]): [*row["vector"], float(row.get("feature_known", True))]
        for row in specialty_artifact.get("rows", [])
        if row.get("hero_id") is not None
    }
    feature_width = len(expected_feature_names)
    feature_matrix = [
        specialty_by_id.get(hero_id, [0.0] * feature_width) for hero_id in hero_ids
    ]
    parameters = model.get("parameters", {})
    required_parameters = {
        "feature_projection",
        "hero_residual",
        "context_embedding",
        "state_projection",
        "source_embedding",
        "hero_bias",
    }
    if set(parameters) != required_parameters:
        raise ValueError("Learnable draft model parameters are incomplete")
    feature_projection = parameters["feature_projection"]
    if len(feature_projection) != feature_width or not feature_projection[0]:
        raise ValueError("Learnable draft model feature width is invalid")
    embedding_dim = len(feature_projection[0])
    if (
        len(parameters["hero_residual"]) != len(hero_ids)
        or any(len(row) != embedding_dim for row in parameters["hero_residual"])
        or len(parameters["context_embedding"]) != len(model.get("context_keys", []))
        or any(len(row) != embedding_dim for row in parameters["context_embedding"])
        or len(parameters["hero_bias"]) != len(hero_ids)
    ):
        raise ValueError("Learnable draft model hero vocabulary is invalid")
    state_width = 4 * (2 * feature_width + 1)
    if (
        len(parameters["state_projection"]) != state_width
        or any(len(row) != embedding_dim for row in parameters["state_projection"])
        or len(parameters["source_embedding"]) != 4
        or any(
            len(role_rows) != len(hero_ids)
            or any(len(row) != embedding_dim for row in role_rows)
            for role_rows in parameters["source_embedding"]
        )
    ):
        raise ValueError("Learnable draft model parameter dimensions are invalid")
    context_to_index = {
        (str(action), str(side), int(slot)): index
        for index, (action, side, slot) in enumerate(model.get("context_keys", []))
    }
    candidate_representations = [
        [
            sum(features[feature_index] * feature_projection[feature_index][dimension]
                for feature_index in range(feature_width))
            + parameters["hero_residual"][hero_index][dimension]
            for dimension in range(embedding_dim)
        ]
        for hero_index, features in enumerate(feature_matrix)
    ]
    model["_hero_ids"] = hero_ids
    model["_hero_to_index"] = hero_to_index
    model["_feature_matrix"] = feature_matrix
    model["_parameters"] = parameters
    model["_context_to_index"] = context_to_index
    model["_candidate_representations"] = candidate_representations
    _LEARNABLE_CACHE[path] = (modified, model)
    return model


def learned_feature_space(league_id: str) -> dict[str, Any]:
    """Return the notebook-exported 2-D learned candidate representation."""
    path = feature_space_path(league_id)
    if not path.is_file():
        raise FileNotFoundError(f"No learned hero feature space has been generated for {league_id}")
    with path.open(encoding="utf-8") as source:
        feature_space = json.load(source)
    if (
        feature_space.get("schema_version") != 1
        or feature_space.get("projection") != "pca"
        or str(feature_space.get("target_season")) != league_id
    ):
        raise ValueError("Unsupported learned hero feature space version")
    model = load_model(league_id)
    rows = []
    for row in feature_space.get("rows", []):
        hero_id = int(row["hero_id"])
        rows.append(
            {
                **row,
                "hero_name": model["hero_names"].get(str(hero_id), row.get("hero_name", str(hero_id))),
                "hero_icon": model["hero_icons"].get(str(hero_id), ""),
            }
        )
    return {
        **{key: value for key, value in feature_space.items() if key != "rows"},
        "rows": rows,
    }


def metadata(league_id: str) -> dict[str, Any]:
    model = load_model(league_id)
    learnable_ready = (
        learnable_model_path(league_id).is_file()
        and SPECIALTY_FEATURES_PATH.is_file()
    )
    return {
        "league_id": league_id,
        "generated_at": model["generated_at"],
        "training_inputs": model["training_inputs"],
        "training_decisions": model["training_decisions"],
        "effective_training_decisions": model.get("effective_training_decisions"),
        "heroes": [
            {
                "hero_id": hero_id,
                "hero_name": model["hero_names"].get(str(hero_id), str(hero_id)),
                "positions": model.get("hero_positions", {}).get(str(hero_id), []),
            }
            for hero_id in model["hero_ids"]
        ],
        "draft_sequence": model["draft_sequence"],
        "default_model_type": "stats",
        "available_models": [
            {
                "id": "stats",
                "label": "Statistical model",
                "available": True,
                "description": "Historical rates with smoothed hero relationships.",
            },
            {
                "id": "learnable",
                "label": "Learnable hybrid",
                "available": learnable_ready,
                "description": "Learned specialty, hero, and draft-context embeddings.",
            },
        ],
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


def _predict_stats(model: dict[str, Any], state: dict[str, Any], step: dict[str, Any]) -> list[dict[str, Any]]:
    config = model["config"]
    action = step["action"]
    context = f"{action}|{step['side']}|{int(step['team_action_type_number'])}"
    alpha = float(config["alpha"])
    max_log_lift = math.log(float(config["max_lift"]))
    max_meta_log_lift = math.log(float(config.get("max_meta_lift", 1.0)))
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
        if int(step["bp_order"]) <= 5:
            meta = model.get("meta", {})
            opportunities = float(meta.get("opportunities", {}).get(str(hero_id), 0))
            if opportunities:
                meta_rate = float(meta.get("selections", {}).get(str(hero_id), 0)) / opportunities
                baseline_rate = float(meta.get("baseline_rate", 0))
                if baseline_rate:
                    score += float(config.get("meta_weight", 0.0)) * max(
                        -max_meta_log_lift,
                        min(max_meta_log_lift, math.log(max(meta_rate, 1e-12) / baseline_rate)),
                    )
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
            role_weight = (
                float(config.get("own_pick_relation_weight", 1.0))
                if role == "own_pick"
                else 1.0
            )
            score += role_weight * weight * max(-max_log_lift, min(max_log_lift, math.log(lift)))
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


def _learnable_role_hero_ids(state: dict[str, Any], side: str) -> tuple[list[int], ...]:
    opponent = "red" if side == "blue" else "blue"
    return (
        [int(hero_id) for hero_id in state.get(f"{side}_picks", []) if int(hero_id) > 0],
        [int(hero_id) for hero_id in state.get(f"{opponent}_picks", []) if int(hero_id) > 0],
        [int(hero_id) for hero_id in state.get(f"{side}_bans", []) if int(hero_id) > 0],
        [int(hero_id) for hero_id in state.get(f"{opponent}_bans", []) if int(hero_id) > 0],
    )


def _predict_learnable(
    base_model: dict[str, Any],
    learnable_model: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
) -> list[dict[str, Any]]:
    """Score legal heroes with the exported recency-weighted choice model."""
    parameters = learnable_model["_parameters"]
    feature_matrix = learnable_model["_feature_matrix"]
    hero_to_index = learnable_model["_hero_to_index"]
    role_hero_ids = _learnable_role_hero_ids(state, str(step["side"]))
    feature_width = len(feature_matrix[0])
    embedding_dim = len(parameters["hero_residual"][0])
    static_state: list[float] = []
    for hero_ids in role_hero_ids:
        indices = [hero_to_index[hero_id] for hero_id in hero_ids if hero_id in hero_to_index]
        vectors = [feature_matrix[index] for index in indices]
        static_state.extend(
            sum((vector[feature_index] for vector in vectors), 0.0)
            for feature_index in range(feature_width)
        )
        static_state.extend(
            max((vector[feature_index] for vector in vectors), default=0.0)
            for feature_index in range(feature_width)
        )
        static_state.append(float(len(indices)))
    context_key = (
        str(step["action"]),
        str(step["side"]),
        int(step["team_action_type_number"]),
    )
    try:
        context_index = learnable_model["_context_to_index"][context_key]
    except KeyError as exc:
        raise ValueError(f"Learnable draft model has no context for {context_key}") from exc
    query = [
        parameters["context_embedding"][context_index][dimension]
        + sum(
            static_state[state_index] * parameters["state_projection"][state_index][dimension]
            for state_index in range(len(static_state))
        )
        for dimension in range(embedding_dim)
    ]
    for role_index, hero_ids in enumerate(role_hero_ids):
        for hero_id in hero_ids:
            hero_index = hero_to_index.get(hero_id)
            if hero_index is not None:
                for dimension in range(embedding_dim):
                    query[dimension] += parameters["source_embedding"][role_index][hero_index][dimension]
    logits = [
        sum(value * query[dimension] for dimension, value in enumerate(candidate))
        + parameters["hero_bias"][hero_index]
        for hero_index, candidate in enumerate(learnable_model["_candidate_representations"])
    ]
    legal_hero_ids = _legal_heroes(base_model, state, step)
    candidate_indices = [hero_to_index[hero_id] for hero_id in legal_hero_ids if hero_id in hero_to_index]
    if not candidate_indices:
        return []
    candidate_logits = [logits[index] for index in candidate_indices]
    maximum = max(candidate_logits)
    weights = [math.exp(score - maximum) for score in candidate_logits]
    total = sum(weights)
    probabilities = [weight / total for weight in weights]
    rows = [
        {
            "hero_id": hero_id,
            "hero_name": base_model["hero_names"].get(str(hero_id), str(hero_id)),
            "probability": float(probability),
        }
        for hero_id, probability in zip(
            (hero_id for hero_id in legal_hero_ids if hero_id in hero_to_index),
            probabilities,
        )
    ]
    return sorted(rows, key=lambda row: row["probability"], reverse=True)


def _predict(
    model: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
    learnable_model: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if learnable_model is not None:
        return _predict_learnable(model, learnable_model, state, step)
    return _predict_stats(model, state, step)


def _apply(state: dict[str, Any], step: dict[str, Any], hero_id: int) -> None:
    field = f"{step['side']}_{'picks' if step['action'] == 'pick' else 'bans'}"
    state.setdefault(field, []).append(hero_id)
    if state.get("legal_hero_ids") is not None:
        state["legal_hero_ids"] = [
            candidate for candidate in state["legal_hero_ids"] if int(candidate) != hero_id
        ]


def simulate(
    league_id: str,
    state: dict[str, Any],
    rollouts: int,
    seed: int | None,
    *,
    model_type: str = "stats",
) -> dict[str, Any]:
    model = load_model(league_id)
    if model_type not in {"stats", "learnable"}:
        raise ValueError(f"Unsupported draft model type: {model_type}")
    learnable_model = load_learnable_model(league_id) if model_type == "learnable" else None
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
    next_probabilities = _predict(model, state, next_step, learnable_model)
    if not next_probabilities:
        raise ValueError("No legal heroes remain")

    randomizer = random.Random(seed)
    event_counts: dict[int, dict[int, int]] = {}
    ban_counts: dict[int, int] = {}
    for _ in range(rollouts):
        current = json.loads(json.dumps(state))
        for index, step in enumerate(sequence[start_index:]):
            probabilities = (
                next_probabilities
                if index == 0
                else _predict(model, current, step, learnable_model)
            )
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
        "model_type": model_type,
        "model_label": "Learnable hybrid" if model_type == "learnable" else "Statistical model",
        "next_step": next_step,
        "next_action_probabilities": next_probabilities,
        "simulation": {
            "rollouts": rollouts,
            "next_actions": {str(order): rows(counts, 8) for order, counts in event_counts.items()},
            "banned_by_end": rows(ban_counts, 20),
        },
    }
