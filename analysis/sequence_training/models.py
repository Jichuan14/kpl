"""PyTorch sequence-aware draft-choice models used by production training."""

from __future__ import annotations

import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import torch
from torch import Tensor, nn
from torch.nn import functional as F


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.draft_strategy import (  # noqa: E402
    STRATEGIC_CONSTRAINT_VERSION,
    hero_lane_masks,
    second_ban_farm_conflicts,
)


MAX_ACTIONS = 20
ACTION_INDEX = {"pick": 1, "ban": 2}
SIDE_INDEX = {"blue": 1, "red": 2}
RELATION_INDEX = {
    ("pick", True): 1,
    ("pick", False): 2,
    ("ban", True): 3,
    ("ban", False): 4,
}


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "analysis" / "exports").is_dir():
            return candidate
    raise FileNotFoundError("Could not find the repository root")


@dataclass(frozen=True)
class ModelConfig:
    hero_count: int
    team_count: int
    feature_width: int
    hidden_dim: int = 48
    app_hidden_dim: int = 16
    hybrid_initial_residual_scale: float = 0.05
    hybrid_residual_scale_penalty: float = 1e-3


@dataclass
class TensorDataset:
    history_heroes: Tensor
    history_actions: Tensor
    history_sides: Tensor
    history_relations: Tensor
    history_positions: Tensor
    history_lags: Tensor
    history_lengths: Tensor
    next_actions: Tensor
    next_sides: Tensor
    next_positions: Tensor
    next_team_slots: Tensor
    acting_teams: Tensor
    opponent_teams: Tensor
    legal_mask: Tensor
    targets: Tensor
    sample_weights: Tensor
    match_ids: list[str]
    battle_ids: list[str]

    def __len__(self) -> int:
        return int(self.targets.shape[0])

    def batch(self, indices: Tensor, device: torch.device) -> dict[str, Tensor]:
        names = (
            "history_heroes",
            "history_actions",
            "history_sides",
            "history_relations",
            "history_positions",
            "history_lags",
            "history_lengths",
            "next_actions",
            "next_sides",
            "next_positions",
            "next_team_slots",
            "acting_teams",
            "opponent_teams",
            "legal_mask",
            "targets",
            "sample_weights",
        )
        return {
            name: getattr(self, name).index_select(0, indices).to(device)
            for name in names
        }


@dataclass
class PreparedData:
    train: TensorDataset
    validation: TensorDataset
    holdout: TensorDataset
    hero_ids: list[int]
    hero_names: dict[int, str]
    team_ids: list[str]
    feature_names: list[str]
    hero_features: Tensor
    training_seasons: list[str]
    season_weights: dict[str, float]
    validation_match_ids: list[str]
    holdout_match_ids: list[str]
    excluded_future_match_ids: list[str]


class DraftChoiceModel(nn.Module):
    """Shared embeddings and masked candidate scoring for each model family."""

    model_name = "base"

    def __init__(self, config: ModelConfig, hero_features: Tensor):
        super().__init__()
        self.config = config
        d = config.hidden_dim
        if tuple(hero_features.shape) != (config.hero_count, config.feature_width):
            raise ValueError("Hero feature matrix does not match the model config")

        self.register_buffer("hero_features", hero_features.float().clone())
        self.feature_projection = nn.Linear(config.feature_width, d, bias=False)
        self.hero_residual = nn.Embedding(config.hero_count + 1, d, padding_idx=config.hero_count)
        self.action_embedding = nn.Embedding(3, d, padding_idx=0)
        self.side_embedding = nn.Embedding(3, d, padding_idx=0)
        self.relation_embedding = nn.Embedding(5, d, padding_idx=0)
        self.position_embedding = nn.Embedding(MAX_ACTIONS + 1, d, padding_idx=0)
        self.lag_embedding = nn.Embedding(MAX_ACTIONS + 1, d, padding_idx=0)
        self.team_slot_embedding = nn.Embedding(6, d, padding_idx=0)
        self.acting_team_embedding = nn.Embedding(config.team_count + 1, d, padding_idx=0)
        self.opponent_team_embedding = nn.Embedding(config.team_count + 1, d, padding_idx=0)
        self.hero_bias = nn.Parameter(torch.zeros(config.hero_count))
        self.reset_parameters()

    def reset_parameters(self) -> None:
        for module in self.modules():
            if isinstance(module, nn.Embedding) and module is not self.hero_residual:
                nn.init.normal_(module.weight, mean=0.0, std=0.03)
                if module.padding_idx is not None:
                    with torch.no_grad():
                        module.weight[module.padding_idx].zero_()
        nn.init.normal_(self.hero_residual.weight, mean=0.0, std=0.03)
        with torch.no_grad():
            self.hero_residual.weight[self.config.hero_count].zero_()
        nn.init.xavier_uniform_(self.feature_projection.weight)

    def candidate_representations(self) -> Tensor:
        hero_indices = torch.arange(self.config.hero_count, device=self.hero_features.device)
        return self.feature_projection(self.hero_features) + self.hero_residual(hero_indices)

    def history_representations(self, history_heroes: Tensor) -> Tensor:
        candidates = self.candidate_representations()
        padding = torch.zeros(
            (1, self.config.hidden_dim),
            device=candidates.device,
            dtype=candidates.dtype,
        )
        return torch.cat((candidates, padding), dim=0)[history_heroes]

    def context_query(self, batch: dict[str, Tensor]) -> Tensor:
        return (
            self.action_embedding(batch["next_actions"])
            + self.side_embedding(batch["next_sides"])
            + self.position_embedding(batch["next_positions"])
            + self.team_slot_embedding(batch["next_team_slots"])
            + self.acting_team_embedding(batch["acting_teams"])
            + self.opponent_team_embedding(batch["opponent_teams"])
        )

    def apply_legal_mask(self, logits: Tensor, legal_mask: Tensor) -> Tensor:
        return logits.masked_fill(~legal_mask, -1e9)


