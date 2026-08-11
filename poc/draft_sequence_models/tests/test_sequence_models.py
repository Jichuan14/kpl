from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


POC_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC_DIR))

from sequence_models import (  # noqa: E402
    AppCurrentChoiceModel,
    BagAblationModel,
    GRUChoiceModel,
    HybridBagGRUModel,
    HybridAppLagGRUModel,
    MAX_ACTIONS,
    ModelConfig,
    PairwiseResponseModel,
    chronological_windows,
    load_checkpoint,
    save_checkpoint,
)


def synthetic_batch(hero_count: int = 6) -> dict[str, torch.Tensor]:
    history_heroes = torch.full((2, MAX_ACTIONS), hero_count, dtype=torch.long)
    history_actions = torch.zeros((2, MAX_ACTIONS), dtype=torch.long)
    history_sides = torch.zeros((2, MAX_ACTIONS), dtype=torch.long)
    history_relations = torch.zeros((2, MAX_ACTIONS), dtype=torch.long)
    history_positions = torch.zeros((2, MAX_ACTIONS), dtype=torch.long)
    history_lags = torch.zeros((2, MAX_ACTIONS), dtype=torch.long)
    for row, heroes in enumerate(((1, 2), (2, 1))):
        history_heroes[row, :2] = torch.tensor(heroes)
        history_actions[row, :2] = torch.tensor((1, 1))
        history_sides[row, :2] = torch.tensor((2, 1))
        history_relations[row, :2] = torch.tensor((2, 1))
        history_positions[row, :2] = torch.tensor((5, 6))
        history_lags[row, :2] = torch.tensor((2, 1))
    legal = torch.ones((2, hero_count), dtype=torch.bool)
    legal[:, 5] = False
    return {
        "history_heroes": history_heroes,
        "history_actions": history_actions,
        "history_sides": history_sides,
        "history_relations": history_relations,
        "history_positions": history_positions,
        "history_lags": history_lags,
        "history_lengths": torch.tensor((2, 2)),
        "next_actions": torch.tensor((1, 1)),
        "next_sides": torch.tensor((1, 1)),
        "next_positions": torch.tensor((7, 7)),
        "next_team_slots": torch.tensor((2, 2)),
        "acting_teams": torch.tensor((1, 1)),
        "opponent_teams": torch.tensor((2, 2)),
        "legal_mask": legal,
        "targets": torch.tensor((0, 0)),
        "sample_weights": torch.ones(2),
    }


class SequenceModelTests(unittest.TestCase):
    def setUp(self) -> None:
        torch.manual_seed(11)
        self.config = ModelConfig(
            hero_count=6,
            team_count=2,
            feature_width=4,
            hidden_dim=8,
        )
        self.features = torch.arange(24, dtype=torch.float32).reshape(6, 4) / 24

    def test_gru_is_order_sensitive_and_masks_illegal_candidates(self) -> None:
        model = GRUChoiceModel(self.config, self.features).eval()
        logits = model(synthetic_batch())
        self.assertEqual(tuple(logits.shape), (2, 6))
        self.assertFalse(torch.allclose(logits[0, :5], logits[1, :5]))
        self.assertTrue(torch.all(logits[:, 5] < -1e8))

    def test_pairwise_model_exposes_per_action_contributions(self) -> None:
        model = PairwiseResponseModel(self.config, self.features).eval()
        batch = synthetic_batch()
        logits, contributions = model(batch, return_contributions=True)
        self.assertEqual(tuple(logits.shape), (2, 6))
        self.assertEqual(tuple(contributions.shape), (2, MAX_ACTIONS, 6))
        self.assertTrue(torch.allclose(contributions[:, 2:], torch.zeros_like(contributions[:, 2:])))

    def test_pairwise_model_changes_when_only_lag_changes(self) -> None:
        model = PairwiseResponseModel(self.config, self.features).eval()
        batch = synthetic_batch()
        changed = {name: value.clone() for name, value in batch.items()}
        changed["history_lags"][:, 0] = 7
        original_logits = model(batch)
        changed_logits = model(changed)
        self.assertFalse(torch.allclose(original_logits[:, :5], changed_logits[:, :5]))

    def test_rolling_window_excludes_all_later_matches(self) -> None:
        match_ids = [f"match-{index}" for index in range(10)]
        validation, holdout, future = chronological_windows(
            match_ids,
            validation_matches=2,
            holdout_matches=2,
            holdout_offset_matches=2,
        )
        self.assertEqual(validation, ["match-4", "match-5"])
        self.assertEqual(holdout, ["match-6", "match-7"])
        self.assertEqual(future, ["match-8", "match-9"])

    def test_hybrid_starts_from_and_freezes_selected_bag(self) -> None:
        bag = BagAblationModel(self.config, self.features).eval()
        hybrid = HybridBagGRUModel(self.config, self.features).eval()
        hybrid.initialize_from_bag(bag)
        with torch.no_grad():
            hybrid.residual_scale_logit.fill_(-20.0)
        batch = synthetic_batch()
        self.assertTrue(
            torch.allclose(bag(batch), hybrid(batch), atol=1e-6, rtol=1e-6)
        )
        self.assertTrue(all(not parameter.requires_grad for parameter in hybrid.bag.parameters()))
        self.assertTrue(any(parameter.requires_grad for parameter in hybrid.gru.parameters()))

    def test_current_app_port_masks_illegal_candidates(self) -> None:
        model = AppCurrentChoiceModel(self.config, self.features).eval()
        logits = model(synthetic_batch())
        self.assertEqual(tuple(logits.shape), (2, 6))
        self.assertTrue(torch.all(logits[:, 5] < -1e8))

    def test_current_app_port_uses_exact_context_not_only_position(self) -> None:
        model = AppCurrentChoiceModel(self.config, self.features).eval()
        batch = synthetic_batch()
        changed = {name: value.clone() for name, value in batch.items()}
        changed["next_sides"][:] = 2
        self.assertFalse(
            torch.allclose(model(batch)[:, :5], model(changed)[:, :5])
        )

    def test_app_lag_gru_hybrid_starts_from_and_freezes_app(self) -> None:
        app = AppCurrentChoiceModel(self.config, self.features).eval()
        hybrid = HybridAppLagGRUModel(self.config, self.features).eval()
        hybrid.initialize_from_app(app)
        with torch.no_grad():
            hybrid.gate.bias.fill_(-30.0)
        batch = synthetic_batch()
        self.assertTrue(
            torch.allclose(app(batch), hybrid(batch), atol=1e-6, rtol=1e-6)
        )
        self.assertTrue(
            all(not parameter.requires_grad for parameter in hybrid.app.parameters())
        )
        self.assertTrue(
            any(parameter.requires_grad for parameter in hybrid.gru.parameters())
        )

    def test_app_lag_gru_hybrid_responds_to_explicit_lag(self) -> None:
        app = AppCurrentChoiceModel(self.config, self.features).eval()
        hybrid = HybridAppLagGRUModel(self.config, self.features).eval()
        hybrid.initialize_from_app(app)
        batch = synthetic_batch()
        changed = {name: value.clone() for name, value in batch.items()}
        changed["history_lags"][:, 0] = 7
        self.assertFalse(
            torch.allclose(hybrid(batch)[:, :5], hybrid(changed)[:, :5])
        )


if __name__ == "__main__":
    unittest.main()
