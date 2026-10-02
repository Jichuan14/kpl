"""Shared statistical I/O and confidence intervals; no season or legal-pool policy."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from common import connect


def read_decisions(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {path}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"Line {line_number} of {path} is not a JSON object"
                )
            rows.append(row)
    return rows


def wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    if trials <= 0:
        return 0.0, 0.0
    z = 1.959963984540054
    probability = successes / trials
    denominator = 1 + z * z / trials
    center = (
        probability + z * z / (2 * trials)
    ) / denominator
    margin = (
        z
        * math.sqrt(
            probability * (1 - probability) / trials
            + z * z / (4 * trials * trials)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def load_hero_metadata(
    db_path: Path,
) -> tuple[dict[int, str], dict[int, str]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT hero_id, hero_name, hero_icon FROM heroes WHERE hero_id > 0"
        ).fetchall()
    names = {int(row["hero_id"]): row["hero_name"] or "" for row in rows}
    icons = {int(row["hero_id"]): row["hero_icon"] or "" for row in rows}
    return names, icons


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected an object in {path}:{line_number}")
            rows.append(row)
    return rows