class BagAblationModel(DraftChoiceModel):
    """Order-insensitive control using the shared production embeddings."""

    model_name = "bag_ablation"

    def __init__(self, config: ModelConfig, hero_features: Tensor):
        super().__init__(config, hero_features)
        self.source_projection = nn.Linear(config.hidden_dim, config.hidden_dim, bias=False)
        self.query_projection = nn.Linear(config.hidden_dim, config.hidden_dim)

    def forward(self, batch: dict[str, Tensor]) -> Tensor:
        source = (
            self.source_projection(self.history_representations(batch["history_heroes"]))
            + self.action_embedding(batch["history_actions"])
            + self.relation_embedding(batch["history_relations"])
        )
        valid = (
            torch.arange(MAX_ACTIONS, device=source.device)[None, :]
            < batch["history_lengths"][:, None]
        )
        source = torch.tanh(source) * valid[..., None]
        scale = batch["history_lengths"].clamp_min(1).sqrt().to(source.dtype)
        pooled = source.sum(dim=1) / scale[:, None]
        query = torch.tanh(self.query_projection(pooled) + self.context_query(batch))
        candidates = self.candidate_representations()
        logits = query @ candidates.T / math.sqrt(self.config.hidden_dim) + self.hero_bias
        return self.apply_legal_mask(logits, batch["legal_mask"])


class GRUChoiceModel(DraftChoiceModel):
    """Chronological action encoder followed by masked candidate scoring."""

    model_name = "gru"

    def __init__(self, config: ModelConfig, hero_features: Tensor):
        super().__init__(config, hero_features)
        self.gru = nn.GRU(config.hidden_dim, config.hidden_dim, batch_first=True)
        self.query_projection = nn.Linear(config.hidden_dim, config.hidden_dim)

    def forward(self, batch: dict[str, Tensor]) -> Tensor:
        tokens = (
            self.history_representations(batch["history_heroes"])
            + self.action_embedding(batch["history_actions"])
            + self.side_embedding(batch["history_sides"])
            + self.relation_embedding(batch["history_relations"])
            + self.position_embedding(batch["history_positions"])
        )
        outputs, _ = self.gru(tokens)
        last_indices = (batch["history_lengths"] - 1).clamp_min(0)
        last = outputs[
            torch.arange(outputs.shape[0], device=outputs.device),
            last_indices,
        ]
        last = torch.where(
            (batch["history_lengths"] > 0)[:, None],
            last,
            torch.zeros_like(last),
        )
        query = torch.tanh(self.query_projection(last) + self.context_query(batch))
        candidates = self.candidate_representations()
        logits = query @ candidates.T / math.sqrt(self.config.hidden_dim) + self.hero_bias
        return self.apply_legal_mask(logits, batch["legal_mask"])


class LagAwareGRUChoiceModel(GRUChoiceModel):
    """GRU branch with explicit distance from each action to the prediction."""

    model_name = "lag_aware_gru"

    def forward(self, batch: dict[str, Tensor]) -> Tensor:
        tokens = (
            self.history_representations(batch["history_heroes"])
            + self.action_embedding(batch["history_actions"])
            + self.side_embedding(batch["history_sides"])
            + self.relation_embedding(batch["history_relations"])
            + self.position_embedding(batch["history_positions"])
            + self.lag_embedding(batch["history_lags"])
        )
        outputs, _ = self.gru(tokens)
        last_indices = (batch["history_lengths"] - 1).clamp_min(0)
        last = outputs[
            torch.arange(outputs.shape[0], device=outputs.device),
            last_indices,
        ]
        last = torch.where(
            (batch["history_lengths"] > 0)[:, None],
            last,
            torch.zeros_like(last),
        )
        query = torch.tanh(self.query_projection(last) + self.context_query(batch))
        candidates = self.candidate_representations()
        logits = query @ candidates.T / math.sqrt(self.config.hidden_dim) + self.hero_bias
        return self.apply_legal_mask(logits, batch["legal_mask"])


