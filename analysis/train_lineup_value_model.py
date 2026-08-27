#!/usr/bin/env python3
"""Train a season-scoped completed-lineup value model for management."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
TRAINER_PATH = REPO_ROOT / "analysis" / "lineup_value" / "training.py"
DEFAULT_DB = REPO_ROOT / "backend" / "data" / "kpl_bp.db"
DEFAULT_MECHANICS = REPO_ROOT / "analysis" / "hero_draft_feature_vectors.json"


def load_trainer():
    spec = importlib.util.spec_from_file_location(
        "managed_lineup_value_training", TRAINER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load lineup value trainer: {TRAINER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--mechanics", type=Path, default=DEFAULT_MECHANICS)
    parser.add_argument("--trials", type=int, default=32)
    parser.add_argument("--seed", type=int, default=17)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.trials < 1:
        raise ValueError("--trials must be positive")
    output_dir = args.output_dir.resolve()
    trainer = load_trainer()
    return int(
        trainer.main(
            [
                "tune",
                "--db",
                str(args.db.resolve()),
                "--mechanics",
                str(args.mechanics.resolve()),
                "--trials",
                str(args.trials),
                "--seed",
                str(args.seed),
                "--target-league-id",
                args.league_id,
                "--model-output",
                str(output_dir / "lineup_value_model.json"),
                "--search-output",
                str(output_dir / "lineup_value_parameter_search.json"),
                "--validation-output",
                str(output_dir / "lineup_value_validation.json"),
            ]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
