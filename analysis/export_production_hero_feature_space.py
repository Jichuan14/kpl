#!/usr/bin/env python3
"""Export the frozen bag hero representation of the active production policy."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np

ANALYSIS_DIR = Path(__file__).resolve().parent


def production_vectors(model: dict) -> np.ndarray:
    if model.get('model_type') != 'sequence_familiarity_residual_choice' or not model.get('model_fingerprint'):
        raise ValueError('A fingerprinted production policy is required')
    base = model['base_artifact']
    features = np.asarray(model['hero_feature_matrix'], dtype=np.float32)
    bag = base['parameters']['bag']
    projection = np.asarray(bag['feature_projection.weight'], dtype=np.float32)
    residual = np.asarray(bag['hero_residual.weight'], dtype=np.float32)
    count = len(base['hero_ids'])
    if not count or features.ndim != 2 or features.shape != (count, len(base['feature_names'])):
        raise ValueError('Production hero features do not match the vocabulary')
    if projection.ndim != 2 or not projection.shape[0] or projection.shape[1] != features.shape[1] or residual.ndim != 2 or residual.shape[0] < count or residual.shape[1] != projection.shape[0]:
        raise ValueError('Production bag dimensions are inconsistent')
    vectors = features @ projection.T + residual[:count]
    if not np.isfinite(vectors).all() or not np.isfinite(features).all():
        raise ValueError('Production hero vectors contain non-finite values')
    return vectors


def build_feature_space(model: dict, league_id: str, decisions: list[dict]) -> dict:
    base = model['base_artifact']
    if base.get('target_season') != league_id:
        raise ValueError('Production policy belongs to a different season')
    vectors = production_vectors(model)
    centered = vectors - vectors.mean(axis=0, keepdims=True)
    _, singular, components = np.linalg.svd(centered, full_matrices=False)
    coordinates = np.zeros((len(vectors), 2))
    projected = centered @ components[:2].T
    coordinates[:, :projected.shape[1]] = projected
    variance = singular ** 2
    ratios = np.zeros(2)
    if variance.sum() > 0:
        ratios[:min(2, len(variance))] = (variance / variance.sum())[:2]
    distances = np.linalg.norm(vectors[:, None, :] - vectors[None, :, :], axis=2)
    hero_ids = [int(value) for value in base['hero_ids']]
    picks = Counter(int(row['selected_hero_id']) for row in decisions if row['action'] == 'pick')
    bans = Counter(int(row['selected_hero_id']) for row in decisions if row['action'] == 'ban')
    rows = []
    for index, hero_id in enumerate(hero_ids):
        values = dict(zip(base['feature_names'], model['hero_feature_matrix'][index]))
        lanes = [lane for lane in ('clash', 'mid', 'jungle', 'farm', 'roam') if values.get('lane__' + lane, 0) > 0]
        neighbors = [i for i in np.argsort(distances[index], kind='stable') if i != index][:5]
        rows.append({
            'hero_id': hero_id, 'hero_name': base.get('hero_names', {}).get(str(hero_id), str(hero_id)),
            'x': float(coordinates[index, 0]), 'y': float(coordinates[index, 1]),
            'primary_lane': lanes[0] if lanes else 'unknown',
            'damage_types': [damage for damage in ('physical', 'magic', 'true') if values.get('damage__' + damage, 0) > 0],
            'feature_known': bool(model['hero_feature_matrix'][index][-1]),
            'gameplay_mechanic_keys': [key for key, value in values.items() if value > 0 and key.startswith(('mechanic__', 'condition__'))],
            'pick_count': picks[hero_id], 'ban_count': bans[hero_id],
            'bp_action_count': picks[hero_id] + bans[hero_id],
            'weighted_bp_action_count': float(picks[hero_id] + bans[hero_id]),
            'nearest_hero_ids': [hero_ids[i] for i in neighbors],
        })
    return {
        'schema_version': 1, 'target_season': league_id, 'model_type': model['model_type'],
        'source_model_type': model['model_type'], 'source_model_fingerprint': model['model_fingerprint'],
        'source_branch': 'base_artifact.parameters.bag', 'source_space': 'production_frozen_bag_representation',
        'source_dimension': int(vectors.shape[1]), 'counts_scope': 'target_season_observed_decisions',
        'count_weighting': 'equal_weight_per_observed_action',
        'projection': 'pca', 'explained_variance_ratio': ratios.tolist(), 'rows': rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--league-id', required=True)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--decisions', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output_dir = ANALYSIS_DIR / 'outputs' / args.league_id
    model_path = args.model or output_dir / 'personalized_draft_choice_model.json'
    decisions_path = args.decisions or ANALYSIS_DIR / 'exports' / args.league_id / 'bp_decisions.jsonl'
    output = args.output or output_dir / 'learned_hero_feature_space.json'
    model = json.loads(model_path.read_text(encoding='utf-8'))
    decisions = [json.loads(line) for line in decisions_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    artifact = build_feature_space(model, args.league_id, decisions)
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', encoding='utf-8', dir=output.parent, delete=False) as temporary:
        json.dump(artifact, temporary, ensure_ascii=False, indent=2, allow_nan=False)
        temporary.write('\n')
        temporary_path = Path(temporary.name)
    temporary_path.replace(output)
    print(f'Wrote {len(artifact["rows"])} production hero coordinates to {output}')


if __name__ == '__main__':
    main()
