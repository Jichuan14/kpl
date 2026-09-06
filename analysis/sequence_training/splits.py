"""Chronological, whole-series split manifests for draft-policy experiments."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


SPLIT_NAMES = ("train", "validation", "calibration", "holdout")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(value, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temporary = Path(tmp.name)
    temporary.replace(path)


def _series(path: Path, eligible_match_ids: set[str] | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            raw = json.loads(line)
            match_id = str(raw.get("match_id") or "")
            start_time = str(raw.get("start_time") or "")
            if not match_id or not start_time:
                raise ValueError(f"Missing match_id/start_time in {path}")
            if eligible_match_ids is not None and match_id not in eligible_match_ids:
                continue
            rows.append({"match_id": match_id, "start_time": start_time})
    if len({row["match_id"] for row in rows}) != len(rows):
        raise ValueError(f"Duplicate match IDs in {path}")
    return sorted(rows, key=lambda row: (row["start_time"], row["match_id"]))


def _take_date_grouped_tail(
    rows: list[dict[str, str]], count: int
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Take at least count rows without splitting equal-start-time groups."""
    if count < 1 or len(rows) < count:
        raise ValueError("Insufficient series for a requested chronological window")
    boundary = len(rows) - count
    boundary_time = rows[boundary]["start_time"]
    while boundary > 0 and rows[boundary - 1]["start_time"] == boundary_time:
        boundary -= 1
    return rows[:boundary], rows[boundary:]


def build_split_manifest(
    exports_dir: Path,
    *,
    target_season: str,
    source_seasons: list[str],
    validation_series: int = 10,
    calibration_series: int = 10,
    holdout_series: int = 10,
    holdout_offset_series: int = 0,
) -> dict[str, Any]:
    """Build a pinned provenance manifest with four disjoint series windows."""
    if target_season not in source_seasons or len(set(source_seasons)) != len(source_seasons):
        raise ValueError("Source seasons must be unique and include the target season")
    source_files: dict[str, dict[str, str]] = {}
    all_rows: dict[str, list[dict[str, str]]] = {}
    for season in source_seasons:
        matches = exports_dir / season / "matches.jsonl"
        decisions = exports_dir / season / "bp_decisions.jsonl"
        if not matches.is_file() or not decisions.is_file():
            raise FileNotFoundError(f"Missing local exports for season {season}")
        source_files[season] = {
            "matches": str(matches.resolve()),
            "matches_sha256": file_sha256(matches),
            "decisions": str(decisions.resolve()),
            "decisions_sha256": file_sha256(decisions),
        }
        eligible_match_ids: set[str] = set()
        with decisions.open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    row = json.loads(line)
                    if not row.get("is_peak_battle") and row.get("selected_hero_id"):
                        eligible_match_ids.add(str(row.get("match_id")))
        all_rows[season] = _series(matches, eligible_match_ids)

    target_rows = all_rows[target_season]
    if holdout_offset_series < 0 or holdout_offset_series >= len(target_rows):
        raise ValueError("Invalid holdout offset")
    excluded_future = target_rows[len(target_rows)-holdout_offset_series:] if holdout_offset_series else []
    target_rows = target_rows[:len(target_rows)-holdout_offset_series] if holdout_offset_series else target_rows
    earlier, holdout = _take_date_grouped_tail(target_rows, holdout_series)
    earlier, calibration = _take_date_grouped_tail(earlier, calibration_series)
    target_train, validation = _take_date_grouped_tail(earlier, validation_series)
    if len(target_train) < 10:
        raise ValueError("Target-season training block has fewer than 10 series")

    train = [
        {**row, "season": season}
        for season in source_seasons
        if season != target_season
        for row in all_rows[season]
    ] + [{**row, "season": target_season} for row in target_train]
    splits = {
        "train": train,
        "validation": [{**row, "season": target_season} for row in validation],
        "calibration": [{**row, "season": target_season} for row in calibration],
        "holdout": [{**row, "season": target_season} for row in holdout],
    }
    validate_split_manifest({"splits": splits})
    v_boundary = min(row["start_time"] for row in splits["validation"])
    if any(row["start_time"] >= v_boundary for row in splits["train"]):
        raise ValueError("A training series is not earlier than validation")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_season": target_season,
        "source_seasons": source_seasons,
        "source_files": source_files,
        "boundary_rule": "equal_start_times_move_to_later_window",
        "splits": splits,
        "counts": {name: len(rows) for name, rows in splits.items()},
        "holdout_offset_series": holdout_offset_series,
        "excluded_future": [{**row, "season": target_season} for row in excluded_future],
        "feature_vintage_limitation": (
            "Hero catalogue features are the pinned local artifact; historical snapshots are unavailable."
        ),
    }
    identity = {key: value for key, value in payload.items() if key != "generated_at"}
    payload["manifest_sha256"] = canonical_sha256(identity)
    return payload


def validate_split_manifest(manifest: dict[str, Any]) -> None:
    splits = manifest.get("splits", {})
    if set(splits) != set(SPLIT_NAMES):
        raise ValueError("Manifest must contain train/validation/calibration/holdout")
    seen: dict[str, str] = {}
    for split in SPLIT_NAMES:
        if not splits[split]:
            raise ValueError(f"Split {split} is empty")
        for row in splits[split]:
            key = f"{row.get('season')}:{row.get('match_id')}"
            if key in seen:
                raise ValueError(f"Series {key} overlaps {seen[key]} and {split}")
            seen[key] = split


def write_split_manifest(path: Path, manifest: dict[str, Any]) -> None:
    validate_split_manifest(manifest)
    _atomic_json(path, manifest)
