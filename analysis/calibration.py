"""Pure probability calibration, scoring, and prediction-record utilities."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


def masked_softmax(
    logits: NDArray[np.floating], mask: NDArray[np.bool_], temperature: float = 1.0
) -> NDArray[np.float64]:
    values = np.asarray(logits, dtype=np.float64)
    accepted = np.asarray(mask, dtype=np.bool_)
    if values.shape != accepted.shape or values.ndim not in {1, 2}:
        raise ValueError("Logits and mask must have equal one- or two-dimensional shapes")
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("Temperature must be finite and strictly positive")
    if values.ndim == 1:
        values, accepted, squeeze = values[None, :], accepted[None, :], True
    else:
        squeeze = False
    if np.any(accepted.sum(axis=1) == 0):
        raise ValueError("Accepted candidate set is empty")
    if not np.isfinite(values[accepted]).all():
        raise ValueError("Accepted logits contain non-finite values")
    scaled = np.where(accepted, values / temperature, -np.inf)
    maximum = np.max(scaled, axis=1, keepdims=True)
    weights = np.where(accepted, np.exp(scaled - maximum), 0.0)
    result = weights / weights.sum(axis=1, keepdims=True)
    return result[0] if squeeze else result


@dataclass(frozen=True)
class PredictionRecords:
    logits: NDArray[np.float64]
    accepted_mask: NDArray[np.bool_]
    targets: NDArray[np.int64]
    series_ids: NDArray[np.str_]
    actions: NDArray[np.str_]
    phases: NDArray[np.str_]

    def validate(self, *, allow_excluded_targets: bool = False) -> dict[str, Any]:
        n, h = self.logits.shape
        if self.accepted_mask.shape != (n, h) or self.targets.shape != (n,):
            raise ValueError("Malformed prediction arrays")
        if n == 0 or h == 0:
            raise ValueError("Prediction window is empty")
        if any(array.shape != (n,) for array in (self.series_ids, self.actions, self.phases)):
            raise ValueError("Prediction metadata length mismatch")
        if np.any((self.targets < 0) | (self.targets >= h)):
            raise ValueError("Target index is outside the hero vocabulary")
        empty = np.flatnonzero(self.accepted_mask.sum(axis=1) == 0)
        if len(empty) and not allow_excluded_targets:
            raise ValueError("A prediction has an empty candidate set")
        if not np.isfinite(self.logits[self.accepted_mask]).all():
            raise ValueError("Accepted logits contain non-finite values")
        covered = self.accepted_mask[np.arange(n), self.targets]
        excluded = np.flatnonzero(~covered)
        if len(excluded) and not allow_excluded_targets:
            raise ValueError(f"{len(excluded)} targets are excluded by the candidate policy")
        return {"decisions": n, "target_excluded": int(len(excluded)), "empty_candidate_set": int(len(empty)), "excluded_rows": excluded.tolist(), "empty_rows": empty.tolist()}


def load_prediction_records(path: Path) -> PredictionRecords:
    with np.load(path, allow_pickle=False) as data:
        return PredictionRecords(
            logits=np.asarray(data["logits"], dtype=np.float64),
            accepted_mask=np.asarray(data["accepted_mask"], dtype=np.bool_),
            targets=np.asarray(data["targets"], dtype=np.int64),
            series_ids=np.asarray(data["series_ids"], dtype=np.str_),
            actions=np.asarray(data["actions"], dtype=np.str_),
            phases=np.asarray(data["phases"], dtype=np.str_),
        )


def per_decision_nll(records: PredictionRecords, temperature: float) -> NDArray[np.float64]:
    probabilities = masked_softmax(records.logits, records.accepted_mask, temperature)
    selected = probabilities[np.arange(len(records.targets)), records.targets]
    result = np.full(len(selected), np.inf, dtype=np.float64)
    positive = selected > 0
    result[positive] = -np.log(selected[positive])
    return result


def fit_global_temperature(records: PredictionRecords) -> dict[str, Any]:
    coverage = records.validate(allow_excluded_targets=True)
    if coverage["target_excluded"]:
        return {
            "status": "not_fit_due_to_candidate_exclusion",
            "temperature": 1.0,
            "coverage": coverage,
            "fit": {"range": [0.5, 2.0], "boundary_optimum": False},
            "metrics": {"calibration": {"nll_t1": None, "nll_fitted": None, "full_distribution_nll": "infinite"}},
        }
    grid = np.unique(np.r_[np.geomspace(0.5, 2.0, 161), 1.0])
    objectives = np.asarray([per_decision_nll(records, float(t)).mean() for t in grid])
    minimum = objectives.min()
    candidates = np.flatnonzero(np.isclose(objectives, minimum, rtol=0, atol=1e-12))
    best_index = min(candidates, key=lambda index: abs(float(grid[index]) - 1.0))
    temperature = float(grid[best_index])
    fitted = float(objectives[best_index])
    original = float(objectives[np.flatnonzero(grid == 1.0)[0]])
    if fitted > original + 1e-12:
        raise AssertionError("Temperature grid increased calibration NLL")
    return {
        "status": "experimental",
        "temperature": temperature,
        "coverage": coverage,
        "fit": {
            "range": [0.5, 2.0],
            "grid_size": int(len(grid)),
            "boundary_optimum": best_index in {0, len(grid) - 1},
        },
        "metrics": {"calibration": {"nll_t1": original, "nll_fitted": fitted}},
    }


def score_metrics(records: PredictionRecords, temperature: float = 1.0) -> dict[str, Any]:
    coverage = records.validate(allow_excluded_targets=True)
    if coverage["target_excluded"]:
        return {"decisions": len(records.targets), "target_excluded": coverage["target_excluded"], "negative_log_likelihood": None, "full_distribution_nll": "infinite"}
    probabilities = masked_softmax(records.logits, records.accepted_mask, temperature)
    selected = probabilities[np.arange(len(records.targets)), records.targets]
    ranking = np.argsort(-probabilities, axis=1, kind="stable")
    ranks = np.argmax(ranking == records.targets[:, None], axis=1) + 1
    confidence = probabilities.max(axis=1)
    correct = ranks == 1
    bins = np.minimum((confidence * 10).astype(int), 9)
    ece = 0.0
    for index in range(10):
        chosen = bins == index
        if chosen.any():
            ece += chosen.mean() * abs(correct[chosen].mean() - confidence[chosen].mean())
    squared = np.square(probabilities).sum(axis=1) - 2 * selected + 1
    return {
        "decisions": int(len(selected)),
        "target_excluded": 0,
        "negative_log_likelihood": float(-np.log(selected).mean()),
        "multiclass_brier": float(squared.mean()),
        "top_1_accuracy": float((ranks <= 1).mean()),
        "top_3_accuracy": float((ranks <= 3).mean()),
        "top_5_accuracy": float((ranks <= 5).mean()),
        "mean_reciprocal_rank": float((1.0 / ranks).mean()),
        "ece_10_bin": float(ece),
        "mean_confidence": float(confidence.mean()),
    }


def series_balanced_nll(records: PredictionRecords, temperature: float) -> float:
    losses = per_decision_nll(records, temperature)
    return float(np.mean([losses[records.series_ids == key].mean() for key in np.unique(records.series_ids)]))


def paired_series_bootstrap_nll(
    records: PredictionRecords, first_temperature: float, second_temperature: float,
    *, samples: int = 5000, seed: int = 7,
) -> dict[str, float]:
    first = per_decision_nll(records, first_temperature)
    second = per_decision_nll(records, second_temperature)
    series = np.unique(records.series_ids)
    deltas = np.asarray([(second[records.series_ids == key] - first[records.series_ids == key]).mean() for key in series])
    random = np.random.default_rng(seed)
    draws = random.choice(deltas, size=(samples, len(deltas)), replace=True).mean(axis=1)
    return {"difference": float(deltas.mean()), "ci_95_low": float(np.quantile(draws, 0.025)), "ci_95_high": float(np.quantile(draws, 0.975)), "series": int(len(series)), "samples": samples}


def read_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Manifest must be a JSON object")
    return value
