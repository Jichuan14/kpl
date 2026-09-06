"""Validated optional temperature sidecars for draft-policy inference."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


CALIBRATION_FILENAME = "draft_probability_calibration.json"
LEGACY_POLICY_ID = "legacy_role_filter_v1"
GAME_AVAILABILITY_POLICY_ID = "game_availability_v1"


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def semantic_model_fingerprint(model: dict[str, Any], feature_matrix: Any) -> str:
    """Identity of every static input capable of changing sequence scores."""
    identity = {
        "schema_version": model.get("schema_version"),
        "model_type": model.get("model_type"),
        "target_season": model.get("target_season"),
        "config": model.get("config"),
        "parameters": model.get("parameters"),
        "hero_ids": model.get("hero_ids"),
        "team_ids": model.get("team_ids"),
        "feature_names": model.get("feature_names"),
        "feature_matrix": np.asarray(feature_matrix, dtype=np.float32).tolist(),
    }
    return canonical_sha256(identity)


def candidate_policy_fingerprint(
    policy_id: str, *, role_map: dict[int, int] | dict[str, Any] | None = None,
    availability_config: dict[str, Any] | None = None,
) -> str:
    if policy_id not in {LEGACY_POLICY_ID, GAME_AVAILABILITY_POLICY_ID}:
        raise ValueError(f"Unsupported candidate policy: {policy_id}")
    return canonical_sha256({"policy_id": policy_id, "role_map": role_map or {}, "availability_config": availability_config or {}})


def masked_softmax(
    logits: NDArray[np.floating], mask: NDArray[np.bool_], temperature: float = 1.0
) -> NDArray[np.float32]:
    values = np.asarray(logits, dtype=np.float32)
    accepted = np.asarray(mask, dtype=np.bool_)
    if values.shape != accepted.shape or values.ndim != 1:
        raise ValueError("Logits and candidate mask must be equal one-dimensional arrays")
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("Temperature must be finite and strictly positive")
    if not accepted.any():
        raise ValueError("Accepted candidate set is empty")
    if not np.isfinite(values[accepted]).all():
        raise ValueError("Accepted logits contain non-finite values")
    scaled = values[accepted] / np.float32(temperature)
    weights = np.exp(scaled - scaled.max())
    result = np.zeros_like(values, dtype=np.float32)
    result[accepted] = weights / weights.sum(dtype=np.float32)
    return result


@dataclass(frozen=True)
class CalibrationResolution:
    temperature: float = 1.0
    status: str = "uncalibrated"
    diagnostic: str = "sidecar_absent_or_disabled"
    method: str | None = None
    model_fingerprint: str | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "status": self.status,
            "temperature": self.temperature,
            "model_fingerprint": self.model_fingerprint,
            "diagnostic": self.diagnostic,
        }


def resolve_calibration(
    path: Path,
    *,
    enabled_mode: str,
    policy_model_type: str,
    model_fingerprint: str,
    candidate_policy_id: str,
    candidate_policy_fingerprint_value: str,
) -> CalibrationResolution:
    """Return T=1 on any sidecar problem without hiding main-model errors."""
    if enabled_mode not in {"off", "eligible", "experimental"}:
        return CalibrationResolution(diagnostic="invalid_feature_switch")
    if enabled_mode == "off" or not path.is_file():
        return CalibrationResolution(diagnostic="feature_disabled" if enabled_mode == "off" else "sidecar_absent")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CalibrationResolution(diagnostic="sidecar_corrupt")
    checks = (
        (raw.get("schema_version") == 1, "unsupported_schema"),
        (raw.get("method") == "global_temperature", "unsupported_method"),
        (raw.get("policy_model_type") == policy_model_type, "model_type_mismatch"),
        (raw.get("model_fingerprint") == model_fingerprint, "model_fingerprint_mismatch"),
        (raw.get("candidate_policy_id") == candidate_policy_id, "candidate_policy_mismatch"),
        (raw.get("candidate_policy_fingerprint") == candidate_policy_fingerprint_value, "candidate_policy_fingerprint_mismatch"),
    )
    for valid, diagnostic in checks:
        if not valid:
            return CalibrationResolution(diagnostic=diagnostic, model_fingerprint=model_fingerprint)
    status = str(raw.get("status") or "")
    allowed = {"eligible"} if enabled_mode == "eligible" else {"eligible", "experimental"}
    if status not in allowed:
        return CalibrationResolution(diagnostic=f"ineligible_status:{status or 'missing'}", model_fingerprint=model_fingerprint)
    try:
        temperature = float(raw["temperature"])
    except (KeyError, TypeError, ValueError):
        return CalibrationResolution(diagnostic="invalid_temperature", model_fingerprint=model_fingerprint)
    if not math.isfinite(temperature) or temperature <= 0:
        return CalibrationResolution(diagnostic="invalid_temperature", model_fingerprint=model_fingerprint)
    return CalibrationResolution(temperature, status, "ok", "global_temperature", model_fingerprint)

