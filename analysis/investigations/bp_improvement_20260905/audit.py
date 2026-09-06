"""Read-only model audit; writes diagnostics only beside this script.

Uses the preserved evaluation checkpoint, never the release refit. Calibration
is selected using validation labels only. The historical test is already used
by earlier experiments, so these results are exploratory, not a fresh test.
"""
from pathlib import Path
import json
import sys
from collections import Counter

import numpy as np
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from analysis.sequence_training.models import prepare_data, load_checkpoint, iter_batches
from app.services.draft_simulator import load_model, _legal_heroes

OUT = Path(__file__).resolve().parent
DEVICE = torch.device("cpu")
torch.set_num_threads(4)


@torch.inference_mode()
def logits_for(model, dataset):
    return torch.cat([model(batch) for batch in iter_batches(
        dataset, batch_size=256, device=DEVICE, shuffle=False)])


def metrics(logits, targets, temperature=1.0):
    p = (logits / temperature).softmax(1)
    ranking = p.topk(5, dim=1).indices
    top1 = ranking[:, 0].eq(targets)
    confidence = p.max(1).values
    ece = 0.0
    for low in np.linspace(0, 0.9, 10):
        mask = (confidence >= low) & (confidence < low + 0.1 + (1e-6 if low == 0.9 else 0))
        if mask.any():
            ece += float(mask.float().mean() * (confidence[mask].mean() - top1[mask].float().mean()).abs())
    return dict(n=len(targets), nll=float(F.cross_entropy(logits / temperature, targets)),
                top1=float(top1.float().mean()),
                top5=float(ranking.eq(targets[:, None]).any(1).float().mean()),
                brier=float((p.square().sum(1) - 2 * p.gather(1, targets[:, None]).squeeze(1) + 1).mean()),
                ece_10_bins=ece, mean_confidence=float(confidence.mean()))


def temperature_fit(logits, targets):
    grid = np.exp(np.linspace(np.log(0.5), np.log(2.0), 141))
    losses = [float(F.cross_entropy(logits / float(t), targets)) for t in grid]
    return float(grid[int(np.argmin(losses))])


