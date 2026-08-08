"""Combine specialty and Tencent-derived mechanics into ML-ready hero vectors."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from common import REPO_ROOT

DEFAULT_SPECIALTY_PATH = REPO_ROOT / "analysis" / "hero_specialty_vectors_thermometer.json"
DEFAULT_MECHANICS_PATH = REPO_ROOT / "analysis" / "hero_ability_mechanics.json"
DEFAULT_OUTPUT = REPO_ROOT / "analysis" / "hero_draft_feature_vectors.json"


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
    if not isinstance(data, dict):
        raise ValueError(f"Expected an object in {path}")
    return data


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_feature_artifact(
    specialty: dict[str, Any],
    mechanics: dict[str, Any],
    *,
    specialty_path: Path,
    mechanics_path: Path,
) -> dict[str, Any]:
    if specialty.get("schema_version") != 1:
        raise ValueError("Unsupported specialty feature schema")
    if mechanics.get("schema_version") != 1:
        raise ValueError("Unsupported hero mechanics schema")

    specialty_names = [str(name) for name in specialty.get("feature_names", [])]
    mechanic_names = sorted(str(name) for name in mechanics.get("taxonomy", {}).get("mechanics", {}))
    condition_names = sorted(str(name) for name in mechanics.get("taxonomy", {}).get("conditions", {}))
    if not specialty_names or not mechanic_names or not condition_names:
        raise ValueError("Feature inputs are missing a required taxonomy")

    specialty_by_id = {
        int(row["hero_id"]): row
        for row in specialty.get("rows", [])
        if row.get("hero_id") is not None
    }
    mechanics_by_id = {
        int(row["hero_id"]): row
        for row in mechanics.get("heroes", [])
        if row.get("hero_id") is not None
    }
    hero_ids = sorted(set(specialty_by_id) | set(mechanics_by_id))
    rows: list[dict[str, Any]] = []
    for hero_id in hero_ids:
        specialty_row = specialty_by_id.get(hero_id)
        mechanics_row = mechanics_by_id.get(hero_id)
        legacy_vector = (
            [float(value) for value in specialty_row.get("vector", [])]
            if specialty_row is not None
            else [0.0] * len(specialty_names)
        )
        if len(legacy_vector) != len(specialty_names):
            raise ValueError(f"Invalid specialty vector width for hero {hero_id}")
        hero_mechanics = set(mechanics_row.get("mechanics", [])) if mechanics_row else set()
        hero_conditions = set(mechanics_row.get("conditions", [])) if mechanics_row else set()
        vector = [
            *legacy_vector,
            *(1.0 if tag in hero_mechanics else 0.0 for tag in mechanic_names),
            *(1.0 if tag in hero_conditions else 0.0 for tag in condition_names),
            float(bool(specialty_row and specialty_row.get("feature_known"))),
            float(mechanics_row is not None),
        ]
        rows.append(
            {
                "hero_id": hero_id,
                "hero_name": str(
                    (mechanics_row or specialty_row or {}).get("hero_name") or hero_id
                ),
                "feature_known": bool(
                    (specialty_row and specialty_row.get("feature_known"))
                    or mechanics_row is not None
                ),
                "vector": vector,
            }
        )

    feature_names = [
        *specialty_names,
        *(f"mechanic__{tag}" for tag in mechanic_names),
        *(f"condition__{tag}" for tag in condition_names),
        "legacy_feature_known",
        "mechanics_feature_known",
    ]
    return {
        "schema_version": 1,
        "artifact_type": "hero_draft_feature_vectors",
        "source": {
            "specialty_path": str(specialty_path.relative_to(REPO_ROOT)),
            "specialty_sha256": sha256(specialty_path),
            "mechanics_path": str(mechanics_path.relative_to(REPO_ROOT)),
            "mechanics_sha256": sha256(mechanics_path),
        },
        "feature_names": feature_names,
        "coverage": {
            "hero_count": len(rows),
            "specialty_feature_count": len(specialty_names),
            "mechanics_feature_count": len(mechanic_names),
            "condition_feature_count": len(condition_names),
            "feature_width": len(feature_names),
            "heroes_with_mechanics": len(mechanics_by_id),
            "heroes_with_legacy_specialties": sum(
                bool(row.get("feature_known")) for row in specialty_by_id.values()
            ),
        },
        "rows": rows,
    }


def validate(artifact: dict[str, Any]) -> None:
    names = artifact.get("feature_names", [])
    rows = artifact.get("rows", [])
    if not names or len(names) != len(set(names)):
        raise ValueError("Feature names must be present and unique")
    if len({row["hero_id"] for row in rows}) != len(rows):
        raise ValueError("Hero IDs must be unique")
    for row in rows:
        if len(row.get("vector", [])) != len(names):
            raise ValueError(f"Invalid feature width for hero {row.get('hero_id')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specialty", type=Path, default=DEFAULT_SPECIALTY_PATH)
    parser.add_argument("--mechanics", type=Path, default=DEFAULT_MECHANICS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    specialty_path = arguments.specialty.resolve()
    mechanics_path = arguments.mechanics.resolve()
    artifact = build_feature_artifact(
        read_json(specialty_path),
        read_json(mechanics_path),
        specialty_path=specialty_path,
        mechanics_path=mechanics_path,
    )
    validate(artifact)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(arguments.output), **artifact["coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
