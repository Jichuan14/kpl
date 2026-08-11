from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch


POC_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = POC_DIR.parents[1]
sys.path.insert(0, str(POC_DIR))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.sequence_model_runtime import (  # noqa: E402
    prepare_sequence_parameters,
    sequence_logits,
)
from sequence_models import MAX_ACTIONS, load_checkpoint  # noqa: E402


class ProductionSequenceRuntimeParityTest(unittest.TestCase):
    def test_numpy_export_matches_pytorch_logits(self) -> None:
        checkpoint_path = POC_DIR / "artifacts" / "hybrid_bag_gru.pt"
        checkpoint = torch.load(
            checkpoint_path, map_location="cpu", weights_only=False
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact_path = Path(temporary_directory) / "sequence-model.json"
            subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "analysis" / "export_sequence_draft_choice_model.py"),
                    "--league-id",
                    "20260003",
                    "--checkpoint",
                    str(checkpoint_path),
                    "--experiment-results",
                    str(POC_DIR / "artifacts" / "results.json"),
                    "--output",
                    str(artifact_path),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        prepared = prepare_sequence_parameters(
            artifact, checkpoint["state_dict"]["bag.hero_features"].numpy()
        )
        model = load_checkpoint(checkpoint_path, torch.device("cpu"))
        hero_count = int(checkpoint["config"]["hero_count"])
        pad = hero_count
        history_heroes = torch.full((1, MAX_ACTIONS), pad, dtype=torch.long)
        history_actions = torch.zeros((1, MAX_ACTIONS), dtype=torch.long)
        history_sides = torch.zeros((1, MAX_ACTIONS), dtype=torch.long)
        history_relations = torch.zeros((1, MAX_ACTIONS), dtype=torch.long)
        history_positions = torch.zeros((1, MAX_ACTIONS), dtype=torch.long)
        history_lags = torch.zeros((1, MAX_ACTIONS), dtype=torch.long)
        history_heroes[0, :3] = torch.tensor((1, 2, 3))
        history_actions[0, :3] = torch.tensor((2, 2, 1))
        history_sides[0, :3] = torch.tensor((1, 2, 1))
        history_relations[0, :3] = torch.tensor((4, 3, 2))
        history_positions[0, :3] = torch.tensor((1, 2, 3))
        history_lags[0, :3] = torch.tensor((3, 2, 1))
        legal = torch.ones((1, hero_count), dtype=torch.bool)
        legal[0, 1:4] = False
        batch = {
            "history_heroes": history_heroes,
            "history_actions": history_actions,
            "history_sides": history_sides,
            "history_relations": history_relations,
            "history_positions": history_positions,
            "history_lags": history_lags,
            "history_lengths": torch.tensor((3,)),
            "next_actions": torch.tensor((1,)),
            "next_sides": torch.tensor((2,)),
            "next_positions": torch.tensor((4,)),
            "next_team_slots": torch.tensor((1,)),
            "acting_teams": torch.tensor((1,)),
            "opponent_teams": torch.tensor((2,)),
            "legal_mask": legal,
            "targets": torch.tensor((0,)),
            "sample_weights": torch.ones(1),
        }
        with torch.inference_mode():
            expected = model(batch)[0].numpy()
        actual = sequence_logits(
            prepared,
            history_heroes=np.asarray([1, 2, 3], dtype=np.int64),
            history_actions=np.asarray([2, 2, 1], dtype=np.int64),
            history_sides=np.asarray([1, 2, 1], dtype=np.int64),
            history_relations=np.asarray([4, 3, 2], dtype=np.int64),
            history_positions=np.asarray([1, 2, 3], dtype=np.int64),
            next_action=1,
            next_side=2,
            next_position=4,
            next_team_slot=1,
            acting_team=1,
            opponent_team=2,
            legal_mask=legal[0].numpy(),
        )
        np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=5e-6)


if __name__ == "__main__":
    unittest.main()