def main():
    checkpoint = ROOT / "analysis/outputs/20260003/sequence_training_series/hybrid_bag_gru.pt"
    saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
    metadata = json.loads(checkpoint.with_name("results.json").read_text())
    data = prepare_data(ROOT, target_season="20260003", previous_seasons=4,
                        validation_matches=10, holdout_matches=10, holdout_offset_matches=0,
                        recency_decay=0.65, winning_pick_weight=1.5)
    assert data.hero_ids == saved["hero_ids"]
    assert data.validation_match_ids == metadata["validation_match_ids"]
    assert data.holdout_match_ids == metadata["holdout_match_ids"]
    # A historical season was added after the saved run. Map current preparation
    # IDs into the checkpoint's vocabulary; never reuse potentially shifted IDs.
    mapping = {team: i + 1 for i, team in enumerate(saved["team_ids"])}
    remap = torch.tensor([0] + [mapping.get(team, 0) for team in data.team_ids])
    for ds in (data.validation, data.holdout):
        ds.acting_teams = remap[ds.acting_teams]
        ds.opponent_teams = remap[ds.opponent_teams]
    model = load_checkpoint(checkpoint, DEVICE)
    validation_logits = logits_for(model, data.validation)
    test_logits = logits_for(model, data.holdout)
    baseline = metrics(test_logits, data.holdout.targets)
    assert abs(baseline["nll"] - saved["holdout_metrics"]["negative_log_likelihood"]) < 1e-5
    temperature = temperature_fit(validation_logits, data.validation.targets)
    calibrated = metrics(test_logits, data.holdout.targets, temperature)
    phase_t = {}
    phase_logits = test_logits.clone()
    phases = [("opening", 1, 5), ("first_picks", 6, 10), ("second_bans", 11, 16), ("closing", 17, 20)]
    phase_metrics = {}
    for name, low, high in phases:
        v = (data.validation.next_positions >= low) & (data.validation.next_positions <= high)
        h = (data.holdout.next_positions >= low) & (data.holdout.next_positions <= high)
        t = temperature_fit(validation_logits[v], data.validation.targets[v])
        phase_t[name] = t
        phase_logits[h] /= t
        phase_metrics[name] = {"baseline": metrics(test_logits[h], data.holdout.targets[h]),
                               "calibrated": metrics(phase_logits[h], data.holdout.targets[h])}
    # Paired match-block bootstrap, preserving dependence within an entire BO series.
    base_losses = F.cross_entropy(test_logits, data.holdout.targets, reduction="none").numpy()
    new_losses = F.cross_entropy(test_logits / temperature, data.holdout.targets, reduction="none").numpy()
    ids = np.array(data.holdout.match_ids)
    blocks = [(float((new_losses - base_losses)[ids == m].sum()), int((ids == m).sum())) for m in sorted(set(ids))]
    rng = np.random.default_rng(7)
    resamples = rng.integers(0, len(blocks), size=(5000, len(blocks)))
    blocks = np.array(blocks)
    deltas = blocks[resamples, 0].sum(1) / blocks[resamples, 1].sum(1)
    result = {"checkpoint": str(checkpoint.relative_to(ROOT)),
              "validation_matches": data.validation_match_ids, "holdout_matches": data.holdout_match_ids,
              "baseline": baseline, "global_temperature": temperature, "global_calibration": calibrated,
              "global_nll_delta_match_bootstrap_95pct": np.quantile(deltas, [0.025, 0.975]).tolist(),
              "phase_temperatures": phase_t, "phase_calibration": metrics(phase_logits, data.holdout.targets),
              "by_phase": phase_metrics}

    # Diagnose the exact currently loaded role-filter behavior. This role map
    # includes the full season; this is a runtime audit, not a temporal backtest.
    base_model = load_model("20260003")
    hero_set = set(saved["hero_ids"])
    rows = [json.loads(line) for line in (ROOT / "analysis/exports/20260003/bp_decisions.jsonl").read_text().splitlines()]
    totals, excluded, examples = Counter(), Counter(), []
    for row in rows:
        target = int(row.get("selected_hero_id") or 0)
        if row.get("is_peak_battle") or target not in hero_set or target not in row["legal_hero_ids"] or not row.get("acting_team_id") or not row.get("opponent_team_id"):
            continue
        side = row["side"]
        other = "red" if side == "blue" else "blue"
        state = {"legal_hero_ids": row["legal_hero_ids"],
                 f"{side}_picks": row["current_team_picks"], f"{other}_picks": row["current_opponent_picks"],
                 f"{side}_bans": row["current_team_bans"], f"{other}_bans": row["current_opponent_bans"],
                 f"{side}_used_previous_battles": row["team_used_in_previous_battles"]}
        legal = _legal_heroes(base_model, state, row)
        groups = ["all", row["action"], "holdout" if row["match_id"] in data.holdout_match_ids else "outside_holdout"]
        for group in groups:
            totals[group] += 1
            excluded[group] += target not in legal
        if target not in legal and len(examples) < 12:
            examples.append({k: row[k] for k in ("match_id", "battle_id", "bp_order", "action", "selected_hero_id", "selected_hero_name", "current_team_picks", "current_opponent_picks")})
    result["runtime_role_filter_audit"] = {"eligible_counts": dict(totals), "actual_choice_excluded": dict(excluded), "examples": examples,
                                             "caveat": "Full-season current role map; measures runtime exclusion, not a leakage-free filter benchmark."}
    (OUT / "audit_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("by_phase", "runtime_role_filter_audit")}, indent=2))
    print("Runtime role filter:", totals, excluded)


if __name__ == "__main__":
    main()
