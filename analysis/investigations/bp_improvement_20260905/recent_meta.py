"""Validation-selected mixture of neural policy and causal recent draft counts.

Predeclared search: half-life 7/14/28 days, mixture weight 0/.1/.25/.5.
All counts update only AFTER an entire calendar day. Test-series outcomes are
never used; observed draft choices may inform later test days.
This is an online backtest, not a frozen independent-test deployment claim.
"""
from collections import defaultdict
from datetime import datetime
import json

import numpy as np
import torch

from audit import ROOT, OUT, DEVICE, logits_for, metrics
from analysis.sequence_training.models import prepare_data, load_checkpoint, ACTION_INDEX


def recent_predictions(snapshot, data, half_life):
    current = snapshot / "analysis/exports/20260003"
    matches = sorted([json.loads(x) for x in (current / "matches.jsonl").read_text().splitlines()],
                     key=lambda r: (r["start_time"], r["match_id"]))
    rows = defaultdict(list)
    for line in (current / "bp_decisions.jsonl").read_text().splitlines():
        r = json.loads(line)
        if not r["is_peak_battle"] and r["selected_hero_id"] in data.hero_ids and r["selected_hero_id"] in r["legal_hero_ids"]:
            rows[r["match_id"]].append(r)
    hero_map = {h: i for i, h in enumerate(data.hero_ids)}
    h = len(hero_map)
    exact = np.zeros((21, h))
    action = np.zeros((3, h))
    result = {}
    previous = None
    days = defaultdict(list)
    for match in matches:
        days[match["start_time"][:10]].append(match)
    for day, day_matches in sorted(days.items()):
        date = datetime.fromisoformat(day)
        if previous is not None:
            decay = 0.5 ** ((date - previous).total_seconds() / 86400 / half_life)
            exact *= decay
            action *= decay
        previous = date
        # Completion times are unavailable. Delay updates to the next day to
        # avoid assuming an earlier-starting same-day series has finished.
        day_rows = [r for match in day_matches for r in rows[match["match_id"]]]
        for r in day_rows:
            order = int(r["bp_order"])
            if not 1 <= order <= 20:
                continue
            a = ACTION_INDEX[r["action"]]
            prior = (action[a] + 0.1) / (action[a].sum() + 0.1 * h)
            p = exact[order] + 20 * prior
            legal = np.array([hero in r["legal_hero_ids"] for hero in data.hero_ids])
            p = p * legal
            result[(r["battle_id"], order)] = p / p.sum()
        for r in day_rows:
            order = int(r["bp_order"])
            if 1 <= order <= 20:
                exact[order, hero_map[r["selected_hero_id"]]] += 1
                action[ACTION_INDEX[r["action"]], hero_map[r["selected_hero_id"]]] += 1
    return [torch.tensor(np.array([result[(battle, order)] for battle, order in zip(ds.battle_ids, ds.next_positions.tolist())]), dtype=torch.float32)
            for ds in (data.validation, data.holdout)]


def main():
    from pathlib import Path
    august = json.loads((OUT / "august_results.json").read_text())
    snapshot = Path(august["snapshot_path"])
    data = prepare_data(snapshot, target_season="20260003", previous_seasons=4,
                        validation_matches=12, holdout_matches=12, holdout_offset_matches=0,
                        recency_decay=0.65, winning_pick_weight=1.5)
    checkpoint = ROOT / "analysis/outputs/20260003/sequence_training/hybrid_bag_gru.pt"
    saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
    assert data.hero_ids == saved["hero_ids"]
    assert data.validation_match_ids == august["validation_matches"]
    assert data.holdout_match_ids == august["holdout_matches"]
    teams = {t: i + 1 for i, t in enumerate(saved["team_ids"])}
    remap = torch.tensor([0] + [teams.get(t, 0) for t in data.team_ids])
    for ds in (data.validation, data.holdout):
        ds.acting_teams = remap[ds.acting_teams]
        ds.opponent_teams = remap[ds.opponent_teams]
    model = load_checkpoint(checkpoint, DEVICE)
    temperature = august["august_validation_temperature"]
    vp = (logits_for(model, data.validation) / temperature).softmax(1)
    hp = (logits_for(model, data.holdout) / temperature).softmax(1)
    candidates, distributions = [], {}
    for half_life in [7, 14, 28]:
        recent_v, recent_h = recent_predictions(snapshot, data, half_life)
        distributions[half_life] = (recent_v, recent_h)
        for weight in [0.0, 0.1, 0.25, 0.5]:
            p = (1 - weight) * vp + weight * recent_v
            val = metrics(p.clamp_min(1e-30).log(), data.validation.targets)
            candidates.append(dict(half_life_days=half_life, recent_weight=weight, validation=val))
    selected = min(candidates, key=lambda r: r["validation"]["nll"])
    weight = selected["recent_weight"]
    final_p = (1 - weight) * hp + weight * distributions[selected["half_life_days"]][1]
    out = {"protocol": "Prequential counts use only earlier calendar days; fixed release neural weights; hyperparameters chosen only on Aug 12-16 validation NLL.",
           "validation_search": candidates, "selected": selected,
           "test_calibrated_neural": metrics(hp.clamp_min(1e-30).log(), data.holdout.targets),
           "test_selected_mixture": metrics(final_p.clamp_min(1e-30).log(), data.holdout.targets)}
    out["by_phase"] = {}
    for name, lo, hi in [("opening", 1, 5), ("first_picks", 6, 10), ("second_bans", 11, 16), ("closing", 17, 20)]:
        mask = (data.holdout.next_positions >= lo) & (data.holdout.next_positions <= hi)
        out["by_phase"][name] = {"baseline": metrics(hp[mask].clamp_min(1e-30).log(), data.holdout.targets[mask]),
                                 "mixture": metrics(final_p[mask].clamp_min(1e-30).log(), data.holdout.targets[mask])}
    (OUT / "recent_meta_results.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k not in ("validation_search", "by_phase")}, indent=2))


if __name__ == "__main__":
    main()
