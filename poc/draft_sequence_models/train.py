#!/usr/bin/env python3
"""Compatibility entry point for historical POC commands."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAINING_DIR = REPO_ROOT / "analysis" / "sequence_training"
if str(TRAINING_DIR) not in sys.path:
    sys.path.insert(0, str(TRAINING_DIR))

from train import main  # noqa: E402


if __name__ == "__main__":
    if "--output-dir" not in sys.argv:
        sys.argv.extend(
            ["--output-dir", str(Path(__file__).resolve().parent / "artifacts")]
        )
    main()
