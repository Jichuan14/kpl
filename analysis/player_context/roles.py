"""Soft hero-to-role assignment without final-lineup leakage."""

from __future__ import annotations

import itertools
import math

import numpy as np
from numpy.typing import NDArray


def normalized_role_prior(values: NDArray[np.floating], epsilon: float = 1e-4) -> NDArray[np.float64]:
    raw = np.asarray(values, dtype=np.float64)
    if raw.ndim != 2 or raw.shape[1] != 5 or not np.isfinite(raw).all():
        raise ValueError("Hero-role prior must have shape [H,5] and finite values")
    raw = np.maximum(raw, 0.0) + epsilon
    return raw / raw.sum(axis=1, keepdims=True)


def open_role_probabilities(
    picked_hero_indices: list[int], hero_role_prior: NDArray[np.floating]
) -> NDArray[np.float64]:
    """Marginalize injective assignments; never hard-mask an unexpected role."""
    prior = normalized_role_prior(hero_role_prior)
    if len(picked_hero_indices) > 5 or len(set(picked_hero_indices)) != len(picked_hero_indices):
        raise ValueError("Visible picks must be distinct and no longer than five")
    if not picked_hero_indices:
        return np.ones(5, dtype=np.float64)
    if any(index < 0 or index >= len(prior) for index in picked_hero_indices):
        raise ValueError("Picked hero is outside the role-prior vocabulary")
    assignments = list(itertools.permutations(range(5), len(picked_hero_indices)))
    log_weights = np.asarray([
        sum(math.log(prior[hero, role]) for hero, role in zip(picked_hero_indices, assignment, strict=True))
        for assignment in assignments
    ])
    weights = np.exp(log_weights - log_weights.max()); weights /= weights.sum()
    result = np.zeros(5, dtype=np.float64)
    for weight, assignment in zip(weights, assignments, strict=True):
        used = set(assignment)
        result += weight * np.asarray([role not in used for role in range(5)])
    if not np.isclose(result.sum(), 5 - len(picked_hero_indices), atol=1e-10):
        raise AssertionError("Open-role probabilities violate assignment cardinality")
    return result