class AppCurrentChoiceModel(DraftChoiceModel):
    """PyTorch architectural port of the app's current schema-v2 choice model."""

    model_name = "app_current"
    optimizer_kind = "adam"
    recommended_learning_rate = 0.005
    recommended_batch_size = 512
    clip_gradients = False

    def __init__(self, config: ModelConfig, hero_features: Tensor):
        nn.Module.__init__(self)
        self.config = config
        d = config.app_hidden_dim
        feature_width = config.feature_width
        hero_count = config.hero_count
        self.register_buffer("hero_features", hero_features.float().clone())
        self.feature_projection = nn.Parameter(torch.empty(feature_width, d))
        self.hero_residual = nn.Parameter(torch.empty(hero_count, d))
        # The production artifact sorts 20 (action, side, team-action-number)
        # keys: ban before pick, blue before red, then slots 1 through 5.
        self.context_embedding = nn.Parameter(torch.empty(20, d))
        self.state_projection = nn.Parameter(
            torch.empty(4 * (2 * feature_width + 1), d)
        )
        self.source_embedding = nn.Parameter(torch.empty(4, hero_count, d))
        self.acting_team_embedding = nn.Parameter(torch.empty(config.team_count + 1, d))
        self.opponent_team_embedding = nn.Parameter(torch.empty(config.team_count + 1, d))
        self.hero_bias = nn.Parameter(torch.zeros(hero_count))
        self.reset_parameters()

    def reset_parameters(self) -> None:
        for parameter in (
            self.feature_projection,
            self.hero_residual,
            self.context_embedding,
            self.state_projection,
            self.source_embedding,
            self.acting_team_embedding,
            self.opponent_team_embedding,
        ):
            nn.init.normal_(parameter, mean=0.0, std=0.03)
        with torch.no_grad():
            self.acting_team_embedding[0].zero_()
            self.opponent_team_embedding[0].zero_()

    def candidate_representations(self) -> Tensor:
        return self.hero_features @ self.feature_projection + self.hero_residual

    def static_state(self, batch: dict[str, Tensor]) -> Tensor:
        padding = torch.zeros(
            (1, self.config.feature_width),
            device=self.hero_features.device,
            dtype=self.hero_features.dtype,
        )
        history_features = torch.cat((self.hero_features, padding), dim=0)[
            batch["history_heroes"]
        ]
        pieces = []
        for relation in range(1, 5):
            mask = batch["history_relations"].eq(relation)
            masked = history_features * mask[..., None]
            count = mask.sum(dim=1, keepdim=True).to(history_features.dtype)
            maximum = history_features.masked_fill(
                ~mask[..., None], -torch.inf
            ).amax(dim=1)
            maximum = torch.where(count > 0, maximum, torch.zeros_like(maximum))
            pieces.extend((masked.sum(dim=1), maximum, count))
        return torch.cat(pieces, dim=1)

    def forward(self, batch: dict[str, Tensor]) -> Tensor:
        candidates = self.candidate_representations()
        valid = (
            torch.arange(MAX_ACTIONS, device=batch["history_heroes"].device)[None, :]
            < batch["history_lengths"][:, None]
        )
        source = self.source_embedding[
            (batch["history_relations"] - 1).clamp(min=0, max=3),
            batch["history_heroes"].clamp_max(self.config.hero_count - 1),
        ]
        source = (source * valid[..., None]).sum(dim=1)
        context_indices = (
            batch["next_actions"].eq(ACTION_INDEX["pick"]).long() * 10
            + batch["next_sides"].eq(SIDE_INDEX["red"]).long() * 5
            + batch["next_team_slots"]
            - 1
        )
        query = (
            self.context_embedding[context_indices]
            + self.static_state(batch) @ self.state_projection
            + source
            + self.acting_team_embedding[batch["acting_teams"]]
            + self.opponent_team_embedding[batch["opponent_teams"]]
        )
        logits = query @ candidates.T + self.hero_bias
        return logits.masked_fill(~batch["legal_mask"], -1e9)


class PairwiseResponseModel(DraftChoiceModel):
    """Additive candidate/action responses with explicit absolute and relative time."""

    model_name = "pairwise"

    def __init__(self, config: ModelConfig, hero_features: Tensor):
        super().__init__(config, hero_features)
        self.source_projection = nn.Linear(config.hidden_dim, config.hidden_dim, bias=False)
        self.response_gate = nn.Linear(config.hidden_dim, config.hidden_dim)
        self.context_projection = nn.Linear(config.hidden_dim, config.hidden_dim)

    def response_contributions(
        self,
        batch: dict[str, Tensor],
        candidates: Tensor,
    ) -> Tensor:
        source = (
            self.source_projection(self.history_representations(batch["history_heroes"]))
            + self.action_embedding(batch["history_actions"])
            + self.side_embedding(batch["history_sides"])
            + self.relation_embedding(batch["history_relations"])
            + self.position_embedding(batch["history_positions"])
            + self.lag_embedding(batch["history_lags"])
        )
        response = torch.tanh(source) * torch.sigmoid(self.response_gate(source))
        valid = (
            torch.arange(MAX_ACTIONS, device=response.device)[None, :]
            < batch["history_lengths"][:, None]
        )
        response = response * valid[..., None]
        scale = batch["history_lengths"].clamp_min(1).sqrt().to(response.dtype)
        response = response / scale[:, None, None]
        return torch.einsum("bld,hd->blh", response, candidates) / math.sqrt(
            self.config.hidden_dim
        )

    def forward(
        self,
        batch: dict[str, Tensor],
        *,
        return_contributions: bool = False,
    ) -> Tensor | tuple[Tensor, Tensor]:
        candidates = self.candidate_representations()
        base_query = torch.tanh(self.context_projection(self.context_query(batch)))
        base_logits = base_query @ candidates.T / math.sqrt(self.config.hidden_dim)
        contributions = self.response_contributions(batch, candidates)
        logits = base_logits + contributions.sum(dim=1) + self.hero_bias
        logits = self.apply_legal_mask(logits, batch["legal_mask"])
        if return_contributions:
            return logits, contributions
        return logits


class HybridBagGRUModel(DraftChoiceModel):
    """Frozen bag baseline plus a bounded, regularized GRU logit residual."""

    model_name = "hybrid_bag_gru"

    def __init__(self, config: ModelConfig, hero_features: Tensor):
        # This composite intentionally owns two independent branches instead of
        # creating a third unused copy of DraftChoiceModel's shared embeddings.
        nn.Module.__init__(self)
        self.config = config
        self.bag = BagAblationModel(config, hero_features)
        self.gru = GRUChoiceModel(config, hero_features)
        initial_scale = min(max(config.hybrid_initial_residual_scale, 1e-4), 1 - 1e-4)
        self.residual_scale_logit = nn.Parameter(
            torch.tensor(math.log(initial_scale / (1.0 - initial_scale)))
        )
        self._bag_frozen = False

    @property
    def residual_scale(self) -> Tensor:
        return self.residual_scale_logit.sigmoid()

    def initialize_from_bag(self, bag: BagAblationModel) -> None:
        """Copy a selected bag checkpoint and freeze it as the baseline."""
        self.bag.load_state_dict(bag.state_dict())
        for parameter in self.bag.parameters():
            parameter.requires_grad_(False)
        self.bag.eval()
        self._bag_frozen = True

    def regularization_loss(self) -> Tensor:
        return self.config.hybrid_residual_scale_penalty * self.residual_scale.square()

    def diagnostics(self) -> dict[str, float]:
        return {"residual_scale": float(self.residual_scale.detach().cpu().item())}

    def forward(self, batch: dict[str, Tensor]) -> Tensor:
        if self._bag_frozen:
            with torch.no_grad():
                bag_logits = self.bag(batch)
        else:
            bag_logits = self.bag(batch)
        gru_logits = self.gru(batch)
        legal = batch["legal_mask"]
        legal_count = legal.sum(dim=1, keepdim=True).clamp_min(1)
        legal_gru_sum = gru_logits.masked_fill(~legal, 0.0).sum(dim=1, keepdim=True)
        centered_gru = gru_logits - legal_gru_sum / legal_count
        combined = bag_logits + self.residual_scale * centered_gru
        return combined.masked_fill(~legal, -1e9)


