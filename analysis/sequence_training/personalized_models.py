"""Production familiarity residual on a frozen sequence behavior policy."""

from __future__ import annotations

import torch
from torch import Tensor, nn


def _bounded_residual(raw: Tensor, legal: Tensor, coverage: Tensor) -> tuple[Tensor, Tensor]:
    count = legal.sum(dim=1, keepdim=True).clamp_min(1)
    centered = raw - raw.masked_fill(~legal, 0).sum(dim=1, keepdim=True) / count
    delta = centered.tanh().masked_fill(~legal, 0)
    gate = coverage.clamp(0, 1)
    return gate * delta, gate


class FamiliarityResidual(nn.Module):
    """Action-specific linear comparator over nine safe candidate features."""

    model_name = "familiarity_residual"

    def __init__(self, feature_width: int = 9):
        super().__init__()
        self.pick_head = nn.Linear(feature_width, 1)
        self.ban_head = nn.Linear(feature_width, 1)
        nn.init.zeros_(self.pick_head.weight); nn.init.zeros_(self.pick_head.bias)
        nn.init.zeros_(self.ban_head.weight); nn.init.zeros_(self.ban_head.bias)
        self._last_gate = torch.tensor(0.0)

    def forward(self, base_logits: Tensor, candidate_features: Tensor, next_actions: Tensor, legal_mask: Tensor) -> Tensor:
        pick = self.pick_head(candidate_features).squeeze(-1)
        ban = self.ban_head(candidate_features).squeeze(-1)
        raw = torch.where(next_actions[:, None].eq(1), pick, ban)
        coverage = candidate_features[:, 0, 6:7]
        residual, gate = _bounded_residual(raw, legal_mask, coverage)
        self._last_gate = gate
        return (base_logits + residual).masked_fill(~legal_mask, -1e9)

    def regularization_loss(self) -> Tensor:
        return 1e-3 * self._last_gate.square().mean()

