"""Evaluate saved release on newly exported August games without changing live data.

Fixes the first 12 August series for calibration and the last 12 for testing.
No network calls and no writes to the source database or production artifacts.
"""
from pathlib import Path
import dataclasses
import hashlib
import json
import sqlite3
import sys
import tempfile

import numpy as np
import torch
import torch.nn.functional as F

from audit import ROOT, OUT, DEVICE, logits_for, metrics, temperature_fit
sys.path.insert(0, str(ROOT / "analysis"))
from export_match_data import list_matches, write_jsonl
from build_bp_decisions import write_decisions
from analysis.sequence_training.models import prepare_data, load_checkpoint
from app.services.draft_simulator import load_model, _legal_heroes, _predict_sequence, load_sequence_model


def subset(ds, mask):
    values = {}
    for field in dataclasses.fields(ds):
        value = getattr(ds, field.name)
        values[field.name] = value[mask] if isinstance(value, torch.Tensor) else [v for v, keep in zip(value, mask.tolist()) if keep]
    return type(ds)(**values)


def main():
    release_dir = ROOT / "analysis/outputs/20260003/sequence_training"
    checkpoint = release_dir / "hybrid_bag_gru.pt"
    saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
    experiment = json.loads((release_dir / "results.json").read_text())
    release_json = json.loads((release_dir.parent / "sequence_draft_choice_model.json").read_text())
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == release_json["training"]["source_checkpoint_sha256"]
    snapshot = Path(tempfile.mkdtemp(prefix="kpl-bp-audit-"))
    a = snapshot / "analysis"
    exports = a / "exports"
    exports.mkdir(parents=True)
    (a / "hero_draft_feature_vectors.json").symlink_to(ROOT / "analysis/hero_draft_feature_vectors.json")
    for season in experiment["training_seasons"]:
        if season != "20260003":
            (exports / season).symlink_to(ROOT / "analysis/exports" / season)
    current = exports / "20260003"
    current.mkdir()
    db = ROOT / "backend/data/kpl_bp.db"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        matches = [m for m in list_matches(conn, "20260003")
                   if str(m["start_time"] or "") < "2026-08-24"]
        write_jsonl(conn, matches, current / "matches.jsonl")
        heroes = conn.execute("SELECT hero_id, hero_name FROM heroes WHERE hero_id > 0 ORDER BY hero_id").fetchall()
    write_decisions(input_path=current / "matches.jsonl", output_path=current / "bp_decisions.jsonl",
                    hero_roster=[int(h["hero_id"]) for h in heroes],
                    hero_names={int(h["hero_id"]): h["hero_name"] for h in heroes})
    data = prepare_data(snapshot, target_season="20260003", previous_seasons=4,
                        validation_matches=12, holdout_matches=12, holdout_offset_matches=0,
                        recency_decay=0.65, winning_pick_weight=1.5)
    assert all(mid > "2026073199" for mid in data.validation_match_ids + data.holdout_match_ids)
    assert not (set(data.validation_match_ids + data.holdout_match_ids) & set(experiment["holdout_match_ids"]))
    assert data.hero_ids == saved["hero_ids"]
    team_map = {team: i + 1 for i, team in enumerate(saved["team_ids"])}
    remap = torch.tensor([0] + [team_map.get(team, 0) for team in data.team_ids])
    for ds in (data.validation, data.holdout):
        ds.acting_teams = remap[ds.acting_teams]
        ds.opponent_teams = remap[ds.opponent_teams]
    model = load_checkpoint(checkpoint, DEVICE)
    vl = logits_for(model, data.validation)
    hl = logits_for(model, data.holdout)
    temp = temperature_fit(vl, data.validation.targets)
    old_t = json.loads((OUT / "audit_results.json").read_text())["global_temperature"]
    out = {"source_checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
           "snapshot_path": str(snapshot), "training_seasons": experiment["training_seasons"],
           "validation_matches": data.validation_match_ids, "holdout_matches": data.holdout_match_ids,
           "validation_uncalibrated": metrics(vl, data.validation.targets),
           "test_uncalibrated": metrics(hl, data.holdout.targets),
           "august_validation_temperature": temp, "test_august_calibrated": metrics(hl, data.holdout.targets, temp),
           "july_temperature_transfer": old_t, "test_july_calibrated": metrics(hl, data.holdout.targets, old_t)}
    losses = F.cross_entropy(hl / temp, data.holdout.targets, reduction="none") - F.cross_entropy(hl, data.holdout.targets, reduction="none")
    ids = np.array(data.holdout.match_ids)
    blocks = np.array([(float(losses[ids == m].sum()), int((ids == m).sum())) for m in sorted(set(ids))])
    samples = np.random.default_rng(7).integers(0, len(blocks), size=(5000, len(blocks)))
    delta = blocks[samples, 0].sum(1) / blocks[samples, 1].sum(1)
    out["calibration_nll_delta_match_bootstrap_95pct"] = np.quantile(delta, [0.025, 0.975]).tolist()
    out["by_phase"] = {}
    for name, low, high in [("opening", 1, 5), ("first_picks", 6, 10), ("second_bans", 11, 16), ("closing", 17, 20)]:
        mask = (data.holdout.next_positions >= low) & (data.holdout.next_positions <= high)
        out["by_phase"][name] = metrics(hl[mask], data.holdout.targets[mask])

    # Replay the same observed prefixes through the actual NumPy API scorer,
    # including its additional role mask. All excluded targets stay in counts.
    base = load_model("20260003")
    sequence = load_sequence_model("20260003")
    rows = [json.loads(x) for x in (current / "bp_decisions.jsonl").read_text().splitlines()]
    row_map = {(r["battle_id"], int(r["bp_order"])): r for r in rows}
    api_logits = hl.clone()
    removed, missing, parity = [], [], []
    for idx, (battle_id, order) in enumerate(zip(data.holdout.battle_ids, data.holdout.next_positions.tolist())):
        row = row_map[(battle_id, order)]
        side = row["side"]
        other = "red" if side == "blue" else "blue"
        state = {"bp_order": order, "legal_hero_ids": row["legal_hero_ids"],
                 f"{side}_team_id": row["acting_team_id"], f"{other}_team_id": row["opponent_team_id"],
                 f"{side}_picks": row["current_team_picks"], f"{other}_picks": row["current_opponent_picks"],
                 f"{side}_bans": row["current_team_bans"], f"{other}_bans": row["current_opponent_bans"],
                 f"{side}_used_previous_battles": row["team_used_in_previous_battles"],
                 f"{other}_used_previous_battles": row["opponent_used_in_previous_battles"]}
        legal = set(_legal_heroes(base, state, row))
        api_logits[idx, torch.tensor([h not in legal for h in data.hero_ids])] = -1e9
        if row["selected_hero_id"] not in legal:
            removed.append({k: row[k] for k in ("match_id", "battle_id", "bp_order", "action", "selected_hero_name")})
        # Verify a spread of prefixes using the production NumPy path.
        if idx % 75 == 0:
            try:
                prediction = _predict_sequence(base, sequence, state, row)
            except ValueError as exc:
                missing.append(str(exc))
                continue
            probs = torch.zeros(len(data.hero_ids))
            hero_map = {h: i for i, h in enumerate(data.hero_ids)}
            for p in prediction:
                probs[hero_map[p["hero_id"]]] = p["probability"]
            parity.append(float((probs - api_logits[idx].softmax(0)).abs().max()))
    out["runtime_replay"] = {"actual_choice_excluded": removed, "sample_prefix_errors": missing,
                              "max_sample_numpy_torch_probability_error": max(parity) if parity else None,
                              "parity_prefixes": len(parity),
                              "all_rows_top1": float(api_logits.argmax(1).eq(data.holdout.targets).float().mean()),
                              "all_rows_top5": float(api_logits.topk(5, 1).indices.eq(data.holdout.targets[:, None]).any(1).float().mean())}
    if not removed:
        out["runtime_replay"]["test_metrics"] = metrics(api_logits, data.holdout.targets)
    else:
        out["runtime_replay"]["nll"] = "Infinite under hard zero probability for excluded targets; no conditional-on-survival NLL reported."
    (OUT / "august_results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
