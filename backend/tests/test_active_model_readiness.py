import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app.models import League
from app.api import data


class ActiveModelReadinessTests(unittest.TestCase):
    def test_shared_bundle_readiness_is_independent_of_seasonal_models_and_deferred_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / 'exports' / 'season'; exports.mkdir(parents=True)
            outputs = root / 'outputs' / 'season'; outputs.mkdir(parents=True)
            files = ['draft_model.json', 'personalized_draft_choice_model.json', 'personalized_draft_probability_calibration.json', 'player_draft_context.json', 'ban_value_model.json', 'lineup_value_model.json', 'power_rankings.json', 'meta_hero_stats.jsonl', 'team_synergy_stats.jsonl']
            files += [filename for _, filename in data.STAT_ARTIFACTS] + [filename for _, filename, _ in data.TEAM_PROFILE_ARTIFACTS]
            for name in ('matches.jsonl', 'bp_decisions.jsonl'):
                path = exports / name; path.write_text('{}\n'); os.utime(path, (10, 10))
            for name in files:
                path = outputs / name; path.write_text('{}\n'); os.utime(path, (20, 20))
            (outputs / 'personalized_draft_choice_model.json').write_text(json.dumps({'model_fingerprint': 'current'}))
            os.utime(outputs / 'personalized_draft_choice_model.json', (20, 20))
            map_path = outputs / 'learned_hero_feature_space.json'
            valid = {'schema_version': 1, 'projection': 'pca', 'target_season': 'season', 'source_model_fingerprint': 'current', 'source_space': 'production_frozen_bag_representation', 'rows': [{'hero_id': 1, 'x': 0, 'y': 0}]}
            def write_map(value=valid, timestamp=30):
                map_path.write_text(json.dumps(value)); os.utime(map_path, (timestamp, timestamp))
            engine = create_engine('sqlite://')
            Base.metadata.create_all(engine)
            with Session(engine) as db, patch.object(data, 'REPO_ROOT', root), patch.object(data, 'EXPORT_DIR', root / 'exports'), patch.object(data, 'OUTPUT_DIR', root / 'outputs'), patch.object(data, 'PUBLISHED_DIR', root / 'published'):
                db.add(League(league_id='season', league_name='Season', year=2026, season=3, start_time='2026-01-01')); db.commit()
                handle=SimpleNamespace(metadata=lambda:{'model_version':'fixture','context_reference_cutoff':'2020-01-01'})
                with patch('app.services.model_registry.resolve_bundle',return_value=handle), patch('app.services.model_registry.REGISTRY_ROOT',root/'outputs/models'):
                    write_map()
                    opened_models = []
                    original_open = Path.open
                    def counted_open(path, *args, **kwargs):
                        if path.name == 'personalized_draft_choice_model.json': opened_models.append(path)
                        return original_open(path, *args, **kwargs)
                    with patch.object(Path, 'open', counted_open):
                        result = data.data_status('season', db).data
                    self.assertEqual(len(opened_models), 1)
                    self.assertTrue(result['analysis_ready'])
                    self.assertTrue(result['active_model']['ready'])
                    self.assertNotIn('learnable_draft_model', [row['key'] for row in result['pipeline']])
                    # Missing seasonal model files do not disable the shared active model.
                    for name in ['draft_model.json','player_draft_context.json','personalized_draft_choice_model.json','ban_value_model.json','lineup_value_model.json']:
                        (outputs/name).unlink()
                    self.assertTrue(data.data_status('season',db).data['analysis_ready'])
                    candidate=root/'outputs/models/candidates/latest_status.json';candidate.parent.mkdir(parents=True)
                    candidate.write_text(json.dumps({'status':'DEFERRED','reason':'Sparse current season','active_preserved':True}))
                    result=data.data_status('season',db).data
                    self.assertTrue(result['analysis_ready'])
                    self.assertEqual(result['model_candidate']['status'],'DEFERRED')
                for error in [FileNotFoundError('No current bundle'),ValueError('hash mismatch')]:
                    with patch('app.services.model_registry.resolve_bundle',side_effect=error):
                        result=data.data_status('season',db).data
                        self.assertFalse(result['analysis_ready'])
                        self.assertFalse(result['active_model']['ready'])