class HybridAppLagGRUModel(DraftChoiceModel):
    """Frozen current-app model plus normalized lag-GRU residual and dynamic gate."""

    model_name = "hybrid_app_lag_gru"

    def __init__(self, config: ModelConfig, hero_features: Tensor):
        nn.Module.__init__(self)
        self.config = config
        self.app = AppCurrentChoiceModel(config, hero_features)
        self.gru = LagAwareGRUChoiceModel(config, hero_features)
        max_scale = 0.5
        initial_ratio = config.hybrid_initial_residual_scale / max_scale
        initial_ratio = min(max(initial_ratio, 1e-4), 1 - 1e-4)
        self.register_buffer("maximum_residual_scale", torch.tensor(max_scale))
        self.gate = nn.Linear(3, 1)
        nn.init.zeros_(self.gate.weight)
        nn.init.constant_(self.gate.bias, math.log(initial_ratio / (1 - initial_ratio)))
        self._app_frozen = False
        self._last_gate_penalty: Tensor | None = None

    def initialize_from_app(self, app: AppCurrentChoiceModel) -> None:
        self.app.load_state_dict(app.state_dict())
        for parameter in self.app.parameters():
            parameter.requires_grad_(False)
        self.app.eval()
        self._app_frozen = True

    def diagnostics(self) -> dict[str, Any]:
        return {
            "maximum_residual_scale": float(self.maximum_residual_scale.item()),
            "gate_weights": [
                float(value) for value in self.gate.weight.detach().cpu().flatten()
            ],
            "gate_bias": float(self.gate.bias.detach().cpu().item()),
        }

    def regularization_loss(self) -> Tensor:
        if self._last_gate_penalty is None:
            return self.gate.weight.square().mean() * 0.0
        return self.config.hybrid_residual_scale_penalty * self._last_gate_penalty

    def forward(self, batch: dict[str, Tensor]) -> Tensor:
        if self._app_frozen:
            with torch.no_grad():
                app_logits = self.app(batch)
        else:
            app_logits = self.app(batch)
        gru_logits = self.gru(batch)
        legal = batch["legal_mask"]
        legal_count = legal.sum(dim=1, keepdim=True).clamp_min(1)
        legal_gru = gru_logits.masked_fill(~legal, 0.0)
        mean = legal_gru.sum(dim=1, keepdim=True) / legal_count
        centered = (gru_logits - mean).masked_fill(~legal, 0.0)
        variance = centered.square().sum(dim=1, keepdim=True) / legal_count
        normalized_residual = centered / (variance + 1e-6).sqrt()

        app_probabilities = app_logits.softmax(dim=1)
        entropy = -(
            app_probabilities
            * app_probabilities.clamp_min(1e-12).log()
        ).sum(dim=1, keepdim=True)
        normalized_entropy = entropy / math.log(self.config.hero_count)
        gate_features = torch.cat(
            (
                batch["next_positions"][:, None].to(app_logits.dtype) / MAX_ACTIONS,
                batch["history_lengths"][:, None].to(app_logits.dtype) / MAX_ACTIONS,
                normalized_entropy,
            ),
            dim=1,
        )
        scale = self.maximum_residual_scale * self.gate(gate_features).sigmoid()
        self._last_gate_penalty = scale.square().mean()
        combined = app_logits + scale * normalized_residual
        return combined.masked_fill(~legal, -1e9)


MODEL_TYPES: dict[str, type[DraftChoiceModel]] = {
    AppCurrentChoiceModel.model_name: AppCurrentChoiceModel,
    BagAblationModel.model_name: BagAblationModel,
    GRUChoiceModel.model_name: GRUChoiceModel,
    PairwiseResponseModel.model_name: PairwiseResponseModel,
    HybridBagGRUModel.model_name: HybridBagGRUModel,
    HybridAppLagGRUModel.model_name: HybridAppLagGRUModel,
}


def _usable(row: dict[str, Any], hero_to_index: dict[int, int]) -> bool:
    selected = int(row.get("selected_hero_id") or 0)
    legal = {int(hero_id) for hero_id in row.get("legal_hero_ids", [])}
    return bool(
        not row.get("is_peak_battle")
        and row.get("action") in ACTION_INDEX
        and row.get("side") in SIDE_INDEX
        and row.get("acting_team_id")
        and row.get("opponent_team_id")
        and selected in hero_to_index
        and selected in legal
    )


def _ordered_match_ids(
    matches_path: Path,
    eligible_match_ids: set[str],
) -> list[str]:
    matches = []
    with matches_path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                row = json.loads(line)
                match_id = str(row["match_id"])
                if match_id in eligible_match_ids:
                    matches.append((str(row.get("start_time") or ""), match_id))
    matches.sort()
    return [match_id for _, match_id in matches]


def chronological_windows(
    ordered_match_ids: list[str],
    *,
    validation_matches: int,
    holdout_matches: int,
    holdout_offset_matches: int,
) -> tuple[list[str], list[str], list[str]]:
    """Return validation, holdout, and later matches excluded from training."""
    if min(validation_matches, holdout_matches) < 1:
        raise ValueError("Validation and holdout windows must be non-empty")
    if holdout_offset_matches < 0:
        raise ValueError("Holdout offset cannot be negative")
    end = len(ordered_match_ids) - holdout_offset_matches
    start = end - validation_matches - holdout_matches
    if start < 0 or end <= 0:
        raise ValueError("Not enough completed matches for the requested rolling window")
    validation_end = start + validation_matches
    return (
        ordered_match_ids[start:validation_end],
        ordered_match_ids[validation_end:end],
        ordered_match_ids[end:],
    )


