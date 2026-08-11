"""Compatibility import for historical POC commands and tests.

The maintained implementation lives in ``analysis/sequence_training``.
"""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAINING_DIR = REPO_ROOT / "analysis" / "sequence_training"
if str(TRAINING_DIR) not in sys.path:
    sys.path.insert(0, str(TRAINING_DIR))

from models import *  # noqa: E402,F401,F403