def _empty_lists() -> dict[str, list[Any]]:
    return {
        "history_heroes": [],
        "history_actions": [],
        "history_sides": [],
        "history_relations": [],
        "history_positions": [],
        "history_lags": [],
        "history_lengths": [],
        "next_actions": [],
        "next_sides": [],
        "next_positions": [],
        "next_team_slots": [],
        "acting_teams": [],
        "opponent_teams": [],
        "legal_mask": [],
        "targets": [],
        "sample_weights": [],
        "match_ids": [],
        "battle_ids": [],
    }


def _as_tensor_dataset(values: dict[str, list[Any]]) -> TensorDataset:
    tensor_fields = {
        "history_heroes": torch.tensor(values["history_heroes"], dtype=torch.long),
        "history_actions": torch.tensor(values["history_actions"], dtype=torch.long),
        "history_sides": torch.tensor(values["history_sides"], dtype=torch.long),
        "history_relations": torch.tensor(values["history_relations"], dtype=torch.long),
        "history_positions": torch.tensor(values["history_positions"], dtype=torch.long),
        "history_lags": torch.tensor(values["history_lags"], dtype=torch.long),
        "history_lengths": torch.tensor(values["history_lengths"], dtype=torch.long),
        "next_actions": torch.tensor(values["next_actions"], dtype=torch.long),
        "next_sides": torch.tensor(values["next_sides"], dtype=torch.long),
        "next_positions": torch.tensor(values["next_positions"], dtype=torch.long),
        "next_team_slots": torch.tensor(values["next_team_slots"], dtype=torch.long),
        "acting_teams": torch.tensor(values["acting_teams"], dtype=torch.long),
        "opponent_teams": torch.tensor(values["opponent_teams"], dtype=torch.long),
        "legal_mask": torch.tensor(values["legal_mask"], dtype=torch.bool),
        "targets": torch.tensor(values["targets"], dtype=torch.long),
        "sample_weights": torch.tensor(values["sample_weights"], dtype=torch.float32),
    }
    return TensorDataset(
        **tensor_fields,
        match_ids=list(values["match_ids"]),
        battle_ids=list(values["battle_ids"]),
    )


def prepare_data(
    repo_root: Path,
    *,
    target_season: str,
    previous_seasons: int,
    validation_matches: int,
    holdout_matches: int,
    holdout_offset_matches: int,
    recency_decay: float,
    winning_pick_weight: float,
) -> PreparedData:
    """Reconstruct chronological prefixes from existing decision exports."""
    analysis_dir = repo_root / "analysis"
    exports_dir = analysis_dir / "exports"
    available = {
        path.parent.name: path
        for path in exports_dir.glob("*/bp_decisions.jsonl")
    }
    seasons = sorted(available)
    if target_season not in seasons:
        raise ValueError(f"No decision export for {target_season}")
    target_index = seasons.index(target_season)
    training_seasons = seasons[
        max(0, target_index - previous_seasons) : target_index + 1
    ]
    if len(training_seasons) != previous_seasons + 1:
        raise ValueError("Not enough previous seasons for the requested window")
    season_weights = {
        season: recency_decay ** (len(training_seasons) - 1 - index)
        for index, season in enumerate(training_seasons)
    }

    feature_artifact = json.loads(
        (analysis_dir / "hero_draft_feature_vectors.json").read_text(encoding="utf-8")
    )
    hero_ids = sorted(int(row["hero_id"]) for row in feature_artifact["rows"])
    hero_to_index = {hero_id: index for index, hero_id in enumerate(hero_ids)}
    feature_rows = {int(row["hero_id"]): row for row in feature_artifact["rows"]}
    hero_features = torch.tensor(
        [
            [
                *feature_rows[hero_id]["vector"],
                float(feature_rows[hero_id].get("feature_known", True)),
            ]
            for hero_id in hero_ids
        ],
        dtype=torch.float32,
    )
    feature_names = [*feature_artifact["feature_names"], "feature_known"]
    lane_masks = hero_lane_masks(
        hero_ids,
        feature_names,
        hero_features.tolist(),
    )
    hero_names = {
        hero_id: str(feature_rows[hero_id].get("hero_name") or hero_id)
        for hero_id in hero_ids
    }

    rows_by_battle: dict[tuple[str, str], list[dict[str, Any]]] = {}
    all_rows: list[dict[str, Any]] = []
    for season in training_seasons:
        with available[season].open(encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                row = json.loads(line)
                row["_season"] = season
                all_rows.append(row)
                rows_by_battle.setdefault(
                    (season, str(row["battle_id"])), []
                ).append(row)

    ordered_match_ids = _ordered_match_ids(
        exports_dir / target_season / "matches.jsonl",
        {
            str(row["match_id"])
            for row in all_rows
            if row["_season"] == target_season
        },
    )
    (
        validation_match_ids,
        holdout_match_ids,
        excluded_future_match_ids,
    ) = chronological_windows(
        ordered_match_ids,
        validation_matches=validation_matches,
        holdout_matches=holdout_matches,
        holdout_offset_matches=holdout_offset_matches,
    )
    validation_set = set(validation_match_ids)
    holdout_set = set(holdout_match_ids)
    excluded_future_set = set(excluded_future_match_ids)
    team_ids = sorted(
        {
            str(row[field])
            for row in all_rows
            if not (
                row["_season"] == target_season
                and str(row["match_id"])
                in validation_set | holdout_set | excluded_future_set
            )
            for field in ("acting_team_id", "opponent_team_id")
            if row.get(field)
        }
    )
    team_to_index = {team_id: index + 1 for index, team_id in enumerate(team_ids)}

    train_values = _empty_lists()
    validation_values = _empty_lists()
    holdout_values = _empty_lists()
    pad_hero = len(hero_ids)

    for (_, battle_id), battle_rows in sorted(rows_by_battle.items()):
        battle_rows.sort(key=lambda row: int(row.get("bp_order") or 0))
        history: list[dict[str, Any]] = []
        for row in battle_rows:
            is_excluded_future = bool(
                row["_season"] == target_season
                and str(row["match_id"]) in excluded_future_set
            )
            if _usable(row, hero_to_index) and not is_excluded_future:
                position = int(row["bp_order"])
                if not 1 <= position <= MAX_ACTIONS:
                    continue
                is_holdout = bool(
                    row["_season"] == target_season
                    and str(row["match_id"]) in holdout_set
                )
                is_validation = bool(
                    row["_season"] == target_season
                    and str(row["match_id"]) in validation_set
                )
                values = (
                    holdout_values
                    if is_holdout
                    else validation_values
                    if is_validation
                    else train_values
                )
                current_side = str(row["side"])
                usable_history = [
                    item
                    for item in history
                    if int(item["hero_id"]) in hero_to_index
                    and 0 < int(item["bp_order"]) < position
                ][-MAX_ACTIONS:]
                length = len(usable_history)
                history_heroes = [
                    hero_to_index[int(item["hero_id"])] for item in usable_history
                ]
                history_actions = [ACTION_INDEX[str(item["action"])] for item in usable_history]
                history_sides = [SIDE_INDEX[str(item["side"])] for item in usable_history]
                history_relations = [
                    RELATION_INDEX[
                        (str(item["action"]), str(item["side"]) == current_side)
                    ]
                    for item in usable_history
                ]
                history_positions = [int(item["bp_order"]) for item in usable_history]
                history_lags = [position - prior for prior in history_positions]
                padding = MAX_ACTIONS - length
                values["history_heroes"].append(history_heroes + [pad_hero] * padding)
                values["history_actions"].append(history_actions + [0] * padding)
                values["history_sides"].append(history_sides + [0] * padding)
                values["history_relations"].append(history_relations + [0] * padding)
                values["history_positions"].append(history_positions + [0] * padding)
                values["history_lags"].append(history_lags + [0] * padding)
                values["history_lengths"].append(length)
                values["next_actions"].append(ACTION_INDEX[str(row["action"])])
                values["next_sides"].append(SIDE_INDEX[current_side])
                values["next_positions"].append(position)
                values["next_team_slots"].append(int(row["team_action_type_number"]))
                values["acting_teams"].append(
                    team_to_index.get(str(row["acting_team_id"]), 0)
                )
                values["opponent_teams"].append(
                    team_to_index.get(str(row["opponent_team_id"]), 0)
                )
                legal_ids = {int(hero_id) for hero_id in row["legal_hero_ids"]}
                legal_ids -= second_ban_farm_conflicts(
                    action=str(row["action"]),
                    bp_order=position,
                    opponent_pick_ids=row.get("current_opponent_picks", []),
                    candidate_ids=legal_ids,
                    lane_masks=lane_masks,
                    feature_names=feature_names,
                )
                if int(row["selected_hero_id"]) not in legal_ids:
                    raise ValueError(
                        "Strategic ban mask removed an observed selection: "
                        f"season={row['_season']} match={row['match_id']} "
                        f"battle={row['battle_id']} bp_order={position} "
                        f"hero_id={row['selected_hero_id']}"
                    )
                values["legal_mask"].append(
                    [hero_id in legal_ids for hero_id in hero_ids]
                )
                values["targets"].append(hero_to_index[int(row["selected_hero_id"])])
                outcome_weight = (
                    winning_pick_weight
                    if row["action"] == "pick"
                    and row.get("acting_team_won_battle") is True
                    else 1.0
                )
                values["sample_weights"].append(
                    1.0
                    if is_holdout or is_validation
                    else season_weights[row["_season"]] * outcome_weight
                )
                values["match_ids"].append(str(row["match_id"]))
                values["battle_ids"].append(battle_id)

            selected = int(row.get("selected_hero_id") or 0)
            if (
                selected > 0
                and row.get("action") in ACTION_INDEX
                and row.get("side") in SIDE_INDEX
            ):
                history.append(
                    {
                        "hero_id": selected,
                        "action": str(row["action"]),
                        "side": str(row["side"]),
                        "bp_order": int(row.get("bp_order") or 0),
                    }
                )

    train = _as_tensor_dataset(train_values)
    validation = _as_tensor_dataset(validation_values)
    holdout = _as_tensor_dataset(holdout_values)
    if not len(train) or not len(validation) or not len(holdout):
        raise ValueError("The chronological split produced an empty dataset")
    if not bool(train.legal_mask.gather(1, train.targets[:, None]).all()):
        raise AssertionError("A training target is outside its legal mask")
    if not bool(holdout.legal_mask.gather(1, holdout.targets[:, None]).all()):
        raise AssertionError("A holdout target is outside its legal mask")
    if not bool(
        validation.legal_mask.gather(1, validation.targets[:, None]).all()
    ):
        raise AssertionError("A validation target is outside its legal mask")
    return PreparedData(
        train=train,
        validation=validation,
        holdout=holdout,
        hero_ids=hero_ids,
        hero_names=hero_names,
        team_ids=team_ids,
        feature_names=feature_names,
        hero_features=hero_features,
        training_seasons=training_seasons,
        season_weights=season_weights,
        validation_match_ids=validation_match_ids,
        holdout_match_ids=holdout_match_ids,
        excluded_future_match_ids=excluded_future_match_ids,
    )


def iter_batches(
    dataset: TensorDataset,
    *,
    batch_size: int,
    device: torch.device,
    shuffle: bool,
    generator: torch.Generator | None = None,
) -> Iterable[dict[str, Tensor]]:
    indices = (
        torch.randperm(len(dataset), generator=generator)
        if shuffle
        else torch.arange(len(dataset))
    )
    for start in range(0, len(dataset), batch_size):
        yield dataset.batch(indices[start : start + batch_size], device)


def weighted_choice_loss(logits: Tensor, targets: Tensor, weights: Tensor) -> Tensor:
    losses = F.cross_entropy(logits, targets, reduction="none")
    return (losses * weights).sum() / weights.sum()


@torch.inference_mode()
def evaluate(
    model: DraftChoiceModel,
    dataset: TensorDataset,
    *,
    batch_size: int,
    device: torch.device,
) -> dict[str, Any]:
    model.eval()
    total_loss = 0.0
    total_top1 = 0
    total_top5 = 0
    position_stats = {
        position: {"count": 0, "nll": 0.0, "top1": 0, "top5": 0}
        for position in range(1, MAX_ACTIONS + 1)
    }
    response_lag_stats = {
        lag: {"count": 0, "nll": 0.0, "top1": 0, "top5": 0}
        for lag in range(1, 6)
    }
    phase_ranges = {
        "opening_bans_and_first_pick": (1, 5),
        "first_pick_phase": (6, 10),
        "second_ban_phase": (11, 16),
        "closing_picks": (17, 20),
    }
    phase_stats = {
        name: {"count": 0, "nll": 0.0, "top1": 0, "top5": 0}
        for name in phase_ranges
    }

    def add_stats(stats: dict[str, float | int], mask: Tensor) -> None:
        stats["count"] += int(mask.sum().item())
        stats["nll"] += float(losses[mask].sum().item())
        stats["top1"] += int(correct1[mask].sum().item())
        stats["top5"] += int(correct5[mask].sum().item())

    for batch in iter_batches(
        dataset, batch_size=batch_size, device=device, shuffle=False
    ):
        logits = model(batch)
        losses = F.cross_entropy(logits, batch["targets"], reduction="none")
        ranking = logits.topk(k=5, dim=1).indices
        correct1 = ranking[:, 0].eq(batch["targets"])
        correct5 = ranking.eq(batch["targets"][:, None]).any(dim=1)
        total_loss += float(losses.sum().item())
        total_top1 += int(correct1.sum().item())
        total_top5 += int(correct5.sum().item())
        for position in batch["next_positions"].unique().tolist():
            mask = batch["next_positions"].eq(position)
            add_stats(position_stats[int(position)], mask)
        for lag, stats in response_lag_stats.items():
            mask = (
                batch["history_relations"].eq(RELATION_INDEX[("pick", False)])
                & batch["history_lags"].eq(lag)
            ).any(dim=1)
            if bool(mask.any()):
                add_stats(stats, mask)
        for name, (first, last) in phase_ranges.items():
            mask = batch["next_positions"].ge(first) & batch["next_positions"].le(last)
            if bool(mask.any()):
                add_stats(phase_stats[name], mask)

    count = len(dataset)
    by_position = {
        str(position): {
            "decisions": stats["count"],
            "negative_log_likelihood": stats["nll"] / stats["count"],
            "top_1_accuracy": stats["top1"] / stats["count"],
            "top_5_accuracy": stats["top5"] / stats["count"],
        }
        for position, stats in position_stats.items()
        if stats["count"]
    }

    def finalized(
        grouped_stats: dict[Any, dict[str, float | int]],
    ) -> dict[str, dict[str, float | int]]:
        return {
            str(name): {
                "decisions": int(stats["count"]),
                "negative_log_likelihood": float(stats["nll"]) / int(stats["count"]),
                "top_1_accuracy": int(stats["top1"]) / int(stats["count"]),
                "top_5_accuracy": int(stats["top5"]) / int(stats["count"]),
            }
            for name, stats in grouped_stats.items()
            if stats["count"]
        }

    return {
        "decisions": count,
        "negative_log_likelihood": total_loss / count,
        "top_1_accuracy": total_top1 / count,
        "top_5_accuracy": total_top5 / count,
        "by_position": by_position,
        "by_phase": finalized(phase_stats),
        "after_opponent_pick_by_lag": finalized(response_lag_stats),
    }


def train_model(
    model: DraftChoiceModel,
    train: TensorDataset,
    validation: TensorDataset,
    holdout: TensorDataset,
    *,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
    device: torch.device,
    seed: int,
) -> tuple[
    DraftChoiceModel,
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
]:
    model.to(device)
    trainable_parameters = [
        parameter for parameter in model.parameters() if parameter.requires_grad
    ]
    if not trainable_parameters:
        raise ValueError("Model has no trainable parameters")
    if getattr(model, "optimizer_kind", "adamw") == "adam":
        bias = getattr(model, "hero_bias", None)
        regular_parameters = [
            parameter for parameter in trainable_parameters if parameter is not bias
        ]
        parameter_groups: list[dict[str, Any]] = [
            {"params": regular_parameters, "weight_decay": weight_decay}
        ]
        if bias is not None and bias.requires_grad:
            parameter_groups.append({"params": [bias], "weight_decay": 0.0})
        optimizer = torch.optim.Adam(parameter_groups, lr=learning_rate)
    else:
        optimizer = torch.optim.AdamW(
            trainable_parameters, lr=learning_rate, weight_decay=weight_decay
        )
    generator = torch.Generator().manual_seed(seed)
    history = []
    best_state: dict[str, Tensor] | None = None
    best_nll = float("inf")
    for epoch in range(1, epochs + 1):
        model.train()
        weighted_loss_sum = 0.0
        weight_sum = 0.0
        for batch in iter_batches(
            train,
            batch_size=batch_size,
            device=device,
            shuffle=True,
            generator=generator,
        ):
            optimizer.zero_grad(set_to_none=True)
            logits = model(batch)
            loss = weighted_choice_loss(
                logits, batch["targets"], batch["sample_weights"]
            )
            regularization = getattr(model, "regularization_loss", None)
            if regularization is not None:
                loss = loss + regularization()
            loss.backward()
            if getattr(model, "clip_gradients", True):
                nn.utils.clip_grad_norm_(trainable_parameters, max_norm=1.0)
            optimizer.step()
            batch_weight = float(batch["sample_weights"].sum().item())
            weighted_loss_sum += float(loss.item()) * batch_weight
            weight_sum += batch_weight

        validation_metrics = evaluate(
            model, validation, batch_size=batch_size, device=device
        )
        row = {
            "epoch": epoch,
            "weighted_training_loss": weighted_loss_sum / weight_sum,
            "validation_negative_log_likelihood": validation_metrics[
                "negative_log_likelihood"
            ],
            "validation_top_1_accuracy": validation_metrics["top_1_accuracy"],
            "validation_top_5_accuracy": validation_metrics["top_5_accuracy"],
        }
        diagnostics = getattr(model, "diagnostics", None)
        if diagnostics is not None:
            row["model_diagnostics"] = diagnostics()
        history.append(row)
        if validation_metrics["negative_log_likelihood"] < best_nll:
            best_nll = validation_metrics["negative_log_likelihood"]
            best_state = {
                name: value.detach().cpu().clone()
                for name, value in model.state_dict().items()
            }
    if best_state is None:
        raise AssertionError("Training did not produce a checkpoint")
    model.load_state_dict(best_state)
    final_validation_metrics = evaluate(
        model, validation, batch_size=batch_size, device=device
    )
    final_holdout_metrics = evaluate(
        model, holdout, batch_size=batch_size, device=device
    )
    return (
        model,
        history,
        final_validation_metrics,
        final_holdout_metrics,
    )


@torch.inference_mode()
def benchmark_single_prediction(
    model: DraftChoiceModel,
    dataset: TensorDataset,
    *,
    device: torch.device,
    iterations: int = 200,
) -> dict[str, float]:
    model.eval()
    batch = dataset.batch(torch.tensor([0]), device)
    for _ in range(20):
        model(batch)
    started = time.perf_counter()
    for _ in range(iterations):
        model(batch)
    elapsed = time.perf_counter() - started
    return {
        "iterations": float(iterations),
        "total_seconds": elapsed,
        "mean_milliseconds": elapsed * 1000.0 / iterations,
    }


@torch.inference_mode()
def pairwise_example(
    model: PairwiseResponseModel,
    dataset: TensorDataset,
    data: PreparedData,
    *,
    device: torch.device,
) -> dict[str, Any]:
    model.eval()
    sample_index = next(
        (
            index
            for index in range(len(dataset))
            if dataset.history_lengths[index] >= 2
            and 2 in dataset.history_lags[index].tolist()
            and RELATION_INDEX[("pick", False)]
            in dataset.history_relations[index].tolist()
        ),
        0,
    )
    batch = dataset.batch(torch.tensor([sample_index]), device)
    logits, contributions = model(batch, return_contributions=True)
    target = int(batch["targets"][0].item())
    target_contributions = contributions[0, :, target].cpu()
    length = int(batch["history_lengths"][0].item())
    action_names = {value: key for key, value in ACTION_INDEX.items()}
    side_names = {value: key for key, value in SIDE_INDEX.items()}
    actions = []
    for index in range(length):
        hero_index = int(batch["history_heroes"][0, index].item())
        actions.append(
            {
                "bp_order": int(batch["history_positions"][0, index].item()),
                "lag": int(batch["history_lags"][0, index].item()),
                "action": action_names[int(batch["history_actions"][0, index].item())],
                "side": side_names[int(batch["history_sides"][0, index].item())],
                "hero_id": data.hero_ids[hero_index],
                "hero_name": data.hero_names[data.hero_ids[hero_index]],
                "target_logit_contribution": float(target_contributions[index].item()),
            }
        )
    probabilities = logits.softmax(dim=1)[0]
    top = probabilities.topk(k=5).indices.cpu().tolist()
    return {
        "match_id": dataset.match_ids[sample_index],
        "battle_id": dataset.battle_ids[sample_index],
        "next_bp_order": int(batch["next_positions"][0].item()),
        "observed_hero_id": data.hero_ids[target],
        "observed_hero_name": data.hero_names[data.hero_ids[target]],
        "top_5_predictions": [
            {
                "hero_id": data.hero_ids[index],
                "hero_name": data.hero_names[data.hero_ids[index]],
                "probability": float(probabilities[index].item()),
            }
            for index in top
        ],
        "history_contributions_to_observed_hero": actions,
    }


def save_checkpoint(
    path: Path,
    model: DraftChoiceModel,
    data: PreparedData,
    metrics: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "training_schema_version": 1,
            "model_type": model.model_name,
            "config": asdict(model.config),
            "hero_ids": data.hero_ids,
            "hero_names": data.hero_names,
            "team_ids": data.team_ids,
            "feature_names": data.feature_names,
            "state_dict": {
                name: value.detach().cpu() for name, value in model.state_dict().items()
            },
            "holdout_metrics": metrics,
        },
        path,
    )


def load_checkpoint(path: Path, device: torch.device) -> DraftChoiceModel:
    payload = torch.load(path, map_location=device, weights_only=False)
    config = ModelConfig(**payload["config"])
    state = payload["state_dict"]
    hero_features = state.get("hero_features")
    if hero_features is None:
        for feature_key in ("bag.hero_features", "app.hero_features"):
            hero_features = state.get(feature_key)
            if hero_features is not None:
                break
    if hero_features is None:
        raise KeyError("Checkpoint does not contain hero features")
    model_class = MODEL_TYPES[payload["model_type"]]
    model = model_class(config, hero_features)
    model.load_state_dict(state)
    if isinstance(model, HybridBagGRUModel):
        for parameter in model.bag.parameters():
            parameter.requires_grad_(False)
        model._bag_frozen = True
    if isinstance(model, HybridAppLagGRUModel):
        for parameter in model.app.parameters():
            parameter.requires_grad_(False)
        model._app_frozen = True
    model.to(device).eval()
    return model


def seed_everything(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
